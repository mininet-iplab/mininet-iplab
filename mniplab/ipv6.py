"""Centralized IPv6 configuration for mnIPLab nodes and interfaces."""

import os
import re
import shlex
import ipaddress
from typing import Optional, List

from mniplab.errors import LinkError
from mniplab.logging import get_logger

logger = get_logger(__name__)

# DAD mode constants
DAD_MODE_FAST = 'fast'
DAD_MODE_STRICT = 'strict'
DAD_VALID_MODES = {DAD_MODE_FAST, DAD_MODE_STRICT}

# DAD timing constants
DEFAULT_DAD_MAX_WAIT = 3
DEFAULT_DAD_POLL_INTERVAL = 0.1


def get_dad_mode(params: Optional[dict] = None) -> str:
    """Extract and validate DAD mode from params or environment.
    
    Args:
        params: Optional parameter dictionary that may contain 'dad_mode'
        
    Returns:
        Validated DAD mode ('fast' or 'strict')
        
    Raises:
        LinkError: If DAD mode is invalid
    """
    dad_mode = str(
        (params or {}).get('dad_mode', os.getenv('MNIPLAB_IPV6_DAD_MODE', DAD_MODE_FAST))
    ).strip().lower()
    if dad_mode not in DAD_VALID_MODES:
        raise LinkError(
            f"Invalid IPv6 DAD mode '{dad_mode}'\n"
            f"Valid values: {', '.join(sorted(DAD_VALID_MODES))}\n"
            f"Set via params['dad_mode'] or MNIPLAB_IPV6_DAD_MODE"
        )
    return dad_mode


def validate_ipv6_address(ip: str, node_name: str = 'unknown') -> str:
    """Validate and normalize IPv6 address format.
    
    Args:
        ip: IPv6 address string (must include prefix like /64)
        node_name: Node name for error messages
        
    Returns:
        Normalized IPv6 address string
        
    Raises:
        ValueError: If address is invalid or missing prefix
    """
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
            f"Valid example: 2001:db8::1/64"
        )

    return str(ipv6_intf)


def normalize_ipv6_address(ip6: Optional[str], node_name: str = 'unknown') -> Optional[str]:
    """Normalize and validate IPv6 address.
    
    Args:
        ip6: IPv6 address string
        node_name: Node name for error messages
        
    Returns:
        Normalized IPv6 address or None if ip6 is None
        
    Raises:
        LinkError: If IPv6 address format is invalid
    """
    if not ip6:
        return None
    try:
        return str(ipaddress.IPv6Interface(ip6))
    except (ipaddress.AddressValueError, ValueError) as e:
        raise LinkError(
            f"{node_name}: Invalid IPv6 address format\n"
            f"Address: {ip6}\n"
            f"Error: {e}\n"
            f"Valid IPv6 format: 2001:db8::1/64"
        ) from e


def is_ipv6_address(addr: Optional[str]) -> bool:
    """Check if address is IPv6 (contains ':' separator).
    
    Args:
        addr: IP address string
        
    Returns:
        True if address contains ':', False otherwise
    """
    return bool(addr and ':' in str(addr))


def is_ipv4_address(addr: Optional[str]) -> bool:
    """Check if an address, or any word of a route spec, is IPv4.

    Args:
        addr: IP address string, with or without a prefix length, or a route
            spec such as ``'via 10.0.0.1'``.

    Returns:
        True if any word parses as an IPv4 address, False otherwise
    """
    for word in str(addr or '').split():
        try:
            ipaddress.IPv4Interface(word)
            return True
        except ValueError:
            continue
    return False


def is_ipv6_config_error(result: str) -> bool:
    """Check whether a command result reports a configuration error.

    An "exists" response is not an error: re-applying an address that is
    already present is harmless and happens during reconciliation.

    Args:
        result: Command output text.

    Returns:
        True if the output indicates a genuine failure.
    """
    if not result:
        return False
    lowered = result.lower()
    return 'error' in lowered and 'exist' not in lowered


def build_addr_add_command(intf_name: str, ipv6_addr: str, dad_mode: str = DAD_MODE_FAST) -> str:
    """Build the command that adds an IPv6 address to an interface.

    Args:
        intf_name: Interface name.
        ipv6_addr: IPv6 address with prefix length.
        dad_mode: DAD mode ('fast' skips detection, 'strict' performs it).

    Returns:
        The shell command to run on the owning node.
    """
    safe_intf = shlex.quote(intf_name)
    safe_ip = shlex.quote(ipv6_addr)
    dad_flag = '' if dad_mode == DAD_MODE_STRICT else ' nodad'
    return f'ip -6 addr add {safe_ip} dev {safe_intf}{dad_flag}'


def build_keep_addr_on_down_command(intf_name: str) -> str:
    """Build the command that keeps an interface's IPv6 addresses across a link down.

    A stock kernel discards an interface's IPv6 addresses when the link goes
    down and keeps its IPv4 ones; with this setting the two families behave
    alike, so a Link brought down and up — or an Interface Mininet takes
    down to set its MAC at start — comes back with its address.

    Args:
        intf_name: Interface name.

    Returns:
        The shell command to run on the owning node.
    """
    return f'sysctl -q -w net.ipv6.conf.{shlex.quote(intf_name)}.keep_addr_on_down=1'


def build_addr_show_command(intf_name: str) -> str:
    """Build the command that reads an interface's IPv6 addresses.

    Args:
        intf_name: Interface name.

    Returns:
        The shell command to run on the owning node.
    """
    return f'ip -6 addr show dev {shlex.quote(intf_name)}'


_INET6_RE = re.compile(r'inet6\s+([0-9a-fA-F:]+(?:/\d{1,3})?)')


def parse_addresses(output: str, include_link_local: bool = False) -> List[str]:
    """Extract IPv6 addresses from ``ip -6 addr show`` output.

    Mininet's own address read-back matches dotted quads, so it reports
    nothing for an IPv6-only interface.  This is the IPv6 counterpart.

    Args:
        output: Raw command output.
        include_link_local: Whether to include fe80::/10 addresses. Off by
            default, since a link-local address is the kernel's business
            rather than something a Lab Example configured.

    Returns:
        Addresses in the order they appeared, each with its prefix length
        when the output carried one.
    """
    if not output:
        return []

    found: List[str] = []
    for match in _INET6_RE.findall(output):
        addr_only = match.split('/')[0]
        if not include_link_local and addr_only.lower().startswith('fe80'):
            continue
        found.append(match)
    return found


def dad_failed(output: str) -> bool:
    """Return True if the output shows Duplicate Address Detection failed.

    Args:
        output: Output of an IPv6 address show command.

    Returns:
        True if a duplicate address was detected on the network.
    """
    return 'dadfailed' in (output or '')


def dad_settled(output: str, ipv6_addr: str) -> bool:
    """Return True if the address is present and no longer tentative.

    Args:
        output: Output of an IPv6 address show command.
        ipv6_addr: Address being waited on, with or without prefix length.

    Returns:
        True once the address has been proven unique.
    """
    if not output:
        return False
    addr_only = ipv6_addr.split('/')[0]
    return addr_only in output and 'tentative' not in output
