"""RPKI domain models: Route Origin Authorisations, ASPAs and Hosted CAs.

A :class:`ROA` is one signed statement that a prefix, up to a maximum length,
may be originated by an Autonomous System. An :class:`ASPA` lists the provider
Autonomous Systems of one customer. A :class:`HostedCA` is one Autonomous
System's certificate authority, holding only its own ASN and prefixes, and the
starting ROAs and ASPAs an Instructor declares for it.

Each model parses the text Krill and ``krillc`` use, so a declaration reads
the way a Learner would type it: ``10.1.0.0/24-24 => 65002`` for a ROA and
``AS65002 => AS65001, AS65005`` for an ASPA.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Sequence, Union

MAX_ASN = 4294967295

_Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

_ASN_TEXT = re.compile(r"(?:[Aa][Ss])?(\d+)")


def as_asn(value: Any) -> int:
    """Return an Autonomous System number from an integer or an ``AS`` string.

    Args:
        value: An ASN as an int, or text such as ``AS65002`` or ``65002``.

    Returns:
        The ASN as an int.

    Raises:
        ValueError: If the value is not an ASN, or is outside 1 to 4294967295.
    """
    if isinstance(value, str):
        match = _ASN_TEXT.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"'{value}' is not an AS number such as AS65002")
        value = int(match.group(1))
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"AS number must be a whole number, not {value!r}")
    if not 1 <= value <= MAX_ASN:
        raise ValueError(f"AS number {value} is outside 1 to {MAX_ASN}")
    return value


def _strict_network(text: Any) -> Union[ipaddress.IPv4Network, ipaddress.IPv6Network]:
    """Return a prefix, refusing one with host bits set.

    Args:
        text: A prefix in CIDR notation, IPv4 or IPv6.

    Returns:
        The parsed network.

    Raises:
        ValueError: If the text is not a prefix in CIDR notation.
    """
    try:
        return ipaddress.ip_network(str(text).strip(), strict=True)
    except ValueError as exc:
        raise ValueError(f"'{text}' is not a prefix in CIDR notation: {exc}") from exc


def _within(candidate: _Network, held: _Network) -> bool:
    """Report whether a prefix lies inside a held prefix of the same family.

    Args:
        candidate: The prefix to test.
        held: A prefix the Hosted CA holds.

    Returns:
        True if candidate is the same as, or a sub-prefix of, held.
    """
    if isinstance(candidate, ipaddress.IPv4Network) and isinstance(held, ipaddress.IPv4Network):
        return candidate.subnet_of(held)
    if isinstance(candidate, ipaddress.IPv6Network) and isinstance(held, ipaddress.IPv6Network):
        return candidate.subnet_of(held)
    return False


@dataclass
class ROA:
    """A Route Origin Authorisation's content: a prefix, a maximum length and an origin.

    Attributes:
        prefix: The authorised prefix, normalized to CIDR notation.
        max_length: The longest prefix the origin may announce, between the
            prefix's own length and the address family's full length.
        origin: The Autonomous System authorised to originate the prefix.
    """

    prefix: str
    max_length: int
    origin: int

    def __post_init__(self) -> None:
        """Validate the prefix, maximum length and origin.

        Raises:
            ValueError: If any field is invalid.
        """
        network = _strict_network(self.prefix)
        if isinstance(self.max_length, bool) or not isinstance(self.max_length, int):
            raise ValueError(f"maxLength must be a whole number, not {self.max_length!r}")
        if not network.prefixlen <= self.max_length <= network.max_prefixlen:
            raise ValueError(
                f"maxLength {self.max_length} for {network} must be between "
                f"{network.prefixlen} and {network.max_prefixlen}"
            )
        self.prefix = str(network)
        self.origin = as_asn(self.origin)

    @classmethod
    def parse(cls, text: str) -> ROA:
        """Parse ``<prefix>[-<maxLength>] => <origin>``.

        Args:
            text: The ROA as Krill and ``krillc`` write it. The maximum length
                defaults to the prefix length; the origin may carry an ``AS``.

        Returns:
            The parsed ROA.

        Raises:
            ValueError: If the text does not parse or the ROA is invalid.
        """
        body, arrow, origin = text.partition("=>")
        if not arrow:
            raise ValueError(f"ROA '{text}' must read '<prefix>[-<maxLength>] => <origin>'")
        prefix_text, _dash, max_text = body.strip().partition("-")
        network = _strict_network(prefix_text)
        if max_text.strip():
            try:
                max_length = int(max_text)
            except ValueError as exc:
                raise ValueError(f"maxLength '{max_text.strip()}' is not a whole number") from exc
        else:
            max_length = network.prefixlen
        return cls(prefix=str(network), max_length=max_length, origin=as_asn(origin))

    def __str__(self) -> str:
        """Return the ROA as Krill writes it, always with its maximum length.

        Returns:
            For example ``10.1.0.0/24-24 => 65002``.
        """
        return f"{self.prefix}-{self.max_length} => {self.origin}"


@dataclass
class ASPA:
    """An Autonomous System Provider Authorisation: a customer and its providers.

    Attributes:
        customer: The customer Autonomous System, which signs the ASPA.
        providers: The Autonomous Systems that may carry its routes upstream.
    """

    customer: int
    providers: List[int]

    def __post_init__(self) -> None:
        """Validate the customer and providers.

        Raises:
            ValueError: If the customer is not an ASN, or no provider is given.
        """
        self.customer = as_asn(self.customer)
        providers = list(self.providers)
        if not providers:
            raise ValueError(f"ASPA for AS{self.customer} needs at least one provider")
        self.providers = [as_asn(provider) for provider in providers]

    @classmethod
    def parse(cls, text: str) -> ASPA:
        """Parse ``<customer> => <provider>[, <provider>...]``.

        Args:
            text: The ASPA as a Learner would type it. ``AS`` prefixes are
                optional.

        Returns:
            The parsed ASPA.

        Raises:
            ValueError: If the text does not parse or the ASPA is invalid.
        """
        customer, arrow, providers = text.partition("=>")
        if not arrow:
            raise ValueError(f"ASPA '{text}' must read '<customer> => <provider>[, <provider>...]'")
        items = [item.strip() for item in providers.split(",")] if providers.strip() else []
        return cls(customer=as_asn(customer), providers=[as_asn(item) for item in items])

    def __str__(self) -> str:
        """Return the ASPA with its ``AS`` prefixes.

        Returns:
            For example ``AS65002 => AS65001, AS65005``.
        """
        providers = ", ".join(f"AS{provider}" for provider in self.providers)
        return f"AS{self.customer} => {providers}"


@dataclass
class VRP:
    """A Validated ROA Payload: one prefix, a maximum length and its origin, as a Validator serves it.

    Attributes:
        prefix: The validated prefix, in CIDR notation.
        max_length: The longest prefix the origin may announce.
        origin: The Autonomous System that validated for the prefix.
    """

    prefix: str
    max_length: int
    origin: int


class HostedCA:
    """One Autonomous System's Hosted CA, with the starting objects declared for it.

    A Hosted CA can sign only for the prefixes it holds, so a starting ROA must
    fall inside one of them, and a starting ASPA must name the Hosted CA's own
    ASN as its customer.

    Attributes:
        asn: The Autonomous System this Hosted CA belongs to.
        prefixes: The prefixes it holds, normalized to CIDR notation.
        roas: The starting ROAs, as :class:`ROA` models.
        aspas: The starting ASPAs, as :class:`ASPA` models.
    """

    def __init__(
        self,
        asn: int,
        prefixes: Sequence[str],
        roas: Iterable[Union[ROA, str]] = (),
        aspas: Iterable[Union[ASPA, str]] = (),
    ) -> None:
        """Declare a Hosted CA and check its starting objects against its resources.

        Args:
            asn: The Autonomous System, as an int or ``AS`` text.
            prefixes: The prefixes it holds; at least one.
            roas: Starting ROAs, as models or their text.
            aspas: Starting ASPAs, as models or their text.

        Raises:
            ValueError: If the ASN or a prefix is invalid, no prefix is given,
                or a starting object falls outside the Hosted CA's resources.
        """
        self.asn: int = as_asn(asn)
        if isinstance(prefixes, str):
            raise ValueError(f"Hosted CA AS{self.asn}: prefixes must be a list of prefixes")
        networks = [_strict_network(prefix) for prefix in prefixes]
        if not networks:
            raise ValueError(f"Hosted CA AS{self.asn} needs at least one prefix")
        self.prefixes: List[str] = [str(network) for network in networks]
        self.roas: List[ROA] = [self._own_roa(roa, networks) for roa in roas]
        self.aspas: List[ASPA] = [self._own_aspa(aspa) for aspa in aspas]

    @property
    def handle(self) -> str:
        """The name Krill and ``krillc --ca`` know this Hosted CA by.

        Returns:
            ``as<asn>``, for example ``as65002``.
        """
        return f"as{self.asn}"

    def _own_roa(self, roa: Union[ROA, str], networks: Sequence[_Network]) -> ROA:
        """Parse a starting ROA and check it falls inside the held prefixes.

        Args:
            roa: The ROA model, or its text.
            networks: The held prefixes, parsed.

        Returns:
            The ROA as a model.

        Raises:
            ValueError: If the ROA's prefix is not inside a held prefix.
        """
        model = roa if isinstance(roa, ROA) else ROA.parse(roa)
        candidate = ipaddress.ip_network(model.prefix)
        if not any(_within(candidate, held) for held in networks):
            held_text = ", ".join(self.prefixes)
            raise ValueError(
                f"Hosted CA AS{self.asn}: starting ROA {model.prefix} is outside "
                f"its resources ({held_text})"
            )
        return model

    def _own_aspa(self, aspa: Union[ASPA, str]) -> ASPA:
        """Parse a starting ASPA and check its customer is this Hosted CA.

        Args:
            aspa: The ASPA model, or its text.

        Returns:
            The ASPA as a model.

        Raises:
            ValueError: If the ASPA's customer is another Autonomous System.
        """
        model = aspa if isinstance(aspa, ASPA) else ASPA.parse(aspa)
        if model.customer != self.asn:
            raise ValueError(
                f"Hosted CA AS{self.asn}: starting ASPA customer AS{model.customer} "
                f"is not its own ASN"
            )
        return model
