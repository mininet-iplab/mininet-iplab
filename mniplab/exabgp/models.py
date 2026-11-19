"""Simple BGP model classes for ExaBGP configuration generation."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BGPRoute:
    """Represents a route to be announced by ExaBGP.

    One shape for a route declared in a Lab Example and a route announced
    at runtime, so what a Speaker reports back can be compared with what
    was asked of it. ``local_preference``, ``origin`` and extended
    communities are not modelled.

    Attributes:
        prefix: IP prefix (e.g., '10.0.100.0/24')
        next_hop: Next-hop IP address
        community: Optional list of communities (e.g., ['65000:100'])
        as_path: Optional AS path to attach to this route when announcing via ExaBGP
        large_community: Optional list of large communities (e.g., ['65000:1:2'])
        med: Optional multi-exit discriminator
    """
    prefix: str
    next_hop: str
    community: Optional[List[str]] = field(default_factory=list)
    as_path: Optional[List[int]] = field(default_factory=list)
    large_community: Optional[List[str]] = field(default_factory=list)
    med: Optional[int] = None


@dataclass
class BGPNeighbor:
    """Represents a BGP neighbor/session definition.

    Attributes:
        peer: Peer IP address (IPv4 or IPv6)
        local_address: Local IP address for this session (IPv4 or IPv6)
        local_as: Local AS number
        peer_as: Peer AS number
        hold_time: Optional hold time in seconds
        routes: Per-neighbor routes; overrides the global routes list if set.
        address_family: Address family ('ipv4' or 'ipv6').
                       Auto-detected from peer address at construction time.
    """
    peer: str
    local_address: str
    local_as: int
    peer_as: int
    hold_time: Optional[int] = None
    routes: Optional[List['BGPRoute']] = field(default_factory=list)
    address_family: Optional[str] = None

    def __post_init__(self):
        """Auto-detect address family from peer address if not specified."""
        if self.address_family is None:
            # Auto-detect based on presence of colon (simple IPv6 detection)
            self.address_family = 'ipv6' if ':' in self.peer else 'ipv4'
