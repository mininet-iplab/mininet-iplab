"""Shared utility functions for Mininet-IPLab."""

import os
import shutil
import time
from typing import Any, Callable, List, Tuple, Optional
from mininet.log import info
from mniplab.logging import get_logger
from mniplab.rpki.service import rpki_ca_services

logger = get_logger(__name__)


def remove_stale_directory(path: str) -> bool:
    """Remove a directory standing where an ephemeral file belongs.

    Mininet's ``mountPrivateDirs`` creates a missing bind source with a plain
    ``mkdir -p``, so a Lab that starts before a backing file exists turns that
    path into a directory — mounted empty over the Node's own file, and left
    behind to break every later Lab. A regular file at ``path`` is left
    untouched: it is the backing file, not the damage.

    Args:
        path: The backing path to inspect.

    Returns:
        ``True`` if a stale directory was removed.

    Raises:
        OSError: If the directory cannot be removed. Deliberately not
            swallowed: a removal that silently failed resurfaces later as an
            ``IsADirectoryError`` naming the symptom instead of the reason.
    """
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
        return True
    return False


def is_hidden(node) -> bool:
    """Return True if *node* is marked as hidden from CLI and Web UI.

    The ``hidden`` attribute is the convention used throughout mnIPLab to
    suppress infrastructure nodes (e.g. NAT gateways) from topology listings
    without removing them from the network.

    Args:
        node: Any Mininet node.

    Returns:
        ``True`` when ``node.hidden`` is set and truthy; ``False`` otherwise.
    """
    return bool(getattr(node, 'hidden', False))


def is_drawn(node: Any) -> bool:
    """Return True if the Topology draws *node*.

    A hidden Node stays out of the Topology, except an Internet Gateway: the
    Learner sees it as the cloud the Lab's traffic leaves by, though the CLI's
    node list still leaves it out.

    Args:
        node: Any Mininet node.

    Returns:
        ``True`` when the Node is not hidden, or is an Internet Gateway.
    """
    return not is_hidden(node) or getattr(node, 'display_type', None) == 'internet'


def lives_in_host_network(node: Any) -> bool:
    """Return True if *node* has no namespace of its own.

    Such a Node runs in the Host machine's network: a Switch's ports and an
    Internet Gateway's uplink. A shell or a capture on it would act on the
    machine every Lab shares, so the levers refuse it by this rule rather
    than by whether it is hidden.

    Args:
        node: Any Mininet node.

    Returns:
        ``True`` when the Node's ``inNamespace`` is false; ``False`` otherwise.
    """
    return not getattr(node, 'inNamespace', True)


def host_network_refusal(name: str, is_switch: bool) -> str:
    """Return the reason a lever gives for refusing a Node in the Host machine's network.

    A Switch keeps the message that points to its Switch panel; any other
    such Node is an Internet Gateway, which is outside the Lab.

    Args:
        name: The Node's name.
        is_switch: Whether the Node is a Switch.

    Returns:
        The reason to show the Learner.
    """
    if is_switch:
        return f"{name} is a Switch; open its Switch panel instead"
    return f"{name} is the Internet, which is outside the Lab"


def _parse_ip_address_from_line(line: str) -> Optional[Tuple[str, str]]:
    """Parse an 'ip addr show' line to extract interface name and address.
    
    Args:
        line: Single line from 'ip -o addr show' output
        
    Returns:
        Tuple of (intf_name, addr) or None if parsing fails
    """
    fields = line.split()
    if len(fields) >= 4:
        intf_name = fields[1]
        addr = fields[3].split('/')[0]
        return intf_name, addr
    return None


def _should_skip_address(addr: str, ipv6: bool) -> bool:
    """Determine if an IP address should be skipped.
    
    Args:
        addr: IP address string
        ipv6: True if address is IPv6
        
    Returns:
        True if address should be skipped (link-local or localhost)
    """
    if ipv6:
        # Skip link-local IPv6 addresses (fe80::) and localhost
        return addr.startswith('fe80:') or addr == '::1'
    else:
        # Skip localhost IPv4
        return addr == '127.0.0.1'


def _collect_node_addresses(node, ipv6: bool) -> List[Tuple[str, str, str]]:
    """Collect addresses for a single node.
    
    Args:
        node: Mininet node
        ipv6: True to collect IPv6, False for IPv4
        
    Returns:
        List of tuples: (node_name, intf_name, addr)
    """
    addresses: List[Tuple[str, str, str]] = []
    ip_version = '-6' if ipv6 else '-4'
    ip_output = node.cmd(f"ip -o {ip_version} addr show").strip()
    
    if not ip_output:
        return addresses
    
    # Parse all addresses on the node
    for line in ip_output.split('\n'):
        parsed = _parse_ip_address_from_line(line)
        if not parsed:
            continue
        
        intf_name, addr = parsed
        
        # Skip unwanted addresses
        if _should_skip_address(addr, ipv6):
            continue
        
        addresses.append((node.name, intf_name, addr))
    
    return addresses


def collect_ip_entries(mininet_net, ipv6: bool = False) -> List[Tuple[str, str, str]]:
    """Collect (node_name, intf_name, ip) tuples for all IPv4 or IPv6 interfaces.
    
    This function queries all addresses on each node, including those added
    via cmd() (e.g., loopback addresses). It combines data from both the
    interface object list and direct system queries to ensure comprehensive
    address discovery, with deduplication to avoid listing the same address twice.
    Switches are skipped as they do not require IP address configuration.
    
    Args:
        mininet_net: Mininet network instance
        ipv6: If True, collect IPv6 addresses; otherwise IPv4 (default: False)
    
    Returns:
        List of tuples: (node_name, intf_name, ip)
    """
    entries = []
    seen_addresses = set()  # Track (node_name, intf_name, ip) to avoid duplicates
    
    # Get set of switch names to skip them
    switch_names = {switch.name for switch in mininet_net.switches}
    
    # Iterate over all nodes (hosts, routers)
    for node in mininet_net.values():
        # Skip switches and hidden nodes
        if node.name in switch_names or is_hidden(node):
            continue
        
        # Collect addresses for this node
        node_addresses = _collect_node_addresses(node, ipv6)
        
        # Add addresses to result, deduplicating
        for addr_tuple in node_addresses:
            if addr_tuple not in seen_addresses:
                seen_addresses.add(addr_tuple)
                entries.append(addr_tuple)
    
    return entries


def update_hosts_file(mininet_net: Any, hosts_file: Optional[str] = None) -> None:
    """Update /etc/hosts on all nodes with all IP addresses (IPv4 and IPv6).
    
    Args:
        mininet_net: Mininet network instance
        hosts_file: Path to the hosts file (default: the Lab's own)
    """
    if hosts_file is None:
        hosts_file = mininet_net.hosts_file
    # Collect both IPv4 and IPv6 addresses
    ipv4_entries = collect_ip_entries(mininet_net, ipv6=False)
    ipv6_entries = collect_ip_entries(mininet_net, ipv6=True)
    
    # Build hosts file content
    lines = ["127.0.0.1 localhost", "::1 localhost"]
    
    # Add IPv4 entries
    for node, _intf, ip in ipv4_entries:
        lines.append(f"{ip} {node}")
    
    # Add IPv6 entries
    for node, _intf, ip in ipv6_entries:
        lines.append(f"{ip} {node}")
    
    # An RPKI CA declared under its own hostname keeps that name too
    for host in getattr(mininet_net, 'hosts', []):
        for service in rpki_ca_services(host):
            if service.hostname not in (None, host.name):
                lines.extend(service.hosts_lines())

    hosts_content = "\n".join(lines) + "\n"
    
    try:
        with open(hosts_file, 'w') as f:
            f.write(hosts_content)
        info(f'\n*** Updated {hosts_file} with {len(lines)} entries (shared by all nodes)\n')
    except OSError as e:
        logger.error(f'Failed to update {hosts_file}: {e}')


def ensure_directory(path: str, mode: int = 0o755, silent: bool = False) -> bool:
    """Ensure a directory exists with specified permissions.
    
    Args:
        path: Directory path to create
        mode: Permission mode (default: 0o755)
        silent: If True, suppress errors and return False on failure
    
    Returns:
        True if directory exists or was created, False on error (if silent=True)
    
    Raises:
        OSError: If creation fails (when silent=False)
    """
    try:
        os.makedirs(path, exist_ok=True)
        os.chmod(path, mode)
        return True
    except OSError as e:
        if silent:
            logger.debug(f"Failed to ensure directory {path}: {e}")
            return False
        raise


def disable_icmp_rate_limiting(node) -> None:
    """Disable ICMP rate limiting on a node.
    
    Allows ICMP packets (ping, traceroute) to flow freely without rate limiting,
    which is useful for network testing and lab environments.
    
    Args:
        node: Mininet node to configure
    """
    from mniplab.kernel import KernelConfigManager
    KernelConfigManager(node).disable_icmp_rate_limiting()


def validate_ipv6_address(ip: str, node_name: str = 'unknown') -> str:
    """Validate and normalize IPv6 address format.
    
    Ensures IPv6 address includes prefix length and is in valid format.
    
    Args:
        ip: IPv6 address string (must include prefix like /64)
        node_name: Node name for error messages
    
    Returns:
        Normalized IPv6 address string
    
    Raises:
        ValueError: If address is invalid or missing prefix
    """
    import ipaddress
    
    try:
        ipv6_intf = ipaddress.IPv6Interface(ip)
    except (ipaddress.AddressValueError, ValueError) as e:
        raise ValueError(
            f"{node_name}: Invalid IPv6 address format\n"
            f"Address: {ip}\n"
            f"Error: {e}\n"
            f"Valid format: 2001:db8::1/64"
        ) from e
    
    if '/' not in ip:
        raise ValueError(
            f"{node_name}: IPv6 address must include prefix length\n"
            f"Invalid: {ip}\n"
            f"Valid example: 2001:db8::1/64 (not just 2001:db8::1)\n"
            f"Common prefix lengths: /64 (standard), /48 (site), /128 (host-only)"
        )
    
    return str(ipv6_intf)


def wait_until(
    probe: Callable[[], bool], timeout: float, interval: float = 0.05
) -> bool:
    """Poll ``probe`` until it returns True or ``timeout`` seconds elapse.

    Args:
        probe: A side-effect-free check; called at least once, even when
            ``timeout`` is zero.
        timeout: Seconds to keep polling after the first failed probe.
        interval: Seconds to sleep between probes.

    Returns:
        True as soon as ``probe`` does; False once the deadline passes.
    """
    deadline = time.monotonic() + timeout
    while True:
        if probe():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
