"""Utility functions for ExaBGP route generation and configuration.

Provides helper functions for creating random BGP routes and managing
ExaBGP configuration in lab scenarios.
"""

import random
import ipaddress
from typing import List, Optional, Tuple, Union
from mniplab.exabgp.models import BGPRoute

# Default AS numbers
DEFAULT_ORIGIN_AS = 65002

# IPv4 constants
DEFAULT_IPV4_NEXT_HOP = '10.0.0.2'
IPV4_PREFIX_LEN_MIN = 8
IPV4_PREFIX_LEN_MAX = 24
IPV4_SUBNET_MAX_PREFIX = 24  # Maximum prefix length for sub-prefixes

# IPv6 constants
DEFAULT_IPV6_NEXT_HOP = '2001:db8::2'
IPV6_PREFIX_LEN_MIN = 32
IPV6_PREFIX_LEN_MAX = 48
IPV6_SUBNET_MAX_PREFIX = 64  # Maximum prefix length for sub-prefixes

# AS-PATH generation constants
AS_PATH_MAX_LENGTH = 5
AS_NUMBER_MIN = 1
AS_NUMBER_MAX_PUBLIC = 64511  # Avoid private AS range (64512-65534)

# Sub-prefix generation constants
SUBNET_BITS_MIN = 2
SUBNET_BITS_MAX = 4


def _generate_as_path(origin_as: int, random_gen: random.Random, 
                      max_length: int = AS_PATH_MAX_LENGTH) -> List[int]:
    """Generate a random AS-PATH starting with origin AS.
    
    Args:
        origin_as: Origin AS number (prepended to path)
        random_gen: Random number generator instance
        max_length: Maximum additional ASNs in path (0-max_length)
    
    Returns:
        List of AS numbers starting with origin_as
    """
    tail_length = random_gen.randint(0, max_length)
    tail_asns = [random_gen.randint(AS_NUMBER_MIN, AS_NUMBER_MAX_PUBLIC) 
                 for _ in range(tail_length)]
    return [origin_as] + tail_asns


def _generate_random_ipv4_network(random_gen: random.Random) -> ipaddress.IPv4Network:
    """Generate a random public IPv4 network.
    
    Avoids private, multicast, loopback, link-local, and reserved ranges.
    
    Args:
        random_gen: Random number generator instance
    
    Returns:
        Random public IPv4 network with prefix length between /8 and /24
    """
    while True:
        # Generate random IPv4 address
        first = random_gen.randint(1, 223)
        second = random_gen.randint(0, 255)
        third = random_gen.randint(0, 255)
        fourth = random_gen.randint(0, 255)
        ip_int = (first << 24) | (second << 16) | (third << 8) | fourth
        addr = ipaddress.IPv4Address(ip_int)

        # Reject invalid ranges
        if not (addr.is_private or addr.is_multicast or addr.is_loopback
                or addr.is_link_local or addr.is_reserved):
            break
    
    # Use random prefix length
    prefix_len = random_gen.randint(IPV4_PREFIX_LEN_MIN, IPV4_PREFIX_LEN_MAX)
    return ipaddress.IPv4Network(f"{addr}/{prefix_len}", strict=False)


def _generate_random_ipv6_network(random_gen: random.Random) -> ipaddress.IPv6Network:
    """Generate a random IPv6 network in documentation range.
    
    Uses 2001:db8::/32 documentation range for lab safety.
    
    Args:
        random_gen: Random number generator instance
    
    Returns:
        Random IPv6 network with prefix length between /32 and /48
    """
    # Stay in 2001:db8::/32 documentation range
    first_hex = 0x2001
    second_hex = 0x0db8
    third_hex = random_gen.randint(0x0000, 0xffff)
    fourth_hex = random_gen.randint(0x0000, 0xffff)
    
    addr = ipaddress.IPv6Address(
        (first_hex << 112) | (second_hex << 96) | (third_hex << 80) | (fourth_hex << 64)
    )
    
    prefix_len = random_gen.randint(IPV6_PREFIX_LEN_MIN, IPV6_PREFIX_LEN_MAX)
    return ipaddress.IPv6Network(f"{addr}/{prefix_len}", strict=False)


def _generate_sub_prefixes(
    parent_net: Union[ipaddress.IPv4Network, ipaddress.IPv6Network],
    num_children: int,
    max_prefix_len: int,
    next_hop: str,
    as_path: List[int],
    random_gen: random.Random
) -> List[BGPRoute]:
    """Generate sub-prefixes within a parent network for LPM testing.
    
    Args:
        parent_net: Parent network to subdivide
        num_children: Number of sub-prefixes to generate
        max_prefix_len: Maximum allowed prefix length (/24 for IPv4, /64 for IPv6)
        next_hop: Next-hop IP for routes
        as_path: AS-PATH to use (inherited from parent)
        random_gen: Random number generator instance
    
    Returns:
        List of BGPRoute objects for sub-prefixes
    """
    sub_prefixes: List[BGPRoute] = []
    available_bits = max_prefix_len - parent_net.prefixlen
    
    # Only generate if there's room for subdivision
    if available_bits < 1:
        return sub_prefixes
    
    # Determine subdivision depth (2-4 bits, respecting available space)
    bits_to_add = min(random_gen.randint(SUBNET_BITS_MIN, SUBNET_BITS_MAX), available_bits)
    child_prefix_len = parent_net.prefixlen + bits_to_add
    
    # Generate subnets within the parent network
    for subnet in parent_net.subnets(new_prefix=child_prefix_len):
        if len(sub_prefixes) >= num_children:
            break
        sub_prefixes.append(
            BGPRoute(
                prefix=str(subnet.with_prefixlen),
                next_hop=next_hop,
                as_path=as_path
            )
        )
    
    return sub_prefixes


def make_v4_routes(num_prefixes: int = 20, prefix_seed: Optional[int] = None,
                    as_path_seed: Optional[int] = None,
                    num_sub_prefixes: Optional[int] = None) -> List[BGPRoute]:
    """Create random IPv4 routes with AS-PATH attributes.

    Generates public IPv4 prefixes (/8-/24) with diverse AS-PATH lists (AS 65002 origin).
    With num_sub_prefixes, creates hierarchical prefix structures for LPM testing (max /24).

    Args:
        num_prefixes: Routes to generate (base prefixes if num_sub_prefixes set). Defaults to 20.
        prefix_seed: Random seed for reproducible prefix generation.
        as_path_seed: Random seed for reproducible AS-PATH generation.
        num_sub_prefixes: Sub-prefixes per base (enables LPM mode with hierarchical structure).

    Returns:
        List[BGPRoute]: Generated routes ready for announcement.
    """
    return make_v4_peer_routes(
        num_prefixes=num_prefixes,
        local_asn=DEFAULT_ORIGIN_AS,
        next_hop=DEFAULT_IPV4_NEXT_HOP,
        prefix_seed=prefix_seed,
        as_path_seed=as_path_seed,
        num_sub_prefixes=num_sub_prefixes
    )


def make_v4_peer_routes(num_prefixes: int, local_asn: int,
                         next_hop: str, seed: Optional[int] = None,
                         prefix_seed: Optional[int] = None, as_path_seed: Optional[int] = None,
                         num_sub_prefixes: Optional[int] = None) -> List[BGPRoute]:
    """Create random IPv4 routes for a BGP peer.

    Like make_v4_routes() but with custom ASN and next-hop.
    See make_v4_routes() for full parameter descriptions.

    Args:
        num_prefixes: Routes to generate.
        local_asn: AS number for route origin.
        next_hop: Next-hop IP address for all routes.
        seed: deprecated — use prefix_seed/as_path_seed instead.
        prefix_seed: Random seed for prefix generation.
        as_path_seed: Random seed for AS-PATH generation.
        num_sub_prefixes: Sub-prefixes per base (LPM mode, max /24).

    Returns:
        List[BGPRoute]: Generated routes ready for announcement.
    """
    # Handle deprecated 'seed' parameter for backward compatibility
    if seed is not None:
        if prefix_seed is None:
            prefix_seed = seed
        if as_path_seed is None:
            as_path_seed = seed

    prefix_rnd = random.Random(prefix_seed) if prefix_seed is not None else random.Random()
    path_rnd = random.Random(as_path_seed) if as_path_seed is not None else random.Random()

    prefixes = []
    for _ in range(num_prefixes):
        # Generate random public IPv4 network
        net = _generate_random_ipv4_network(prefix_rnd)
        
        # Build AS-PATH with the specified local_asn
        as_path = _generate_as_path(local_asn, path_rnd)

        prefixes.append(
            BGPRoute(prefix=str(net.with_prefixlen), next_hop=next_hop, as_path=as_path)
        )

        # In LPM mode, generate sub-prefixes for this base prefix
        if num_sub_prefixes is not None:
            sub_routes = _generate_sub_prefixes(
                parent_net=net,
                num_children=num_sub_prefixes,
                max_prefix_len=IPV4_SUBNET_MAX_PREFIX,
                next_hop=next_hop,
                as_path=as_path,
                random_gen=prefix_rnd
            )
            prefixes.extend(sub_routes)

    return prefixes


def make_v6_routes(num_prefixes: int = 20, prefix_seed: Optional[int] = None,
                    as_path_seed: Optional[int] = None,
                    num_sub_prefixes: Optional[int] = None) -> List[BGPRoute]:
    """Create random IPv6 routes with AS-PATH attributes.

    Generates global IPv6 prefixes (/32-/48) with diverse AS-PATH lists (AS 65002 origin).
    With num_sub_prefixes, creates hierarchical prefix structures for LPM testing (max /64).

    Args:
        num_prefixes: Routes to generate (base prefixes if num_sub_prefixes set). Defaults to 20.
        prefix_seed: Random seed for reproducible prefix generation.
        as_path_seed: Random seed for reproducible AS-PATH generation.
        num_sub_prefixes: Sub-prefixes per base (enables LPM mode with hierarchical structure).

    Returns:
        List[BGPRoute]: Generated routes ready for announcement.
    """
    return make_v6_peer_routes(
        num_prefixes=num_prefixes,
        local_asn=DEFAULT_ORIGIN_AS,
        next_hop=DEFAULT_IPV6_NEXT_HOP,
        prefix_seed=prefix_seed,
        as_path_seed=as_path_seed,
        num_sub_prefixes=num_sub_prefixes
    )


def make_v6_peer_routes(num_prefixes: int, local_asn: int,
                         next_hop: str, seed: Optional[int] = None,
                         prefix_seed: Optional[int] = None, as_path_seed: Optional[int] = None,
                         num_sub_prefixes: Optional[int] = None) -> List[BGPRoute]:
    """Create random IPv6 routes for a BGP peer.

    Variant of make_v6_routes() with custom ASN and next-hop.
    Supports hierarchical prefix generation for LPM testing (max /64).

    Args:
        num_prefixes: Routes to generate (base prefixes if num_sub_prefixes set).
        local_asn: AS number for route origin.
        next_hop: IPv6 next-hop address for all routes.
        seed: (Deprecated) Seed for both prefix and AS-PATH.
        prefix_seed: Random seed for reproducible prefix generation.
        as_path_seed: Random seed for reproducible AS-PATH generation.
        num_sub_prefixes: Sub-prefixes per base (enables LPM mode with hierarchical structure).

    Returns:
        List[BGPRoute]: Generated routes ready for announcement.
    """
    # Handle deprecated 'seed' parameter for backward compatibility
    if seed is not None:
        if prefix_seed is None:
            prefix_seed = seed
        if as_path_seed is None:
            as_path_seed = seed

    prefix_rnd = random.Random(prefix_seed) if prefix_seed is not None else random.Random()
    path_rnd = random.Random(as_path_seed) if as_path_seed is not None else random.Random()

    prefixes = []
    for _ in range(num_prefixes):
        # Generate random IPv6 network
        net = _generate_random_ipv6_network(prefix_rnd)

        # Build AS-PATH with the specified local_asn
        as_path = _generate_as_path(local_asn, path_rnd)

        prefixes.append(
            BGPRoute(prefix=str(net.with_prefixlen), next_hop=next_hop, as_path=as_path)
        )

        # In LPM mode, generate sub-prefixes for this base prefix
        if num_sub_prefixes is not None:
            sub_routes = _generate_sub_prefixes(
                parent_net=net,
                num_children=num_sub_prefixes,
                max_prefix_len=IPV6_SUBNET_MAX_PREFIX,
                next_hop=next_hop,
                as_path=as_path,
                random_gen=prefix_rnd
            )
            prefixes.extend(sub_routes)

    return prefixes
