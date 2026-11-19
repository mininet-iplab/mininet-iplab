"""ResolverService: a Recursive Caching Resolver Service, backed by Unbound.

One Service per Node. An Instructor declares either ``root_hints`` (the
Resolver performs an Iterative Walk across the in-lab hierarchy) or
``forwarders`` (the Resolver forwards to upstream addresses); declaring both
is legal and leaves forwarding in charge, with the hints available for a
Learner to switch to.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Iterable, List, Optional, Sequence, Union

from mniplab.resolver.models import AccessRule, RootHint
from mniplab.service import Service

DEFAULT_MAX_TTL = 300
DEFAULT_MAX_NEGATIVE_TTL = 60

DEFAULT_ACCESS_RULES = (("0.0.0.0/0", "allow"), ("::/0", "allow"))

class ResolverService(Service):
    """A Recursive Caching Resolver: Unbound answering and caching client queries.

    Attributes:
        name: The canonical identifier for this Service ('resolver').
        root_hints: The declared Root Hints, seeding the Iterative Walk.
        forwarders: Upstream addresses queries are forwarded to, if any.
        max_ttl: Cap in seconds on how long a positive answer is cached.
        max_negative_ttl: Cap in seconds on how long an NXDOMAIN or NODATA
            answer is cached.
        access_control: The active Access Control rules, in match order.
        node: The Node this Service is attached to.
    """

    name: str = "resolver"

    def __init__(
        self,
        root_hints: Optional[Sequence[Any]] = None,
        forwarders: Optional[Sequence[str]] = None,
        max_ttl: int = DEFAULT_MAX_TTL,
        max_negative_ttl: int = DEFAULT_MAX_NEGATIVE_TTL,
        access_control: Optional[Iterable[AccessRule]] = None,
        trust_anchors: Optional[List[Union[str, Any]]] = None,
    ) -> None:
        """Initialize a Resolver Service around its hierarchy configuration.

        Args:
            root_hints: Root nameservers to seed the Iterative Walk, each
                either a Node or an IP address string.
            forwarders: Upstream resolver addresses to forward queries to.
            max_ttl: Cap in seconds on cached positive answers, so a
                classroom exercise never waits out a production TTL.
            max_negative_ttl: Cap in seconds on cached negative answers.
            access_control: Initial Access Control rules; defaults to
                accepting all in-lab traffic.
            trust_anchors: Cryptographic trust anchors for DNSSEC validation,
                each either a Node, Zone, DSRecord, or raw RR string.

        Raises:
            ValueError: If a forwarder is not a valid IP address, or a TTL cap
                is not a positive number of seconds.
        """
        self.root_hints: List[RootHint] = self._build_hints(root_hints)
        self.forwarders: List[str] = self._validate_forwarders(forwarders)
        self.max_ttl = self._validate_ttl(max_ttl, "max_ttl")
        self.max_negative_ttl = self._validate_ttl(
            max_negative_ttl, "max_negative_ttl"
        )
        self.trust_anchors: List[Any] = (
            list(trust_anchors) if trust_anchors is not None else []
        )

        declared = (
            list(access_control)
            if access_control is not None
            else [AccessRule(subnet, action) for subnet, action in DEFAULT_ACCESS_RULES]
        )
        self._declared_access_control: List[AccessRule] = list(declared)
        self.access_control: List[AccessRule] = list(declared)

    @staticmethod
    def _build_hints(
        declarations: Optional[Sequence[Any]],
    ) -> List[RootHint]:
        """Turn declared Root Hints into RootHint models.

        Args:
            declarations: Nodes or IP address strings, in declaration order.

        Returns:
            One RootHint per declaration, labelled by Node name or position.

        Raises:
            ValueError: If a string declaration is not a valid IP address.
        """
        hints: List[RootHint] = []
        for index, declared in enumerate(declarations or [], start=1):
            if isinstance(declared, str):
                address = declared.strip()
                try:
                    ipaddress.ip_address(address)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid Root Hint address '{declared}': {exc}"
                    ) from exc
                hints.append(RootHint(f"hint{index}", addresses=[address]))
            else:
                label = getattr(declared, "name", None)
                if not label:
                    raise ValueError(
                        f"Invalid Root Hint {declared!r}: expected a Node or an "
                        f"IP address string."
                    )
                hints.append(RootHint(label, node=declared))
        return hints

    @staticmethod
    def _validate_forwarders(forwarders: Optional[Sequence[str]]) -> List[str]:
        """Validate declared forwarder addresses.

        Args:
            forwarders: Upstream resolver IP address strings.

        Returns:
            The normalized addresses, in declaration order.

        Raises:
            ValueError: If any forwarder is not a valid IPv4 or IPv6 address.
        """
        validated: List[str] = []
        for forwarder in forwarders or []:
            address = str(forwarder).strip()
            try:
                validated.append(str(ipaddress.ip_address(address)))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid forwarder address '{forwarder}': {exc}"
                ) from exc
        return validated

    @staticmethod
    def _validate_ttl(value: int, field: str) -> int:
        """Validate a TTL cap.

        Args:
            value: The declared cap in seconds.
            field: The parameter name, for the error message.

        Returns:
            The cap as an integer.

        Raises:
            ValueError: If the cap is not a positive integer.
        """
        try:
            seconds = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a whole number of seconds") from exc
        if seconds <= 0:
            raise ValueError(f"{field} must be greater than zero seconds")
        return seconds

    @property
    def mode(self) -> str:
        """How this Resolver reaches the hierarchy.

        Returns:
            ``'forwarding'`` when forwarders are declared, ``'iterative'``
            when Root Hints are, and ``'default'`` when neither is — in which
            case Unbound falls back to its built-in public root hints.
        """
        if self.forwarders:
            return "forwarding"
        if self.root_hints:
            return "iterative"
        return "default"

    @property
    def badge_text(self) -> str:
        """The badge label for this Service.

        Returns:
            "RESOLVER"
        """
        return "RESOLVER"

    @property
    def families(self) -> List[int]:
        """Address families served by this Service.

        Returns:
            [4, 6] (the Resolver listens for queries over IPv4 and IPv6).
        """
        return [4, 6]

    def set_access_control(self, rules: Iterable[AccessRule]) -> None:
        """Replace the active Access Control rules.

        Args:
            rules: The rules to enforce, in match order.
        """
        self.access_control = list(rules)

    def reset_access_control(self) -> None:
        """Restore the Access Control rules the Lab Example declared.

        A Learner's rules are Exercise Configuration and are Ephemeral: they
        vanish when the Lab stops, so the next class starts from the Lab
        Example's own definition.
        """
        self.access_control = list(self._declared_access_control)

    @classmethod
    def find_on(cls, node: Any) -> ResolverService:
        """Return the Resolver Service attached to node.

        Args:
            node: The Node to search.

        Returns:
            The Node's attached Resolver Service.

        Raises:
            ValueError: If the Node has no Resolver Service attached.
        """
        for service in getattr(node, "services", []):
            if isinstance(service, cls):
                return service
        node_name = getattr(node, "name", "Node")
        raise ValueError(f"{node_name} has no Resolver Service attached")


def resolver_services(node: Any) -> List[ResolverService]:
    """Return every Resolver Service attached to a Node.

    Args:
        node: The Node to inspect, or anything without Services.

    Returns:
        The Node's Resolver Services, in attachment order.
    """
    return [
        service for service in getattr(node, "services", [])
        if isinstance(service, ResolverService)
    ]
