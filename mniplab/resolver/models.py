"""Resolver domain models: Access Control rules, Root Hints, and cache state.

An :class:`AccessRule` is one subnet policy a Resolver enforces on incoming
queries; a :class:`RootHint` is one seed nameserver a Resolver bootstraps its
Iterative Walk from. Both are declared by an Instructor as Lab shape, and
Access Control rules are additionally authored by a Learner as Exercise
Configuration.

A :class:`CacheEntry` is one record the Resolver is holding in memory and a
:class:`ResolverMetrics` is a snapshot of its query counters; together they
are what a Learner watches while the cold-versus-warm contrast plays out.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, List, Optional, Tuple

ACCESS_ACTIONS = ("allow", "refuse", "deny")

ROOT_HINT_DOMAIN = "root-servers.iplab."
ROOT_HINT_TTL = 3600000


def interface_addresses(node: Any) -> List[str]:
    """Return every IPv4 and IPv6 address configured on a Node's interfaces.

    Args:
        node: The Node to read, or anything without interfaces.

    Returns:
        The addresses without prefix lengths, in interface order, IPv4 before
        IPv6 on each interface, without duplicates.
    """
    found: List[str] = []
    for intf in getattr(node, "intfList", lambda: [])():
        for address in (getattr(intf, "ip", None), getattr(intf, "ip6", None)):
            if address and address not in found:
                found.append(address)
    return found


def _is_usable_hint_target(address: str) -> bool:
    """Return whether an address is worth pointing a Root Hint at.

    Args:
        address: An IPv4 or IPv6 address without prefix length.

    Returns:
        False for loopback addresses, which no other Node can query, and for
        anything that does not parse as an address at all.
    """
    try:
        return not ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


class AccessRule:
    """One subnet policy a Resolver applies to incoming client queries.

    Attributes:
        subnet: The client subnet in CIDR notation, normalized to its network
            address (``10.0.2.5/24`` is stored as ``10.0.2.0/24``).
        action: ``'allow'``, ``'refuse'`` (answer REFUSED), or ``'deny'``
            (drop without answering).
    """

    def __init__(self, subnet: str, action: str = "allow") -> None:
        """Initialize one Access Control rule.

        Args:
            subnet: Client subnet in CIDR notation, IPv4 or IPv6.
            action: One of ``'allow'``, ``'refuse'``, or ``'deny'``.

        Raises:
            ValueError: If the subnet is not valid CIDR or the action is not
                one of the three Unbound policies.
        """
        try:
            network = ipaddress.ip_network(subnet.strip(), strict=False)
        except ValueError as exc:
            raise ValueError(
                f"Invalid Access Control subnet '{subnet}': {exc}"
            ) from exc

        normalized_action = action.strip().lower()
        if normalized_action not in ACCESS_ACTIONS:
            raise ValueError(
                f"Invalid Access Control action '{action}': expected one of "
                f"{', '.join(ACCESS_ACTIONS)}."
            )

        self.subnet = str(network)
        self.action = normalized_action

    @property
    def family(self) -> int:
        """The address family this rule covers, 4 or 6."""
        return ipaddress.ip_network(self.subnet).version

    def to_dict(self) -> Dict[str, Any]:
        """Return the rule as a plain dictionary for the CLI and Web API.

        Returns:
            A dict with ``subnet``, ``action``, and ``family`` keys.
        """
        return {"subnet": self.subnet, "action": self.action, "family": self.family}

    def __eq__(self, other: object) -> bool:
        """Two rules are equal when they name the same subnet and action."""
        if not isinstance(other, AccessRule):
            return NotImplemented
        return self.subnet == other.subnet and self.action == other.action

    def __hash__(self) -> int:
        """Hash on the same fields equality compares."""
        return hash((self.subnet, self.action))

    def __repr__(self) -> str:
        """Return an unambiguous representation naming subnet and action."""
        return f"AccessRule({self.subnet!r}, {self.action!r})"


class RootHint:
    """One seed nameserver a Resolver bootstraps its Iterative Walk from.

    An Instructor declares Root Hints either as a Node (the common case — the
    in-lab root nameserver) or as a bare IP string.  A Node is resolved to its
    addresses at start time, once its interfaces are up.

    Attributes:
        label: The hint's DNS label, a Node's name or a generated ``hintN``.
        node: The declared Node, or None when declared as an address.
        addresses: Explicitly declared addresses; empty for a Node hint until
            :meth:`resolve_addresses` reads them off the Node.
    """

    def __init__(
        self,
        label: str,
        node: Optional[Any] = None,
        addresses: Optional[List[str]] = None,
    ) -> None:
        """Initialize one Root Hint.

        Args:
            label: DNS label identifying this hint within the hints file.
            node: The declared Node, when the hint names one.
            addresses: Declared IP addresses, when the hint names addresses.
        """
        self.label = label
        self.node = node
        self.addresses: List[str] = list(addresses or [])

    @property
    def fqdn(self) -> str:
        """The hint's fully-qualified name inside the synthesized hints file."""
        return f"{self.label}.{ROOT_HINT_DOMAIN}"

    def resolve_addresses(self) -> List[str]:
        """Return the addresses this hint points at.

        For a Node hint, reads every IPv4 and IPv6 address configured across
        the Node's interfaces; interfaces are up by the time the Resolver
        starts, so this is where a Node becomes addresses.

        Returns:
            The hint's IP addresses, IPv4 first, without duplicates.
        """
        if self.node is None:
            return list(self.addresses)

        return [
            address
            for address in interface_addresses(self.node)
            if _is_usable_hint_target(address)
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Return the hint as a plain dictionary for the CLI and Web API.

        Returns:
            A dict with ``label``, ``fqdn``, and resolved ``addresses``.
        """
        return {
            "label": self.label,
            "fqdn": self.fqdn,
            "addresses": self.resolve_addresses(),
        }

    def __repr__(self) -> str:
        """Return an unambiguous representation naming the hint's label."""
        return f"RootHint({self.label!r})"


CACHE_STATUS_POSITIVE = "positive"
CACHE_STATUS_NXDOMAIN = "NXDOMAIN"
CACHE_STATUS_NODATA = "NODATA"

NEGATIVE_STATUSES = (CACHE_STATUS_NXDOMAIN, CACHE_STATUS_NODATA)


class CacheEntry:
    """One record a Resolver is holding in memory, positive or negative.

    A positive entry is an RRset Unbound learned during an Iterative Walk and
    serves from cache until its TTL reaches zero. A negative entry is an
    ``NXDOMAIN`` or ``NODATA`` answer cached against the authoritative SOA's
    minimum TTL per RFC 2308, which is why it carries the SOA that bounds it
    rather than an answer of its own.

    Attributes:
        name: The QNAME this entry answers for, fully qualified.
        rtype: The RR type, or the queried type for a negative entry.
        ttl: Seconds remaining before the entry is evicted.
        data: The rdata, or the caching SOA's rdata for a negative entry.
        status: ``'positive'``, ``'NXDOMAIN'``, or ``'NODATA'``.
        soa: The owner name of the SOA bounding a negative entry, empty for
            a positive one.
    """

    def __init__(
        self,
        name: str,
        rtype: str,
        ttl: int,
        data: str,
        status: str = CACHE_STATUS_POSITIVE,
        soa: str = "",
        security: str = "insecure",
        authenticated_data: bool = False,
    ) -> None:
        """Initialize one cached entry.

        Args:
            name: The QNAME this entry answers for.
            rtype: The RR type, or the queried type for a negative entry.
            ttl: Seconds remaining before eviction.
            data: The rdata, or the caching SOA's rdata.
            status: ``'positive'``, ``'NXDOMAIN'``, or ``'NODATA'``.
            soa: Owner name of the SOA bounding a negative entry.
            security: Validation status: ``'secure'``, ``'insecure'``, or ``'bogus'``.
            authenticated_data: Whether DNSSEC validation confirmed this entry (AD flag).
        """
        self.name = name
        self.rtype = rtype
        self.ttl = int(ttl)
        self.data = data
        self.status = status
        self.soa = soa
        self.security = security
        self.authenticated_data = authenticated_data

    @property
    def negative(self) -> bool:
        """Whether this entry caches the absence of an answer."""
        return self.status in NEGATIVE_STATUSES

    @property
    def sort_key(self) -> Tuple[str, str, str]:
        """A stable ordering key, so a redisplayed cache does not reshuffle.

        Unbound dumps its cache in hash-table order, which changes between
        dumps; a Learner watching TTLs tick down needs the rows to stay put.
        """
        return (self.name, self.rtype, self.data)

    def to_dict(self) -> Dict[str, Any]:
        """Return the entry as a plain dictionary for the CLI and Web API.

        Returns:
            A dict with ``name``, ``type``, ``ttl``, ``data``, ``status``,
            ``negative``, ``soa``, ``security``, and ``authenticated_data`` keys.
        """
        return {
            "name": self.name,
            "type": self.rtype,
            "ttl": self.ttl,
            "data": self.data,
            "status": self.status,
            "negative": self.negative,
            "soa": self.soa,
            "security": self.security,
            "authenticated_data": self.authenticated_data,
        }

    def __eq__(self, other: object) -> bool:
        """Two entries are equal when every displayed field matches."""
        if not isinstance(other, CacheEntry):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __repr__(self) -> str:
        """Return an unambiguous representation naming the entry and status."""
        return (
            f"CacheEntry({self.name!r}, {self.rtype!r}, {self.status!r}, "
            f"security={self.security!r}, ad={self.authenticated_data!r})"
        )


class ResolverMetrics:
    """Aggregate query counters for one Resolver.

    The cold-versus-warm contrast the Lab teaches is legible here: a rising
    hit ratio against a flat recursion time means the cache is doing the work.

    Attributes:
        queries: Total queries the Resolver has answered.
        hits: Queries answered from cache.
        misses: Queries that required recursion.
        recursion_time_ms: Mean time spent on a recursive resolution, in
            milliseconds.
        dnssec_secure: Total queries validated as DNSSEC secure.
        dnssec_bogus: Total queries evaluated as DNSSEC bogus.
        dnssec_insecure: Total queries evaluated as DNSSEC insecure.
    """

    def __init__(
        self,
        queries: int = 0,
        hits: int = 0,
        misses: int = 0,
        recursion_time_ms: float = 0.0,
        dnssec_secure: int = 0,
        dnssec_bogus: int = 0,
        dnssec_insecure: int = 0,
    ) -> None:
        """Initialize one metrics snapshot.

        Args:
            queries: Total queries answered.
            hits: Queries answered from cache.
            misses: Queries that required recursion.
            recursion_time_ms: Mean recursion time in milliseconds.
            dnssec_secure: DNSSEC secure query count.
            dnssec_bogus: DNSSEC bogus query count.
            dnssec_insecure: DNSSEC insecure query count.
        """
        self.queries = int(queries)
        self.hits = int(hits)
        self.misses = int(misses)
        self.recursion_time_ms = round(float(recursion_time_ms), 3)
        self.dnssec_secure = int(dnssec_secure)
        self.dnssec_bogus = int(dnssec_bogus)
        self.dnssec_insecure = int(dnssec_insecure)

    @property
    def hit_ratio(self) -> float:
        """Cache hits as a percentage of all queries, 0.0 before any query."""
        if self.queries <= 0:
            return 0.0
        return round(self.hits * 100.0 / self.queries, 1)

    def to_dict(self) -> Dict[str, Any]:
        """Return the snapshot as a plain dictionary for the CLI and Web API.

        Returns:
            A dict with ``queries``, ``hits``, ``misses``, ``hit_ratio``,
            ``recursion_time_ms``, ``dnssec_secure``, ``dnssec_bogus``,
            and ``dnssec_insecure`` keys.
        """
        return {
            "queries": self.queries,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "recursion_time_ms": self.recursion_time_ms,
            "dnssec_secure": self.dnssec_secure,
            "dnssec_bogus": self.dnssec_bogus,
            "dnssec_insecure": self.dnssec_insecure,
        }

    def __repr__(self) -> str:
        """Return an unambiguous representation naming the headline counters."""
        return (
            f"ResolverMetrics(queries={self.queries}, hits={self.hits}, "
            f"misses={self.misses}, secure={self.dnssec_secure})"
        )
