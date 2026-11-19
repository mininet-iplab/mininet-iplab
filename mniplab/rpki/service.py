"""RPKICAService: an RPKI CA Service, backed by Krill.

One RPKI CA per Lab, on any Node. An Instructor declares one Hosted CA per
Autonomous System, each holding only that AS's ASN and prefixes, and optionally
the starting ROAs and ASPAs. The Service is data only: its provisioner runs
Krill on the Node when the Lab starts.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Set

from mniplab.rpki.models import HostedCA
from mniplab.service import Service

DEFAULT_REFRESH = 10

MAX_HOSTNAME_LENGTH = 253
_HOSTNAME_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(rf"{_HOSTNAME_LABEL}(?:\.{_HOSTNAME_LABEL})*")


def _validate_hostname(name: str) -> str:
    """Check that a name is a DNS hostname a Node can answer to.

    Args:
        name: The candidate hostname.

    Returns:
        The name, unchanged.

    Raises:
        ValueError: If the name is not a DNS hostname.
    """
    if len(name) > MAX_HOSTNAME_LENGTH or _HOSTNAME.fullmatch(name) is None:
        raise ValueError(f"'{name}' is not a DNS hostname (letters, digits and hyphens)")
    return name


class RPKICAService(Service):
    """An RPKI CA: Krill holding one Hosted CA per Autonomous System.

    Attributes:
        name: The canonical identifier for this Service ('rpki-ca').
        hosted_cas: The declared Hosted CAs, in declaration order.
        hostname: The name Krill is reached by, when declared; otherwise the
            Node's own name.
        node: The Node this Service is attached to.
    """

    name: str = "rpki-ca"

    def __init__(
        self,
        hosted_cas: Sequence[HostedCA],
        hostname: Optional[str] = None,
    ) -> None:
        """Initialize the RPKI CA around its Hosted CAs.

        Args:
            hosted_cas: One Hosted CA per Autonomous System the Lab holds.
            hostname: The DNS name Krill answers to and its certificate names.
                Defaults to the Node's name.

        Raises:
            ValueError: If there is no Hosted CA, an ASN is declared twice, or
                the hostname is not a DNS name.
        """
        declared = list(hosted_cas)
        if not declared:
            raise ValueError("An RPKI CA needs at least one Hosted CA")
        seen: Set[int] = set()
        for hosted in declared:
            if hosted.asn in seen:
                raise ValueError(f"Hosted CA AS{hosted.asn} is declared more than once")
            seen.add(hosted.asn)
        self.hosted_cas: List[HostedCA] = declared
        self.hostname: Optional[str] = (
            _validate_hostname(hostname) if hostname is not None else None
        )

    @property
    def resolved_hostname(self) -> str:
        """The name Krill is reached by: the declared one, or the Node's name.

        Returns:
            The hostname written into the Lab's hosts file and Krill's
            configuration and certificate.

        Raises:
            ValueError: If the Service is not attached to a Node, or the Node's
                name is not a DNS hostname.
        """
        if self.hostname is not None:
            return self.hostname
        node_name = getattr(self.node, "name", None)
        if not node_name:
            raise ValueError("RPKI CA is not attached to a Node")
        return _validate_hostname(node_name)

    def hosts_lines(self) -> List[str]:
        """Return the hosts-file lines that name this CA's Node by its hostname.

        Krill listens on 0.0.0.0 only, so only the Node's IPv4 addresses are
        named; an IPv6 name would resolve to an address nothing answers on.

        Returns:
            One ``<address> <hostname>`` line per IPv4 address the Node's
            interfaces carry.
        """
        hostname = self.resolved_hostname
        lines: List[str] = []
        for intf in getattr(self.node, "intfList", lambda: [])():
            address = getattr(intf, "ip", None)
            if address:
                lines.append(f"{address} {hostname}")
        return lines

    @property
    def badge_text(self) -> str:
        """The badge label for this Service.

        Returns:
            "RPKI CA"
        """
        return "RPKI CA"

    @property
    def families(self) -> List[int]:
        """Address families served by this Service.

        Krill listens on 0.0.0.0, so its API is IPv4 only.

        Returns:
            [4]
        """
        return [4]


def rpki_ca_services(node: Any) -> List[RPKICAService]:
    """Return every RPKI CA Service attached to a Node.

    Args:
        node: The Node to inspect, or anything without Services.

    Returns:
        The Node's RPKI CA Services, in attachment order.
    """
    return [
        service for service in getattr(node, "services", [])
        if isinstance(service, RPKICAService)
    ]


class ValidatorService(Service):
    """A Validator: Routinator fetching the Lab's RPKI objects and serving them over RTR.

    Data only: the Validator takes its TAL and trust from the Lab's RPKI CA at
    start, so the Instructor declares nothing but how often it refreshes.

    Attributes:
        name: The canonical identifier for this Service ('validator').
        refresh: Seconds between validation runs.
        node: The Node this Service is attached to.
    """

    name: str = "validator"

    def __init__(self, refresh: int = DEFAULT_REFRESH) -> None:
        """Initialize the Validator around its refresh interval.

        Args:
            refresh: Seconds between validation runs. Defaults to 10, so a
                Learner sees a change within about a quarter of a minute.

        Raises:
            ValueError: If refresh is not a whole number of seconds above zero.
        """
        self.refresh: int = self._validate_refresh(refresh)

    @staticmethod
    def _validate_refresh(value: Any) -> int:
        """Check a refresh interval.

        Args:
            value: The declared interval in seconds.

        Returns:
            The interval, unchanged.

        Raises:
            ValueError: If the value is not an integer above zero.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("refresh must be a whole number of seconds")
        if value <= 0:
            raise ValueError("refresh must be greater than zero seconds")
        return value

    @property
    def badge_text(self) -> str:
        """The badge label for this Service.

        Returns:
            "Validator"
        """
        return "Validator"

    @property
    def families(self) -> List[int]:
        """Address families served by this Service.

        Routinator listens on 0.0.0.0, so its RTR and HTTP are IPv4 only.

        Returns:
            [4]
        """
        return [4]


def validator_services(node: Any) -> List[ValidatorService]:
    """Return every Validator Service attached to a Node.

    Args:
        node: The Node to inspect, or anything without Services.

    Returns:
        The Node's Validator Services, in attachment order.
    """
    return [
        service for service in getattr(node, "services", [])
        if isinstance(service, ValidatorService)
    ]
