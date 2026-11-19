"""Lifecycle provisioner for the Validator Service.

Runs Routinator on the Node, with every file it writes under the Lab
directory. At start it fetches the Lab's TAL from the RPKI CA over Krill's
unix socket and trusts the CA's TLS root, then starts Routinator serving VRPs
over RTR and HTTP. It waits only until Routinator's HTTP answers, never for
the first VRPs. Mirrors ``mniplab.rpki.provisioner.RPKICAProvisioner``.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from typing import Any, List, Optional

from mininet.log import info

from mniplab.constants import SERVICE_READY_TIMEOUT
from mniplab.errors import ConfigurationError, KrillError
from mniplab.frrouter import config as frr_config
from mniplab.rpki.krill_client import KrillClient
from mniplab.rpki.provisioner import RPKICAProvisioner, stop_recorded_pid
from mniplab.rpki.service import ValidatorService, rpki_ca_services, validator_services
from mniplab.utils import ensure_directory, wait_until


class ValidatorProvisioner:
    """Owns the Validator lifecycle: TAL, trust, Routinator and its preflight.

    Routinator runs in the Node's own namespaces with host-side paths, since a
    Node sees the Lab directory where it is. Its RTR and HTTP listeners are
    IPv4 on 0.0.0.0, and its expiry is 600 seconds: the floor FRR enforces.
    """

    ROUTINATOR_BINARY = "routinator"
    TALS_DIRNAME = "tals"
    TAL_FILENAME = "registry.tal"
    CONFIG_FILENAME = ".routinator.conf"
    PID_FILENAME = "routinator.pid"
    LOG_FILENAME = "routinator.log"
    RTR_PORT = 3323
    HTTP_PORT = 8323
    RETRY_SECONDS = 5
    EXPIRE_SECONDS = 600
    PERM_USER_RWX_GROUP_RX = 0o755
    READY_TIMEOUT = SERVICE_READY_TIMEOUT

    @staticmethod
    def _validator_nodes(nodes: List[Any]) -> List[Any]:
        """Return the Nodes in nodes that carry a Validator Service.

        Args:
            nodes: Nodes to filter.

        Returns:
            Only the Nodes running a Validator.
        """
        return [node for node in nodes if validator_services(node)]

    @classmethod
    def ensure_available(cls, nodes: List[Any]) -> None:
        """Verify the RPKI module, Routinator and Krill are installed when a Validator is declared.

        The module is looked up through its module so a test can stand in
        for the host. Krill is needed too: the Lab's TAL comes from its RPKI CA.

        Args:
            nodes: Nodes to inspect.

        Raises:
            ConfigurationError: If FRR's RPKI module, routinator or krill is
                missing; the message points to the RPKI section of docs/install.md.
        """
        targets = cls._validator_nodes(nodes)
        if not targets:
            return
        name = targets[0].name
        if not frr_config.rpki_module_installed():
            raise ConfigurationError(
                f"Node '{name}': FRR's RPKI module (bgpd_rpki.so) is not installed, "
                f"which a Lab with a Validator needs. See the RPKI section of "
                f"docs/install.md."
            )
        for binary in (cls.ROUTINATOR_BINARY, RPKICAProvisioner.KRILL_BINARY):
            if shutil.which(binary) is None:
                raise ConfigurationError(
                    f"Node '{name}': '{binary}' not found in PATH. A Lab with a "
                    f"Validator needs Routinator and Krill; see the RPKI section of "
                    f"docs/install.md."
                )

    @staticmethod
    def _rpki_ca_node(net: Any) -> Optional[Any]:
        """Return the Lab's RPKI CA Node, if the Lab declares one.

        Args:
            net: The Lab to search.

        Returns:
            The first Node carrying an RPKI CA Service, or None.
        """
        for host in getattr(net, "hosts", []):
            if rpki_ca_services(host):
                return host
        return None

    @classmethod
    def fetch_tal(cls, node: Any, ca_node: Any, net: Any) -> str:
        """Fetch the Lab's TAL from the RPKI CA over its unix socket.

        Args:
            node: The Validator Node, for the error message.
            ca_node: The RPKI CA Node.
            net: The Lab the Nodes belong to.

        Returns:
            The trust anchor locator's text.

        Raises:
            ConfigurationError: If the RPKI CA does not serve its TAL.
        """
        client = KrillClient(net.rpki_ca_socket_path(ca_node.name))
        try:
            return client.tal()
        except KrillError as exc:
            raise ConfigurationError(
                f"Node '{node.name}': could not fetch the TAL from RPKI CA "
                f"'{ca_node.name}': {exc}"
            ) from exc

    @classmethod
    def write_tal(cls, node_name: str, net: Any, tal: str) -> None:
        """Write the TAL where Routinator reads extra TALs.

        Args:
            node_name: The Validator Node.
            net: The Lab the Node belongs to.
            tal: The trust anchor locator's text.
        """
        tals_dir = os.path.join(net.validator_dir(node_name, "etc"), cls.TALS_DIRNAME)
        ensure_directory(tals_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        with open(os.path.join(tals_dir, cls.TAL_FILENAME), "w", encoding="utf-8") as handle:
            handle.write(tal)

    @classmethod
    def write_config(cls, node_name: str, net: Any, ca_node: Any) -> None:
        """Write Routinator's configuration file into the Node's home directory.

        Routinator reads ``$HOME/.routinator.conf`` when no ``-c`` is given. A
        Learner's bare ``routinator vrps --noupdate`` in a Terminal on the Node
        then reads the Lab's cache and TAL, not an empty default. The server's
        command line sets the same values and takes precedence over this file.
        The paths are written as JSON strings, which are valid TOML basic
        strings for them.

        Args:
            node_name: The Validator Node.
            net: The Lab the Node belongs to.
            ca_node: The RPKI CA Node, whose TLS root Routinator trusts.
        """
        home = net.node_home_dir(node_name)
        ensure_directory(home, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        tals_dir = os.path.join(net.validator_dir(node_name, "etc"), cls.TALS_DIRNAME)
        root_cert = net.rpki_ca_root_cert_path(ca_node.name)
        config = (
            f"repository-dir = {json.dumps(net.validator_dir(node_name, 'lib'))}\n"
            f"extra-tals-dir = {json.dumps(tals_dir)}\n"
            "no-rir-tals = true\n"
            f"rrdp-root-certs = [{json.dumps(root_cert)}]\n"
            "disable-rsync = true\n"
            "allow-dubious-hosts = true\n"
        )
        config_path = os.path.join(home, cls.CONFIG_FILENAME)
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(config)

    @classmethod
    def launch(cls, node: Any, net: Any, service: ValidatorService, ca_node: Any) -> None:
        """Start Routinator in the background on the Node and record its PID.

        The first run's VRPs are not waited for: only the HTTP status endpoint
        is, in :meth:`await_ready`.

        Args:
            node: The Validator Node.
            net: The Lab the Node belongs to.
            service: The Validator Service, for its refresh interval.
            ca_node: The RPKI CA Node, whose TLS root Routinator trusts.
        """
        command = [
            cls.ROUTINATOR_BINARY,
            "--no-rir-tals",
            "--extra-tals-dir", os.path.join(net.validator_dir(node.name, "etc"), cls.TALS_DIRNAME),
            "-r", net.validator_dir(node.name, "lib"),
            "--rrdp-root-cert", net.rpki_ca_root_cert_path(ca_node.name),
            "--disable-rsync",
            "--allow-dubious-hosts",
            # No ASPA option: Routinator 0.16 validates ASPAs by default and
            # refuses the --enable-aspa that 0.15 needed.
            "server",
            "--rtr", f"0.0.0.0:{cls.RTR_PORT}",
            "--http", f"0.0.0.0:{cls.HTTP_PORT}",
            "--refresh", str(service.refresh),
            "--retry", str(cls.RETRY_SECONDS),
            "--expire", str(cls.EXPIRE_SECONDS),
            "--rtr-client-metrics",
        ]
        run_dir = net.validator_dir(node.name, "run")
        log_file = shlex.quote(os.path.join(run_dir, cls.LOG_FILENAME))
        pid_file = shlex.quote(os.path.join(run_dir, cls.PID_FILENAME))
        node.cmd(
            f"nohup {' '.join(shlex.quote(part) for part in command)} "
            f"> {log_file} 2>&1 < /dev/null & echo $! > {pid_file}"
        )

    @classmethod
    def await_ready(cls, node: Any, net: Any) -> None:
        """Block until Routinator's HTTP status endpoint answers inside the Node.

        The endpoint listens on the Node's own address space, so the probe runs
        inside the Node. Answering is not the same as having VRPs.

        Args:
            node: The Validator Node.
            net: The Lab the Node belongs to.

        Raises:
            ConfigurationError: If Routinator did not answer within READY_TIMEOUT.
        """
        status_url = shlex.quote(f"http://127.0.0.1:{cls.HTTP_PORT}/api/v1/status")

        def answering() -> bool:
            return bool(node.cmd(f"curl -sf --max-time 2 {status_url}").strip())

        if not wait_until(answering, cls.READY_TIMEOUT):
            log_file = os.path.join(net.validator_dir(node.name, "run"), cls.LOG_FILENAME)
            raise ConfigurationError(
                f"Node '{node.name}': {cls.ROUTINATOR_BINARY} did not answer its HTTP "
                f"status endpoint within {cls.READY_TIMEOUT:.0f}s. Check {log_file}."
            )

    @classmethod
    def start_node_services(cls, node: Any, net: Any) -> None:
        """Start the Validator on one Node.

        Args:
            node: The Validator Node.
            net: The Lab the Node belongs to.

        Raises:
            ConfigurationError: If the Lab has no RPKI CA, the TAL cannot be
                fetched, or Routinator does not answer.
        """
        service = validator_services(node)[0]
        ca_node = cls._rpki_ca_node(net)
        if ca_node is None:
            raise ConfigurationError(
                f"Node '{node.name}': a Validator needs an RPKI CA in the Lab, to take "
                f"its TAL and trust from, but this Lab declares none."
            )
        for subdir in ("etc", "run", "lib"):
            ensure_directory(net.validator_dir(node.name, subdir), cls.PERM_USER_RWX_GROUP_RX, silent=True)
        cls.write_tal(node.name, net, cls.fetch_tal(node, ca_node, net))
        cls.write_config(node.name, net, ca_node)
        cls.launch(node, net, service, ca_node)
        cls.await_ready(node, net)

    @classmethod
    def stop_node_services(cls, node: Any, net: Any) -> None:
        """Stop the Validator's Routinator by its recorded PID.

        Args:
            node: The Validator Node.
            net: The Lab the Node belongs to.
        """
        stop_recorded_pid(node, os.path.join(net.validator_dir(node.name, "run"), cls.PID_FILENAME))

    @classmethod
    def start_all(cls, net: Any, nodes: List[Any]) -> None:
        """Start the Validator on every Node in nodes that carries one.

        Args:
            net: Network orchestrator instance.
            nodes: Nodes to inspect and start Validators on.
        """
        targets = cls._validator_nodes(nodes)
        if not targets:
            return
        info("\n*** Starting Validators\n")
        for node in targets:
            info(node.name + " ")
            cls.start_node_services(node, net)
        info("\n")

    @classmethod
    def stop_all(cls, net: Any, nodes: List[Any]) -> None:
        """Stop the Validator on every Node in nodes that carries one.

        Args:
            net: The Lab the Nodes belong to.
            nodes: Nodes to inspect and stop Validators on.
        """
        targets = cls._validator_nodes(nodes)
        if not targets:
            return
        info("\n*** Stopping Validators\n")
        for node in targets:
            info(node.name + " ")
            cls.stop_node_services(node, net)
        info("\n")
