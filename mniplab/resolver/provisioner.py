"""Lifecycle provisioner for the Recursive Caching Resolver Service.

Owns the ephemeral runtime directories, ``unbound.conf`` and Root Hints file
rendering, and the Unbound daemon's lifecycle inside a Node's network and
mount namespaces. Mirrors ``mniplab.dns.provisioner.DNSProvisioner``, which
does the same job for the Authoritative DNS Service.
"""

from __future__ import annotations

import os
import shlex
import shutil
from typing import Any, List, Optional

from mininet.log import info

from mniplab.constants import SERVICE_READY_TIMEOUT
from mniplab.errors import ConfigurationError
from mniplab.resolver.models import ROOT_HINT_TTL, RootHint
from mniplab.resolver.control_client import UnboundControlClient
from mniplab.resolver.service import ResolverService, resolver_services
from mniplab.utils import ensure_directory, wait_until


class ResolverProvisioner:
    """Owns the Resolver Service lifecycle: directories, config, and unbound.

    Prepares ephemeral runtime directories on the host under
    the Lab directory, bind-mounts them into the Node's private
    mount namespace (``/etc/unbound``, ``/run/unbound``), renders
    ``unbound.conf`` and — in Iterative mode — an RFC 1035 Root Hints file,
    and starts Unbound listening on ``0.0.0.0`` and ``::`` port 53 with its
    control socket on a UNIX domain socket.
    """

    ETC_UNBOUND_PATH = "/etc/unbound"
    RUN_UNBOUND_PATH = "/run/unbound"

    CONFIG_FILENAME = "unbound.conf"
    ROOT_HINTS_FILENAME = "root.hints"
    CONTROL_SOCKET_FILENAME = "unbound.sock"
    PID_FILENAME = "unbound.pid"
    LOG_FILENAME = "unbound.log"
    UNBOUND_BINARY = "unbound"

    PERM_USER_RWX_GROUP_RX = 0o755
    READY_TIMEOUT = SERVICE_READY_TIMEOUT

    @classmethod
    def host_dir(cls, node_name: str, subdir: str = "", *, net: Any) -> str:
        """Return the host-side directory backing one of a Node's Unbound paths.

        Args:
            node_name: The resolving Node's name.
            subdir: ``'etc'`` or ``'run'``; omitted for the Node's base directory.
            net: The Lab the Node belongs to.

        Returns:
            The host-side path.
        """
        return net.resolver_dir(node_name, subdir)

    @classmethod
    def control_socket_host_path(cls, node_name: str, *, net: Any) -> str:
        """Return the host-side path of a Node's Unbound control socket.

        Args:
            node_name: The resolving Node's name.
            net: The Lab the Node belongs to.

        Returns:
            The host-side control socket path.
        """
        return net.resolver_control_socket_path(node_name)

    @classmethod
    def bind_service_directories(cls, node: Any) -> None:
        """Bind-mount a Node's Unbound directories into its own mount namespace.

        Args:
            node: The Node the Service is attached to.
        """
        net = node.net
        for container_path, subdir in (
            (cls.ETC_UNBOUND_PATH, "etc"),
            (cls.RUN_UNBOUND_PATH, "run"),
        ):
            host_dir = cls.host_dir(node.name, subdir, net=net)
            ensure_directory(host_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
            quoted_container = shlex.quote(container_path)
            node.cmd(f"mkdir -p {quoted_container}")
            node.cmd(f"mount --bind {shlex.quote(host_dir)} {quoted_container}")

    @classmethod
    def render_root_hints(cls, hints: List[RootHint]) -> str:
        """Render Root Hints into an RFC 1035 hints file.

        Args:
            hints: The Root Hints to seed the Iterative Walk with.

        Returns:
            The hints file content: one NS record for the root zone per hint,
            each followed by its glue A/AAAA records.
        """
        lines: List[str] = [
            "; Root Hints synthesized by Mininet-IPLab for this Lab's own root",
            "; nameservers. The public root servers are unreachable in an",
            "; isolated Lab, so the Iterative Walk starts here instead.",
        ]
        for hint in hints:
            lines.append(f".{' ' * 8}{ROOT_HINT_TTL}  IN  NS  {hint.fqdn}")
        for hint in hints:
            for address in hint.resolve_addresses():
                rtype = "AAAA" if ":" in address else "A"
                lines.append(
                    f"{hint.fqdn}  {ROOT_HINT_TTL}  IN  {rtype}  {address}"
                )
        lines.append("")
        return "\n".join(lines)

    @classmethod
    def _resolve_trust_anchor(cls, anchor: Any) -> str:
        """Resolve a trust anchor (Node, Zone, DSRecord, or string) to an RR string.

        Args:
            anchor: Trust anchor definition.

        Returns:
            Resource record string suitable for Unbound's trust-anchor directive.

        Raises:
            ValueError: If the anchor cannot be resolved to a valid record.
        """
        if isinstance(anchor, str):
            return anchor.strip().strip('"')
        if hasattr(anchor, "rdata") and hasattr(anchor, "name"):
            owner = anchor.name if anchor.name else "."
            return f"{owner} {anchor.ttl} IN {anchor.rtype} {anchor.rdata}"
        if hasattr(anchor, "ds_record"):
            ds = anchor.ds_record(digest_type=2)
            return f"{ds.name} {ds.ttl} IN DS {ds.rdata}"
        for s in getattr(anchor, "services", []):
            if hasattr(s, "primary_zones"):
                zone = s.primary_zones.get(".")
                if not zone:
                    signed_zones = [
                        z for z in s.primary_zones.values() if getattr(z, "dnssec", False)
                    ]
                    if signed_zones:
                        zone = signed_zones[0]
                    elif s.primary_zones:
                        zone = next(iter(s.primary_zones.values()))
                if zone:
                    ds = zone.ds_record(digest_type=2)
                    return f"{ds.name} {ds.ttl} IN DS {ds.rdata}"
        raise ValueError(f"Unable to resolve trust anchor from {anchor!r}")

    @classmethod
    def render_config(cls, service: ResolverService) -> str:
        """Render ``unbound.conf`` for a Resolver Service.

        Args:
            service: The Resolver Service being configured.

        Returns:
            The configuration file content.
        """
        run = cls.RUN_UNBOUND_PATH
        lines = [
            "server:",
            "    verbosity: 1",
            # The dev container runs as root and Unbound's own unbound user
            # cannot enter the Node's namespaces, so privilege dropping and
            # chroot are both disabled here.
            '    username: ""',
            '    chroot: ""',
            f'    directory: "{cls.ETC_UNBOUND_PATH}"',
            f'    pidfile: "{run}/{cls.PID_FILENAME}"',
            f'    logfile: "{run}/{cls.LOG_FILENAME}"',
            "    use-syslog: no",
            "    interface: 0.0.0.0",
            "    interface: ::",
            "    port: 53",
            "    do-ip4: yes",
            "    do-ip6: yes",
            "    do-udp: yes",
            "    do-tcp: yes",
        ]

        if service.trust_anchors:
            lines.extend([
                '    module-config: "validator iterator"',
                "    val-log-level: 2",
                "    val-permissive-mode: no",
            ])
        else:
            lines.append('    module-config: "iterator"')

        lines.extend([
            f"    cache-max-ttl: {service.max_ttl}",
            f"    cache-max-negative-ttl: {service.max_negative_ttl}",
            # Lab hierarchies live on RFC 1918 addresses, which Unbound
            # otherwise answers for locally instead of walking out to.
            "    unblock-lan-zones: yes",
            "    insecure-lan-zones: yes",
            "    do-not-query-localhost: no",
            # The full QNAME at every hop is what a Learner traces in a
            # capture; minimisation would hide the walk it teaches.
            "    qname-minimisation: no",
            "    extended-statistics: yes",
            "    statistics-cumulative: yes",
        ])

        for anchor in service.trust_anchors:
            rr = cls._resolve_trust_anchor(anchor)
            if rr:
                lines.append(f'    trust-anchor: "{rr}"')

        for rule in service.access_control:
            lines.append(f"    access-control: {rule.subnet} {rule.action}")

        if service.root_hints:
            lines.append(
                f'    root-hints: "{cls.ETC_UNBOUND_PATH}/{cls.ROOT_HINTS_FILENAME}"'
            )

        lines.extend([
            "",
            "remote-control:",
            "    control-enable: yes",
            # An absolute path makes this a UNIX socket, over which Unbound
            # needs no certificate of its own.
            f"    control-interface: {run}/{cls.CONTROL_SOCKET_FILENAME}",
        ])

        if service.forwarders:
            lines.extend(["", "forward-zone:", '    name: "."'])
            for forwarder in service.forwarders:
                lines.append(f"    forward-addr: {forwarder}")

        lines.append("")
        return "\n".join(lines)

    @classmethod
    def write_config(
        cls, node_name: str, service: ResolverService, net: Optional[Any] = None
    ) -> str:
        """Write ``unbound.conf`` and any Root Hints file for a resolving Node.

        Args:
            node_name: The resolving Node's name.
            service: The Resolver Service being configured.
            net: Optional Lab instance to resolve paths through.

        Returns:
            The host-side path to ``unbound.conf``.
        """
        if net is None:
            node = getattr(service, "node", None)
            if node is not None:
                net = getattr(node, "net", getattr(node, "lab", None))
        etc_dir = cls.host_dir(node_name, "etc", net=net)
        run_dir = cls.host_dir(node_name, "run", net=net)
        ensure_directory(etc_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        ensure_directory(run_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)

        if service.root_hints:
            hints_path = os.path.join(etc_dir, cls.ROOT_HINTS_FILENAME)
            with open(hints_path, "w", encoding="utf-8") as handle:
                handle.write(cls.render_root_hints(service.root_hints))

        config_path = os.path.join(etc_dir, cls.CONFIG_FILENAME)
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(cls.render_config(service))

        return config_path

    @classmethod
    def start_resolver_service(
        cls, node: Any, service: ResolverService, net: Optional[Any] = None
    ) -> None:
        """Bind directories, render config, and start Unbound in the Node namespace.

        Args:
            node: The resolving Node.
            service: The Resolver Service to start.
            net: Optional Lab instance to resolve paths through.
        """
        if net is None:
            net = getattr(node, "net", getattr(node, "lab", None))
        cls.bind_service_directories(node)
        cls.write_config(node.name, service, net=net)

        quoted_binary = shlex.quote(cls.UNBOUND_BINARY)
        quoted_config = shlex.quote(
            f"{cls.ETC_UNBOUND_PATH}/{cls.CONFIG_FILENAME}"
        )
        node.cmd(f"{quoted_binary} -c {quoted_config}")
        cls.await_ready(node, net=net)

    @classmethod
    def await_ready(cls, node: Any, net: Optional[Any] = None) -> None:
        """Block until the Node's Unbound answers on its control socket.

        ``start_resolver_service`` returns only once this has passed, so a
        caller may read the cache or set Access Control the moment the Lab
        has started.

        Args:
            node: The resolving Node.
            net: Optional Lab instance to resolve paths through.

        Raises:
            ConfigurationError: If the daemon did not answer within
                :attr:`READY_TIMEOUT` seconds.
        """
        if net is None:
            net = getattr(node, "net", getattr(node, "lab", None))
        client = UnboundControlClient(cls.control_socket_host_path(node.name, net=net))
        if not wait_until(client.is_running, cls.READY_TIMEOUT):
            log_path = os.path.join(cls.host_dir(node.name, "run", net=net), cls.LOG_FILENAME)
            raise ConfigurationError(
                f"Node '{node.name}': {cls.UNBOUND_BINARY} did not answer on its "
                f"control socket within {cls.READY_TIMEOUT:.0f}s. Check {log_path}."
            )

    @classmethod
    def start_node_services(cls, node: Any, net: Optional[Any] = None) -> None:
        """Start every Resolver Service attached to node.

        Args:
            node: The Node with Resolver Services.
            net: Optional Lab instance to resolve paths through.
        """
        if net is None:
            net = getattr(node, "net", getattr(node, "lab", None))
        for service in resolver_services(node):
            cls.start_resolver_service(node, service, net=net)

    @classmethod
    def stop_node_services(cls, node: Any) -> None:
        """Stop a Node's Resolver daemon and discard its Ephemeral state.

        The daemon and the ephemeral tree are per Node rather than per
        Service, so this runs once however many Resolver Services the Node
        carries; only the Access Control reset is per Service.

        Args:
            node: The Node with Resolver Services.
        """
        services = resolver_services(node)
        if not services:
            return

        node.cmd(f"pkill -x {cls.UNBOUND_BINARY} || true")
        net = getattr(node, "net", getattr(node, "lab", None))
        shutil.rmtree(cls.host_dir(node.name, net=net), ignore_errors=True)
        for service in services:
            service.reset_access_control()

    @classmethod
    def start_all(cls, net: Any, nodes: List[Any]) -> None:
        """Start Resolver Services on every resolving Node in nodes.

        Args:
            net: Network orchestrator instance.
            nodes: Nodes to inspect and start Resolver Services on.
        """
        targets = cls._resolving_nodes(nodes)
        if not targets:
            return

        info("\n*** Starting Resolver Services\n")
        for node in targets:
            info(node.name + " ")
            cls.start_node_services(node, net=net)
        info("\n")

    @classmethod
    def stop_all(cls, nodes: List[Any]) -> None:
        """Stop Resolver Services on every resolving Node in nodes.

        Args:
            nodes: Nodes to inspect and stop Resolver Services on.
        """
        targets = cls._resolving_nodes(nodes)
        if not targets:
            return

        info("\n*** Stopping Resolver Services\n")
        for node in targets:
            info(node.name + " ")
            cls.stop_node_services(node)
        info("\n")

    @staticmethod
    def _resolving_nodes(nodes: List[Any]) -> List[Any]:
        """Return the Nodes in nodes that have a Resolver Service attached.

        Args:
            nodes: Nodes to filter.

        Returns:
            Only the Nodes offering recursive resolution.
        """
        return [node for node in nodes if resolver_services(node)]

    @classmethod
    def ensure_available(cls, nodes: List[Any]) -> None:
        """Verify Unbound is installed when any Node in nodes runs a Resolver.

        A missing daemon is an environment fact, not a Lab-shape diagnostic,
        so it is checked here before ResolverDiagnostics reads the Lab.

        Args:
            nodes: Nodes to inspect.

        Raises:
            ConfigurationError: If unbound is not found in PATH.
        """
        resolving = cls._resolving_nodes(nodes)
        if resolving and shutil.which(cls.UNBOUND_BINARY) is None:
            raise ConfigurationError(
                f"Node '{resolving[0].name}': Unbound binary "
                f"'{cls.UNBOUND_BINARY}' not found in PATH. Ensure Unbound is "
                f"installed."
            )
