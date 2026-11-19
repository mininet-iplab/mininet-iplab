"""Network provisioners for mnIPLab node types.

Each provisioner encapsulates the setup logic for a specific node category,
allowing mnIPLab to remain a thin orchestrator while the provisioning
implementation details become independently testable.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shlex
import shutil
import time
from typing import Optional, List, Dict, Tuple, Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from mniplab.router import FRRouter
    from mniplab.exabgp.node import ExaBGPNode
    from mniplab.net import mnIPLab

from mininet.log import info, warn
from mininet.link import TCLink

from mniplab.cli import mnIPLabCLI
from mniplab.frrouter.config import FRRConfigBuilder
from mniplab.constants import (
    SERVICE_READY_TIMEOUT,
    get_daemon_for_protocol,
    get_valid_protocols,
)
from mniplab.dhcp.config import KeaConfigBuilder
from mniplab.dhcp.control_client import KeaControlClient
from mniplab.dhcp.pool import Pool
from mniplab.dhcp.relay import Relay, DHCPRelayDiagnostics
from mniplab.dhcp.router_advertisement import RouterAdvertisementDiagnostics
from mniplab.dhcp.service import DHCPService
from mniplab.errors import ConfigurationError, LinkError
from mniplab.service import ServiceMixin
from mniplab.utils import ensure_directory, wait_until
from mniplab.link import IPv6Intf, IPv6TCIntf
from mniplab import ipv6


class FRRProvisioner:
    """Handles FRR router configuration, protocol validation, and directory setup."""

    ETCFRR_PATH = '/etc/frr'
    PERM_USER_RWX_GROUP_RX = 0o755

    @staticmethod
    def normalize_daemons(daemons: Union[str, List[str], None]) -> List[str]:
        """Normalize daemon specification to list format."""
        if daemons is None:
            return []
        if isinstance(daemons, str):
            return [daemons]
        return list(daemons)

    @staticmethod
    def parse_protocols(proto: Union[str, List[str]], name: str,
                        existing_daemons: List[str]) -> List[str]:
        """Parse and validate protocol specification, returning daemon list."""
        if isinstance(proto, str):
            proto_str = proto.replace(',', '|')
            protos = [p.strip() for p in proto_str.split('|')]
        elif isinstance(proto, list):
            protos = proto
        else:
            protos = [proto]

        daemons = list(existing_daemons)
        for p in protos:
            p_upper = p.upper()
            try:
                daemon = get_daemon_for_protocol(p_upper)
                if daemon not in daemons:
                    daemons.append(daemon)
            except KeyError:
                valid_protocols = get_valid_protocols()
                raise ConfigurationError(
                    f"Unknown routing protocol '{p}' for router '{name}'\n"
                    f"Valid protocols are:\n" +
                    "\n".join(f"  - {proto}" for proto in valid_protocols[:10]) +
                    (f"\n  ... and {len(valid_protocols) - 10} more" if len(valid_protocols) > 10 else "") +
                    f"\n\nExample: proto='OSPF' or proto='OSPF,BGP'"
                )
        return daemons

    @classmethod
    def customize_private_dirs(cls, private_dirs: List[Union[str, Tuple[str, str]]],
                               target_path: str, replacement_path: str):
        """Customize private_dirs by replacing a specific path mapping."""
        return [
            d if not (isinstance(d, tuple) and d[0] == target_path)
            else (target_path, replacement_path)
            for d in private_dirs
        ]

    @classmethod
    def is_ephemeral_config_template(cls, config_template: str, net: Any) -> bool:
        """Is this FRR config directory binding the Lab's ephemeral default?

        Only the default binding is ephemeral, and only it is regenerated:
        matching the exact template rather than a path prefix keeps an
        Instructor's ``frr_dir`` persistent even when they point it inside
        the Lab directory.

        Args:
            config_template: The host path bound at ``/etc/frr``, still
                carrying its ``%(name)s`` placeholder.
            net: The Lab whose default binding is compared against.

        Returns:
            True if the binding is the ephemeral default.
        """
        return os.path.normpath(config_template) == os.path.normpath(net.frr_config_template())

    @classmethod
    def setup_config_dir(cls, name: str,
                         private_dirs: List[Union[str, Tuple[str, str]]],
                         net: Any,
                         **kwargs) -> None:
        """Setup FRR config directory with template files if needed."""
        frr_host_dir = None
        for d in private_dirs:
            if isinstance(d, tuple) and d[0] == cls.ETCFRR_PATH:
                frr_host_dir = d[1]
                break

        if frr_host_dir is None:
            return

        reset = cls.is_ephemeral_config_template(frr_host_dir, net=net)
        frr_host_dir = frr_host_dir.replace('%(name)s', name)
        config_builder = FRRConfigBuilder()
        try:
            config_builder.setup_config_dir(name, frr_host_dir, reset=reset, **kwargs)
        except (ConfigurationError, OSError) as e:
            # Raise rather than log: a router whose config directory could not be
            # prepared will not route, and the daemon-level failure it produces
            # later says nothing about the directory that caused it.
            raise ConfigurationError(
                f"Router '{name}': cannot prepare FRR config directory\n"
                f"Directory: {frr_host_dir}\n"
                f"Error: {e}\n"
                f"Check that the path is writable and its parent exists."
            ) from e

    @classmethod
    def add_router(cls, net, name: str,
                   private_dirs: Optional[List[Union[str, Tuple[str, str]]]] = None,
                   frr_dir: Optional[str] = None,
                   proto: Optional[Union[str, List[str]]] = None,
                   **kwargs) -> 'FRRouter':
        """Add a router to the network with FRR routing support.

        This is the implementation behind :meth:`mnIPLab.addRouter`.
        """
        from mniplab.router import FRRouter

        if frr_dir is not None and hasattr(net, 'persistent_config_template'):
            frr_dir = net.persistent_config_template(name, frr_dir)
        if private_dirs is None:
            private_dirs = net.router_private_dirs(name, frr_dir=frr_dir)
        elif frr_dir is not None:
            private_dirs = cls.customize_private_dirs(
                private_dirs, cls.ETCFRR_PATH, frr_dir
            )

        if proto:
            existing_daemons = cls.normalize_daemons(kwargs.get('daemons'))
            daemons = cls.parse_protocols(proto, name, existing_daemons)
            kwargs['daemons'] = daemons

        cls.setup_config_dir(name, private_dirs, net=net, **kwargs)

        router = net.addHost(
            name, cls=FRRouter, ip=None, privateDirs=private_dirs, **kwargs
        )
        router.net = net
        router.lab = net
        router.frr_daemons = cls.normalize_daemons(kwargs.get('daemons'))
        return router


class ExaBGPProvisioner:
    """Handles ExaBGP node configuration, directory setup, and FIFO provisioning."""

    ETCEXABGP_PATH = '/etc/exabgp'
    VAR_LOG_EXABGP = '/var/log/exabgp'
    VAR_RUN_EXABGP = '/var/run/exabgp'
    PERM_USER_RWX_GROUP_RX = 0o755

    @classmethod
    def resolve_config_directory(cls,
                                 private_dirs: List[Union[str, Tuple[str, str]]],
                                 name: str) -> str:
        """Resolve config directory from private_dirs for ExaBGP."""
        for d in private_dirs:
            if isinstance(d, tuple) and d[0] == cls.ETCEXABGP_PATH:
                return d[1].replace('%(name)s', name)
        raise ConfigurationError(f"Missing {cls.ETCEXABGP_PATH} privateDirs mapping")

    @classmethod
    def setup_directories(cls,
                          private_dirs: List[Union[str, Tuple[str, str]]],
                          exabgp_dir: Optional[str],
                          name: str) -> Tuple[Optional[str], Optional[str]]:
        """Setup ExaBGP log and run directories.

        Returns:
            Tuple of (host_log_dir, host_run_dir)
        """
        host_log_dir = None
        host_run_dir = None

        if exabgp_dir is not None:
            if '%(name)s' in exabgp_dir:
                base = exabgp_dir.replace('%(name)s', '').rstrip('/')
            else:
                base = exabgp_dir.rstrip('/')
            host_log_dir = os.path.join(base, 'logs', '%(name)s').replace('%(name)s', name)
            host_run_dir = os.path.join(base, 'run', '%(name)s').replace('%(name)s', name)
        else:
            for d in private_dirs:
                if isinstance(d, tuple):
                    if d[0] == cls.VAR_LOG_EXABGP:
                        host_log_dir = d[1].replace('%(name)s', name)
                    elif d[0] == cls.VAR_RUN_EXABGP:
                        host_run_dir = d[1].replace('%(name)s', name)

        if host_log_dir:
            ensure_directory(host_log_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        if host_run_dir:
            ensure_directory(host_run_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)

        return host_log_dir, host_run_dir

    @staticmethod
    def prepare_variables(name: str, **kwargs: Any) -> dict:
        """Prepare template variables for ExaBGP config builder."""
        return {
            'name': name,
            'neighbors': kwargs.get('neighbors', []),
            'routes': kwargs.get('routes', []),
            'local_as': kwargs.get('local_as', None),
        }

    @classmethod
    def add_exabgp(cls, net, name: str,
                   private_dirs: Optional[List[Union[str, Tuple[str, str]]]] = None,
                   exabgp_dir: Optional[str] = None,
                   cls_node: Optional[type] = None,
                   **kwargs) -> 'ExaBGPNode':
        """Add an ExaBGP speaker to the network.

        This is the implementation behind :meth:`mnIPLab.addExaBGP`.
        """
        from mniplab.exabgp.node import ExaBGPNode
        from mniplab.exabgp.config import ExaBGPConfigBuilder
        from mniplab.errors import ExaBGPError

        if private_dirs is None:
            private_dirs = net.exabgp_private_dirs(name)

        if exabgp_dir is not None:
            private_dirs = FRRProvisioner.customize_private_dirs(
                private_dirs, cls.ETCEXABGP_PATH, exabgp_dir
            )

        exabgp_host_dir = cls.resolve_config_directory(private_dirs, name)

        config_builder = ExaBGPConfigBuilder()
        variables = cls.prepare_variables(name, **kwargs)
        try:
            config_builder.setup_config_dir(name, exabgp_host_dir, variables)
        except (ConfigurationError, OSError) as e:
            raise ExaBGPError(f"Failed to setup ExaBGP config for {name}: {e}") from e

        cls.setup_directories(private_dirs, exabgp_dir, name)

        node_params = dict(kwargs)
        node_params.update(variables)
        node_params.pop('name', None)

        node = net.addHost(
            name,
            cls=cls_node or ExaBGPNode,
            ip=None,
            privateDirs=private_dirs,
            **node_params,
        )
        node.net = net
        node.lab = net

        if hasattr(node, 'ensure_fifos'):
            node.ensure_fifos()

        return node


class NATProvisioner:
    """Handles NAT default route auto-configuration."""

    @staticmethod
    def configure_default_route(link, node1, node2) -> None:
        """Auto-configure default route when linking to a MasqueradeNAT."""
        from mniplab.nodelib import MasqueradeNAT

        if isinstance(node1, MasqueradeNAT) and getattr(node1, '_peer_default_gw', False):
            raw = link.intf1.ip or link.intf1.params.get('ip')
            nat_ip = raw.split('/')[0] if raw else ''
            if nat_ip and 'defaultRoute' not in node2.params:
                node2.params['defaultRoute'] = f'via {nat_ip}'
        elif isinstance(node2, MasqueradeNAT) and getattr(node2, '_peer_default_gw', False):
            raw = link.intf2.ip or link.intf2.params.get('ip')
            nat_ip = raw.split('/')[0] if raw else ''
            if nat_ip and 'defaultRoute' not in node1.params:
                node1.params['defaultRoute'] = f'via {nat_ip}'


class VRFProvisioner:
    """Handles VRF interface attachments for router links."""

    @staticmethod
    def handle_attachments(link, params1: Dict, params2: Dict, node1, node2) -> None:
        """Handle VRF interface attachments for a link."""
        from mniplab.router import FRRouter
        if 'vrf' in params1 and isinstance(node1, FRRouter):
            node1.attach_interface(link.intf1.name, params1['vrf'])
        if 'vrf' in params2 and isinstance(node2, FRRouter):
            node2.attach_interface(link.intf2.name, params2['vrf'])


class DHCPProvisioner:
    """Owns the DHCP Service lifecycle: directories, config, and the Kea servers.

    A DHCP Service attaches to an already-built Node (``node.addService(...)``
    via :class:`mniplab.service.ServiceMixin`), which runs *after* Mininet has
    already mounted that Node's ``privateDirs`` — so, unlike the FRR and
    ExaBGP provisioners, this one cannot prepare its directories at Node
    construction time. Instead it binds them from *inside* the Node's own
    shell once the Lab starts: a capable Node already has a private mount
    namespace (see :mod:`mniplab.namespace`), so a bind mount performed there
    (``node.cmd('mount --bind ...')``) affects only that Node — the same
    outcome ``privateDirs`` gives FRR and ExaBGP, reached a stage later.

    CIDR-to-Interface resolution also happens here, at Lab start, once every
    Interface has the address a Lab Example gave it — mirroring how the IPv6
    pipeline resolves addresses only once interfaces exist, and
    reading the address *recorded* on the Interface rather than the kernel's
    current state.

    One Service may hold IPv4 Pools, IPv6 Pools, or both. The provisioner
    starts ``kea-dhcp4`` and/or ``kea-dhcp6`` accordingly — each with its
    own configuration, control socket, lease database and log — and stops
    every started server when the Service stops. That two processes back
    one Service is this class's business, never the Service's.
    """

    ETC_KEA_PATH = '/etc/kea'
    # Kea 3.2 enforces control sockets under this runtime directory.
    VAR_RUN_KEA_PATH = '/var/run/kea'
    VAR_LIB_KEA_PATH = '/var/lib/kea'
    # Family-paired per-server filenames, kept beside the original IPv4
    # constants those names name (callers that predate DHCPv6 keep working).
    CONFIG_FILENAME = 'kea-dhcp4.conf'
    CONTROL_SOCKET_FILENAME = 'kea4-ctrl-socket'
    LEASE_DATABASE_FILENAME = 'kea-leases4.csv'
    LOG_FILENAME = 'kea-dhcp4.log'
    CONFIG_FILENAMES = {4: 'kea-dhcp4.conf', 6: 'kea-dhcp6.conf'}
    CONTROL_SOCKET_FILENAMES = {4: 'kea4-ctrl-socket', 6: 'kea6-ctrl-socket'}
    LEASE_DATABASE_FILENAMES = {4: 'kea-leases4.csv', 6: 'kea-leases6.csv'}
    LOG_FILENAMES = {4: 'kea-dhcp4.log', 6: 'kea-dhcp6.log'}
    KEA_BINARIES = {4: 'kea-dhcp4', 6: 'kea-dhcp6'}
    PERM_USER_RWX_GROUP_RX = 0o755
    PERM_KEA_RUN_DIR = 0o750
    READY_TIMEOUT = SERVICE_READY_TIMEOUT

    @classmethod
    def host_dir(cls, node_name: str, subdir: str = '', *, net: Any) -> str:
        """Return the host-side directory backing one of a Node's Kea paths.

        Args:
            node_name: The serving Node's name.
            subdir: ``'etc'``, ``'run'`` or ``'lib'`` for the directory bound
                to ``/etc/kea``, ``/var/run/kea`` or ``/var/lib/kea``
                respectively; omitted for the Node's whole ephemeral tree.
            net: The Lab the Node belongs to.

        Returns:
            The host-side path.
        """
        return net.dhcp_dir(node_name, subdir)

    @classmethod
    def control_socket_host_path(cls, node_name: str, family: int = 4, *, net: Any) -> str:
        """Return the host-side path of a Node's Kea control socket.

        This is the same unix socket the Kea server opens inside the Node at
        ``VAR_RUN_KEA_PATH`` (per :meth:`render_config`), reached from the
        root namespace via the bind mount :meth:`bind_service_directories`
        sets up. The only path :class:`mniplab.dhcp.control_client.KeaControlClient`
        should ever be given. IPv4 and IPv6 servers use separate sockets.

        Args:
            node_name: The serving Node's name.
            family: The address family served (4 or 6).
            net: The Lab the Node belongs to.

        Returns:
            The host-side control socket path.
        """
        return net.dhcp_control_socket_path(node_name, family)

    @classmethod
    def bind_service_directories(cls, node: Any) -> None:
        """Bind-mount a Node's Kea directories into its own mount namespace.

        Idempotent: re-binding an already-bound mount point is harmless, so
        this needs no guard against being called more than once for the
        same Node.

        Args:
            node: The Node the Service is attached to.
        """
        net = node.net
        for container_path, subdir in (
            (cls.ETC_KEA_PATH, 'etc'),
            (cls.VAR_RUN_KEA_PATH, 'run'),
            (cls.VAR_LIB_KEA_PATH, 'lib'),
        ):
            host_dir = cls.host_dir(node.name, subdir, net=net)
            mode = (
                cls.PERM_KEA_RUN_DIR
                if subdir == 'run'
                else cls.PERM_USER_RWX_GROUP_RX
            )
            ensure_directory(host_dir, mode, silent=True)
            node.cmd(f'mkdir -p {container_path}')
            node.cmd(f'mount --bind {host_dir} {container_path}')

    @staticmethod
    def _recorded_addresses(intf: Any) -> Tuple[Optional[str], Optional[str]]:
        """Return the addresses *recorded* on an Interface, both families.

        These are the addresses the Interface object was given — ``ip`` for
        IPv4 (Mininet's own record) and ``ip6``/``prefixLen6`` for IPv6
        (:class:`mniplab.link._IPv6ConfigMixin`) — never what the kernel
        currently holds. That distinction is load-bearing, not a convenience:
        the DHCP Service starts *after* FRR has wiped IPv6 addresses off
        Router Interfaces and *before* the reconciliation pass restores
        them, so asking the kernel at this moment reports nothing and every
        IPv6 Pool on a Router would fail to resolve. Do not "simplify" this
        to a live read.

        Args:
            intf: The Interface to read.

        Returns:
            The recorded IPv4 and IPv6 addresses, each without prefix
            length; either may be ``None``.
        """
        return getattr(intf, 'ip', None), getattr(intf, 'ip6', None)

    @classmethod
    def _interface_on_network(cls, node: Any, network: Any) -> Optional[str]:
        """Return the name of ``node``'s Interface carrying an address on ``network``.

        Args:
            node: The Node whose Interfaces to search.
            network: The Subnet to match, as an ``ip_network``.

        Returns:
            The matching Interface's name, or ``None`` if it has none.
        """
        for intf in node.intfList():
            # Recorded state only — see _recorded_addresses.
            for recorded in cls._recorded_addresses(intf):
                if not recorded:
                    continue
                try:
                    if ipaddress.ip_address(recorded) in network:
                        return intf.name
                except ValueError:
                    continue
        return None

    @classmethod
    def _find_remote_interface(cls, net: Any, node: Any, network: Any) -> Optional[str]:
        """Find another Node in the Lab with an Interface on ``network``.

        Used only once a Pool's CIDR has already failed to match anything on
        its own Node, to tell an unmatched CIDR (a typo) apart from a CIDR
        that names a real Subnet reachable only through another Router (the
        relay case).

        Args:
            net: The Lab, whose ``hosts`` includes both Host and Router
                Nodes (a Router is added via ``addHost(cls=FRRouter, ...)``).
            node: The Service's own Node, excluded from the search.
            network: The Pool's CIDR, as an ``ip_network``.

        Returns:
            The name of the other Node carrying a matching Interface, or
            ``None`` if no Node in the Lab has one.
        """
        raw_hosts = getattr(net, 'hosts', [])
        hosts = raw_hosts if isinstance(raw_hosts, (list, tuple)) else []
        matching = [
            other for other in hosts
            if other is not node and cls._interface_on_network(other, network) is not None
        ]
        if not matching:
            return None
        for other in matching:
            if (
                getattr(other, 'is_router', False)
                or hasattr(other, 'frr_daemons')
                or other.__class__.__name__ == 'FRRouter'
            ):
                return other.name
        return matching[0].name

    @classmethod
    def _find_relay_covering_subnet(
        cls,
        net: Any,
        subnet_cidr: str,
        on_node: Optional[Any] = None,
    ) -> Optional[Tuple[Any, Relay]]:
        """Find a Relay in net that covers subnet_cidr.

        Args:
            net: The Lab network.
            subnet_cidr: The Subnet CIDR to match.
            on_node: If specified, only look for relays on this Node.

        Returns:
            Tuple of (relaying_node, relay) or None.
        """
        try:
            target_net = ipaddress.ip_network(subnet_cidr, strict=False)
        except ValueError:
            return None

        if on_node is not None:
            hosts = [on_node]
        else:
            raw_hosts = getattr(net, 'hosts', [])
            hosts = list(raw_hosts) if isinstance(raw_hosts, (list, tuple)) else []
        for host in hosts:
            relays = getattr(host, 'relays', None)
            if not isinstance(relays, (list, tuple)):
                continue
            for relay in relays:
                subnets = getattr(relay, 'subnets', None)
                if not isinstance(subnets, (list, tuple)):
                    continue
                for covered in subnets:
                    try:
                        cov_net = ipaddress.ip_network(covered, strict=False)
                        if cov_net == target_net:
                            return host, relay
                    except ValueError:
                        continue
        return None

    @classmethod
    def _find_interface_carrying_address(cls, node: Any, address: str) -> Optional[str]:
        """Return the name of node's Interface carrying the given IP address."""
        try:
            target_ip = ipaddress.ip_address(address)
        except ValueError:
            return None

        for intf in node.intfList():
            for recorded in cls._recorded_addresses(intf):
                if not recorded:
                    continue
                try:
                    recorded_ip = ipaddress.ip_address(recorded.split('/')[0])
                    if recorded_ip == target_ip:
                        return intf.name
                except ValueError:
                    continue
        return None

    @classmethod
    def resolve_interface(cls, node: Any, pool: Pool, net: Any) -> Optional[str]:
        """Resolve a Pool's CIDR to one of the Node's attached Interfaces.

        Args:
            node: The Node the DHCP Service is attached to.
            pool: The Pool whose CIDR names the Subnet to serve.
            net: The Lab, searched for a relay explanation when the CIDR
                does not match this Node.

        Returns:
            The name of the matching Interface, or ``None`` if the Pool is
            covered by a Relay elsewhere in the Lab (relayed Pools contribute
            no local Interface of their own).

        Raises:
            ConfigurationError: No attached Interface carries an address in
                the Pool's CIDR. If the CIDR matches an Interface elsewhere
                in the Lab without a Relay covering it, the message names
                relay as the reason. Otherwise the CIDR matches nothing in the
                Lab at all, which is usually a typo.
        """
        network = ipaddress.ip_network(pool.cidr, strict=False)
        interface_name = cls._interface_on_network(node, network)
        relay_match = cls._find_relay_covering_subnet(net, pool.cidr) if net is not None else None

        if interface_name is not None and relay_match is not None:
            relay_node, _ = relay_match
            raise ConfigurationError(
                f"{node.name}: Pool Subnet {pool.cidr} is both directly attached to "
                f"{node.name} ({interface_name}) and covered by a Relay on {relay_node.name}.\n"
                f"A Subnet is either directly served or relayed, never both."
            )

        if interface_name is not None:
            return interface_name

        if relay_match is not None:
            # Legal and relayed: a relayed pool contributes no local interface of its own
            return None

        remote_node_name = cls._find_remote_interface(net, node, network)
        if remote_node_name is not None:
            server_hint = "<service-ip>"
            for intf in getattr(node, "intfList", lambda: [])():
                for recorded in cls._recorded_addresses(intf):
                    if not recorded:
                        continue
                    try:
                        ip_obj = ipaddress.ip_address(recorded.split("/")[0])
                        if (pool.family == 6 and isinstance(ip_obj, ipaddress.IPv6Address) and not ip_obj.is_link_local) or \
                           (pool.family == 4 and isinstance(ip_obj, ipaddress.IPv4Address)):
                            server_hint = str(ip_obj)
                            break
                    except ValueError:
                        continue
                if server_hint != "<service-ip>":
                    break

            raise ConfigurationError(
                f"{node.name}: Pool CIDR {pool.cidr} belongs to a Subnet "
                f"attached to {remote_node_name}, not to {node.name}.\n"
                f"This needs DHCP relay. Declare a Relay on {remote_node_name} to forward DHCP requests "
                f"to this Service: {remote_node_name}.addRelay(Relay('{pool.cidr}', server='{server_hint}'))"
            )

        raise ConfigurationError(
            f"{node.name}: no attached Interface found for Pool CIDR {pool.cidr}\n"
            "Pool CIDR matches no Subnet in the Lab: check for typos."
        )

    @classmethod
    def resolve_interfaces(cls, node: Any, service: DHCPService, net: Any) -> Dict[str, str]:
        """Resolve every Pool's CIDR to one of the Node's attached Interfaces.

        Each Pool resolves independently. Directly-served Pools map to their
        matching Interface name; relayed Pools contribute no local Interface
        and are omitted from the mapping.

        Args:
            node: The Node the DHCP Service is attached to.
            service: The DHCP Service whose Pools to resolve.
            net: The Lab, passed through to :meth:`resolve_interface`.

        Returns:
            A mapping of Pool CIDR to Interface name for directly-served Pools.

        Raises:
            ConfigurationError: Any Pool's CIDR matches no attached Interface
                and is not covered by a Relay.
        """
        resolved: Dict[str, str] = {}
        for cidr, pool in service.pools.items():
            intf = cls.resolve_interface(node, pool, net)
            if intf is not None:
                resolved[cidr] = intf
        return resolved

    @classmethod
    def listening_interfaces(
        cls,
        node: Any,
        service: DHCPService,
        family: int = 4,
        net: Optional[Any] = None,
    ) -> List[str]:
        """Compute the set of Interfaces Kea should listen on.

        Includes interfaces directly resolved for pools of this family,
        plus any interface carrying the server address a Relay forwards to
        for relayed pools of this family.

        Args:
            node: The serving Node.
            service: The DHCP Service.
            family: Address family (4 or 6).
            net: The Lab network, if available.

        Returns:
            List of interface names Kea should listen on.
        """
        pools = service.pools_of_family(family)
        intfs: List[str] = [
            service.interface_names[cidr]
            for cidr in pools
            if cidr in service.interface_names and service.interface_names[cidr]
        ]

        if net is not None:
            for cidr in pools:
                if cidr not in service.interface_names or not service.interface_names[cidr]:
                    relay_match = cls._find_relay_covering_subnet(net, cidr)
                    if relay_match is not None:
                        _, relay = relay_match
                        srv_addr = (
                            relay.server_for_family(family, net=net)
                            if hasattr(relay, "server_for_family")
                            else relay.server
                        )
                        if srv_addr:
                            srv_intf = cls._find_interface_carrying_address(node, srv_addr)
                            if srv_intf:
                                listen_entry = (
                                    f"{srv_intf}/{srv_addr}" if family == 6 else srv_intf
                                )
                                if listen_entry not in intfs:
                                    intfs.append(listen_entry)

        return list(dict.fromkeys(intfs))

    @classmethod
    def render_config(
        cls,
        node_name: str,
        service: DHCPService,
        family: int = 4,
        net: Optional[Any] = None,
    ) -> str:
        """Render one Kea server's configuration for a Service and write it out.

        Args:
            node_name: The serving Node's name.
            service: The DHCP Service being configured, with its Pools'
                Interfaces already resolved.
            family: Which server's configuration to render (4 or 6).
            net: The Lab, used to resolve relay-facing listening interfaces.

        Returns:
            The config file's path as the Kea server will see it, inside the Node.
        """
        pools = service.pools_of_family(family)
        control_socket_path = os.path.join(
            cls.VAR_RUN_KEA_PATH, cls.CONTROL_SOCKET_FILENAMES[family]
        )
        lease_database_path = os.path.join(
            cls.VAR_LIB_KEA_PATH, cls.LEASE_DATABASE_FILENAMES[family]
        )
        node = getattr(service, "node", None)
        if node is not None:
            interfaces = cls.listening_interfaces(node, service, family, net)
        else:
            interfaces = [
                service.interface_names[cidr]
                for cidr in pools
                if cidr in service.interface_names and service.interface_names[cidr]
            ]

        config = KeaConfigBuilder.build(
            pools=pools.values(),
            interface_names=interfaces,
            control_socket_path=control_socket_path,
            lease_database_path=lease_database_path,
            family=family,
            subnet_interfaces=(
                {cidr: service.interface_names[cidr] for cidr in pools if cidr in service.interface_names}
                if family == 6 else None
            ),
        )

        host_config_path = os.path.join(
            cls.host_dir(node_name, 'etc', net=net), cls.CONFIG_FILENAMES[family]
        )
        try:
            os.makedirs(os.path.dirname(host_config_path), exist_ok=True)
            with open(host_config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
        except OSError as exc:
            raise ConfigurationError(
                f"{node_name}: failed to write Kea configuration to "
                f"{host_config_path}: {exc}"
            ) from exc

        return os.path.join(cls.ETC_KEA_PATH, cls.CONFIG_FILENAMES[family])

    @classmethod
    def _wait_for_link_local_dad(
        cls,
        node: Any,
        intf_name: str,
        timeout: float = 3.0,
        poll_interval: float = 0.05,
    ) -> None:
        """Wait for link-local DAD to settle on an Interface before starting kea-dhcp6.

        Linux runs Duplicate Address Detection on an Interface's link-local
        address when the link is brought up. While DAD is running, the
        address is marked 'tentative' in the kernel and any attempt to bind
        a socket to it fails with EADDRNOTAVAIL ('Cannot assign requested
        address'). Kea DHCPv6 binds a link-local socket on port 547 for every
        configured interface; starting kea-dhcp6 before DAD settles causes
        it to fail to open listening sockets (DHCPSRV_OPEN_SOCKET_FAIL).

        Args:
            node: The Node owning the Interface.
            intf_name: The Interface whose link-local address to check.
            timeout: Maximum seconds to wait before continuing.
            poll_interval: Seconds between polls.
        """
        elapsed = 0.0
        quoted_intf = shlex.quote(intf_name)
        while elapsed < timeout:
            output = node.cmd(f"ip -6 addr show dev {quoted_intf}")
            if not isinstance(output, str) or "tentative" not in output:
                return
            time.sleep(poll_interval)
            elapsed += poll_interval

    @classmethod
    def start_dhcp_service(cls, node: Any, service: DHCPService, net: Any) -> None:
        """Bind directories, resolve Pools, and start one Kea server per family.

        A Service holding only IPv6 Pools starts only kea-dhcp6; one holding
        both families starts both, each with its own configuration, control
        socket, lease database and log.

        Args:
            node: The Node the Service is attached to.
            service: The DHCP Service to start.
            net: The Lab, passed through to :meth:`resolve_interfaces` for
                the relay explanation.
        """
        cls.bind_service_directories(node)
        DHCPRelayProvisioner.validate_no_relay_service_collision(node)
        service.interface_names = cls.resolve_interfaces(node, service, net)

        if service.serves_ipv6:
            for msg in RouterAdvertisementDiagnostics.diagnose_service(
                service, net
            ):
                warn(f"*** Warning: {msg}\n")
            for msg in DHCPRelayDiagnostics.diagnose_reservations(
                service, net
            ):
                warn(f"*** Warning: {msg}\n")

        for family in (4, 6):
            if not service.pools_of_family(family):
                continue
            if family == 6:
                listening = cls.listening_interfaces(node, service, family=6, net=net)
                for intf_entry in listening:
                    intf_name = intf_entry.split("/")[0]
                    cls._wait_for_link_local_dad(node, intf_name)
            config_path = cls.render_config(node.name, service, family=family, net=net)
            log_path = os.path.join(cls.VAR_RUN_KEA_PATH, cls.LOG_FILENAMES[family])
            binary = shlex.quote(cls.KEA_BINARIES[family])
            quoted_config = shlex.quote(config_path)
            quoted_log = shlex.quote(log_path)
            node.cmd(
                f"nohup {binary} -c {quoted_config} -d "
                f"> {quoted_log} 2>&1 &"
            )
            cls.await_ready(node, family, net=net)

    @classmethod
    def await_ready(cls, node: Any, family: int, net: Optional[Any] = None) -> None:
        """Block until the Node's Kea server for one family answers commands.

        ``start_dhcp_service`` returns only once this has passed, so a caller
        may query Leases or set a Pool the moment the Lab has started.

        Args:
            node: The serving Node.
            family: The address family whose server was just launched.
            net: Optional Lab network to resolve socket path through.

        Raises:
            ConfigurationError: If the server did not answer on its control
                socket within :attr:`READY_TIMEOUT` seconds.
        """
        if net is None:
            net = getattr(node, 'net', getattr(node, 'lab', None))
        if not isinstance(getattr(net, 'lab_key', None), str):
            net = None
        client = KeaControlClient(
            cls.control_socket_host_path(node.name, family=family, net=net), family=family
        )
        if not wait_until(client.is_running, cls.READY_TIMEOUT):
            log_path = os.path.join(
                cls.host_dir(node.name, 'run', net=net), cls.LOG_FILENAMES[family]
            )
            raise ConfigurationError(
                f"Node '{node.name}': {cls.KEA_BINARIES[family]} did not answer on "
                f"its control socket within {cls.READY_TIMEOUT:.0f}s. "
                f"Check {log_path}."
            )

    @classmethod
    def stop_dhcp_service(cls, node: Any) -> None:
        """Stop every Kea process running for this Node, if any.

        ``pkill`` is scoped to the Node's own PID namespace, exactly as
        ``ExaBGPNode.terminate()``'s ``pkill -f 'exabgp...'`` is — this only
        ever reaches the Kea processes inside this Node. Stopping the
        Service also discards Learner edits and restores the declared Pools.

        Args:
            node: The Node whose DHCP Service(s) are stopping.
        """
        for binary in cls.KEA_BINARIES.values():
            quoted_pattern = shlex.quote(f"{binary} -c")
            node.cmd(f"pkill -f {quoted_pattern} || true")
        for service in getattr(node, 'services', []):
            if isinstance(service, DHCPService):
                service.reset_pools()


    @classmethod
    def start_node_services(cls, node: Any, net: Any) -> None:
        """Start every DHCP Service attached to one Node.

        Args:
            node: A :class:`~mniplab.service.ServiceMixin` Node.
            net: The Lab, passed through to :meth:`start_dhcp_service` for
                the relay explanation.
        """
        for service in node.services:
            if isinstance(service, DHCPService):
                cls.start_dhcp_service(node, service, net)

    @classmethod
    def stop_node_services(cls, node: Any) -> None:
        """Stop every DHCP Service attached to one Node.

        Args:
            node: A :class:`~mniplab.service.ServiceMixin` Node.
        """
        if any(isinstance(service, DHCPService) for service in node.services):
            cls.stop_dhcp_service(node)

    @classmethod
    def start_all(cls, net: 'mnIPLab', nodes: List[Any]) -> None:
        """Start DHCP Services on every Node that has one, Mininet-style.

        Silent when no Node in the Lab has a DHCP Service attached, unlike
        the FRR/ExaBGP start callbacks, which are only ever registered for
        Nodes their own ``add*`` method created. Every Host and Router
        carries :class:`~mniplab.service.ServiceMixin`, whether or not a
        Lab Example ever calls ``addService``, so the filtering that would
        otherwise happen in the lifecycle registry has to happen here.

        Args:
            net: The Lab, so each Node's ephemeral directory can be tracked
                for cleanup on stop.
            nodes: Every :class:`~mniplab.service.ServiceMixin` Node in
                the Lab.
        """
        targets = [n for n in nodes if any(isinstance(s, DHCPService) for s in n.services)]
        if not targets:
            return

        info('\n*** Starting DHCP Services\n')
        for node in targets:
            info(node.name + ' ')
            cls.start_node_services(node, net)
        info('\n')

    @classmethod
    def stop_all(cls, nodes: List[Any]) -> None:
        """Stop DHCP Services on every Node that has one, Mininet-style.

        Args:
            nodes: Every :class:`~mniplab.service.ServiceMixin` Node in
                the Lab.
        """
        targets = [n for n in nodes if any(isinstance(s, DHCPService) for s in n.services)]
        if not targets:
            return

        info('\n*** Stopping DHCP Services\n')
        for node in targets:
            info(node.name + ' ')
            cls.stop_node_services(node)
        info('\n')


class DHCPRelayProvisioner:
    """Owns the DHCP Relay lifecycle: address derivation, command construction, and process management.

    A Relay attaches to a Node via ``node.addRelay(...)`` and forwards DHCP
    exchanges for its covered Subnets to a DHCP Service. It runs dnsmasq in
    relay-only mode (``--port=0 --log-dhcp --dhcp-relay=...``).
    """

    PERM_USER_RWX_GROUP_RX = 0o755
    BINARY = 'dnsmasq'
    GUEST_LOG_DIR = '/var/log/dnsmasq'
    _STARTUP_ERRORS = (
        'failed to',
        'bad command line',
        'cannot read',
        'fatal',
        'bad option',
        'failed to bind',
        'cannot run',
    )

    @classmethod
    def host_dir(cls, node_name: str, *, net: Any) -> str:
        """Return the host-side directory backing one Node's relay log.

        Args:
            node_name: The relaying Node's name.
            net: The Lab the Node belongs to.

        Returns:
            The directory path on the host.
        """
        return net.dhcp_relay_dir(node_name)

    @classmethod
    def log_path(cls, node_name: str, *, net: Any) -> str:
        """Return the host-side path to a Node's relay log.

        Args:
            node_name: The relaying Node's name.
            net: The Lab the Node belongs to.

        Returns:
            The log file path on the host.
        """
        return net.dhcp_relay_log_path(node_name)

    @classmethod
    def read_startup_error(cls, log_file: str) -> Optional[str]:
        """Return the first recognised dnsmasq startup error from a relay log.

        A Relay is started with ``--log-dhcp`` unconditionally, so its log is
        the only per-packet record; this reads that log once at start time to
        surface a failed bind or bad command line as a teachable error rather
        than a silent no-op. A missing or unreadable log is not an error here:
        the process may not have written anything yet.

        Args:
            log_file: Host-side path to the relay log.

        Returns:
            The first recognised error line, or None if none is present.
        """
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                log_content = f.read()
        except OSError:
            return None
        for line in log_content.splitlines():
            if any(err in line.lower() for err in cls._STARTUP_ERRORS):
                return line
        return None

    @classmethod
    def ensure_available(cls) -> None:
        """Validate dnsmasq installation and availability.

        Raises:
            ConfigurationError: If dnsmasq binary is not installed or not in PATH.
        """
        if shutil.which(cls.BINARY) is None:
            raise ConfigurationError(
                "dnsmasq not installed or not in PATH.\n"
                "Install dnsmasq for DHCP relay:\n"
                "  apt-get install dnsmasq-base"
            )

    @classmethod
    def resolve_local_address(cls, node: Any, cidr: str, net: Optional[Any] = None) -> str:
        """Derive the local IP address for ``cidr`` from ``node``'s Interfaces.

        Reads recorded Interface state only, never the kernel.

        Args:
            node: The relaying Node.
            cidr: The covered Subnet CIDR.
            net: Optional Lab network to detect if the CIDR belongs to another node.

        Returns:
            The local IP address without prefix length.

        Raises:
            ConfigurationError: No attached Interface carries an address in ``cidr``.
        """
        network = ipaddress.ip_network(cidr, strict=False)
        for intf in node.intfList():
            for recorded in DHCPProvisioner._recorded_addresses(intf):
                if not recorded:
                    continue
                try:
                    ip = ipaddress.ip_address(recorded.split('/')[0])
                    if ip in network:
                        return str(ip)
                except ValueError:
                    continue

        if net is not None:
            remote_node_name = DHCPProvisioner._find_remote_interface(net, node, network)
            if remote_node_name is not None:
                raise ConfigurationError(
                    f"{node.name}: Relay Subnet CIDR {cidr} belongs to a Subnet "
                    f"attached to {remote_node_name}, not to {node.name}.\n"
                    f"Attach the Relay to {remote_node_name} instead: "
                    f"{remote_node_name}.addRelay(Relay('{cidr}', ...))"
                )

        raise ConfigurationError(
            f"{node.name}: no attached Interface found for Relay Subnet CIDR {cidr}\n"
            f"A Relay's covered Subnets must be directly attached to the relaying Node."
        )

    @classmethod
    def resolve_relay(cls, node: Any, relay: Relay, net: Optional[Any] = None) -> Dict[str, str]:
        """Resolve local addresses for all Subnets covered by ``relay``.

        Args:
            node: The relaying Node.
            relay: The Relay to resolve.
            net: Optional Lab network passed through for diagnostics.

        Returns:
            Mapping of Subnet CIDR to derived local IP address.
        """
        resolved = {
            cidr: cls.resolve_local_address(node, cidr, net=net)
            for cidr in relay.subnets
        }
        relay.local_addresses = resolved
        return resolved


    @classmethod
    def build_command_args(
        cls,
        relays: Union[Relay, List[Relay]],
        net: Optional[Any] = None,
    ) -> List[str]:
        """Build dnsmasq command-line arguments for the given Relay(s).

        Args:
            relays: One or more Relay instances with local addresses resolved.
            net: Optional Lab network to infer server addresses by family.

        Returns:
            List of command arguments.
        """
        relay_list = [relays] if isinstance(relays, Relay) else relays
        cmd = ['dnsmasq', '-d', '--port=0', '--log-dhcp']
        for r in relay_list:
            for cidr in r.subnets:
                net_obj = ipaddress.ip_network(cidr, strict=False)
                local_ip = r.local_addresses[cidr]
                srv_ip = (
                    r.server_for_family(net_obj.version, net=net)
                    if hasattr(r, "server_for_family")
                    else r.server
                )
                if not srv_ip:
                    srv_ip = r.server
                cmd.append(f'--dhcp-relay={local_ip},{srv_ip}')
        return cmd

    @classmethod
    def start_relay(cls, node: Any, net: Optional[Any] = None) -> None:
        """Resolve addresses and start dnsmasq relay on ``node``.

        Args:
            node: The Node with Relay(s) attached.
            net: Optional Lab network for diagnostics and resolution.
        """
        raw_relays = getattr(node, 'relays', None)
        relays = [r for r in raw_relays if isinstance(r, Relay)] if isinstance(raw_relays, (list, tuple)) else []
        if not relays:
            return

        cls.ensure_available()
        cls.validate_no_relay_service_collision(node)

        for relay in relays:
            cls.resolve_relay(node, relay, net=net)
            if net is not None:
                for msg in DHCPRelayDiagnostics.check_relay(node, relay, net):
                    warn(f"*** Warning: {msg}\n")

        if net is None:
            net = node.net
        # The log is bound into the Node at the path the relay Guide names,
        # the way the DHCP and DNS Services bind theirs: from inside the
        # Node's own mount namespace, once it exists.
        host_dir = cls.host_dir(node.name, net=net)
        ensure_directory(host_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        node.cmd(f'mkdir -p {cls.GUEST_LOG_DIR}')
        node.cmd(f'mount --bind {shlex.quote(host_dir)} {cls.GUEST_LOG_DIR}')
        log_file = cls.log_path(node.name, net=net)
        guest_log_file = f'{cls.GUEST_LOG_DIR}/dnsmasq.log'

        cmd_args = cls.build_command_args(relays, net=net)
        quoted_cmd = ' '.join(shlex.quote(arg) for arg in cmd_args)
        quoted_log = shlex.quote(guest_log_file)
        result = node.cmd(f"nohup {quoted_cmd} > {quoted_log} 2>&1 &")

        if result and any(err in result.lower() for err in ('error', 'fail', 'cannot', 'not found', 'bad option', 'invalid')):
            raise ConfigurationError(
                f"{node.name}: DHCP Relay process failed to start\n"
                f"Command: {quoted_cmd}\n"
                f"Output: {result.strip()}\n"
                f"Check relay log at {guest_log_file} or ensure dnsmasq is installed."
            )

        startup_error = cls.read_startup_error(log_file)
        if startup_error:
            raise ConfigurationError(
                f"{node.name}: DHCP Relay process failed to start\n"
                f"Error from dnsmasq: {startup_error}\n"
                f"Check configuration or interface bindings."
            )

    @classmethod
    def stop_relay(cls, node: Any) -> None:
        """Stop dnsmasq relay on ``node``.

        Args:
            node: The Node whose Relay is stopping.
        """
        node.cmd("pkill -x dnsmasq || true")
        node.cmd(f"umount {cls.GUEST_LOG_DIR} || true")

    @classmethod
    def _relaying_nodes(cls, nodes: List[Any]) -> List[Any]:
        """Return nodes that carry at least one attached Relay."""
        return [
            n for n in nodes
            if isinstance(getattr(n, 'relays', None), (list, tuple)) and getattr(n, 'relays', None)
        ]

    @classmethod
    def start_all(cls, net: Any, nodes: List[Any]) -> None:
        """Start DHCP Relays on every Node that has one.

        Args:
            net: The Lab network.
            nodes: All nodes in the network.
        """
        targets = cls._relaying_nodes(nodes)
        if not targets:
            return

        info('\n*** Starting DHCP Relays\n')
        for node in targets:
            info(node.name + ' ')
            cls.start_relay(node, net=net)
        info('\n')

    @classmethod
    def stop_all(cls, nodes: List[Any]) -> None:
        """Stop DHCP Relays on every Node that has one.

        Args:
            nodes: All nodes in the network.
        """
        targets = cls._relaying_nodes(nodes)
        if not targets:
            return

        info('\n*** Stopping DHCP Relays\n')
        for node in targets:
            info(node.name + ' ')
            cls.stop_relay(node)
        info('\n')

    @classmethod
    def validate_no_relay_service_collision(cls, node: Any) -> None:
        """Ensure node does not declare both a Relay and a DHCP Service on the same Interface.

        Args:
            node: The Node to validate.

        Raises:
            ConfigurationError: If both a Relay and a DHCP Service run on the same Interface.
        """
        raw_relays = getattr(node, 'relays', None)
        relays = [r for r in raw_relays if isinstance(r, Relay)] if isinstance(raw_relays, (list, tuple)) else []
        raw_services = getattr(node, 'services', None)
        services = [s for s in raw_services if isinstance(s, DHCPService)] if isinstance(raw_services, (list, tuple)) else []
        if not relays or not services:
            return

        relay_intfs: Dict[str, str] = {}
        for relay in relays:
            for subnet in relay.subnets:
                network = ipaddress.ip_network(subnet, strict=False)
                intf_name = DHCPProvisioner._interface_on_network(node, network)
                if intf_name:
                    relay_intfs[intf_name] = subnet

        for service in services:
            for pool_cidr, pool in service.pools.items():
                network = ipaddress.ip_network(pool.cidr, strict=False)
                intf_name = DHCPProvisioner._interface_on_network(node, network)
                if intf_name and intf_name in relay_intfs:
                    raise ConfigurationError(
                        f"{node.name}: cannot declare both a Relay and a DHCP Service "
                        f"on the same Interface ({intf_name} for Subnet {pool.cidr}). "
                        f"dnsmasq relay and Kea server cannot run on the same Interface.\n"
                        f"Remove either the Relay covering {pool.cidr} or the DHCP Service Pool on {node.name}."
                    )
