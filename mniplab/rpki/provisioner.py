"""Lifecycle provisioner for the RPKI CA Service.

Runs Krill in testbed mode on the Node, with every file it writes under the
Lab directory. At start it makes a private TLS root and a leaf for the CA's
hostname, names that hostname in the Lab's hosts file, starts Krill, and
creates each Hosted CA under the testbed CA with the seven-step child sequence
of the Krill API. Mirrors ``mniplab.resolver.provisioner.ResolverProvisioner``.
"""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
from typing import Any, Dict, List, Sequence

from mininet.log import info

from mniplab.constants import SERVICE_READY_TIMEOUT
from mniplab.errors import ConfigurationError, KrillError
from mniplab.rpki.krill_client import KrillClient
from mniplab.rpki.models import HostedCA
from mniplab.rpki.service import RPKICAService, rpki_ca_services
from mniplab.utils import ensure_directory, wait_until


def stop_recorded_pid(node: Any, pid_file: str) -> None:
    """Stop the process a Node recorded in a PID file, and wait for it to exit.

    The PID is killed inside the Node, where it was recorded, and never by
    process name: several Labs may each run the same daemon. Nothing happens
    when the file is missing or holds no PID.

    Args:
        node: The Node the process runs in.
        pid_file: Host-side path of the file holding the PID.
    """
    if not os.path.exists(pid_file):
        return
    with open(pid_file, encoding="utf-8") as handle:
        pid = handle.read().strip()
    if not pid.isdigit():
        return
    node.cmd(
        f"kill {pid} 2>/dev/null; i=0; "
        f"while kill -0 {pid} 2>/dev/null && [ $i -lt 50 ]; do "
        f"sleep 0.1; i=$((i+1)); done; kill -9 {pid} 2>/dev/null; true"
    )


class RPKICAProvisioner:
    """Owns the RPKI CA lifecycle: TLS, hosts entry, config, Krill, and Hosted CAs.

    Krill runs in the Node's own namespaces. Its run directory is bind-mounted
    at ``/run/krill``, so the unix socket it serves is krillc's default socket
    in every Terminal on the Node.
    """

    KRILL_BINARY = "krill"
    GUEST_RUN_DIR = "/run/krill"
    CONFIG_FILENAME = "krill.conf"
    PID_FILENAME = "krill.pid"
    LOG_FILENAME = "krill.log"
    OUTPUT_FILENAME = "krill.out"
    PORT = 3000
    PARENT_HANDLE = "testbed"
    TLS_DAYS = 3650
    PERM_USER_RWX_GROUP_RX = 0o755
    READY_TIMEOUT = SERVICE_READY_TIMEOUT

    @staticmethod
    def _rpki_nodes(nodes: List[Any]) -> List[Any]:
        """Return the Nodes in nodes that carry an RPKI CA Service.

        Args:
            nodes: Nodes to filter.

        Returns:
            Only the Nodes running an RPKI CA.
        """
        return [node for node in nodes if rpki_ca_services(node)]

    @classmethod
    def ensure_available(cls, nodes: List[Any]) -> None:
        """Verify Krill is installed when any Node in nodes runs an RPKI CA.

        Args:
            nodes: Nodes to inspect.

        Raises:
            ConfigurationError: If krill is not found in PATH.
        """
        targets = cls._rpki_nodes(nodes)
        if targets and shutil.which(cls.KRILL_BINARY) is None:
            raise ConfigurationError(
                f"Node '{targets[0].name}': Krill binary '{cls.KRILL_BINARY}' not "
                f"found in PATH. The RPKI CA runs Krill; see the RPKI section of "
                f"docs/install.md."
            )

    @classmethod
    def bind_run_directory(cls, node: Any, net: Any) -> None:
        """Bind-mount the Node's Krill run directory at ``/run/krill``.

        Args:
            node: The RPKI CA Node.
            net: The Lab the Node belongs to.
        """
        host_dir = net.rpki_ca_dir(node.name, "run")
        ensure_directory(host_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        node.cmd(f"mkdir -p {cls.GUEST_RUN_DIR}")
        node.cmd(f"mount --bind {shlex.quote(host_dir)} {cls.GUEST_RUN_DIR}")

    @classmethod
    def _openssl(cls, node_name: str, ssl_dir: str, *args: str) -> None:
        """Run one ``openssl`` command in the certificate directory.

        Args:
            node_name: The RPKI CA Node, for the error message.
            ssl_dir: The directory the command runs in.
            *args: The ``openssl`` arguments, passed without a shell.

        Raises:
            ConfigurationError: If openssl is missing or the command fails.
        """
        try:
            subprocess.run(
                ["openssl", *args], cwd=ssl_dir, check=True,
                capture_output=True, text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            raise ConfigurationError(
                f"Node '{node_name}': openssl could not make the RPKI CA's TLS "
                f"certificates: {str(detail).strip()}"
            ) from exc

    @classmethod
    def generate_tls(cls, node_name: str, hostname: str, net: Any) -> None:
        """Make a private TLS root and a leaf certificate for the CA's hostname.

        The leaf is not a CA and carries the hostname as its SAN. Krill serves
        leaf and root as one chain from its data directory, and the root is
        written beside it for a Validator to trust.

        Args:
            node_name: The RPKI CA Node.
            hostname: The DNS name the leaf certificate names.
            net: The Lab the Node belongs to.

        Raises:
            ConfigurationError: If openssl fails.
        """
        root_path = net.rpki_ca_root_cert_path(node_name)
        ssl_dir = os.path.dirname(root_path)
        ensure_directory(ssl_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        with open(os.path.join(ssl_dir, "leaf.ext"), "w", encoding="utf-8") as handle:
            handle.write(
                f"subjectAltName=DNS:{hostname}\n"
                "basicConstraints=CA:FALSE\n"
                "extendedKeyUsage=serverAuth\n"
            )
        days = str(cls.TLS_DAYS)
        cls._openssl(
            node_name, ssl_dir, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", days, "-keyout", "root.key", "-out", "root.pem",
            "-subj", "/CN=mniplab-rpki-tls-root",
        )
        cls._openssl(
            node_name, ssl_dir, "req", "-newkey", "rsa:2048", "-nodes",
            "-keyout", "key.pem", "-out", "leaf.csr", "-subj", f"/CN={hostname}",
        )
        cls._openssl(
            node_name, ssl_dir, "x509", "-req", "-in", "leaf.csr",
            "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial",
            "-days", days, "-extfile", "leaf.ext", "-out", "leaf.pem",
        )
        with open(os.path.join(ssl_dir, "leaf.pem"), encoding="utf-8") as handle:
            leaf = handle.read()
        with open(root_path, encoding="utf-8") as handle:
            root = handle.read()
        with open(os.path.join(ssl_dir, "cert.pem"), "w", encoding="utf-8") as handle:
            handle.write(leaf + root)

    @classmethod
    def write_hosts_lines(cls, service: RPKICAService, net: Any) -> None:
        """Name the CA's Node by its hostname in the Lab's shared hosts file.

        Written before Krill starts, since Krill refuses IP-literal URIs for
        Hosted CAs and its own URIs name this hostname.

        Args:
            service: The RPKI CA Service, attached to its Node.
            net: The Lab whose hosts file is written.
        """
        with open(net.hosts_file, "a", encoding="utf-8") as handle:
            for line in service.hosts_lines():
                handle.write(f"{line}\n")

    @classmethod
    def render_config(cls, hostname: str, data_dir: str, log_file: str, token: str) -> str:
        """Render ``krill.conf`` for testbed mode with an existing TLS certificate.

        Args:
            hostname: The DNS name Krill and its testbed URIs answer to.
            data_dir: Host-side path of Krill's data directory.
            log_file: Host-side path of Krill's log file.
            token: The admin token.

        Returns:
            The configuration file content.
        """
        base = f"https://{hostname}:{cls.PORT}"
        return "\n".join([
            'ip = "0.0.0.0"',
            f"port = {cls.PORT}",
            f'data_dir = "{data_dir}"',
            'log_type = "file"',
            f'log_file = "{log_file}"',
            f'admin_token = "{token}"',
            f'service_uri = "{base}/"',
            'https_mode = "existing"',
            "unix_socket_enabled = true",
            f'unix_socket = "{cls.GUEST_RUN_DIR}/krill.sock"',
            "bgp_riswhois_enabled = false",
            "",
            "[testbed]",
            f'rrdp_base_uri = "{base}/rrdp/"',
            f'rsync_jail = "rsync://{hostname}/repo/"',
            f'ta_aia = "rsync://{hostname}/ta/ta.cer"',
            f'ta_uri = "{base}/ta/ta.cer"',
            "",
        ])

    @classmethod
    def write_config(cls, node_name: str, hostname: str, token: str, net: Any) -> str:
        """Write ``krill.conf`` into the Node's configuration directory.

        Args:
            node_name: The RPKI CA Node.
            hostname: The DNS name Krill answers to.
            token: The admin token.
            net: The Lab the Node belongs to.

        Returns:
            The host-side path of the written configuration file.
        """
        etc_dir = net.rpki_ca_dir(node_name, "etc")
        lib_dir = net.rpki_ca_dir(node_name, "lib")
        ensure_directory(etc_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        ensure_directory(lib_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        log_file = os.path.join(net.rpki_ca_dir(node_name, "run"), cls.LOG_FILENAME)
        config_path = os.path.join(etc_dir, cls.CONFIG_FILENAME)
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(cls.render_config(hostname, lib_dir, log_file, token))
        return config_path

    @classmethod
    def launch(cls, node: Any, config_path: str, net: Any) -> None:
        """Start Krill in the background on the Node and record its PID.

        The PID is the Node's own, since a Node has its own PID namespace; it
        is stopped from inside the Node, so it is written there too.

        Args:
            node: The RPKI CA Node.
            config_path: Host-side path of ``krill.conf``, the same path in the Node.
            net: The Lab the Node belongs to.
        """
        socket_path = net.rpki_ca_socket_path(node.name)
        if os.path.exists(socket_path):
            os.remove(socket_path)
        output = shlex.quote(f"{cls.GUEST_RUN_DIR}/{cls.OUTPUT_FILENAME}")
        pid_file = shlex.quote(f"{cls.GUEST_RUN_DIR}/{cls.PID_FILENAME}")
        node.cmd(
            f"nohup {cls.KRILL_BINARY} -c {shlex.quote(config_path)} "
            f"> {output} 2>&1 < /dev/null & echo $! > {pid_file}"
        )

    @classmethod
    def await_ready(cls, node: Any, net: Any, client: KrillClient) -> None:
        """Block until Krill answers on its API socket.

        Args:
            node: The RPKI CA Node.
            net: The Lab the Node belongs to.
            client: A client on the Node's Krill socket.

        Raises:
            ConfigurationError: If Krill did not answer within READY_TIMEOUT.
        """
        if not wait_until(client.is_running, cls.READY_TIMEOUT):
            run_dir = net.rpki_ca_dir(node.name, "run")
            raise ConfigurationError(
                f"Node '{node.name}': krill did not answer on its API socket within "
                f"{cls.READY_TIMEOUT:.0f}s. Check "
                f"{os.path.join(run_dir, cls.LOG_FILENAME)} and "
                f"{os.path.join(run_dir, cls.OUTPUT_FILENAME)}."
            )

    @staticmethod
    def _resources(hosted: HostedCA) -> Dict[str, str]:
        """Return a Hosted CA's resources in Krill's child-request grammar.

        Args:
            hosted: The Hosted CA.

        Returns:
            ``asn``, and comma-separated ``ipv4`` and ``ipv6`` prefix lists.
        """
        return {
            "asn": f"AS{hosted.asn}",
            "ipv4": ", ".join(p for p in hosted.prefixes if ":" not in p),
            "ipv6": ", ".join(p for p in hosted.prefixes if ":" in p),
        }

    @staticmethod
    def _holds_resources(ca: Dict[str, Any]) -> bool:
        """Report whether Krill has activated a CA with its resources.

        Args:
            ca: The CA's description from Krill.

        Returns:
            True once the CA holds an ASN and at least one prefix.
        """
        resources = ca.get("resources") or {}
        return bool(resources.get("asn")) and bool(resources.get("ipv4") or resources.get("ipv6"))

    @classmethod
    def provision_hosted_cas(
        cls, client: KrillClient, node_name: str, hosted_cas: Sequence[HostedCA]
    ) -> None:
        """Create the Hosted CAs under the testbed CA, then sign their starting objects.

        Each CA takes the seven-step child sequence of the Krill API. All of
        them are created before any is waited on, so Krill certifies them
        together rather than one after another. Starting ROAs and ASPAs are
        sent only once their CA holds its resources, since Krill refuses a
        ROA for a prefix the CA does not yet hold.

        Args:
            client: A client on the Node's Krill socket.
            node_name: The RPKI CA Node, for error messages.
            hosted_cas: The Hosted CAs to create.

        Raises:
            ConfigurationError: If Krill refuses a step, or a CA never activates.
        """
        parent = cls.PARENT_HANDLE
        for hosted in hosted_cas:
            handle = hosted.handle
            try:
                client.add_ca(handle)
                request = client.child_request(handle)
                response = client.add_child(
                    parent, handle, cls._resources(hosted), request["id_cert"]
                )
                client.add_parent(handle, parent, response)
                publisher = client.publisher_request(handle)
                repository = client.add_publisher(publisher)
                client.configure_repo(handle, repository)
            except KrillError as exc:
                raise ConfigurationError(
                    f"Node '{node_name}': Hosted CA {handle} refused setup: {exc}"
                ) from exc
        for hosted in hosted_cas:
            handle = hosted.handle
            try:
                if not wait_until(
                    lambda: cls._holds_resources(client.show_ca(handle)), cls.READY_TIMEOUT
                ):
                    raise ConfigurationError(
                        f"Node '{node_name}': Hosted CA {handle} did not receive its "
                        f"resources within {cls.READY_TIMEOUT:.0f}s."
                    )
                if hosted.roas:
                    client.update_roas(handle, added=hosted.roas, removed=[])
                if hosted.aspas:
                    client.update_aspas(handle, add_or_replace=hosted.aspas, remove=[])
            except KrillError as exc:
                raise ConfigurationError(
                    f"Node '{node_name}': Hosted CA {handle} refused setup: {exc}"
                ) from exc

    @classmethod
    def start_node_services(cls, node: Any, net: Any) -> None:
        """Start the RPKI CA on one Node and create its Hosted CAs.

        Args:
            node: The RPKI CA Node.
            net: The Lab the Node belongs to.
        """
        service = rpki_ca_services(node)[0]
        hostname = service.resolved_hostname
        cls.bind_run_directory(node, net)
        cls.generate_tls(node.name, hostname, net)
        cls.write_hosts_lines(service, net)
        token = secrets.token_hex(16)
        config_path = cls.write_config(node.name, hostname, token, net)
        cls.launch(node, config_path, net)
        client = KrillClient(net.rpki_ca_socket_path(node.name), token=token)
        cls.await_ready(node, net, client)
        cls.provision_hosted_cas(client, node.name, service.hosted_cas)

    @classmethod
    def stop_node_services(cls, node: Any, net: Any) -> None:
        """Stop the Node's Krill by its recorded PID and wait for it to exit.

        The Lab directory is removed after this, so Krill must be gone first.

        Args:
            node: The RPKI CA Node.
            net: The Lab the Node belongs to.
        """
        stop_recorded_pid(node, os.path.join(net.rpki_ca_dir(node.name, "run"), cls.PID_FILENAME))

    @classmethod
    def start_all(cls, net: Any, nodes: List[Any]) -> None:
        """Start the RPKI CA on every Node in nodes that carries one.

        Args:
            net: Network orchestrator instance.
            nodes: Nodes to inspect and start RPKI CAs on.
        """
        targets = cls._rpki_nodes(nodes)
        if not targets:
            return
        info("\n*** Starting RPKI CA\n")
        for node in targets:
            info(node.name + " ")
            cls.start_node_services(node, net)
        info("\n")

    @classmethod
    def stop_all(cls, net: Any, nodes: List[Any]) -> None:
        """Stop the RPKI CA on every Node in nodes that carries one.

        Args:
            net: The Lab the Nodes belong to.
            nodes: Nodes to inspect and stop RPKI CAs on.
        """
        targets = cls._rpki_nodes(nodes)
        if not targets:
            return
        info("\n*** Stopping RPKI CA\n")
        for node in targets:
            info(node.name + " ")
            cls.stop_node_services(node, net)
        info("\n")
