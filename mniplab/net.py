"""Extended Mininet Network for mnIPLab with FRR routing support."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
from typing import Optional, List, Dict, Tuple, Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from mniplab.exabgp.node import ExaBGPNode
from mininet.net import Mininet
from mininet.node import Node, OVSBridge, Switch
from mininet.link import Link, TCLink
from mininet.log import error, info
from mniplab.constants import (
    CONTAINER_NAME_PREFIX,
    FRR_INIT_SCRIPT,
    LAB_KEY_ENV,
    LABS_DIR,
    MAX_INTF_NAME_LEN,
    NETNS_DIR,
    ROOT_INTF_PORT_DIGITS,
)
from mniplab.lab_key import DEFAULT_ALLOCATOR
from mniplab.lab_path import LabPathResolverMixin
from mniplab.persistent_config import CONFIG_COPY_ENV, seed_router_copy
from mniplab.routernat import apply_network as apply_router_nat, validate_network as validate_router_nat
from mniplab.startup import DEFAULT_TIMEOUT, StartupCommands
from mniplab import ipv6
from mniplab.errors import (
    FRRError,
    LinkError,
    ConfigurationError,
)
from mniplab.cli import mnIPLabCLI
from mniplab.router import FRRouter, ensure_frr_available
from mniplab.host import mnIPLabHost
from mniplab.kernel import ensure_ovs_available
from mniplab.link import IPv6Intf, IPv6TCIntf
from mniplab.switch import SWITCH_ADDRESS_HINT
from mniplab.lifecycle import NodeLifecycleRegistry
from mniplab.namespace import (
    has_private_namespaces,
    install_netns_fallback_guard,
    install_popen_guard,
)
from mniplab.link_condition import (
    LinkConditionStore,
    VALID_STATUSES,
    build_tc_show_command,
    impairment_matches,
    parse_applied_impairment,
)
from mniplab.provisioners import (
    FRRProvisioner,
    ExaBGPProvisioner,
    NATProvisioner,
    VRFProvisioner,
    DHCPProvisioner,
    DHCPRelayProvisioner,
)
from mniplab.dns.provisioner import DNSProvisioner
from mniplab.dns.diagnostics import DNSDiagnostics
from mniplab.resolver.provisioner import ResolverProvisioner
from mniplab.resolver.diagnostics import ResolverDiagnostics
from mniplab.rpki.provisioner import RPKICAProvisioner
from mniplab.rpki.validator_provisioner import ValidatorProvisioner
from mniplab.rpki.diagnostics import RPKIDiagnostics
from mniplab.service import ServiceMixin

# Constants for file permissions
PERM_USER_RW = 0o600
PERM_USER_RWX_GROUP_RX = 0o755
PERM_WORLD_RWX = 0o777

# Constants for paths
ETCFRR_PATH = '/etc/frr'
ETCEXABGP_PATH = '/etc/exabgp'
VAR_LOG_EXABGP = '/var/log/exabgp'
VAR_RUN_EXABGP = '/var/run/exabgp'



class mnIPLab(LabPathResolverMixin, Mininet):
    """Extended Mininet Network for mnIPLab with FRR routing support.
    
    This class extends Mininet to support FRR-based routers with automatic
    configuration management, IPv6 support, and a Lab directory removed on stop.
    
    Attributes:
        _stopped: Flag to prevent double cleanup
        link_conditions: Authoritative record of each Link's condition
    
    Example:
        >>> net = mnIPLab()
        >>> r1 = net.addRouter('r1', proto='OSPF')
        >>> h1 = net.addHost('h1', defaultRoute='via 10.0.0.1')
        >>> net.addLink(h1, r1, params1={'ip': '10.0.0.2/24'}, params2={'ip': '10.0.0.1/24'})
        >>> net.start()
    """
    
    _active_instances: List['mnIPLab'] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize mnIPLab network with validation and ephemeral config tracking.
        
        Performs pre-flight checks to ensure FRR is available before creating the network.
        
        Args:
            *args: Positional arguments passed to Mininet
            **kwargs: Keyword arguments passed to Mininet
            
        Raises:
            FRRError: If FRR is not installed or init script not found
        """
        # Pre-flight check: Validate FRR is available
        ensure_frr_available()

        # Make Mininet's `netns 1` fallback fail closed before any Link can
        # reach it under a Private Namespace Node's shell.
        install_netns_fallback_guard()

        # Route capable Nodes' Node.popen() through the shared nsenter
        # attach with a process-group-signalling handle.
        install_popen_guard()

        # Use TCLink by default to enable traffic control
        if 'link' not in kwargs:
            kwargs['link'] = TCLink

        # Pop lab_key before passing kwargs to Mininet.  A Lab process
        # launched by the manager is handed its key in the environment, so
        # a Lab Example's build_network() needs no knowledge of it.
        lab_key = kwargs.pop('lab_key', None) or os.environ.get(LAB_KEY_ENV) or None
        self.lab_key: str = DEFAULT_ALLOCATOR.allocate(key=lab_key)
        # The Group's copy directory for Persistent Config travels the same
        # way; None means the seed is used directly, as in CLI Mode.
        self.config_copy_dir: Optional[str] = (
            kwargs.pop('config_copy', None) or os.environ.get(CONFIG_COPY_ENV) or None
        )
        self._startup_commands = StartupCommands()
        #: Failures of Startup commands allowed to fail, from the last start.
        self.startup_warnings: List[str] = []

        # A Lab starts from a clean hosts file — the loopback entry alone —
        # before Mininet builds a Node from a topo, so no Lab Example has to
        # reset it first and a previous Lab's entries under the same key
        # never leak in.
        self.init_hosts_file()

        super(mnIPLab, self).__init__(*args, **kwargs)
        mnIPLab._active_instances.append(self)
        # Track if network has been stopped or started
        self._stopped: bool = False
        self._started: bool = False
        # Authoritative record of the Link Condition asked for on each link
        self.link_conditions = LinkConditionStore()
        # Administrative states declared at construction apply after interfaces exist.
        self._initial_link_statuses: List[Tuple[str, str, str]] = []
        # Queued switch port VLAN configurations to apply on start
        self._queued_vlan_configs: List[Tuple[Any, str, str, Dict[str, Any]]] = []
        # Internet Gateways declared so far, numbering their iptables marks
        self._nat_marks_allocated: int = 0

        # Node lifecycle registry — replaces hard-coded isinstance loops
        self._lifecycle = NodeLifecycleRegistry()
        self._register_lifecycle_handlers()

    def init_hosts_file(self) -> None:
        """Reset this Lab's hosts file with loopback entries."""
        mnIPLabCLI.init_hosts_file(self)

    def ensure_hosts_file(self) -> None:
        """Ensure this Lab's hosts file exists."""
        mnIPLabCLI.ensure_hosts_file(self)

    def update_hosts_file(self) -> None:
        """Update this Lab's hosts file with IP entries for all nodes in this Lab."""
        from mniplab.utils import update_hosts_file
        update_hosts_file(self, hosts_file=self.hosts_file)


    def root_intf_name(self, node_name: str, port: int) -> str:
        """Return the kernel name of a root-namespace Node's interface.

        A switch's or Internet Gateway's veth end lives in the host's one
        namespace, so it carries the Lab key; a namespaced Node's
        ``<node>-ethN`` is unchanged.

        Args:
            node_name: The Node name.
            port: The port number on that Node.

        Returns:
            The interface name, ``<key>-<node>-eth<port>``.
        """
        return f'{self.lab_key}-{node_name}-eth{port}'

    def max_root_node_name_len(self) -> int:
        """Return the longest root-namespace Node name this Lab can key.

        Returns:
            The length at which ``root_intf_name`` still fits the kernel's
            limit with a ``ROOT_INTF_PORT_DIGITS``-digit port.
        """
        fixed = len(f'{self.lab_key}--eth') + ROOT_INTF_PORT_DIGITS
        return MAX_INTF_NAME_LEN - fixed

    def bridge_name(self, switch_name: str) -> str:
        """Return the Open vSwitch bridge name for a switch of this Lab.

        Args:
            switch_name: The switch's Node name.

        Returns:
            The bridge name, ``<key>-<switch>``.
        """
        return f'{self.lab_key}-{switch_name}'

    def container_name(self, node_name: str) -> str:
        """Return the Docker container name for a Container Host of this Lab.

        Args:
            node_name: The Node name.

        Returns:
            The container name, ``mn.<key>.<node>``.
        """
        return f'{CONTAINER_NAME_PREFIX}{self.lab_key}.{node_name}'

    def netns_link_path(self, node_name: str) -> str:
        """Return the namespace link exposing a Container Host to ``ip netns``.

        Args:
            node_name: The Node name.

        Returns:
            The link path under ``NETNS_DIR``, named as the container is.
        """
        return os.path.join(NETNS_DIR, self.container_name(node_name))

    def allocate_nat_mark(self) -> int:
        """Allocate an iptables mark for an Internet Gateway of this Lab.

        Marks live in the host's one ``mangle`` table, so the Lab key
        chooses a 16-bit base and each gateway takes the next number under
        it; two Labs on one host never mark alike, whatever their processes.

        Returns:
            A non-zero fwmark unique to this gateway.

        Raises:
            ConfigurationError: If the Lab already holds 255 Internet Gateways.
        """
        if self._nat_marks_allocated >= 0xff:
            raise ConfigurationError(
                f"Lab '{self.lab_key}' cannot hold more than 255 Internet Gateways"
            )
        self._nat_marks_allocated += 1
        base = int(hashlib.sha256(self.lab_key.encode('utf-8')).hexdigest()[:4], 16)
        return (base << 8) | self._nat_marks_allocated

    def resolve_all_root_names(self, node_name: str) -> List[str]:
        """Resolve every root-namespace object name this Lab creates for a Node.

        Used for verification that all root-namespace names carry the Lab key.

        Args:
            node_name: The Node name to resolve names for.

        Returns:
            List of all resolved host-visible object names.
        """
        return [
            self.root_intf_name(node_name, 0),
            self.bridge_name(node_name),
            self.container_name(node_name),
            self.netns_link_path(node_name),
        ]
    
    def addSwitch(self, name: str, **params: Any) -> Node:
        """Add a switch, checking Open vSwitch is usable first.

        Mininet's switches are Open vSwitch bridges, and OVS depends on a
        kernel module the host must load. Without it, switch setup blocks
        instead of failing, leaving no clue what is wrong. Checking here —
        the moment a Lab declares it needs a switch — turns that into an
        error, while leaving router-only Labs free of the requirement.

        The bridge and its ports live in the host's one namespace, so a
        :class:`~mniplab.switch.ManagedSwitch` (the default) names them with
        the Lab key; the switch's Node name is what a Learner sees. A name
        that could not fit a keyed port name is refused: with the derived
        four-character key that is five characters, and an explicit longer
        key shrinks the budget by as much.

        Args:
            name: Switch name.
            **params: Parameters passed to Mininet's addSwitch.

        Returns:
            The switch node.

        Raises:
            NetworkError: If Open vSwitch is missing or unusable.
            ConfigurationError: If the name cannot fit a keyed interface name.
        """
        from mniplab.switch import ManagedSwitch

        ensure_ovs_available()
        self._ensure_root_node_name_fits(name, 'switch')
        cls = params.setdefault('cls', ManagedSwitch)
        if isinstance(cls, type) and issubclass(cls, ManagedSwitch):
            params.setdefault('bridge_name', self.bridge_name(name))
        return super(mnIPLab, self).addSwitch(name, **params)

    def addHost(self, name: str, **params: Any) -> Node:
        """Add a host with shared /etc/hosts and IPv6 support.

        Its addresses belong on the Link ends that create its Interfaces.

        Args:
            name: Host name
            defaultRoute: IPv4 default route, e.g. ``'via 10.0.0.1'``
            defaultRoute6: IPv6 default route, e.g. ``'via 2001:db8::1'``.
                Each leaves by the Interface its ``dev`` names, or else by
                the Host's lowest-numbered one; :meth:`start` checks it.
            nameservers: Optional DNS nameserver IP addresses
            privateDirs: Override the Lab's host private dirs (use [] to disable)
            homeDir: Home directory (default: the Node's home under the Lab directory)
            **params: Other Mininet addHost parameters

        Returns:
            Host node object

        Raises:
            ConfigurationError: If given an ``ip`` or ``ip6``, or a default
                route of the other family.
        """
        self._refuse_node_address(name, params)
        self._refuse_wrong_family_gateway(name, params)

        # No automatic address: Mininet would number the Host from ipBase and
        # apply that at start(), over what its Link end declares.
        params.setdefault('ip', None)

        # Use mnIPLabHost as default class if not specified
        if 'cls' not in params:
            params['cls'] = mnIPLabHost
        
        homeDir = params.get('homeDir')
        if homeDir is None:
            homeDir = self.node_home_dir(name)

        # Apply the host private dirs for this Lab unless explicitly overridden
        if 'privateDirs' not in params:
            params['privateDirs'] = self.host_private_dirs(name, home_dir=homeDir)
        elif params.get('nameservers'):
            has_resolv = any(
                isinstance(d, tuple) and d[0] == '/etc/resolv.conf'
                for d in params['privateDirs']
            )
            if not has_resolv:
                params['privateDirs'].append(('/etc/resolv.conf', self.resolv_conf_file(name)))

        # Seed the per-Node backing files before the mount, but only when
        # the default set (which binds them) is in effect.
        bound_files = {
            d[0]: d[1] for d in params['privateDirs'] if isinstance(d, tuple)
        }
        if '/etc/hosts' in bound_files:
            # The shared hosts file is a bind *source*: Mininet creates a
            # missing one as a directory, which no Node can read as
            # /etc/hosts. The Lab reset its own file when constructed; this
            # keeps it a file for every Node added since, and makes one for
            # a caller-supplied path the Lab did not reset.
            mnIPLabCLI.ensure_hosts_file(bound_files['/etc/hosts'])
        if '/etc/hostname' in bound_files:
            mnIPLabCLI.seed_hostname_file(name, hostname_file=bound_files['/etc/hostname'])
        if '/etc/resolv.conf' in bound_files:
            nameservers = params.get('nameservers')
            mnIPLabCLI.seed_resolv_conf_file(
                name, resolv_conf_file=bound_files['/etc/resolv.conf'], nameservers=nameservers
            )

        os.makedirs(self.node_dir(name), exist_ok=True)
        os.makedirs(homeDir, exist_ok=True)

        # Create host
        host = super(mnIPLab, self).addHost(name, **params)
        host.net = self
        host.lab = self
        
        # Store homeDir in host.params for later directory creation
        if 'homeDir' not in host.params:
            host.params['homeDir'] = homeDir
        
        return host
    
    def initializeHost(self, host: Node) -> None:
        """Initialize host with HOME environment variable and working directory.
        
        Sets HOME environment variable and creates home directory within the node's
        namespace. Does not modify shell profiles to avoid polluting the host system.
        
        Args:
            host: Host node object or host name string
        """
        # Handle both Node objects and string names
        if isinstance(host, str):
            host = self[host]
        
        # Get homeDir
        homeDir = host.params.get('homeDir', self.node_home_dir(host.name))
        safe_home = shlex.quote(homeDir)

        # Create home directory INSIDE the namespace
        host.cmd(f'mkdir -p {safe_home}')

        # Set HOME and change to home directory in the current session
        # This happens within the node's namespace and doesn't affect the host
        host.cmd(f'export HOME={safe_home} && cd {safe_home}')
    
    def _detect_and_validate_ipv6(self, params_dict: Dict[str, Any], node_name: str) -> bool:
        """Detect and validate IPv6 addresses in link parameters.
        
        Args:
            params_dict: Parameter dictionary (params1 or params2)
            node_name: Node name for error messages
            
        Returns:
            True if IPv6 address detected, False otherwise
        """
        ip6 = params_dict.get('ip6')
        if ip6:
            ipv6.validate_ipv6_address(ip6, node_name)
            return True
        return False
    
    def _configure_ipv6_link(self, params: Dict[str, Any], dad_mode: str, cls: Optional[type]) -> None:
        """Configure IPv6 interface classes for a link.
        
        Args:
            params: Link parameters dictionary (modified in-place)
            dad_mode: DAD mode ('fast' or 'strict')
            cls: Link class
            
        Raises:
            LinkError: If dad_mode is invalid
        """
        if dad_mode not in ipv6.DAD_VALID_MODES:
            raise LinkError(
                f"Invalid IPv6 DAD mode '{dad_mode}'\n"
                f"Valid values: {', '.join(ipv6.DAD_VALID_MODES)}\n"
                f"Set via addLink(..., dad_mode='fast|strict') or MNIPLAB_IPV6_DAD_MODE"
            )

        # Pass per-link DAD mode to IPv6 interface config
        params['dad_mode'] = dad_mode

        link_cls = cls if cls is not None else self.link
        if isinstance(link_cls, type) and issubclass(link_cls, TCLink):
            params['cls1'] = IPv6TCIntf
            params['cls2'] = IPv6TCIntf
        else:
            params['cls1'] = IPv6Intf
            params['cls2'] = IPv6Intf
    


    
    
    def _reapply_ipv6_addresses(self) -> None:
        """Restore interface IPv6 addresses cleared during FRR daemon startup.

        FRR resets IPv6 addresses on router interfaces when its daemons come
        up, so an address configured before :meth:`start` can be missing
        afterwards.  This is the one place intent has to be reconciled against
        reality; each interface knows the address it was given, so the check
        reads that rather than a parallel list.

        Failures are logged rather than raised: this runs mid-startup, and a
        Lab that refuses to start teaches nothing.
        """
        for host in self.hosts:
            for intf in host.intfs.values():
                intended = getattr(intf, 'ip6', None)
                if not intended:
                    continue

                prefix = getattr(intf, 'prefixLen6', None)
                address = f'{intended}/{prefix}' if prefix else intended

                # updateIP6 overwrites ip6 with what the kernel reports, so the
                # intended value has to be captured before the read-back.
                if intf.updateIP6() == intended:
                    continue

                try:
                    intf.setIP6(address)
                except LinkError as e:
                    error(f'{host.name}: Failed to restore IPv6 address on {intf.name}: {e}\n')
    
    @staticmethod
    def _initial_impairment(params: Dict[str, Any], params1: Dict[str, Any],
                            params2: Dict[str, Any]) -> Dict[str, Any]:
        """Collect the impairment a link is created with.

        Mininet accepts traffic-control parameters at the link level or per
        interface, so all three dictionaries are consulted; per-interface
        values win because they are the more specific declaration.

        Args:
            params: Link-level parameters.
            params1: Parameters for the first interface.
            params2: Parameters for the second interface.

        Returns:
            Keyword arguments for :meth:`LinkConditionStore.register`.
        """
        impairment: Dict[str, Any] = {}
        for source in (params, params1, params2):
            for name in ('delay', 'jitter', 'loss'):
                if source.get(name) is not None:
                    value = source[name]
                    impairment[name] = (f'{value:g}%' if name == 'loss' and
                                        isinstance(value, (int, float)) else value)
            if source.get('bw') is not None:
                impairment['bandwidth'] = source['bw']
        return impairment

    def _initialize_hosts(self) -> None:
        """Initialize all host nodes with home directories and environment."""
        for host in self.hosts:
            self.initializeHost(host)

    def addLink(self, node1: Node, node2: Node, port1: Optional[int] = None, port2: Optional[int] = None, cls: Optional[type] = None, **params: Any) -> Link:
        """Add a link between two nodes with IPv6 support.
        
        Automatically uses IPv6Intf if IPv6 addresses are detected in params1/params2.
        Each end takes its IPv4 address as ``ip`` and its IPv6 address as ``ip6``.
        
        Args:
            node1: First node
            node2: Second node
            port1: Port on first node
            port2: Port on second node
            cls: Link class (defaults to self.link)
            **params: Additional link parameters including params1 and params2
        
        Returns:
            Link object
            
        Raises:
            LinkError: If an end gives an address under the other family's
                key, an address to a Switch, or a static address beside a
                same-family DHCP client flag, or a required host-side link
                cannot be created.
        """
        # Mininet accepts Node names; everything below needs the Nodes.
        node1 = self[node1] if isinstance(node1, str) else node1
        node2 = self[node2] if isinstance(node2, str) else node2

        # Extract and normalize link parameters
        params1 = dict(params.get('params1', {}) or {})
        params2 = dict(params.get('params2', {}) or {})
        initial_status = params.pop('initial_status', 'up')
        if initial_status not in ('up', 'down'):
            raise LinkError(f"Invalid initial Link Condition status: {initial_status}")
        dad_mode = str(params.get('dad_mode', os.getenv('MNIPLAB_IPV6_DAD_MODE', ipv6.DAD_MODE_FAST))).strip().lower()

        node1_name = node1.name if hasattr(node1, 'name') else 'unknown'
        node2_name = node2.name if hasattr(node2, 'name') else 'unknown'
        link_label = f"{node1_name}–{node2_name}"
        self._refuse_switch_address(node1, params1, link_label)
        self._refuse_switch_address(node2, params2, link_label)
        self._refuse_wrong_family_address(node1, params1, link_label)
        self._refuse_wrong_family_address(node2, params2, link_label)
        self._refuse_static_address_beside_dhcp(node1, params1, link_label)
        self._refuse_static_address_beside_dhcp(node2, params2, link_label)

        # Detect and validate IPv6 addresses
        has_ipv6_1 = self._detect_and_validate_ipv6(params1, node1_name)
        has_ipv6_2 = self._detect_and_validate_ipv6(params2, node2_name)
        has_ipv6 = (
            has_ipv6_1
            or has_ipv6_2
            or bool(params1.get('dhcp6') or params2.get('dhcp6'))
        )

        # Check for top-level switch VLAN parameters
        vlan_keys = {'vlan', 'trunks', 'native'}
        top_vlan_keys = vlan_keys.intersection(params.keys())
        if top_vlan_keys:
            is_sw1 = hasattr(node1, 'set_access') or isinstance(node1, OVSBridge)
            is_sw2 = hasattr(node2, 'set_access') or isinstance(node2, OVSBridge)
            if is_sw1 and not is_sw2:
                for k in list(top_vlan_keys):
                    params1.setdefault(k, params.pop(k))
            elif is_sw2 and not is_sw1:
                for k in list(top_vlan_keys):
                    params2.setdefault(k, params.pop(k))

        params['params1'] = params1
        params['params2'] = params2

        # A root-namespace end carries the Lab key in its interface name
        self._root_intf_options(node1, port1, 1, params)
        self._root_intf_options(node2, port2, 2, params)
        port1 = params.pop('port1', port1)
        port2 = params.pop('port2', port2)

        # Configure IPv6 interface classes if needed
        if has_ipv6:
            self._configure_ipv6_link(params, dad_mode, cls)

        # Auto-select ContainerLink for container hosts
        from mniplab.container import ContainerHost, ContainerLink
        if cls is None:
            if isinstance(node1, ContainerHost) or isinstance(node2, ContainerHost):
                cls = ContainerLink

        # A normal Link creates its veth pair from node1's shell by default.
        # A capable Node's shell may be in a private PID namespace, so make
        # that path host-side.  ContainerLink already has its own host-side
        # implementation and needs fast=True to receive both Node objects.
        link_cls = self.link if cls is None else cls
        host_side_required = (
            has_private_namespaces(node1) or has_private_namespaces(node2)
        ) and not (
            isinstance(link_cls, type) and issubclass(link_cls, ContainerLink)
        )
        if host_side_required:
            params['fast'] = False

        # Create the link
        try:
            link = super(mnIPLab, self).addLink(
                node1, node2, port1, port2, cls, **params
            )
        except (Exception, SystemExit) as exc:
            if host_side_required:
                raise LinkError(
                    f"Failed to create host-side link between {node1_name} and "
                    f"{node2_name}: {exc}"
                ) from exc
            raise

        # Record the condition this link starts with, so that later changes
        # update one authoritative record rather than creating a second one.
        self.link_conditions.register(
            node1_name, node2_name,
            **self._initial_impairment(params, params1, params2)
        )
        if initial_status == 'down':
            self._initial_link_statuses.append((node1_name, node2_name, initial_status))

        # Queue declarative switch port VLAN configurations
        self._queue_switch_vlan(node1, link.intf1.name, params1)
        self._queue_switch_vlan(node2, link.intf2.name, params2)

        # Handle VRF and NAT configurations
        VRFProvisioner.handle_attachments(link, params1, params2, node1, node2)
        NATProvisioner.configure_default_route(link, node1, node2)

        return link
    
    def _queue_switch_vlan(self, node: Node, intf_name: str, port_params: Dict[str, Any]) -> None:
        """Queue or immediately apply switch port VLAN configuration.

        Args:
            node: Node that owns the interface.
            intf_name: Name of the interface on node.
            port_params: Interface parameters dictionary.
        """
        if not (hasattr(node, "set_access") or hasattr(node, "set_trunk")):
            return

        if "vlan" in port_params:
            vlan_id = port_params["vlan"]
            if self._started:
                node.set_access(intf_name, vlan_id)
            else:
                self._queued_vlan_configs.append((node, "access", intf_name, {"vlan": vlan_id}))
        elif "trunks" in port_params:
            trunks = port_params["trunks"]
            native = port_params.get("native")
            if self._started:
                node.set_trunk(intf_name, trunks, native=native)
            else:
                self._queued_vlan_configs.append(
                    (node, "trunk", intf_name, {"trunks": trunks, "native": native})
                )

    def _apply_queued_vlans(self) -> None:
        """Apply all queued switch port VLAN configurations."""
        for node, mode, port_name, cfg in self._queued_vlan_configs:
            try:
                if mode == "access":
                    node.set_access(port_name, cfg["vlan"])
                elif mode == "trunk":
                    node.set_trunk(port_name, cfg["trunks"], native=cfg.get("native"))
            except Exception as exc:
                error(f"Failed to apply VLAN configuration to {node.name} port {port_name}: {exc}\n")
        self._queued_vlan_configs.clear()
    
    def _register_lifecycle_handlers(self) -> None:
        """Register node-type lifecycle callbacks."""
        from mniplab.exabgp.node import ExaBGPNode
        from mniplab.nodelib import MasqueradeNAT, TransitNAT

        self._lifecycle.register(
            FRRouter,
            start_callback=lambda routers: NodeLifecycleRegistry._grouped_action(
                'Starting FRR routers', routers, lambda r: r.start_frr()
            ),
            stop_callback=lambda routers: NodeLifecycleRegistry._grouped_action(
                'Stopping FRR routers', routers, None
            ),
        )
        self._lifecycle.register(
            ExaBGPNode,
            start_callback=lambda speakers: NodeLifecycleRegistry._grouped_action(
                'Starting ExaBGP speakers', speakers, lambda s: s.start_exabgp()
            ),
            stop_callback=lambda speakers: NodeLifecycleRegistry._grouped_action(
                'Stopping ExaBGP speakers', speakers, None
            ),
        )
        self._lifecycle.register(
            (MasqueradeNAT, TransitNAT),
            start_callback=lambda nats: NodeLifecycleRegistry._grouped_action(
                'Starting NAT nodes', nats, None
            ),
            cleanup_callback=lambda nats: NodeLifecycleRegistry._grouped_action(
                'Stopping NAT nodes', nats, lambda n: n.cleanup_rules()
            ),
        )
        self._lifecycle.register(
            ServiceMixin,
            start_callback=lambda nodes: DHCPProvisioner.start_all(self, nodes),
            stop_callback=lambda nodes: DHCPProvisioner.stop_all(nodes),
        )
        self._lifecycle.register(
            ServiceMixin,
            start_callback=lambda nodes: DNSProvisioner.start_all(self, nodes),
            stop_callback=lambda nodes: DNSProvisioner.stop_all(nodes),
        )
        self._lifecycle.register(
            ServiceMixin,
            start_callback=lambda nodes: ResolverProvisioner.start_all(self, nodes),
            stop_callback=lambda nodes: ResolverProvisioner.stop_all(nodes),
        )
        self._lifecycle.register(
            ServiceMixin,
            start_callback=lambda nodes: RPKICAProvisioner.start_all(self, nodes),
            stop_callback=lambda nodes: RPKICAProvisioner.stop_all(self, nodes),
        )
        # After the RPKI CA: the Validator takes its TAL and TLS root from the
        # running CA, and Routinator then fetches objects by the CA's hostname,
        # which the CA's start has written into the shared hosts file.
        self._lifecycle.register(
            ServiceMixin,
            start_callback=lambda nodes: ValidatorProvisioner.start_all(self, nodes),
            stop_callback=lambda nodes: ValidatorProvisioner.stop_all(self, nodes),
        )
        self._lifecycle.register(
            ServiceMixin,
            stop_callback=lambda nodes: DHCPRelayProvisioner.stop_all(nodes),
            post_ipv6_callback=lambda nodes: DHCPRelayProvisioner.start_all(self, nodes),
        )

    def start(self) -> None:
        """Start network and configure IPv6 addresses.

        Raises:
            ConfigurationError: If a Host's gateway cannot leave by its
                Interface; see :meth:`configHosts`.
        """
        info(f'*** Lab key: {self.lab_key}\n')

        # Pre-flight: the daemons behind declared Services must be installed.
        # An absent binary is an environment fact, not a Lab diagnostic.
        DNSProvisioner.ensure_available(self.hosts)
        ResolverProvisioner.ensure_available(self.hosts)
        RPKICAProvisioner.ensure_available(self.hosts)
        ValidatorProvisioner.ensure_available(self.hosts)

        # Pre-daemon validation: validate DNS, Resolver and RPKI Service configuration
        DNSDiagnostics.validate_network(self)
        ResolverDiagnostics.validate_network(self)
        RPKIDiagnostics.validate_network(self)
        validate_router_nat(self)

        # Build and configure all nodes (interfaces, forwarding, sysctl, etc.)
        super(mnIPLab, self).start()
        self._started = True

        # Apply queued switch port VLAN configurations
        self._apply_queued_vlans()
        for node1_name, node2_name, status in self._initial_link_statuses:
            self.configLinkStatus(node1_name, node2_name, status)

        # Router NAT is rendered from the declaration on every start; Startup
        # commands run after it, so they see the rules
        apply_router_nat(self)

        # Authored setup that daemons must see when they read their config
        self.startup_warnings = self._startup_commands.run('before-daemons')

        # Run registered start callbacks (FRR, ExaBGP, NAT, DHCP Services)
        self._lifecycle.start_all(self.hosts)

        # Re-apply tracked IPv6 link addresses if they were reset during startup
        self._reapply_ipv6_addresses()

        # Run post-IPv6 start callbacks (DHCP Relays start after IPv6 reconciliation)
        self._lifecycle.start_post_ipv6_all(self.hosts)
        
        # Initialize hosts: create home directories and set environment variables
        self._initialize_hosts()

        self.startup_warnings += self._startup_commands.run('after-start')
    
    def _cleanup_ephemeral_config_dirs(self) -> None:
        """Remove this Lab's directory whole.

        Every ephemeral file the Lab wrote lives under it; Persistent Config
        is elsewhere and untouched. Errors are logged but don't fail the
        stop process.
        """
        if os.path.exists(self.lab_dir):
            try:
                shutil.rmtree(self.lab_dir)
            except OSError as e:
                error(f'Failed to cleanup {self.lab_dir}: {e}\n')
        self._cleanup_tmp_parent_dir(LABS_DIR)

    def _cleanup_tmp_parent_dir(self, parent_dir: str) -> None:
        """Remove parent directory if empty.
        
        Args:
            parent_dir: Directory path to check and remove if empty
        """
        if not os.path.exists(parent_dir):
            return
        
        if os.listdir(parent_dir):
            return  # Directory not empty
        
        try:
            os.rmdir(parent_dir)
        except OSError as e:
            error(f'Failed to cleanup {parent_dir}: {e}\n')
    
    def configLinkStatus(self, src: str, dst: str, status: str) -> None:
        """Bring a link administratively up or down and record the intent.

        Applies the change first and records it only once every interface
        accepted it, so a failure leaves no false record behind.  Mininet's own
        version logs failures and returns, which gives the caller nothing to
        check — hence the reimplementation rather than a super() call.

        Impairment set on the link is untouched, so a link brought down and
        back up keeps the delay, jitter, loss and bandwidth it had before.

        Args:
            src: First node name.
            dst: Second node name.
            status: 'up' or 'down'.

        Raises:
            UnknownLinkError: If no link between the two nodes was registered.
            LinkError: If a node is unknown, the nodes are not connected, the
                status is invalid, or an interface refused the change.
        """
        if status not in VALID_STATUSES:
            raise LinkError(
                f"Invalid link condition status '{status}'\n"
                f"Valid values: {', '.join(VALID_STATUSES)}"
            )

        for name in (src, dst):
            if name not in self.nameToNode:
                raise LinkError(f"Node not in the lab: {name}")

        src_node = self.nameToNode[src]
        dst_node = self.nameToNode[dst]

        connections = src_node.connectionsTo(dst_node)
        if not connections:
            raise LinkError(f"No link between {src} and {dst}")

        # Fail before touching the network if the link was never registered,
        # so we can never apply a change we are unable to record.
        self.link_conditions.get(src_node.name, dst_node.name)

        for src_intf, dst_intf in connections:
            for intf in (src_intf, dst_intf):
                result = intf.ifconfig(status)
                if result:
                    raise LinkError(
                        f"Failed to bring {intf.name} {status} "
                        f"on link {src} <-> {dst}\n"
                        f"Error: {result}"
                    )

        self.link_conditions.set_status(src_node.name, dst_node.name, status)

    def get_link_condition(self, n1_name: str, n2_name: str) -> str:
        """Return the recorded administrative status of a link.

        Args:
            n1_name: First node name
            n2_name: Second node name

        Returns:
            'up' or 'down' (defaults to 'up' if the link is not tracked)
        """
        return self.link_conditions.status(n1_name, n2_name)
    
    def link_condition_drift(self) -> List[Dict[str, Any]]:
        """Report links whose applied condition no longer matches the intent.

        Write-through stops a failed change from being recorded; this catches
        the residual case, where reality changed after a successful apply —
        someone ran ``tc`` by hand, or a qdisc was reset. Reads traffic control
        rather than the store, so it can see changes the Lab never made.

        This is deliberately separate from IPv6 address read-back: different
        source, different cadence, different consumer.

        Returns:
            One entry per drifted link, naming the endpoints, the intended
            impairment, and what is actually applied.
        """
        drifted: List[Dict[str, Any]] = []

        for link in self.links:
            intf = getattr(link, 'intf1', None)
            node = getattr(intf, 'node', None)
            if intf is None or node is None or not getattr(link, 'intf2', None):
                continue

            n1, n2 = node.name, link.intf2.node.name
            if not self.link_conditions.knows(n1, n2):
                continue

            condition = self.link_conditions.get(n1, n2)
            applied = parse_applied_impairment(node.cmd(build_tc_show_command(intf.name)))

            if impairment_matches(condition, applied):
                continue

            drifted.append({
                'source': condition.node1,
                'target': condition.node2,
                'intended': {'delay': condition.delay, 'loss': condition.loss},
                'applied': applied,
            })

        return drifted

    def addRouter(self, name: str, private_dirs: Optional[List[Union[str, Tuple[str, str]]]] = None, frr_dir: Optional[str] = None, proto: Optional[Union[str, List[str]]] = None, **kwargs: Any) -> FRRouter:
        """Add a router to the network with FRR routing support.
        
        This method creates a router node with FRR daemons configured according
        to the specified protocols. Configuration files are automatically generated
        from templates unless persistent configs are provided via frr_dir.
        
        Args:
            name: Router name (must be unique in the network)
            private_dirs: List of private directories for the router's namespace.
                Defaults to the Lab's router private dirs (/var/log, /etc/frr, etc.)
            frr_dir: Custom FRR config directory path. Supports %(name)s placeholder.
                Examples: './frr-config/%(name)s', '/persistent/configs/%(name)s'
                If None, uses the ephemeral directory under the Lab directory (deleted on stop)
            proto: Protocol(s) to enable. Multiple formats supported:
                - Single protocol: 'OSPF'
                - Comma-delimited: 'OSPF,BGP'
                - Pipe-delimited: 'OSPF|BGP'
                - List: ['OSPF', 'BGP']
                Valid protocols: OSPF, OSPF6, BGP, ISIS, RIP, RIPNG, EIGRP, BABEL, etc.
                See PROTO_MAP in mniplab.constants for full list.
            **kwargs: Additional arguments:
                daemons: List of FRR daemon names (zebra, ospfd, bgpd, etc.)
                frr_config: Custom FRR configuration string for frr.conf
                nat: Router NAT entries, each a PortForward or StaticNAT. The
                    Router's outside Interface is marked on its Link end with
                    ``'nat': 'outside'``; entries are rendered at start.
                Other args passed to FRR config template rendering
        
        Returns:
            FRRouter instance configured with specified protocols
            
        Raises:
            ConfigurationError: If protocol name is invalid, or an ``ip`` or
                ``ip6`` is given: its addresses belong on its Link ends
            FRRError: If FRR config directory setup fails
            
        Examples:
            Basic router with OSPF:
            >>> net = mnIPLab()
            >>> r1 = net.addRouter('r1', proto='OSPF')
            
            Router with multiple protocols:
            >>> r2 = net.addRouter('r2', proto='OSPF,BGP')
            >>> r3 = net.addRouter('r3', proto=['ISIS', 'BGP'])
            
            Router with persistent config directory:
            >>> r4 = net.addRouter('r4', 
            ...                    frr_dir='./frr-config/%(name)s',
            ...                    proto='BGP')
            
            Router with custom FRR configuration:
            >>> bgp_config = '''
            ... router bgp 65001
            ...  neighbor 10.0.0.2 remote-as 65002
            ... '''
            >>> r5 = net.addRouter('r5', proto='BGP', frr_config=bgp_config)
            
            Router with specific daemons (advanced):
            >>> r6 = net.addRouter('r6', daemons=['zebra', 'ospfd', 'bgpd'])
        """
        # Before the provisioner, whose own ip=None would otherwise turn a
        # given ip into a duplicate-keyword TypeError.
        self._refuse_node_address(name, kwargs)
        return FRRProvisioner.add_router(
            self, name,
            private_dirs=private_dirs,
            frr_dir=frr_dir,
            proto=proto,
            **kwargs
        )

    def addExaBGP(self, name: str, private_dirs: Optional[List[Union[str, Tuple[str, str]]]] = None, exabgp_dir: Optional[str] = None, cls: Optional[type] = None, **kwargs: Any) -> 'ExaBGPNode':
        """Add an ExaBGP speaker to the network.

        This creates a host node that runs ExaBGP inside its namespace. The
        ExaBGP configuration directory is created from templates unless a
        persistent `exabgp_dir` is provided.

        Args:
            name: Node name
            private_dirs: Optional privateDirs mapping. Defaults to the Lab's speaker private dirs
            exabgp_dir: Optional host-side config dir pattern (supports %(name)s)
            cls: Custom ExaBGPNode class. If None, uses default ExaBGPNode.
                Allows extending ExaBGPNode with custom behavior (mirrors Mininet pattern).
            **kwargs: Template variables passed to ExaBGPConfigBuilder (neighbors, routes, local_as)

        Returns:
            ExaBGPNode instance (or custom cls if provided)

        Raises:
            ConfigurationError: If an ``ip`` or ``ip6`` is given: its
                addresses belong on its Link ends.
            
        Examples:
            Basic ExaBGP speaker:
            >>> net = mnIPLab()
            >>> tr1 = net.addExaBGP('tr1', neighbors=[...], local_as=65001)
            
            With persistent config directory:
            >>> tr2 = net.addExaBGP('tr2', 
            ...                     exabgp_dir='./frr-config/my-speakers/%(name)s',
            ...                     neighbors=[...], local_as=65002)
            
            Custom ExaBGPNode subclass:
            >>> class CustomExaBGP(ExaBGPNode):
            ...     def custom_method(self): pass
            >>> tr3 = net.addExaBGP('tr3', cls=CustomExaBGP, neighbors=[...])
        """
        # Lazy import to avoid circular imports
        from mniplab.exabgp.node import ExaBGPNode
        from mniplab.exabgp.config import ExaBGPConfigBuilder
        from mniplab.errors import ExaBGPError

        # Before the provisioner, as in addRouter.
        self._refuse_node_address(name, kwargs)
        return ExaBGPProvisioner.add_exabgp(
            self, name,
            private_dirs=private_dirs,
            exabgp_dir=exabgp_dir,
            cls_node=cls,
            **kwargs
        )

    def addContainer(self, name: str, cls: Optional[type] = None, **params: Any) -> Node:
        """Add a Docker-backed container host to the network.

        Convenience wrapper around :meth:`addHost`; mirrors the style of
        ``addRouter``/``addExaBGP``.  Requires a ``dimage`` parameter.
        Raises :class:`ConfigurationError` if ``dimage`` is missing.
        """
        from mniplab.container import ContainerHost

        if 'dimage' not in params or not params.get('dimage'):
            raise ConfigurationError(
                f"addContainer('{name}') requires a non-empty 'dimage' parameter"
            )

        # Container mounts are controlled via docker volume flags.
        # Skip the /root bind mount (containers have their own filesystem),
        # but keep the shared /etc/hosts so name resolution works across
        # all node types.
        if 'privateDirs' not in params:
            params['privateDirs'] = [
                ('/etc/hosts', self.hosts_file),
            ]

        params.setdefault('net', self)
        return self.addHost(name, cls=cls or ContainerHost, **params)
    

    
    def stop(self) -> None:
        """Stop network and cleanup ephemeral FRR configs.
        
        Cleans up NAT iptables rules early (before downstream nodes exit)
        but defers shell/interface termination to Mininet's normal shutdown.
        """
        # Prevent double cleanup
        if self._stopped:
            return
        
        self._stopped = True
        self._started = False
        
        # Run registered cleanup callbacks (NAT rules early, before nodes exit)
        self._lifecycle.cleanup_all(self.hosts)
        
        # Run registered stop callbacks (FRR, ExaBGP — informational only)
        self._lifecycle.stop_all(self.hosts)

        # Stop the network (Mininet will terminate all nodes cleanly)
        super(mnIPLab, self).stop()
        
        # Remove the Lab directory
        self._cleanup_ephemeral_config_dirs()

        # Release allocated lab_key
        DEFAULT_ALLOCATOR.release(self.lab_key)

        if hasattr(mnIPLab, '_active_instances') and self in mnIPLab._active_instances:
            mnIPLab._active_instances.remove(self)
    
    def addNode(self, name: str, cls: Optional[type] = None, **params: Any) -> Node:
        """Add a generic node to the network.
        
        This wrapper supports both standard Mininet nodes and 
        mnIPLab nodes (routers, hosts).
        
        Args:
            name: Node name
            cls: Node class (e.g., MasqueradeNAT, Host, FRRouter, etc.)
            **params: Additional parameters passed to the node class
            
        Returns:
            Node instance

        Raises:
            ConfigurationError: If given an ``ip`` or ``ip6``, or a default
                route of the other family.
        """
        # Use addHost for all node types (Host, FRRouter, etc.)
        # Mininet's addHost can accept any custom class; as with addHost,
        # it must not number the Node with an automatic address.
        self._refuse_node_address(name, params)
        self._refuse_wrong_family_gateway(name, params)
        params.setdefault('ip', None)
        return super().addHost(name, cls=cls, **params)

    def addNAT(self, name: str, subnet: Optional[str] = None, **params: Any) -> Node:
        """Add a masquerade Internet Gateway to the network.

        Creates a :class:`~mniplab.nodelib.MasqueradeNAT` node that runs in
        the root network namespace and masquerades traffic from the Mininet
        overlay to external networks.  The node is automatically hidden from
        the CLI node list and the Web UI topology graph.

        Unlike the Mininet built-in ``addNAT``, this method does **not**
        auto-connect the NAT node to a switch.  You must add a link manually::

            nat = net.addNAT('nat1', subnet='192.168.100.0/24')
            net.addLink(nat, router, params1={'ip': '192.168.100.1/24'},
                        params2={'ip': '192.168.100.2/24'})

        Args:
            name: Node name.
            subnet: Optional CIDR string for the NAT link subnet (e.g.
                ``'192.168.100.0/24'``).  Stored as metadata on the node.
            **params: Additional keyword arguments forwarded to the node
                constructor. Its address belongs on its Link end.

        Returns:
            :class:`~mniplab.nodelib.MasqueradeNAT` node instance.
        """
        from mniplab.nodelib import MasqueradeNAT

        params = self._nat_gateway_params(name, params)
        node = self.addNode(name, cls=MasqueradeNAT, subnet=subnet, **params)
        # Mark as hidden so CLI and Web UI exclude it from node listings.
        node.hidden = True
        node.params['hidden'] = True
        return node

    def addTransit(self, name: str, downstream=None, subnet: Optional[str] = None, **params: Any) -> Node:
        """Add a transit Internet Gateway for ExaBGP-mediated internet paths.

        Creates a :class:`~mniplab.nodelib.TransitNAT` node that handles the
        3-hop masquerade chain automatically::

            [TransitNAT, root-ns] <--> [ExaBGP peer] <--> [downstream FRR router]

        All three stages of masquerade and default routing are installed during
        ``net.start()`` and fully removed on ``net.stop()`` — no manual
        ``node.cmd()`` calls are needed in the topology script.

        Usage pattern — declare ``downstream`` and the ExaBGP node first, then
        add links *after* creating the transit gateway::

            # single downstream
            isp1 = net.addRouter('isp1', proto='BGP', ...)
            tr1 = net.addExaBGP('tr1', ...)
            nat_tr1 = net.addTransit('nat1', downstream=isp1)
            net.addLink(tr1, isp1, params1={'ip': '203.0.113.0/30'}, params2={'ip': '203.0.113.1/30'})
            net.addLink(nat_tr1, tr1, params1={'ip': '192.168.0.1/30'},
                        params2={'ip': '192.168.0.2/30'})

            # multiple downstreams
            nat_tr1 = net.addTransit('nat1', downstream=[up1, up2])

        Args:
            name: Node name.
            downstream: The FRR router (or list of routers) one hop beyond the
                ExaBGP peer (e.g. ``isp1`` or ``[up1, up2]``).  Required for
                Stage-0 masquerade and default route on each downstream.
            subnet: Optional CIDR string (metadata only).
            **params: Additional keyword arguments forwarded to the node
                constructor. Its address belongs on its Link end.

        Returns:
            :class:`~mniplab.nodelib.TransitNAT` node instance.
        """
        from mniplab.nodelib import TransitNAT

        params = self._nat_gateway_params(name, params)
        node = self.addNode(name, cls=TransitNAT, downstream=downstream, subnet=subnet, **params)
        # Mark as hidden so CLI and Web UI exclude it from node listings.
        node.hidden = True
        node.params['hidden'] = True
        return node

    def _ensure_root_node_name_fits(self, name: str, kind: str) -> None:
        """Refuse a root-namespace Node whose keyed veth could never fit.

        The kernel caps an interface name at ``MAX_INTF_NAME_LEN``, and a
        root-namespace veth carries the Lab key and a port number, so a
        name over the budget can never start: refuse it at declaration.

        Args:
            name: The Node name being declared.
            kind: What is being declared, for the message.

        Raises:
            ConfigurationError: If the name is too long for this Lab's key.
        """
        limit = self.max_root_node_name_len()
        if len(name) > limit:
            raise ConfigurationError(
                f"{kind} name '{name}' is too long: with Lab key '{self.lab_key}' its "
                f"interface '{self.root_intf_name(name, 0)}' would exceed the kernel's "
                f"{MAX_INTF_NAME_LEN}-character interface-name limit. Use at most "
                f"{limit} characters."
            )

    def _root_intf_options(self, node: Node, port: Optional[int], slot: int, params: Dict[str, Any]) -> None:
        """Name a root-namespace Node's interface end with the Lab key.

        A switch's or Internet Gateway's veth end is created in the host's one
        namespace, so Mininet's ``<node>-ethN`` would collide across Labs.
        A namespaced Node's end is left to Mininet's naming.

        Args:
            node: The Node at this end of the link.
            port: The port the caller asked for, if any.
            slot: 1 or 2, the link end.
            params: The link options to update in place.

        Raises:
            LinkError: If the keyed name would exceed the kernel's limit.
        """
        if getattr(node, 'inNamespace', True) or f'intfName{slot}' in params:
            return
        if port is None:
            port = node.newPort()
        intf_name = self.root_intf_name(node.name, port)
        if len(intf_name) > MAX_INTF_NAME_LEN:
            raise LinkError(
                f"Interface '{intf_name}' on {node.name} exceeds the kernel's "
                f"{MAX_INTF_NAME_LEN}-character interface-name limit."
            )
        params[f'port{slot}'] = port
        params[f'intfName{slot}'] = intf_name

    def _nat_gateway_params(self, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Give an Internet Gateway what only its Lab can: a fitting name, a mark, the key.

        Args:
            name: The gateway's Node name.
            params: The caller's constructor parameters.

        Returns:
            The parameters with ``mark`` and ``lab_key`` filled in.

        Raises:
            ConfigurationError: If the name cannot fit a keyed interface name.
        """
        self._ensure_root_node_name_fits(name, 'Internet Gateway')
        params.setdefault('mark', self.allocate_nat_mark())
        params.setdefault('lab_key', self.lab_key)
        return params

    def persistent_config_template(self, name: str, seed_template: str) -> str:
        """Where a Router declared with ``frr_dir`` keeps its Persistent Config.

        The seed itself in CLI Mode.  Under the manager, the Group's copy of
        it: seeded from ``seed_template`` the first time this Group starts
        the Lab Example, resumed after, so ``write memory`` never reaches
        the seed.

        Args:
            name: The Router's name.
            seed_template: Its ``frr_dir``, still carrying ``%(name)s``.

        Returns:
            The host-side directory template to bind at ``/etc/frr``.
        """
        if self.config_copy_dir is None:
            return seed_template
        seed_router_copy(self.config_copy_dir, seed_template.replace('%(name)s', name), name)
        return os.path.join(self.config_copy_dir, '%(name)s')

    def addStartupCommand(self, node: Node, command: str, stage: str = 'after-start',
                          timeout: int = DEFAULT_TIMEOUT, may_fail: bool = False) -> None:
        """Declare a shell command a Node runs at one stage of :meth:`start`.

        ``before-daemons`` runs once Interfaces are up and before FRR,
        ExaBGP, NAT and Services start; ``after-start`` runs when the Lab
        has started. Nodes run in parallel within a stage; one Node's
        commands run in the order declared. A failure or timeout fails the
        start unless ``may_fail``, which records it in ``startup_warnings``.

        Args:
            node: The Node whose shell runs the command.
            command: The shell command line.
            stage: ``before-daemons`` or ``after-start``.
            timeout: Seconds before the command is killed (1–60).
            may_fail: Report a failure as a warning instead of failing start.

        Raises:
            ValueError: The stage, command or timeout is invalid.
        """
        self._startup_commands.add(node, command, stage, timeout=timeout, may_fail=may_fail)

    @staticmethod
    def _refuse_switch_address(node: Any, params_dict: Dict[str, Any], link: str) -> None:
        """Refuse an address on a Switch end of a Link.

        A Switch is a bridge in the host's own namespace, so the address
        would land on the host machine, with a kernel route beside it.

        Args:
            node: The Node at this end of the Link.
            params_dict: That end's ``params1`` or ``params2``.
            link: The Link's two Node names, for the message.

        Raises:
            LinkError: If the end is a Switch's and carries ``ip`` or ``ip6``.
        """
        if not isinstance(node, Switch):
            return
        declared = [params_dict[key] for key in ('ip', 'ip6') if params_dict.get(key)]
        if declared:
            raise LinkError(
                f"Link {link}: Switch '{node.name}' cannot take "
                f"address {', '.join(declared)}: {SWITCH_ADDRESS_HINT}"
            )

    @staticmethod
    def _refuse_node_address(name: str, params: Dict[str, Any]) -> None:
        """Refuse an address declared on a Node rather than on its Interface.

        A Node-level address does not say which Interface it is for, and it
        disagreed silently with the Link end's: Mininet re-applies a
        Node-level ``ip`` over it at start, and a Node-level ``ip6`` became a
        second address beside it.

        Args:
            name: The Node being added.
            params: Its ``add*`` parameters.

        Raises:
            ConfigurationError: If ``ip`` or ``ip6`` is given and not ``None``.
        """
        declared = {key: params[key] for key in ('ip', 'ip6') if params.get(key) is not None}
        if declared:
            given = ', '.join(f"{key}='{value}'" for key, value in declared.items())
            raise ConfigurationError(
                f"{name}: {given} is not a Node setting; declare it on the Link end "
                f"that creates the Interface: net.addLink({name}, …, params1={declared!r})"
            )

    @staticmethod
    def _refuse_wrong_family_address(node: Any, params_dict: Dict[str, Any], link: str) -> None:
        """Refuse a Link end's address written under the other family's key.

        Args:
            node: The Node at this end of the Link.
            params_dict: That end's ``params1`` or ``params2``.
            link: The Link's two Node names, for the message.

        Raises:
            LinkError: If ``ip`` holds IPv6 or ``ip6`` holds IPv4.
        """
        ip, ip6 = params_dict.get('ip'), params_dict.get('ip6')
        if ipv6.is_ipv6_address(ip):
            given, family, key = f"ip='{ip}'", 'IPv6', 'ip6'
            value = ip
        elif ip6 and ipv6.is_ipv4_address(ip6):
            given, family, key = f"ip6='{ip6}'", 'IPv4', 'ip'
            value = ip6
        else:
            return
        raise LinkError(
            f"Link {link}: {node.name}'s {given} is {family}; declare it as "
            f"'{key}' on that Link end: {{'{key}': '{value}'}}"
        )

    @staticmethod
    def _refuse_static_address_beside_dhcp(node: Any, params_dict: Dict[str, Any], link: str) -> None:
        """Refuse a Link end's static address beside a same-family DHCP client flag.

        A DHCP client Interface starts bare, so the Learner runs the client
        on it; a static address there would never be applied.

        Args:
            node: The Node at this end of the Link.
            params_dict: That end's ``params1`` or ``params2``.
            link: The Link's two Node names, for the message.

        Raises:
            LinkError: If ``ip`` is given beside ``dhcp4``, or ``ip6`` beside
                ``dhcp6``.
        """
        for key, flag in (('ip', 'dhcp4'), ('ip6', 'dhcp6')):
            address = params_dict.get(key)
            if params_dict.get(flag) and address:
                raise LinkError(
                    f"Link {link}: {node.name}'s {key}='{address}' is beside {flag}; "
                    "a DHCP client Interface starts bare, so remove the address or the flag"
                )

    @staticmethod
    def _refuse_wrong_family_gateway(name: str, params: Dict[str, Any]) -> None:
        """Refuse a Node's gateway written under the other family's key.

        Args:
            name: The Node being added.
            params: Its ``add*`` parameters.

        Raises:
            ConfigurationError: If ``defaultRoute`` names an IPv6 gateway or
                ``defaultRoute6`` an IPv4 one.
        """
        route, route6 = params.get('defaultRoute'), params.get('defaultRoute6')
        if ipv6.is_ipv6_address(route):
            raise ConfigurationError(
                f"{name}: defaultRoute='{route}' is IPv6; "
                f"declare it as defaultRoute6='{route}'"
            )
        if route6 and ipv6.is_ipv4_address(route6):
            raise ConfigurationError(
                f"{name}: defaultRoute6='{route6}' is IPv4; "
                f"declare it as defaultRoute='{route6}'"
            )

    def configHosts(self) -> None:
        """Check every Host's gateways, then configure the Hosts as Mininet does.

        A Host's ``defaultRoute`` and ``defaultRoute6`` leave by one
        Interface, which is known only once every Link exists. Every Host
        is checked before any is configured, so a refusal leaves no Host
        with one of its gateways installed. Speakers, Routers and NAT
        Gateways are not Hosts and keep Mininet's handling.

        Raises:
            ConfigurationError: If a Host's gateway has no Interface to leave
                by, or leaves by a DHCP client Interface of its family.
        """
        from mniplab.exabgp.node import ExaBGPNode

        routes = [
            (host, key, self._host_gateway_route(host, key, flag))
            for host in self.hosts
            if isinstance(host, mnIPLabHost) and not isinstance(host, ExaBGPNode)
            for key, flag in (('defaultRoute', 'dhcp4'), ('defaultRoute6', 'dhcp6'))
            if host.params.get(key) and host.params[key] != self._nat_peer_route(host)
        ]
        for host, key, route in routes:
            host.params[key] = route
        super(mnIPLab, self).configHosts()

    @staticmethod
    def _host_gateway_route(host: Node, key: str, flag: str) -> str:
        """Return a Host's gateway naming the Interface it leaves by.

        The Interface is the one ``dev`` names, or for ``defaultRoute`` the
        one Mininet's single-word form names. Otherwise it is the Host's
        lowest-numbered Interface: an IPLab convention, since Mininet
        passes a bare ``via`` to the kernel, and an IPv6 link-local gateway
        needs the device named.

        Args:
            host: The Host declaring the gateway.
            key: ``defaultRoute`` or ``defaultRoute6``.
            flag: The DHCP client flag of the gateway's family.

        Returns:
            The route to install, naming its Interface.

        Raises:
            ConfigurationError: If the Interface is missing, or is a DHCP
                client of the gateway's family: the Learner's DHCP exchange
                or Router Advertisement supplies that one's gateway.
        """
        route = str(host.params[key])
        words = route.split()
        if 'dev' in words[:-1]:
            name: Optional[str] = words[words.index('dev') + 1]
        elif key == 'defaultRoute' and len(words) == 1:
            name = route
        else:
            name = None
        if name:
            intf = host.nameToIntf.get(name)
        else:
            intf = host.defaultIntf() if host.intfs else None

        given = f"{host.name}: {key}='{route}'"
        if intf is None and name:
            raise ConfigurationError(
                f"{given} names {name}, which {host.name} does not have; "
                f"its Interfaces are {', '.join(host.intfNames()) or 'none'}"
            )
        if intf is None:
            raise ConfigurationError(
                f"{given} has no Interface to leave by; "
                f"add a Link to {host.name} or remove the gateway"
            )
        if intf.params.get(flag):
            source = 'DHCP exchange' if flag == 'dhcp4' else 'Router Advertisement'
            raise ConfigurationError(
                f"{given} leaves by {intf.name}, a {flag} client Interface, where the "
                f"{source} supplies the gateway; remove the static default or the flag"
            )
        return route if name else f'{route} dev {intf.name}'

    @staticmethod
    def _nat_peer_route(host: Node) -> Optional[str]:
        """Return the ``defaultRoute`` a linked Internet Gateway writes for a Host.

        :class:`~mniplab.provisioners.NATProvisioner` gives an Internet Gateway's
        peer this route when the peer declares none. It is installed as
        written, the kernel choosing its Interface from the NAT's Subnet.
        The route is recognised by its value, so a Host that declares this
        same route itself is treated alike.

        Args:
            host: The Host to look at.

        Returns:
            ``'via <NAT address>'``, or ``None`` when no Internet Gateway writes one.
        """
        from mniplab.nodelib import MasqueradeNAT

        for intf in host.intfList():
            link = intf.link
            if link is None:
                continue
            nat_intf = link.intf2 if link.intf1 is intf else link.intf1
            if isinstance(nat_intf.node, MasqueradeNAT) and getattr(
                    nat_intf.node, '_peer_default_gw', False):
                raw = nat_intf.ip or nat_intf.params.get('ip')
                if raw:
                    return f"via {raw.split('/')[0]}"
        return None
