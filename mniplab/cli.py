"""Extended Mininet CLI with network inspection commands for mnIPLab."""

import contextlib
import os
import time
from typing import (TYPE_CHECKING, Any, ContextManager, Dict, Iterable, List, Optional, Tuple,
                    Union)
from mininet.cli import CLI
from mininet.log import info, setLogLevel, output
from mininet.util import dumpNodeConnections, quietRun
from mniplab.logging import get_logger, set_log_level
from mniplab.utils import collect_ip_entries, remove_stale_directory, update_hosts_file
from mniplab.netutils import NetworkTestService, RoutingQueryService
from mniplab.runner import (
    DEFAULT_WEB_LOG_FILE,
    DEFAULT_WEB_HOST,
    DEFAULT_WEB_PORT,
    DEFAULT_IPERF_DURATION,
    VALID_LOG_LEVELS,
)

if TYPE_CHECKING:
    from mininet.net import Mininet
    from mininet.node import Node

# Module logger
logger = get_logger(__name__)

# File permissions
PERM_WORLD_RWX = 0o777
PERM_USER_RW = 0o600
PERM_USER_RWX_GROUP_RX = 0o755

# Valid values for iperf config
VALID_PROTOCOLS = ['tcp', 'udp']

# How long a transfer verb waits for Knot to act before reporting what moved
TRANSFER_OUTCOME_TIMEOUT = 6.0
TRANSFER_OUTCOME_INTERVAL = 0.5


# Helper Functions
def speaker_route_line(route: Dict[str, Any]) -> str:
    """Return one of a Speaker's routes in ExaBGP's own words.

    The words are the ones ``announce route …`` takes, so what a Learner
    reads here is what they would type; an attribute the route lacks is
    left out.

    Args:
        route: One route as the SpeakerQueryService reported it.

    Returns:
        ``'<prefix>  next-hop <ip>  as-path …  community …  large-community …  med N'``.
    """
    from mniplab.exabgp.query import ROUTE_ATTRIBUTES

    parts = [route['prefix']]
    for word, (field, _) in ROUTE_ATTRIBUTES.items():
        value = route.get(field)
        if value is None or value == []:
            continue
        rendered = ' '.join(str(v) for v in value) if isinstance(value, list) else str(value)
        parts.append(f'{word} {rendered}')
    return '  '.join(parts)


def _speaker_words(line: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Split a Speaker verb's line into its Node, optional Neighbor and the rest.

    ``announce`` and ``withdraw`` share the head ``<node> [neighbor <ip>]``
    and branch on what follows it.

    Args:
        line: The argument line, as typed after the command.

    Returns:
        ``(node, neighbor, words)``; node is None when the line is empty,
        and neighbor is None when no ``neighbor <ip>`` was given. A
        ``neighbor`` word with no address leaves node None too, so the
        caller prints its usage.
    """
    tokens = line.split()
    if not tokens:
        return None, None, []
    node_name, rest = tokens[0], tokens[1:]
    if rest and rest[0].lower() == 'neighbor':
        if len(rest) < 2:
            return None, None, []
        return node_name, rest[1], rest[2:]
    return node_name, None, rest


def relay_path_label(lease: Dict[str, Any]) -> str:
    """Return the Lease's relay path label as a display fragment.

    The label is derived by the LeaseQueryService from the Pool the Lease's
    subnet identifier resolves to; a Lease the Lab does not label (no Relay
    staged, or a subnet identifier matching no known Pool) renders exactly as
    it did before — a relayed Lease is otherwise displayed exactly as a
    direct one, because a relay changes the path, not the Lease. The two
    leading spaces are part of the fragment so an unlabelled Lease leaves the
    row byte-identical to its pre-label formatting.

    Args:
        lease: One Lease dict as the LeaseQueryService reported it; the
            ``relayed`` boolean is present only when the Lab stages a Relay
            for the Lease's family.

    Returns:
        ``'  relayed'``, ``'  direct'``, or ``''`` when the Lease carries no
        label.
    """
    if 'relayed' not in lease:
        return ''
    return '  relayed' if lease['relayed'] else '  direct'


def get_node_from_net(net: 'Mininet', node_name: str) -> Optional['Node']:
    """Safely retrieve a node from the network.
    
    Returns:
        Node if found, None otherwise (with error message printed)
    """
    try:
        return net[node_name]
    except KeyError:
        info(f'Node not found: {node_name}\n')
        return None


def find_link_between_nodes(net: 'Mininet', node1: 'Node', node2: 'Node'):
    """Find a link connecting two nodes.
    
    Returns:
        Link object if found, None otherwise
    """
    for link in net.links:
        if (link.intf1.node == node1 and link.intf2.node == node2) or \
           (link.intf1.node == node2 and link.intf2.node == node1):
            return link
    return None


class mnIPLabCLI(CLI):
    """Extended Mininet CLI with network inspection commands."""

    prompt = 'mininet-iplab> '

    @staticmethod
    def _resolve_hosts_path(hosts_file: Optional[Union[str, Any]] = None) -> Optional[str]:
        """Resolve a hosts file path from either a string or a Lab object.

        Returns ``None`` when nothing was given: the hosts file is per Lab,
        so there is no global path to fall back to.
        """
        if hosts_file is None:
            return None
        if hasattr(hosts_file, "hosts_file"):
            return str(getattr(hosts_file, "hosts_file"))
        return str(hosts_file)

    @staticmethod
    def ensure_hosts_file(hosts_file: Optional[Union[str, Any]] = None) -> None:
        """Guarantee a regular file at a Lab's hosts path, creating it if absent.

        Args:
            hosts_file: The Lab instance or the hosts file path string. With
                neither there is no file to ensure, and nothing is written.
        """
        target = mnIPLabCLI._resolve_hosts_path(hosts_file)
        if target is None:
            return
        mnIPLabCLI._clear_stale_hosts_path(target)
        if not os.path.exists(target):
            mnIPLabCLI._write_loopback_entry(target)

    @staticmethod
    def init_hosts_file(hosts_file: Optional[Union[str, Any]] = None) -> None:
        """Reset a Lab's hosts file to its single loopback entry.

        The Lab calls this on itself when constructed, so a Lab Example has
        nothing to call first. The no-argument form does nothing: the hosts
        file is per Lab, and the unkeyed path it used to write is one no Node
        binds any more. It is kept so a Lab Example written against the
        earlier docs, which told it to call ``mnIPLabCLI.init_hosts_file()``
        before ``mnIPLab()``, still runs.

        Args:
            hosts_file: The Lab instance or the hosts file path string.
        """
        target = mnIPLabCLI._resolve_hosts_path(hosts_file)
        if target is None:
            return
        mnIPLabCLI._clear_stale_hosts_path(target)
        mnIPLabCLI._write_loopback_entry(target)

    @staticmethod
    def _clear_stale_hosts_path(hosts_file: str) -> None:
        """Make the shared hosts path writable as a file, parents included.

        Args:
            hosts_file: The shared hosts file every Node bind-mounts.
        """
        os.makedirs(os.path.dirname(hosts_file), exist_ok=True)
        remove_stale_directory(hosts_file)

    @staticmethod
    def _write_loopback_entry(hosts_file: str) -> None:
        """Write the shared hosts file's single starting entry, replacing any content.

        Args:
            hosts_file: The shared hosts file every Node bind-mounts.
        """
        with open(hosts_file, 'w') as f:
            f.write('127.0.0.1 localhost\n')

    @staticmethod
    def seed_hostname_file(name: str, hostname_file: str) -> None:
        """Write a Node's private /etc/hostname backing file on the host.

        Each Host and Router bind-mounts this file over /etc/hostname (shared
        /etc cannot carry a per-Node name). Seeded with the Node's name: a
        Node's hostname is a projection of ``node.name`` (spec: Identity).
        Must be called before ``net.start()`` mounts the Node's private
        directories.

        Args:
            name: Node name; becomes the file's single line.
            hostname_file: Backing path template; ``%(name)s`` is substituted
                with the Node name, matching the privateDirs binding.
        """
        path = hostname_file % {'name': name}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(f'{name}\n')

    @staticmethod
    def seed_resolv_conf_file(name: str,
                              resolv_conf_file: str,
                              source: str = '/etc/resolv.conf',
                              nameservers: Optional[Union[Iterable[str], str]] = None) -> None:
        """Write a Node's private /etc/resolv.conf backing file on the host.

        Each Host and Router bind-mounts this file over /etc/resolv.conf so
        a DHCP client's rewrite (or a Learner's edit) lands on the Node's own
        file, never on the host's resolver configuration. Seeded with a copy
        of the host's resolver configuration so name resolution works until
        a client overwrites it — privately. When ``nameservers`` is provided,
        the file is populated directly with nameserver entries. Must be called before
        ``net.start()`` mounts the Node's private directories.

        Args:
            name: Node name.
            resolv_conf_file: Backing path template; ``%(name)s`` is
                substituted with the Node name, matching the privateDirs
                binding.
            source: The resolver configuration to copy when nameservers is omitted;
                an unreadable source seeds an empty file rather than failing node creation.
            nameservers: Optional nameserver IP address or collection of IP addresses.
        """
        path = resolv_conf_file % {'name': name}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if nameservers:
            from mniplab.dns.diagnostics import normalize_nameservers
            servers = normalize_nameservers(nameservers)
            contents = "".join(f"nameserver {s}\n" for s in servers)
        else:
            try:
                with open(source) as f:
                    contents = f.read()
            except OSError:
                contents = ''
        with open(path, 'w') as f:
            f.write(contents)

    @staticmethod
    def collect_ip_entries(net: 'Mininet', ipv6: bool = False) -> List[Tuple[str, str, str]]:
        """Collect (node_name, intf_name, ip) tuples for all IPv4 or IPv6 interfaces."""
        return collect_ip_entries(net, ipv6=ipv6)

    @staticmethod
    def update_hosts_file(net: 'Mininet', hosts_file: Optional[Union[str, Any]] = None) -> None:
        """Update the shared /etc/hosts file with all node IPv4 and IPv6 addresses.

        Args:
            net: Mininet network instance to collect IPs from.
            hosts_file: Optional path or Lab instance to resolve hosts file from.
        """
        if hosts_file is None:
            target = net.hosts_file
        else:
            target = mnIPLabCLI._resolve_hosts_path(hosts_file)
        update_hosts_file(net, hosts_file=target)

    def _collect_ip_entries(self, ipv6: bool = False) -> List[Tuple[str, str, str]]:
        """Collect interface address entries from the current Mininet instance."""
        return self.collect_ip_entries(self.mn, ipv6=ipv6)

    def _log_ip_entries(self) -> None:
        """Log all interface IPs (IPv4 and IPv6) for quick inspection."""
        ipv4_entries = self._collect_ip_entries(ipv6=False)
        ipv6_entries = self._collect_ip_entries(ipv6=True)

        info('*** Interface IPs:\n')
        if ipv4_entries:
            info('  IPv4:\n')
            for node, intf, ip in ipv4_entries:
                info(f'    {node} {intf}: {ip}\n')

        if ipv6_entries:
            info('  IPv6:\n')
            for node, intf, ip in ipv6_entries:
                info(f'    {node} {intf}: {ip}\n')

    def _update_hosts_file(self, hosts_file: Optional[str] = None) -> None:
        """Update /etc/hosts on all nodes with all IP addresses (IPv4 and IPv6)."""
        target = hosts_file or self.mn.hosts_file
        self.update_hosts_file(self.mn, hosts_file=target)
        info(f'\n*** Updated {target} (shared by all nodes)\n')

    def do_update_hosts(self, _line: str) -> None:
        """Update /etc/hosts on all nodes with all IP addresses."""
        self._update_hosts_file()

    def do_show_ips(self, _line: str) -> None:
        """Show all interface IPs (both IPv4 and IPv6)."""
        self._log_ip_entries()

    def do_show_leases(self, line: str) -> None:
        """Show the Leases a Node's DHCP Service currently holds.

        Reported separately from configured Interface addresses (see
        ``show_ips``): a Lease is the server's own record, which exists
        before a client applies it and outlives the moment it stops being
        used.

        Usage: show_leases <node>
        """
        # Imported locally: mniplab.dhcp.leases pulls in DHCPProvisioner,
        # which itself imports this module, so a top-level import here
        # would be circular.
        from mniplab.dhcp.leases import LeaseQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_leases <node>\n')
            return

        node_name = tokens[0]
        family_filter = tokens[1] if len(tokens) > 1 else None

        try:
            result = LeaseQueryService.get_leases(self.mn, node_name)
        except ValueError as e:
            info(f'Error: {e}\n')
            return

        has_v4 = 'v4' in result
        has_v6 = 'v6' in result

        show_v4 = has_v4 and (family_filter is None or family_filter in ('4', 'v4', 'ipv4'))
        show_v6 = has_v6 and (family_filter is None or family_filter in ('6', 'v6', 'ipv6'))

        if not show_v4 and not show_v6:
            info(f'*** No Leases held by {node_name}\n')
            return

        if show_v4:
            leases_v4 = result['v4']
            if not leases_v4:
                info(f'*** IPv4: No Leases held by {node_name}\n')
            else:
                info(f'*** IPv4 Leases held by {node_name}:\n')
                for lease in leases_v4:
                    address = lease.get('ip-address', '?')
                    hw_address = lease.get('hw-address', '?')
                    valid_lft = lease.get('valid-lft', '?')
                    info(f'    {address}  hw={hw_address}  valid-lft={valid_lft}s{relay_path_label(lease)}\n')

        if show_v6:
            leases_v6 = result.get('v6', [])
            if not leases_v6:
                info(f'*** IPv6: No Leases held by {node_name}\n')
            else:
                info(f'*** IPv6 Leases held by {node_name}:\n')
                for lease in leases_v6:
                    address = lease.get('ip-address', '?')
                    duid = lease.get('duid', '?')
                    hw_address = lease.get('hw-address')
                    hw_part = f'  hw={hw_address}' if hw_address else ''
                    preferred_lft = lease.get('preferred-lft', '?')
                    valid_lft = lease.get('valid-lft', '?')
                    info(f'    {address}  duid={duid}{hw_part}  preferred-lft={preferred_lft}s  valid-lft={valid_lft}s{relay_path_label(lease)}\n')

    def do_show_relay_activity(self, line: str) -> None:
        """Show the DHCP requests a Node's Relay has forwarded.

        Usage: show_relay_activity <node>
        """
        from mniplab.dhcp.relay_activity import RelayActivityQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_relay_activity <node>\n')
            return

        node_name = tokens[0]
        try:
            result = RelayActivityQueryService.get_activity(self.mn, node_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        activity = result.get('activity', [])
        if not activity:
            info(f'*** No Relay activity recorded by {node_name}\n')
            return

        info(f'*** Relay activity recorded by {node_name}:\n')
        for forwarded in activity:
            source = forwarded.get('source', '?')
            server = forwarded.get('server', '?')
            info(f'    {source} -> {server}\n')


    def do_loglevel(self, line: str) -> None:
        """Set log level for mnIPLab logging.
        
        Usage: loglevel <level>
        
        Levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
        
        Examples:
            loglevel DEBUG    - Enable debug logging
            loglevel INFO     - Standard logging (default)
            loglevel ERROR    - Only errors
        """
        level = line.strip().upper()
        
        if not level:
            info('Usage: loglevel <level>\n')
            info(f'Valid levels: {", ".join(VALID_LOG_LEVELS)}\n')
            return
        
        if level not in VALID_LOG_LEVELS:
            info(f'Invalid log level: {level}\n')
            info(f'Valid levels: {", ".join(VALID_LOG_LEVELS)}\n')
            return
        
        try:
            # Update mniplab logger
            set_log_level(level)
            # Update mininet logger
            setLogLevel(level.lower())
            
            info(f'Log level set to {level} (affects both mnIPLab and Mininet)\n')
            logger.info(f'Log level changed to {level} from CLI')
        except (ValueError, TypeError) as e:
            info(f'Failed to set log level: {e}\n')

    def _visible_nodes(self):
        """Return nodes that are not marked as hidden."""
        from mniplab.utils import is_hidden
        return [node for node in self.mn.values() if not is_hidden(node)]
    
    def do_showlog(self, _line: str) -> None:
        """Show current log level.
        
        Usage: showlog
        """
        import logging as log_module
        mniplab_logger = log_module.getLogger('mniplab')
        level_name = log_module.getLevelName(mniplab_logger.level)
        info(f'Current log level: {level_name}\n')

    def do_nodes(self, _line: str) -> None:
        """List only nodes that are visible."""
        visible = sorted(node.name for node in self._visible_nodes())
        output('available nodes are: \n%s\n' % ' '.join(visible))

    def do_net(self, _line: str) -> None:
        """List network connections between visible nodes."""
        dumpNodeConnections(self._visible_nodes())

    def do_link_config(self, line: str) -> None:
        """Configure link parameters (delay, jitter, loss, bandwidth, scheduling).
        
        Usage: link_config <node1> <node2> [delay=<ms>] [jitter=<ms>] [loss=<%>] [bw=<Mbps>] [htb|tbf]
        
        Scheduling: HFSC is used by default. Specify 'htb' or 'tbf' to use alternative schedulers.
        
        Examples:
            link_config r1 r2 delay=10ms           - Add 10ms delay
            link_config r1 r2 delay=10ms jitter=2ms - Add delay with jitter
            link_config r1 r2 loss=5               - Add 5% packet loss
            link_config r1 r2 bw=100               - Set bandwidth to 100 Mbps (uses HFSC)
            link_config r1 r2 bw=100 htb           - Use HTB scheduling
            link_config r1 r2 bw=100 tbf           - Use TBF scheduling
            link_config r1 r2 delay=5ms loss=2 bw=1000  - Combine parameters
        """
        args = line.strip().split()
        if len(args) < 3:
            info('Usage: link_config <node1> <node2> [delay=<ms>] [jitter=<ms>] [loss=<%>] [bw=<Mbps>] [htb|tbf]\n')
            return
        
        node1_name, node2_name = args[0], args[1]
        
        # Parse parameters
        try:
            parsed_config = NetworkTestService.parse_link_config_params(args[2:])
        except ValueError as e:
            info(f'Error: {e}\n')
            return
        
        # Apply via shared service
        try:
            result = NetworkTestService.configure_link(
                self.mn, node1_name, node2_name,
                delay=parsed_config.get('delay'),
                jitter=parsed_config.get('jitter'),
                loss=parsed_config.get('loss'),
                bandwidth=parsed_config.get('bw'),
                scheduler=parsed_config.get('scheduler', 'hfsc')
            )
        except ValueError as e:
            info(f'Error: {e}\n')
            return
        
        info(f'Link configured: {result["link"]}\n')
        for key, value in result['config'].items():
            info(f'  {key.capitalize()}: {value}\n')
        info('\n')

    def do_show_routes(self, line: str) -> None:
        """Show kernel routing table for a node (IPv4 and IPv6).
        
        Usage: show_routes <node> [-4|-6]
        """
        args = line.strip().split()
        if not args:
            info('Usage: show_routes <node> [-4|-6]\n')
            return
        
        node_name = args[0]
        node = get_node_from_net(self.mn, node_name)
        if not node:
            return
        
        # Determine which IP versions to show
        ip_version = None
        if len(args) > 1:
            if args[1] == '-4':
                ip_version = '4'
            elif args[1] == '-6':
                ip_version = '6'
        
        result = RoutingQueryService.get_node_routes(self.mn, node_name, ip_version)
        info(result['output'])

    def do_iperf3(self, line: str) -> None:
        """Run iperf3 bandwidth test between two nodes with configurable options.
        
        Usage: iperf3 <server> <client> [seconds=10] [protocol=tcp|udp] [json]
        
        Examples:
            iperf3 h1 h2                    # Default: 10 seconds, TCP
            iperf3 h1 h2 seconds=20         # 20-second test
            iperf3 h1 h2 protocol=udp       # UDP test
            iperf3 h1 h2 seconds=5 json     # 5s test with JSON output
        """
        args = line.strip().split()
        
        if len(args) < 2:
            info('Usage: iperf3 <server> <client> [seconds=10] [protocol=tcp|udp] [json]\n')
            return
        
        server_name, client_name = args[0], args[1]
        
        # Parse optional parameters
        config: Dict[str, Any] = {
            'seconds': DEFAULT_IPERF_DURATION,
            'protocol': 'tcp',
            'json_output': False
        }
        for param in args[2:]:
            if param == 'json':
                config['json_output'] = True
            elif param.startswith('seconds='):
                try:
                    config['seconds'] = int(param.split('=')[1])
                except (ValueError, IndexError):
                    info('Error: Invalid seconds value\n')
                    return
            elif param.startswith('protocol='):
                protocol = param.split('=')[1].lower()
                if protocol not in VALID_PROTOCOLS:
                    info(f'Error: Protocol must be one of: {", ".join(VALID_PROTOCOLS)}\n')
                    return
                config['protocol'] = protocol
            else:
                info(f'Error: Unknown parameter: {param}\n')
                return
        
        # Print configuration
        info(f'*** Running iperf3: {server_name} (server) <-> {client_name} (client)\n')
        info(f'    Protocol: {config["protocol"].upper()}\n')
        info(f'    Duration: {config["seconds"]}s\n')
        if config['json_output']:
            info('    Output: JSON\n')
        info('\n')
        
        # Run test via shared service
        try:
            result = NetworkTestService.run_iperf3(
                self.mn, server_name, client_name,
                seconds=config['seconds'],
                protocol=config['protocol'],
                json_output=config['json_output']
            )
            info(result['output'])
        except ValueError as e:
            info(f'Error: {e}\n')

    def do_show_dns(self, line: str) -> None:
        """Show DNS Service status, listener configuration, and hosted zones.

        Usage: show_dns <node>
        """
        from mniplab.dns.query import DNSQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_dns <node>\n')
            return

        node_name = tokens[0]
        try:
            dns_info = DNSQueryService.get_dns_info(self.mn, node_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(f'*** DNS Service on {node_name}:\n')
        status_label = 'Running' if dns_info.get('running') else 'Stopped'
        info(f'    Status: {status_label}\n')
        info(f"    Control socket: {dns_info['socket_path']} ({dns_info['socket_state']})\n")
        listeners = ', '.join(dns_info.get('listeners', []))
        info(f'    Listeners: {listeners}\n')
        zones = dns_info.get('zones', [])
        if zones:
            roles = dns_info.get('roles', {})
            info(f'    Hosted zones ({len(zones)}):\n')
            info(f"      {'Zone':<28} {'Role':<10} {'Primary'}\n")
            info(f"      {'-'*28} {'-'*10} {'-'*20}\n")
            for zone in zones:
                role = roles.get(zone, {})
                label = role.get('role') or '-'
                primary = role.get('primary') or '-'
                info(f"      {zone:<28} {label:<10} {primary}\n")
        else:
            info('    Hosted zones: none\n')

    def do_show_dns_transfers(self, line: str) -> None:
        """Show the Zone Transfers a Node has made, newest last.

        Usage: show_dns_transfers <node> [zone]
        """
        from mniplab.dns.query import DNSQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_dns_transfers <node> [zone]\n')
            return

        node_name = tokens[0]
        zone_name = tokens[1] if len(tokens) > 1 else None
        try:
            result = DNSQueryService.get_dns_transfers(self.mn, node_name, zone_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        scope = f" zone {result['zone']}" if result['zone'] else ''
        info(f'*** Zone Transfers on {node_name}{scope}:\n')
        if result['role']:
            primary = result['primary'] or '-'
            info(f"    Role: {result['role']}    Primary: {primary}\n")

        state = result['state']
        info(f"    State: {state['state']}\n")
        if state['detail']:
            info(f"      {state['detail']}\n")
        if state['since']:
            info(f"      Since: {state['since']}\n")
        if state['next_retry']:
            info(f"      Next retry: {state['next_retry']}\n")

        transfers = result['transfers']
        if not transfers:
            info('    No zone transfers logged yet\n')
            return

        info(f"    {'Time':<26} {'Dir':<9} {'Type':<5} {'Serial':<12} "
             f"{'Bytes':>8} {'Msgs':>5} {'Secs':>7}  {'Remote'}\n")
        info(f"    {'-'*26} {'-'*9} {'-'*5} {'-'*12} {'-'*8} {'-'*5} {'-'*7}  "
             f"{'-'*20}\n")
        for event in transfers:
            serial = (
                f"{event['serial_from'] if event['serial_from'] is not None else '-'}"
                f" -> {event['serial_to'] if event['serial_to'] is not None else '-'}"
            )
            size = '-' if event['bytes'] is None else str(event['bytes'])
            messages = '-' if event['messages'] is None else str(event['messages'])
            duration = '-' if event['duration'] is None else f"{event['duration']:.2f}"
            info(f"    {event['timestamp']:<26} {event['direction']:<9} "
                 f"{event['transfer_type']:<5} {serial:<12} {size:>8} {messages:>5} "
                 f"{duration:>7}  {event['remote']}\n")
            if event['outcome'] != 'finished':
                info(f"      failed: {event['detail']}\n")

    def do_dns_refresh(self, line: str) -> None:
        """Ask a Secondary Zone whether its Primary's Serial has moved.

        A Zone that has not changed transfers nothing at all.

        Usage: dns_refresh <node> <zone>
        """
        self._dns_transfer_command(line, 'dns_refresh', retransfer=False)

    def do_dns_retransfer(self, line: str) -> None:
        """Ask a Secondary Zone to take a full copy regardless of its Serial.

        Usage: dns_retransfer <node> <zone>
        """
        self._dns_transfer_command(line, 'dns_retransfer', retransfer=True)

    def _dns_transfer_command(
        self, line: str, command: str, retransfer: bool
    ) -> None:
        """Run one of the two Zone Transfer verbs against a Secondary Zone.

        The two verbs stay two commands rather than one command with a flag,
        because telling a refresh apart from a forced retransfer is the whole
        lesson: a flag would bury the distinction in an argument.

        Args:
            line: The raw CLI argument line.
            command: The command name, for the usage message.
            retransfer: True to force a full copy, False to check the Serial.
        """
        from mniplab.dns.query import DNSQueryService

        tokens = line.strip().split()
        if len(tokens) < 2:
            info(f'Usage: {command} <node> <zone>\n')
            return

        node_name, zone_name = tokens[0], tokens[1]
        action = (
            DNSQueryService.retransfer_zone if retransfer
            else DNSQueryService.refresh_zone
        )
        try:
            before = DNSQueryService.get_dns_transfers(
                self.mn, node_name, zone_name
            )['transfers']
            result = action(self.mn, node_name, zone_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        verb = 'Full transfer requested' if retransfer else 'Refresh requested'
        info(f"*** {verb} for {result['zone']} on {node_name} "
             f"from {result['primary']}\n")
        self._report_transfer_outcome(node_name, result['zone'], len(before))

    def _report_transfer_outcome(
        self, node_name: str, zone_name: str, before: int
    ) -> None:
        """Report what a refresh or a retransfer actually moved.

        Knot answers the control command before it has spoken to the Primary,
        so the outcome is read back from the Transfer Log a moment later. That
        is what tells the three outcomes apart at the prompt: a Zone that has
        not changed moves nothing at all, an edited one moves an increment,
        and a forced copy moves the whole Zone against an unmoved Serial.

        Args:
            node_name: The Secondary's Node name.
            zone_name: The zone apex FQDN.
            before: How many transfers the Log held before the command.
        """
        from mniplab.dns.query import DNSQueryService

        deadline = time.time() + TRANSFER_OUTCOME_TIMEOUT
        transfers: List[Dict[str, Any]] = []
        state: Dict[str, Any] = {}
        while time.time() < deadline:
            time.sleep(TRANSFER_OUTCOME_INTERVAL)
            try:
                result = DNSQueryService.get_dns_transfers(
                    self.mn, node_name, zone_name
                )
            except ValueError as exc:
                info(f'Error: {exc}\n')
                return
            transfers, state = result['transfers'], result['state']
            if len(transfers) > before:
                break

        if len(transfers) > before:
            moved = transfers[-1]
            serial = (
                f"{moved['serial_from']} -> {moved['serial_to']}"
                if moved['serial_from'] is not None
                else f"serial {moved['serial_to']}"
            )
            info(f"    {moved['transfer_type']} {serial}, "
                 f"{moved['bytes']} bytes in {moved['messages']} messages\n")
            return

        if state.get('state') == 'up-to-date':
            info(f"    Zone is up to date at serial {state['serial']}: "
                 f"nothing transferred\n")
            return

        info(f"    Nothing transferred yet ({state.get('state', 'unknown')}). "
             f"Run 'show_dns_transfers {node_name} {zone_name}' to follow it\n")
        if state.get('detail'):
            info(f"      {state['detail']}\n")

    def do_show_dns_records(self, line: str) -> None:
        """Show active Resource Records for a hosted zone on a Node.

        Usage: show_dns_records <node> <zone>
        """
        from mniplab.dns.query import DNSQueryService

        tokens = line.strip().split()
        if len(tokens) < 2:
            info('Usage: show_dns_records <node> <zone>\n')
            return

        node_name = tokens[0]
        zone_name = tokens[1]
        if not zone_name.endswith('.'):
            zone_name += '.'

        try:
            records = DNSQueryService.get_dns_records(self.mn, node_name, zone_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        if not records:
            info(f'*** No records found in zone {zone_name} on {node_name}\n')
            return

        info(f'*** DNS Records for zone {zone_name} on {node_name}:\n')
        info(f"    {'Owner':<24} {'TTL':<6} {'Type':<8} {'Data'}\n")
        info(f"    {'-'*24} {'-'*6} {'-'*8} {'-'*30}\n")
        for rec in records:
            owner = rec.get('owner', '')
            ttl = str(rec.get('ttl', ''))
            rtype = rec.get('type', '')
            data = rec.get('data', '')
            info(f"    {owner:<24} {ttl:<6} {rtype:<8} {data}\n")

    def do_dns_records(self, line: str) -> None:
        """Add or remove a DNS Resource Record in a zone on a Node.

        Usage: dns_records <node> <zone> add <owner> <type> <data...> [ttl]
               dns_records <node> <zone> del <owner> [type] [data...]
        """
        from mniplab.dns.record_service import DNSRecordService

        tokens = line.strip().split()
        if len(tokens) < 4:
            info(
                'Usage: dns_records <node> <zone> add <owner> <type> <data...> [ttl]\n'
                '       dns_records <node> <zone> del <owner> [type] [data...]\n'
            )
            return

        node_name, zone_name, action, owner = tokens[0], tokens[1], tokens[2].lower(), tokens[3]
        try:
            if action in ('add', 'set'):
                if len(tokens) < 6:
                    info('Usage: dns_records <node> <zone> add <owner> <type> <data...> [ttl]\n')
                    return
                rtype = tokens[4].upper()
                if tokens[-1].isdigit() and len(tokens) > 6:
                    ttl = int(tokens[-1])
                    data = ' '.join(tokens[5:-1])
                else:
                    ttl = 300
                    data = ' '.join(tokens[5:])
                DNSRecordService.set_record(
                    self.mn, node_name, zone_name, owner, rtype, data, ttl=ttl
                )
                info(f'Added record {owner} {rtype} {data} in {zone_name} on {node_name}\n')
            elif action in ('del', 'delete', 'unset', 'remove'):
                del_rtype = tokens[4].upper() if len(tokens) > 4 else None
                del_data = ' '.join(tokens[5:]) if len(tokens) > 5 else None
                DNSRecordService.unset_record(
                    self.mn, node_name, zone_name, owner, rtype=del_rtype, data=del_data
                )
                info(f'Deleted record {owner} in {zone_name} on {node_name}\n')
            else:
                info(f"Unknown action '{action}'. Use 'add' or 'del'.\n")
        except Exception as exc:
            info(f'Error: {exc}\n')

    def do_dnssec_keys(self, line: str) -> None:
        """Show DNSSEC keys and DS records for a hosted zone on a Node.

        Usage: dnssec_keys <node> <zone>
        """
        from mniplab.dns.query import DNSQueryService

        tokens = line.strip().split()
        if len(tokens) < 2:
            info('Usage: dnssec_keys <node> <zone>\n')
            return

        node_name, zone_name = tokens[0], tokens[1]
        try:
            info_data = DNSQueryService.get_zone_dnssec(self.mn, node_name, zone_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(f"*** DNSSEC Keys for zone {info_data['zone']} on {node_name}:\n")
        dnssec_status = "enabled" if info_data.get("dnssec") else "disabled"
        nsec3_status = (
            f"enabled (salt: {info_data.get('nsec3_salt') or 'none'})"
            if info_data.get("nsec3")
            else "disabled"
        )
        info(f"    DNSSEC: {dnssec_status}  NSEC3: {nsec3_status}\n")

        keys = info_data.get("keys", [])
        if keys:
            info(f"    {'Tag':<8} {'Role':<6} {'Alg':<5} {'Size':<6} {'ID'}\n")
            info(f"    {'-'*8} {'-'*6} {'-'*5} {'-'*6} {'-'*32}\n")
            for k in keys:
                tag = str(k.get("tag", ""))
                role = "KSK" if k.get("ksk") else ("ZSK" if k.get("zsk") else "KEY")
                alg = str(k.get("algorithm", ""))
                size = str(k.get("size", ""))
                key_id = str(k.get("id", ""))
                info(f"    {tag:<8} {role:<6} {alg:<5} {size:<6} {key_id}\n")
        else:
            info("    No cryptographic keys found\n")

        ds_recs = info_data.get("ds_records", [])
        if ds_recs:
            info("\n    DS Records (for parent delegation):\n")
            for ds in ds_recs:
                info(
                    f"    {ds['owner']} {ds['key_tag']} {ds['algorithm']} "
                    f"{ds['digest_type']} {ds['digest']}\n"
                )

    def do_dnssec_rollover(self, line: str) -> None:
        """Trigger an on-demand DNSSEC key rollover (ZSK or KSK) for a zone.

        Usage: dnssec_rollover <node> <zone> [zsk|ksk]
        """
        from mniplab.dns.record_service import DNSRecordService

        tokens = line.strip().split()
        if len(tokens) < 2:
            info('Usage: dnssec_rollover <node> <zone> [zsk|ksk]\n')
            return

        node_name, zone_name = tokens[0], tokens[1]
        key_type = tokens[2].lower() if len(tokens) > 2 else "zsk"
        if key_type not in ("zsk", "ksk"):
            info("Error: Key type must be 'zsk' or 'ksk'\n")
            return

        try:
            DNSRecordService.trigger_dnssec_rollover(
                self.mn, node_name, zone_name, key_type
            )
            info(f'Triggered {key_type.upper()} rollover for {zone_name} on {node_name}\n')
        except Exception as exc:
            info(f'Error: {exc}\n')

    def do_show_dns_stats(self, line: str) -> None:
        """Show live DNS query and response statistics for a Node.

        Usage: show_dns_stats <node> [--reset]
        """
        from mniplab.dns.query import DNSQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_dns_stats <node> [--reset]\n')
            return

        node_name = tokens[0]
        if len(tokens) > 1 and tokens[1] == '--reset':
            try:
                DNSQueryService.reset_dns_stats(self.mn, node_name)
                info(f'*** Query statistics reset for {node_name}\n')
            except ValueError as exc:
                info(f'Error: {exc}\n')
            return

        try:
            stats = DNSQueryService.get_dns_stats(self.mn, node_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(f'*** DNS Query Statistics for {node_name}:\n')
        req_proto = stats.get('request-protocol', {})
        info('    Request Protocols:\n')
        if req_proto:
            for proto, count in req_proto.items():
                info(f'      {proto}: {count}\n')
        else:
            info('      (none)\n')

        server_op = stats.get('server-operation', {})
        info('    Server Operations:\n')
        if server_op:
            for op, count in server_op.items():
                info(f'      {op}: {count}\n')
        else:
            info('      (none)\n')

        resp_code = stats.get('response-code', {})
        info('    Response Codes:\n')
        if resp_code:
            for code, count in resp_code.items():
                info(f'      {code}: {count}\n')
        else:
            info('      (none)\n')

        req_bytes = stats.get('request-bytes', {})
        resp_bytes = stats.get('response-bytes', {})
        if req_bytes or resp_bytes:
            info('    Traffic Volume:\n')
            for kind, b in req_bytes.items():
                info(f'      request {kind}: {b} B\n')
            for kind, b in resp_bytes.items():
                info(f'      response {kind}: {b} B\n')

    def do_show_rpki(self, line: str) -> None:
        """Show each Hosted CA on an RPKI CA, with its resources, ROAs and ASPAs.

        Usage: show_rpki [node]
        """
        from mniplab.errors import KrillError
        from mniplab.rpki.models import ASPA, ROA
        from mniplab.rpki.query import RPKIQueryService

        tokens = line.strip().split()
        node_name = tokens[0] if tokens else None
        try:
            cas = RPKIQueryService.list_cas(self.mn, node_name)
        except (ValueError, KrillError) as exc:
            info(f'Error: {exc}\n')
            return

        label = f' on {node_name}' if node_name else ''
        info(f'*** RPKI CA{label}:\n')
        for ca in cas:
            info(f"    AS{ca['asn']} ({ca['handle']})\n")
            info(f"      Resources: {', '.join(ca['prefixes'])}\n")
            roas = [str(ROA(**roa)) for roa in ca['roas']]
            aspas = [str(ASPA(**aspa)) for aspa in ca['aspas']]
            info('      ROAs:\n')
            for text in roas or ['none']:
                info(f'        {text}\n')
            info('      ASPAs:\n')
            for text in aspas or ['none']:
                info(f'        {text}\n')

    def do_rpki_roas(self, line: str) -> None:
        """Add or remove a ROA at a Hosted CA on an RPKI CA.

        Usage: rpki_roas <node> <asn> add|remove <prefix>[-<maxlen>] => <origin>
        """
        from mniplab.errors import KrillError
        from mniplab.rpki.models import ROA
        from mniplab.rpki.objects import RPKIObjectService

        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            info('Usage: rpki_roas <node> <asn> add|remove <prefix>[-<maxlen>] => <origin>\n')
            return

        node_name, asn, action, text = parts
        action = action.lower()
        if action not in ('add', 'remove'):
            info(f"Unknown action '{action}'. Use 'add' or 'remove'.\n")
            return
        try:
            roa = ROA.parse(text)
            if action == 'add':
                result = RPKIObjectService.add_roa(self.mn, node_name, asn, roa)
                verb = 'Added'
            else:
                result = RPKIObjectService.remove_roa(self.mn, node_name, asn, roa)
                verb = 'Removed'
            info(f"{verb} ROA {roa} at AS{result['asn']} on {node_name}\n")
        except (ValueError, KrillError) as exc:
            info(f'Error: {exc}\n')

    def do_rpki_aspas(self, line: str) -> None:
        """Add or remove an ASPA at a Hosted CA on an RPKI CA.

        Usage: rpki_aspas <node> <asn> add <customer> => <providers>
               rpki_aspas <node> <asn> remove <customer> [=> <providers>]
        """
        from mniplab.errors import KrillError
        from mniplab.rpki.models import ASPA, as_asn
        from mniplab.rpki.objects import RPKIObjectService

        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            info('Usage: rpki_aspas <node> <asn> add <customer> => <providers>\n'
                 '       rpki_aspas <node> <asn> remove <customer> [=> <providers>]\n')
            return

        node_name, asn, action, text = parts
        action = action.lower()
        if action not in ('add', 'remove'):
            info(f"Unknown action '{action}'. Use 'add' or 'remove'.\n")
            return
        try:
            if action == 'add':
                aspa = ASPA.parse(text)
                result = RPKIObjectService.add_aspa(self.mn, node_name, asn, aspa)
                info(f"Added ASPA {aspa} at AS{result['asn']} on {node_name}\n")
            else:
                customer = ASPA.parse(text).customer if '=>' in text else as_asn(text)
                result = RPKIObjectService.remove_aspa(self.mn, node_name, asn, customer)
                info(f"Removed ASPA for AS{customer} at AS{result['asn']} on {node_name}\n")
        except (ValueError, KrillError) as exc:
            info(f'Error: {exc}\n')

    def do_show_vrps(self, line: str) -> None:
        """Show what a Validator serves: its VRPs and ASPAs, when it refreshed, and its connected Routers.

        Usage: show_vrps [node]
        """
        from mniplab.rpki.query import RPKIQueryService

        tokens = line.strip().split()
        node_name = tokens[0] if tokens else None
        try:
            view = RPKIQueryService.get_vrps(self.mn, node_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(f"*** Validator on {view['node']}:\n")
        info(f"    Last refresh: {view['last_refresh'] or 'never'}\n")
        info(f"    Next refresh: {view['next_refresh'] or 'never'}\n")
        info(f"    Connected Routers: {', '.join(view['routers']) or 'none'}\n")
        info(f"    VRPs ({len(view['vrps'])}):\n")
        if view['vrps']:
            info(f"      {'Prefix':<20} {'maxLength':<10} Origin\n")
            for vrp in view['vrps']:
                info(f"      {vrp['prefix']:<20} {vrp['max_length']:<10} AS{vrp['origin']}\n")
        else:
            info('      none\n')
        info(f"    ASPAs ({len(view['aspas'])}):\n")
        if view['aspas']:
            info(f"      {'Customer':<10} Providers\n")
            for aspa in view['aspas']:
                providers = ', '.join(f"AS{provider}" for provider in aspa['providers'])
                info(f"      {'AS' + str(aspa['customer']):<10} {providers}\n")
        else:
            info('      none\n')

    def do_show_cache(self, line: str) -> None:
        """Show a Resolver's live cache: query metrics, then every cached entry.

        Usage: show_cache <node>
        """
        from mniplab.resolver.query import ResolverQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_cache <node>\n')
            return

        node_name = tokens[0]
        try:
            metrics = ResolverQueryService.get_metrics(self.mn, node_name)
            entries = ResolverQueryService.get_cache(self.mn, node_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(f'*** Resolver Cache on {node_name}:\n')
        dnssec_summary = ""
        if (
            hasattr(metrics, "dnssec_secure")
            and (metrics.dnssec_secure or metrics.dnssec_bogus or metrics.dnssec_insecure)
        ):
            dnssec_summary = (
                f"  DNSSEC: secure={metrics.dnssec_secure} "
                f"bogus={metrics.dnssec_bogus} insecure={metrics.dnssec_insecure}"
            )
        info(
            f"    Queries: {metrics.queries}  Hits: {metrics.hits}  "
            f"Misses: {metrics.misses}  Hit ratio: {metrics.hit_ratio}%  "
            f"Avg recursion: {metrics.recursion_time_ms} ms{dnssec_summary}\n"
        )

        if not entries:
            info('    Cache is empty\n')
            return

        info(
            f"    {'Name':<32} {'Type':<6} {'TTL':<6} {'Status':<10} "
            f"{'Security':<12} {'AD':<4} {'Data'}\n"
        )
        info(
            f"    {'-'*32} {'-'*6} {'-'*6} {'-'*10} "
            f"{'-'*12} {'-'*4} {'-'*30}\n"
        )
        for entry in entries:
            status = entry.status if entry.negative else 'cached'
            sec_status = getattr(entry, "security", "insecure") or "insecure"
            sec_tag = f"[{sec_status.upper()}]"
            ad_val = "AD" if getattr(entry, "authenticated_data", False) else "-"
            info(
                f"    {entry.name:<32} {entry.rtype:<6} {entry.ttl:<6} "
                f"{status:<10} {sec_tag:<12} {ad_val:<4} {entry.data}\n"
            )

    def do_flush_cache(self, line: str) -> None:
        """Evict cached entries so the next query walks the hierarchy again.

        Usage: flush_cache <node> [name] [--zone]

        Omit the name to clear the whole cache; pass --zone to clear the name
        and everything beneath it.
        """
        from mniplab.resolver.query import (
            FLUSH_SCOPE_NAME,
            FLUSH_SCOPE_ZONE,
            ResolverQueryService,
        )

        tokens = line.strip().split()
        if not tokens:
            info('Usage: flush_cache <node> [name] [--zone]\n')
            return

        node_name = tokens[0]
        scope = FLUSH_SCOPE_ZONE if '--zone' in tokens[1:] else FLUSH_SCOPE_NAME
        targets = [token for token in tokens[1:] if not token.startswith('--')]

        try:
            result = ResolverQueryService.flush_cache(
                self.mn, node_name, targets[0] if targets else None, scope=scope
            )
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(
            f"*** Flushed {result['scope']} {result['target']} from the cache "
            f"on {node_name}\n"
        )

    def do_show_resolver(self, line: str) -> None:
        """Show a Resolver's mode, hierarchy, and Access Control rules.

        Usage: show_resolver <node>
        """
        from mniplab.resolver.query import ResolverQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_resolver <node>\n')
            return

        node_name = tokens[0]
        try:
            resolver = ResolverQueryService.get_resolver_info(self.mn, node_name)
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(f'*** Resolver Service on {node_name}:\n')
        status_label = 'Running' if resolver.get('running') else 'Stopped'
        info(f'    Status: {status_label}\n')
        info(
            f"    Control socket: {resolver['socket_path']} "
            f"({resolver['socket_state']})\n"
        )
        info(f"    Listeners: {', '.join(resolver.get('listeners', []))}\n")
        info(f"    Mode: {resolver['mode']}\n")
        info(
            f"    TTL caps: {resolver['max_ttl']}s positive, "
            f"{resolver['max_negative_ttl']}s negative\n"
        )

        hints = resolver.get('root_hints', [])
        if hints:
            info(f'    Root hints ({len(hints)}):\n')
            for hint in hints:
                addresses = ', '.join(hint.get('addresses', [])) or '(unresolved)'
                info(f"      - {hint['fqdn']}  {addresses}\n")
        else:
            info('    Root hints: none\n')

        forwarders = resolver.get('forwarders', [])
        if forwarders:
            info(f"    Forwarders: {', '.join(forwarders)}\n")
        else:
            info('    Forwarders: none\n')

        rules = resolver.get('access_control', [])
        if rules:
            info(f'    Access control ({len(rules)}):\n')
            for rule in rules:
                info(f"      - {rule['subnet']:<20} {rule['action']}\n")
        else:
            info('    Access control: none\n')

    def do_resolver_access(self, line: str) -> None:
        """Show or author a Resolver's client Access Control rules, applied hot.

        Usage: resolver_access <node>
               resolver_access <node> add <cidr> <allow|refuse|deny>
               resolver_access <node> del <cidr>
        """
        from mniplab.resolver.access import ResolverAccessService

        tokens = line.strip().split()
        usage = (
            'Usage: resolver_access <node>\n'
            '       resolver_access <node> add <cidr> <allow|refuse|deny>\n'
            '       resolver_access <node> del <cidr>\n'
        )
        if not tokens:
            info(usage)
            return

        node_name = tokens[0]
        try:
            if len(tokens) == 1:
                rules = ResolverAccessService.list_rules(self.mn, node_name)
            elif len(tokens) < 3:
                info(usage)
                return
            elif tokens[1].lower() == 'add':
                if len(tokens) < 4:
                    info(usage)
                    return
                rules = ResolverAccessService.add_rule(
                    self.mn, node_name, tokens[2], tokens[3]
                )
            elif tokens[1].lower() == 'del':
                rules = ResolverAccessService.remove_rule(
                    self.mn, node_name, tokens[2]
                )
            else:
                info(usage)
                return
        except ValueError as exc:
            info(f'Error: {exc}\n')
            return

        info(f'*** Access control on {node_name}:\n')
        for rule in rules:
            info(f"      - {rule['subnet']:<20} {rule['action']}\n")

    def do_show_speaker(self, line: str) -> None:
        """Show a Speaker's Neighbors, then what each was sent and sent back.

        Usage: show_speaker <node>
        """
        from mniplab.errors import ExaBGPControlError
        from mniplab.exabgp.query import SpeakerQueryService

        tokens = line.strip().split()
        if not tokens:
            info('Usage: show_speaker <node>\n')
            return

        node_name = tokens[0]
        try:
            speaker = SpeakerQueryService.get_speaker(self.mn, node_name)
        except (ValueError, ExaBGPControlError) as exc:
            info(f'Error: {exc}\n')
            return

        info(f"*** Speaker {speaker['node']} (AS {speaker['local_as']})\n")
        neighbors = speaker['neighbors']
        info(f'    Neighbors ({len(neighbors)}):\n')
        for neighbor in neighbors:
            uptime = f"up {neighbor['uptime']}s" if neighbor['up'] else 'down'
            info(
                f"      - {neighbor['address']:<20} AS {neighbor['peer_as']}  "
                f"{neighbor['family']}  {neighbor['state']}  {uptime}\n"
            )
        for neighbor in neighbors:
            for heading, routes in (
                (f"Announced to {neighbor['address']}", neighbor['announced']),
                (f"Received from {neighbor['address']}", neighbor['received']),
            ):
                info(f'    {heading} ({len(routes)}):\n')
                if not routes:
                    info('      (none)\n')
                for route in routes:
                    info(f'      - {speaker_route_line(route)}\n')

    def do_announce(self, line: str) -> None:
        """Announce a route, or a burst of generated routes, from a Speaker.

        Usage: announce <node> [neighbor <ip>] <prefix> next-hop <ip>
                        [as-path <asn>…] [community <c>…]
                        [large-community <lc>…] [med <n>]
               announce <node> [neighbor <ip>] generate <count>
                        [sub-prefixes N] [prefix-seed S] [as-path-seed S]

        The route form is ExaBGP's own words. Without 'neighbor <ip>' the
        route goes to every Neighbor carrying the prefix's family. A
        community is <asn>:<value> or one of no-export, no-advertise,
        blackhole, no-peer. Attributes may be bracketed the way ExaBGP
        writes them: as-path [ 65001 65002 ].

        The generate form announces <count> random prefixes of the kind the
        Lab Examples ship — the Neighbor's family, its own address as next
        hop, an AS-PATH originating at the Speaker's AS — to one Neighbor
        (named, or the only one). sub-prefixes adds N longer prefixes inside
        each; the seeds make a burst repeatable. The prefixes announced are
        printed so they can be withdrawn by hand; 'withdraw … all' clears.

        Examples:
          announce sp1 neighbor 10.0.1.1 198.18.0.0/24 next-hop 10.0.1.2
          announce sp1 198.18.1.0/24 next-hop 10.0.1.2 as-path 65001 65002 med 50
          announce sp1 neighbor 2001:db8:1::1 2001:db8:200::/48 next-hop 2001:db8:1::2 community no-export
          announce sp1 neighbor 10.0.1.1 generate 20
          announce sp1 neighbor 10.0.1.1 generate 5 sub-prefixes 3 prefix-seed 42 as-path-seed 7
        """
        from mniplab.errors import ExaBGPControlError
        from mniplab.exabgp.grammar import parse_generate, parse_route
        from mniplab.exabgp.routes import SpeakerRouteService

        usage = ('Usage: announce <node> [neighbor <ip>] <prefix> next-hop <ip> '
                 '[as-path …] [community …] [large-community …] [med <n>]\n'
                 '       announce <node> [neighbor <ip>] generate <count> '
                 '[sub-prefixes N] [prefix-seed S] [as-path-seed S]\n')
        node_name, neighbor, words = _speaker_words(line)
        if node_name is None or not words:
            info(usage)
            return
        try:
            if words[0].lower() == 'generate':
                options = parse_generate(words[1:])
                result = SpeakerRouteService.generate(
                    self.mn, node_name, neighbor, options.count,
                    sub_prefixes=options.sub_prefixes,
                    prefix_seed=options.prefix_seed, as_path_seed=options.as_path_seed,
                )
                announced = result['announced']
                info(f"*** {node_name}: announced {len(announced)} generated "
                     f"{'prefix' if len(announced) == 1 else 'prefixes'} to {result['neighbor']}\n")
                for route in announced:
                    info(f'      - {speaker_route_line(route)}\n')
                return
            route = parse_route(words)
            result = SpeakerRouteService.announce(self.mn, node_name, route, neighbor=neighbor)
        except (ValueError, ExaBGPControlError) as exc:
            info(f'Error: {exc}\n')
            return
        info(f"*** {node_name}: announced {speaker_route_line(result['route'])}\n")
        info(f"    to {', '.join(result['neighbors'])}\n")

    def do_withdraw(self, line: str) -> None:
        """Withdraw a route, or everything announced, from a Speaker.

        Usage: withdraw <node> [neighbor <ip>] <prefix>
               withdraw <node> [neighbor <ip>] all

        Without 'neighbor <ip>' the prefix is withdrawn from every Neighbor
        carrying its family, and 'all' clears every Neighbor. 'all' is
        ExaBGP's clear adj-rib out: it withdraws what the Lab Example
        shipped along with what was announced since.

        Examples:
          withdraw sp1 neighbor 10.0.1.1 198.18.0.0/24
          withdraw sp1 198.18.0.0/24
          withdraw sp1 neighbor 10.0.1.1 all
        """
        from mniplab.errors import ExaBGPControlError
        from mniplab.exabgp.routes import SpeakerRouteService

        usage = 'Usage: withdraw <node> [neighbor <ip>] (<prefix> | all)\n'
        node_name, neighbor, words = _speaker_words(line)
        if node_name is None or len(words) != 1:
            info(usage)
            return
        try:
            if words[0].lower() == 'all':
                result = SpeakerRouteService.withdraw_all(self.mn, node_name, neighbor=neighbor)
                what = 'everything announced'
            else:
                result = SpeakerRouteService.withdraw(self.mn, node_name, words[0], neighbor=neighbor)
                what = result['prefix']
        except (ValueError, ExaBGPControlError) as exc:
            info(f'Error: {exc}\n')
            return
        info(f"*** {node_name}: withdrew {what} from {', '.join(result['neighbors'])}\n")

    def do_show_vlans(self, line: str) -> None:
        """Show 802.1Q VLAN configuration for manageable switches.

        Usage: show_vlans [switch]
        """
        from mniplab.switch import resolve_peer_info

        tokens = line.strip().split()
        target_switch = tokens[0] if tokens else None

        if target_switch:
            try:
                switch = self.mn.get(target_switch)
            except (KeyError, AttributeError):
                switch = None
            if not switch:
                info(f"Error: Switch '{target_switch}' not found\n")
                return
            if switch not in getattr(self.mn, "switches", []):
                info(f"Error: Node '{target_switch}' is not a switch\n")
                return
            switches = [switch]
        else:
            switches = list(getattr(self.mn, "switches", []))
            if not switches:
                info("*** No switches in network\n")
                return

        info(f"{'Switch':<10} {'Port':<14} {'Connected Peer':<24} {'Mode':<10} {'VLAN Tag(s)':<20}\n")
        info(f"{'-' * 10} {'-' * 14} {'-' * 24} {'-' * 10} {'-' * 20}\n")

        for switch in switches:
            if hasattr(switch, "get_vlans"):
                vlans_data = switch.get_vlans()
            else:
                vlans_data = {}
                for intf in getattr(switch, "intfs", {}).values():
                    if intf and intf.name != switch.name:
                        peer_node, peer_intf = resolve_peer_info(intf)
                        vlans_data[intf.name] = {
                            "port": intf.name,
                            "mode": "none",
                            "tag": None,
                            "trunks": [],
                            "native": None,
                            "peer_node": peer_node,
                            "peer_intf": peer_intf,
                        }

            for port_name in sorted(vlans_data.keys()):
                pinfo = vlans_data[port_name]
                mode = pinfo.get("mode", "none")
                tag = pinfo.get("tag")
                trunks = pinfo.get("trunks", [])
                native = pinfo.get("native")
                peer_node = pinfo.get("peer_node")
                peer_intf = pinfo.get("peer_intf")

                if peer_node and peer_intf:
                    peer_str = f"{peer_node} ({peer_intf})"
                elif peer_node:
                    peer_str = f"{peer_node}"
                else:
                    peer_str = "-"

                if mode == "access":
                    tags_str = str(tag) if tag is not None else "-"
                elif mode == "trunk":
                    trunks_repr = ", ".join(str(t) for t in trunks) if trunks else "all"
                    if native is not None:
                        tags_str = f"{trunks_repr} (native: {native})"
                    else:
                        tags_str = trunks_repr
                else:
                    tags_str = "-"

                info(f"{switch.name:<10} {port_name:<14} {peer_str:<24} {mode:<10} {tags_str:<20}\n")

    def help_show_vlans(self) -> None:
        """Help for show_vlans command."""
        info("Show 802.1Q VLAN configuration for manageable switches.\n"
             "Usage: show_vlans [switch]\n")

    def do_vlan(self, line: str) -> None:
        """Configure 802.1Q VLAN on a switch port.

        Usage: vlan <switch> <port> <access|trunk|none> [vlan_ids] [native_vlan]

        Examples:
          vlan s1 s1-eth1 access 10
          vlan s1 s1-eth2 trunk 10,20
          vlan s1 s1-eth2 trunk 10,20 1
          vlan s1 s1-eth1 none
        """
        from mniplab.switch import validate_vlan_id, parse_vlan_list

        usage = "Usage: vlan <switch> <port> <access|trunk|none> [vlan_ids] [native_vlan]\n"
        tokens = line.strip().split()
        if len(tokens) < 3:
            info(usage)
            return

        switch_name = tokens[0]
        port_name = tokens[1]
        mode = tokens[2].lower()

        try:
            switch = self.mn.get(switch_name)
        except (KeyError, AttributeError):
            switch = None
        if not switch:
            info(f"Error: Switch '{switch_name}' not found\n")
            return

        if not hasattr(switch, "set_access") or not hasattr(switch, "set_trunk"):
            info(f"Error: Switch '{switch_name}' is not a manageable switch\n")
            return

        if hasattr(switch, "resolve_port"):
            port_name = switch.resolve_port(port_name)

        port_exists = False
        if hasattr(switch, "nameToIntf") and port_name in switch.nameToIntf:
            port_exists = True
        elif hasattr(switch, "intfs"):
            port_exists = any(p.name == port_name for p in switch.intfs.values() if p)

        if not port_exists:
            info(f"Error: Port '{port_name}' not found on switch '{switch_name}'\n")
            return

        if mode == "access":
            if len(tokens) != 4:
                info("Error: Access mode requires exactly one VLAN ID (1-4094)\n")
                return
            try:
                vlan_id = validate_vlan_id(tokens[3])
            except ValueError as e:
                info(f"Error: {e}\n")
                return
            switch.set_access(port_name, vlan_id)
            info(f"*** Configured {switch_name} port {port_name} as access VLAN {vlan_id}\n")

        elif mode == "trunk":
            if len(tokens) not in (4, 5):
                info("Error: Trunk mode requires VLAN ID(s) and optional native VLAN (1-4094)\n")
                return
            try:
                trunks = parse_vlan_list(tokens[3])
            except ValueError as e:
                info(f"Error: {e}\n")
                return

            native = None
            if len(tokens) == 5:
                try:
                    native = validate_vlan_id(tokens[4], name="Native VLAN ID")
                except ValueError as e:
                    info(f"Error: {e}\n")
                    return

            switch.set_trunk(port_name, trunks, native=native)
            native_str = f" with native VLAN {native}" if native is not None else ""
            info(f"*** Configured {switch_name} port {port_name} as trunk VLANs {trunks}{native_str}\n")

        elif mode == "none":
            if len(tokens) != 3:
                info("Error: None mode takes no additional arguments\n")
                return
            switch.clear_vlan(port_name)
            info(f"*** Cleared VLAN configuration on {switch_name} port {port_name}\n")

        else:
            info("Error: Mode must be 'access', 'trunk', or 'none'\n")

    def help_vlan(self) -> None:
        """Help for vlan command."""
        info("Configure 802.1Q VLAN on a switch port.\n"
             "Usage: vlan <switch> <port> <access|trunk|none> [vlan_ids] [native_vlan]\n")

    def __init__(self, mininet: Any, *args: Any,
                 node_lock: Optional[ContextManager[Any]] = None, **kwargs: Any) -> None:
        """Start the CLI on a Lab and run it until it exits.

        Args:
            mininet: The Lab.
            *args: Passed on to Mininet's CLI.
            node_lock: A lock that another thread holds while it uses the
                Node shells, like the Lab process's Web UI operations. None
                when this CLI is the shells' only user.
            **kwargs: Passed on to Mininet's CLI.
        """
        self.node_lock = node_lock if node_lock is not None else contextlib.nullcontext()
        super().__init__(mininet, *args, **kwargs)

    def run(self) -> None:
        """Run the command loop, first ending commands an interrupt left running.

        Mininet sends Ctrl+C to every busy Node as the CLI starts and after
        each Ctrl+C at its prompt, then reads that Node's output. Busy Nodes
        are listed under ``node_lock``, once another thread's command has
        finished, so only the CLI's own commands are interrupted; the lock
        is not held while they end, which a command may take a while to do.
        """
        while True:
            try:
                with self.node_lock:
                    busy = [node for node in self.mn.values() if node.waiting]
                for node in busy:
                    while node.waiting:
                        info('stopping', node, '\n')
                        node.sendInt()
                        node.waitOutput()
                if self.isatty():
                    quietRun('stty echo sane intr ^C')
                self.cmdloop()
                break
            except KeyboardInterrupt:
                try:
                    output('\nInterrupt\n')
                except Exception:  # pylint: disable=broad-except
                    pass
