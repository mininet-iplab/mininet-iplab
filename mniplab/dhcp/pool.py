"""The Pool: the addresses a DHCP Service may hand out on a Subnet."""

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

_MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')

# Kea reports a Lease's DUID as colon-separated hex octets
# (e.g. 00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9) and takes the same spelling
# in a host reservation. Real DUIDs are never shorter than seven octets
# (RFC 8415 §11); four is a floor well under that, rejecting obvious typos
# without second-guessing Kea's own parser.
_DUID_RE = re.compile(r'^([0-9A-Fa-f]{2}:){3,}[0-9A-Fa-f]{2}$')

# The exactly-one-identifier rule has one wording everywhere a
# breach of it is rejected, so a Learner authoring from the Web UI and an
# Instructor authoring a Lab Example meet the same explanation.
_IDENT_MESSAGE = (
    "A Reservation must carry exactly one client identifier: a hardware "
    "address or a DUID, not both and not neither."
)


def infer_family(cidr: str) -> int:
    """Infer an address family (4 or 6) from a Subnet's CIDR.

    A CIDR is self-describing — ``2001:db8:1::/64`` names IPv6 without any
    family argument — so one Pool type serves both families and everything
    downstream (validation, configuration, process choice) derives the family
    from the CIDR.

    Args:
        cidr: The Subnet CIDR to classify.

    Returns:
        ``4`` or ``6``, matching :attr:`ipaddress.IPv4Network.version`.

    Raises:
        ValueError: ``cidr`` is not a valid network CIDR.
    """
    return ipaddress.ip_network(cidr, strict=False).version


@dataclass(frozen=True)
class Reservation:
    """A fixed address reserved for one named client, in place of the Pool.

    A Reservation carries exactly one client identifier: a hardware (MAC)
    address or a DUID, never both and never neither. Which one is the
    baseline differs by family and follows from the protocol: DHCPv4's
    ``chaddr`` makes the hardware address the only identifier an IPv4
    Reservation can name, while DHCPv6 replaced ``chaddr`` with the Client
    Identifier option carrying a DUID — the only identity the protocol
    defines — so an IPv6 Reservation is named by DUID in the general case.
    A hardware-address IPv6 Reservation is also rendered and accepted,
    because it is the identity a Relay supplies (RFC 6939) and, for a
    directly attached client, only contingently derivable from the DUID
    (this container's ``dhcpcd`` presents a DUID-LLT that embeds its MAC, so
    Kea can match either); it is the convenience, not the mechanism.

    ``hostname`` is a label only, carried through to the Web UI and CLI so
    a Learner can tell which client a Reservation is for without decoding
    a DUID or MAC address; Kea does not use it to match clients.

    Attributes:
        ip: The address handed out to that client instead of one from the Pool.
        mac: The client's hardware address, e.g. ``'aa:bb:cc:dd:ee:ff'``.
            Empty when the Reservation is identified by DUID.
        duid: The client's DHCP Unique Identifier, colon-separated hex
            octets as Kea's Lease table reports it. Empty when the
            Reservation is identified by hardware address.
        hostname: Optional display label for the reserved client.
    """

    ip: str
    mac: str = ""
    duid: str = ""
    hostname: str = ""

    def __post_init__(self) -> None:
        """Reject a Reservation carrying both identifiers or neither."""
        if bool(self.mac) == bool(self.duid):
            raise ValueError(_IDENT_MESSAGE)


@dataclass(frozen=True)
class Pool:
    """One Pool: the addresses served on one Subnet, identified by CIDR.

    A Pool names its Subnet by CIDR rather than by Interface or link order,
    so a Lab Example's declaration survives reordering its ``addLink`` calls
    unchanged. The CIDR also carries the
    Pool's address family — IPv4 or IPv6 — with no separate family argument:
    see :func:`infer_family`. The CIDR is resolved to the Service's Node's
    attached Interface at Lab start — see
    :meth:`mniplab.provisioners.DHCPProvisioner.resolve_interface`.

    Several Pools per Service are supported (one per Subnet the Service
    serves). CIDR validation — a CIDR matching no attached Interface, or one
    that belongs to a Subnet reachable only through another Router — is out
    of scope here; it happens in
    :meth:`mniplab.provisioners.DHCPProvisioner.resolve_interface` at Lab
    start, once every Interface has the address a Lab Example gave it.

    Attributes:
        cidr: The Subnet this Pool serves, e.g. ``'10.50.0.0/24'``.
        start: First address Kea may hand out.
        end: Last address Kea may hand out.
        lease_time: How long, in seconds, a Lease is valid before it must be
            renewed. ``None`` leaves Kea's own default in place.
        preferred_lifetime: How long, in seconds, a leased IPv6 address is
            preferred before becoming deprecated (IPv6 only). Must be positive
            and not greater than ``lease_time``. ``None`` leaves Kea's default.
        router: The router option handed to clients alongside their address,
            e.g. ``'10.50.0.1'`` (IPv4 only; DHCPv6 has no router option).
            Empty means the option is omitted.
        dns_server: The DNS-server option handed to clients. Empty means the
            option is omitted.
        reservations: Reservations pinning named clients to fixed addresses,
            each the exception to this Pool rather than a Lease drawn from it.
    """

    cidr: str
    start: str
    end: str
    lease_time: Optional[int] = None
    preferred_lifetime: Optional[int] = None
    router: str = ""
    dns_server: str = ""
    reservations: Tuple[Reservation, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate Pool fields against its inferred address family."""
        if self.family == 4 and self.preferred_lifetime is not None:
            raise ValueError("Preferred lifetime is only supported for IPv6 Pools")
        if self.preferred_lifetime is not None:
            validate_preferred_lifetime(self.preferred_lifetime, self.lease_time)
        if self.lease_time is not None:
            validate_lease_time(self.lease_time)
        if self.dns_server:
            validate_option_address("DNS server", self.dns_server, family=self.family)
        if self.router:
            validate_option_address("router", self.router, family=self.family)
        if self.start and self.end:
            validate_pool_range(self.cidr, self.start, self.end)
        elif self.start or self.end:
            raise ValueError("Both start and end must be specified, or both omitted")
        for res in self.reservations:
            validate_reservation(self.cidr, res.mac, res.ip, res.duid)

    @property
    def family(self) -> int:
        """The address family this Pool serves: 4 or 6, inferred from the CIDR.

        Raises:
            ValueError: The CIDR is not a valid network CIDR.
        """
        return infer_family(self.cidr)


def validate_pool_range(cidr: str, start: str, end: str) -> None:
    """Validate that start and end addresses form a valid Pool on cidr.

    The Pool's family is inferred from ``cidr`` (see :func:`infer_family`);
    both addresses must belong to that family and sit inside the Subnet.

    Args:
        cidr: The Subnet the Pool belongs to, e.g. ``'10.50.0.0/24'`` or
            ``'2001:db8:1::/64'``.
        start: First address Kea may hand out.
        end: Last address Kea may hand out.

    Raises:
        ValueError: If start or end is not a valid address, does not belong
            to the Subnet's family, is not contained in cidr, start is after
            end, or start/end is the Subnet's network address (or, for IPv4,
            its broadcast address).
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid Subnet CIDR '{cidr}': {exc}") from exc

    family = network.version

    try:
        start_ip = ipaddress.ip_address(start)
    except ValueError as exc:
        raise ValueError(f"Invalid start address '{start}': {exc}") from exc

    try:
        end_ip = ipaddress.ip_address(end)
    except ValueError as exc:
        raise ValueError(f"Invalid end address '{end}': {exc}") from exc

    if start_ip.version != family:
        raise ValueError(f"Start address {start} is not an IPv{family} address")

    if end_ip.version != family:
        raise ValueError(f"End address {end} is not an IPv{family} address")

    if start_ip not in network:
        raise ValueError(f"Start address {start} is not in subnet {cidr}")

    if end_ip not in network:
        raise ValueError(f"End address {end} is not in subnet {cidr}")

    if int(start_ip) > int(end_ip):
        raise ValueError(f"Start address {start} cannot be after end address {end}")

    # IPv4 reserves the all-zeroes host identifier as the network address
    # and all-ones as the directed broadcast address (RFC 791 / RFC 919).
    # IPv6 has no broadcast addresses (RFC 4291 §2.6: superseded by multicast).
    # The all-zeroes address in an IPv6 prefix (e.g. prefix::0) is the
    # Subnet-Router anycast address (RFC 4291 §2.6.1), reserved for routers
    # on the subnet and not assignable to hosts. Because IPv6 has no broadcast
    # address, the highest address in a prefix carries no equivalent broadcast
    # restriction and remains syntactically valid unicast/anycast.
    if family == 4 and network.prefixlen <= 30:
        if start_ip == network.network_address:
            raise ValueError(f"Start address {start} cannot be the subnet network address")
        if end_ip == network.broadcast_address:
            raise ValueError(f"End address {end} cannot be the subnet broadcast address")
    elif family == 6:
        if start_ip == network.network_address:
            raise ValueError(
                f"Start address {start} cannot be the Subnet-Router anycast address"
            )


def validate_lease_time(lease_time: int) -> None:
    """Validate a Pool's lease time.

    Args:
        lease_time: How long, in seconds, a Lease is valid before renewal.

    Raises:
        ValueError: ``lease_time`` is not a positive whole number of seconds.
    """
    if isinstance(lease_time, bool) or not isinstance(lease_time, int):
        raise ValueError(f"Lease time must be a whole number of seconds, got {lease_time!r}")
    if lease_time <= 0:
        raise ValueError(f"Lease time must be a positive number of seconds, got {lease_time}")


def validate_preferred_lifetime(
    preferred_lifetime: int, valid_lifetime: Optional[int] = None
) -> None:
    """Validate an IPv6 Pool's preferred lifetime.

    Preferred lifetime must be a positive whole number of seconds. If
    ``valid_lifetime`` is also specified, ``preferred_lifetime`` cannot be
    greater than ``valid_lifetime``.

    Args:
        preferred_lifetime: How long, in seconds, a leased IPv6 address is
            preferred before becoming deprecated.
        valid_lifetime: The Pool's lease time (valid lifetime) in seconds,
            if specified.

    Raises:
        ValueError: ``preferred_lifetime`` is not a positive whole number of
            seconds, or is greater than ``valid_lifetime``.
    """
    if isinstance(preferred_lifetime, bool) or not isinstance(preferred_lifetime, int):
        raise ValueError(
            f"Preferred lifetime must be a whole number of seconds, got {preferred_lifetime!r}"
        )
    if preferred_lifetime <= 0:
        raise ValueError(
            f"Preferred lifetime must be a positive number of seconds, got {preferred_lifetime}"
        )
    if valid_lifetime is not None and preferred_lifetime > valid_lifetime:
        raise ValueError(
            f"Preferred lifetime ({preferred_lifetime}s) cannot be greater than "
            f"valid lifetime ({valid_lifetime}s)"
        )


def validate_option_address(label: str, value: str, family: int = 4) -> None:
    """Validate an optional address option value (router, DNS server).

    Args:
        label: Human-readable name of the option, used in the error message.
        value: The address to validate; an empty string means the option is
            omitted and is always valid.
        family: The address family the Pool serves — the option must match it.

    Raises:
        ValueError: ``value`` is non-empty and not a valid address of ``family``.
    """
    if not value:
        return
    if label.lower() == "router" and family == 6:
        raise ValueError(
            "A DHCPv6 Pool has no router option: the default gateway comes "
            "from the Router Advertisement, not from DHCPv6."
        )
    try:
        addr = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {label} address '{value}': {exc}") from exc
    if addr.version != family:
        raise ValueError(f"Only IPv{family} addresses are supported for the {label} option")


def validate_reservation(cidr: str, mac: str, ip: str, duid: str = "") -> None:
    """Validate one Reservation's identifier and reserved IP against a Subnet.

    The identifier rules differ by the Subnet's family, because the protocols
    identify clients differently: DHCPv4's ``chaddr`` means an IPv4
    Reservation names a hardware address only, while DHCPv6 carries a DUID in
    the Client Identifier option, so an IPv6 Reservation names a hardware
    address **or** a DUID — exactly one of the two.
    The hardware-address branch exists for the
    relayed client (RFC 6939) and, contingently, for a directly attached
    client whose DUID embeds its link-layer address.

    Args:
        cidr: The Subnet the Reservation's Pool belongs to.
        mac: The client's hardware address, or ``""`` when identified by DUID.
        ip: The address reserved for that client.
        duid: The client's DUID (colon-separated hex octets, as Kea's Lease
            table reports it), or ``""`` when identified by hardware address.

    Raises:
        ValueError: Both identifiers are set, neither is set, the set
            identifier's format is wrong, or ``ip`` is not a valid address of
            the Subnet's family within ``cidr``.
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid Subnet CIDR '{cidr}': {exc}") from exc

    if network.version == 4:
        if duid:
            raise ValueError(
                "A DHCPv4 Reservation names a hardware address; a DUID is an "
                "IPv6 client identifier and belongs on an IPv6 Pool."
            )
        if not mac:
            raise ValueError(_IDENT_MESSAGE)
        _validate_mac(mac)
    else:
        # bool equality is the exactly-one rule: True/True is both,
        # False/False is neither.
        if bool(mac) == bool(duid):
            raise ValueError(_IDENT_MESSAGE)
        if mac:
            _validate_mac(mac)
        else:
            _validate_duid(duid)

    try:
        ip_addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ValueError(f"Invalid Reservation address '{ip}': {exc}") from exc

    if ip_addr.version != network.version:
        raise ValueError(
            f"Reservation address {ip} is not an IPv{network.version} address"
        )

    if ip_addr not in network:
        raise ValueError(f"Reservation address {ip} is not in subnet {cidr}")


def _validate_mac(mac: str) -> None:
    """Validate one hardware address' form: six colon-separated hex octets."""
    if not _MAC_RE.match(mac):
        raise ValueError(
            f"Invalid MAC address '{mac}': expected six colon-separated hex "
            "octets, e.g. 'aa:bb:cc:dd:ee:ff'"
        )


def _validate_duid(duid: str) -> None:
    """Validate one DUID's form: at least four colon-separated hex octets.

    The spelling matches what Kea's Lease table reports (and accepts back in
    a host reservation), so a Learner can copy one into the other.
    """
    if not _DUID_RE.match(duid):
        raise ValueError(
            f"Invalid DUID '{duid}': expected colon-separated hex octets "
            "as the Lease table reports them, e.g. '00:01:00:01:aa:bb:cc:dd'"
        )
