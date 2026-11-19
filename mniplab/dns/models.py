"""DNS Resource Records and Zone data models for Authoritative DNS Service.

Provides immutable Record dataclasses for all standard RFC record types (A, AAAA,
CNAME, PTR, TXT, MX, NS, SOA), the Zone container representing an apex a Node is
Primary for, and the SecondaryZone one a Node replicates from a Primary over the
wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
from typing import Any, Dict, Iterable, List, Optional, Union

# Classroom SOA timers for auto-generated SOA records. `refresh`
# deliberately stays long so Notify, rather than the poll timer, is
# unambiguously what a Learner watches propagate a change.
AUTO_SOA_REFRESH = 3600
AUTO_SOA_RETRY = 60
AUTO_SOA_EXPIRE = 600
AUTO_SOA_MINIMUM = 300


@dataclass(frozen=True)
class Record:
    """Base immutable dataclass representing a DNS Resource Record (RR).

    Attributes:
        name: Record owner name relative to the zone apex (e.g. 'web') or '@' / FQDN.
        rtype: Uppercase DNS record type string (e.g. 'A', 'AAAA', 'CNAME').
        rdata: Formatted record data payload string.
        ttl: Time-to-live in seconds (default: 300).
    """

    name: str
    rtype: str
    rdata: str
    ttl: int = 300

    def to_dict(self) -> Dict[str, Any]:
        """Serialize record to dictionary.

        Returns:
            Dictionary containing record fields.
        """
        return {
            "name": self.name,
            "type": self.rtype,
            "rdata": self.rdata,
            "ttl": self.ttl,
        }


@dataclass(frozen=True, init=False)
class ARecord(Record):
    """IPv4 Address Resource Record.

    Attributes:
        name: Owner name.
        address: IPv4 address string.
        ttl: Time-to-live in seconds (default: 300).
    """

    address: str = ""

    def __init__(self, name: str, address: str, ttl: int = 300) -> None:
        """Initialize an ARecord.

        Args:
            name: Owner name.
            address: Valid IPv4 address string.
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If address is not a valid IPv4 address.
        """
        try:
            ip = ipaddress.IPv4Address(address)
        except (ipaddress.AddressValueError, ValueError) as e:
            raise ValueError(f"Invalid IPv4 address '{address}' for A record: {e}") from e

        object.__setattr__(self, "address", str(ip))
        super().__init__(name=name, rtype="A", rdata=str(ip), ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize A record to dictionary.

        Returns:
            Dictionary containing record fields including IPv4 address.
        """
        d = super().to_dict()
        d["address"] = self.address
        return d


@dataclass(frozen=True, init=False)
class AAAARecord(Record):
    """IPv6 Address Resource Record.

    Attributes:
        name: Owner name.
        address: IPv6 address string.
        ttl: Time-to-live in seconds (default: 300).
    """

    address: str = ""

    def __init__(self, name: str, address: str, ttl: int = 300) -> None:
        """Initialize an AAAARecord.

        Args:
            name: Owner name.
            address: Valid IPv6 address string.
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If address is not a valid IPv6 address.
        """
        try:
            ip = ipaddress.IPv6Address(address)
        except (ipaddress.AddressValueError, ValueError) as e:
            raise ValueError(f"Invalid IPv6 address '{address}' for AAAA record: {e}") from e

        object.__setattr__(self, "address", str(ip))
        super().__init__(name=name, rtype="AAAA", rdata=str(ip), ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize AAAA record to dictionary.

        Returns:
            Dictionary containing record fields including IPv6 address.
        """
        d = super().to_dict()
        d["address"] = self.address
        return d


@dataclass(frozen=True, init=False)
class CNAMERecord(Record):
    """Canonical Name Resource Record.

    Attributes:
        name: Owner name alias.
        target: Canonical target domain name.
        ttl: Time-to-live in seconds (default: 300).
    """

    target: str = ""

    def __init__(self, name: str, target: str, ttl: int = 300) -> None:
        """Initialize a CNAMERecord.

        Args:
            name: Owner name alias.
            target: Canonical target domain name.
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If target is empty.
        """
        if not target or not target.strip():
            raise ValueError("CNAME target cannot be empty")
        stripped = target.strip()
        object.__setattr__(self, "target", stripped)
        super().__init__(name=name, rtype="CNAME", rdata=stripped, ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize CNAME record to dictionary.

        Returns:
            Dictionary containing record fields including target.
        """
        d = super().to_dict()
        d["target"] = self.target
        return d


@dataclass(frozen=True, init=False)
class PTRRecord(Record):
    """Pointer Resource Record for reverse DNS lookups.

    Attributes:
        name: Reverse lookup owner label (e.g. '1' or IP octet).
        ptrdname: Target domain name pointed to.
        ttl: Time-to-live in seconds (default: 300).
    """

    ptrdname: str = ""

    def __init__(self, name: str, ptrdname: str, ttl: int = 300) -> None:
        """Initialize a PTRRecord.

        Args:
            name: Reverse lookup owner label.
            ptrdname: Target domain name pointed to.
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If ptrdname is empty.
        """
        if not ptrdname or not ptrdname.strip():
            raise ValueError("PTR target ptrdname cannot be empty")
        stripped = ptrdname.strip()
        object.__setattr__(self, "ptrdname", stripped)
        super().__init__(name=name, rtype="PTR", rdata=stripped, ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize PTR record to dictionary.

        Returns:
            Dictionary containing record fields including ptrdname.
        """
        d = super().to_dict()
        d["ptrdname"] = self.ptrdname
        return d


@dataclass(frozen=True, init=False)
class TXTRecord(Record):
    """Text Resource Record.

    Attributes:
        name: Owner name.
        text: Text string payload.
        ttl: Time-to-live in seconds (default: 300).
    """

    text: str = ""

    def __init__(self, name: str, text: str, ttl: int = 300) -> None:
        """Initialize a TXTRecord.

        Args:
            name: Owner name.
            text: Text string payload.
            ttl: Time-to-live in seconds.
        """
        clean_text = text
        if clean_text.startswith('"') and clean_text.endswith('"') and len(clean_text) >= 2:
            clean_text = clean_text[1:-1]
        object.__setattr__(self, "text", clean_text)
        rdata = f'"{clean_text}"'
        super().__init__(name=name, rtype="TXT", rdata=rdata, ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize TXT record to dictionary.

        Returns:
            Dictionary containing record fields including text payload.
        """
        d = super().to_dict()
        d["text"] = self.text
        return d


@dataclass(frozen=True, init=False)
class MXRecord(Record):
    """Mail Exchanger Resource Record.

    Attributes:
        name: Owner name.
        exchange: Domain name of mail server.
        preference: Priority integer (lower is preferred, default 10).
        ttl: Time-to-live in seconds (default: 300).
    """

    exchange: str = ""
    preference: int = 10

    def __init__(self, name: str, exchange: str, preference: int = 10, ttl: int = 300) -> None:
        """Initialize an MXRecord.

        Args:
            name: Owner name.
            exchange: Domain name of mail server.
            preference: Priority integer (0 to 65535).
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If exchange is empty or preference is outside 0-65535.
        """
        if not exchange or not exchange.strip():
            raise ValueError("MX exchange cannot be empty")
        if not (0 <= preference <= 65535):
            raise ValueError(f"MX preference must be between 0 and 65535, got {preference}")

        clean_ex = exchange.strip()
        object.__setattr__(self, "exchange", clean_ex)
        object.__setattr__(self, "preference", int(preference))
        rdata = f"{preference} {clean_ex}"
        super().__init__(name=name, rtype="MX", rdata=rdata, ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize MX record to dictionary.

        Returns:
            Dictionary containing record fields including exchange and preference.
        """
        d = super().to_dict()
        d["exchange"] = self.exchange
        d["preference"] = self.preference
        return d


@dataclass(frozen=True, init=False)
class NSRecord(Record):
    """Name Server Resource Record.

    Attributes:
        name: Zone apex or delegation label.
        nameserver: Domain name of the authoritative nameserver.
        ttl: Time-to-live in seconds (default: 300).
    """

    nameserver: str = ""

    def __init__(self, name: str, nameserver: str, ttl: int = 300) -> None:
        """Initialize an NSRecord.

        Args:
            name: Zone apex or delegation label.
            nameserver: Domain name of the authoritative nameserver.
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If nameserver is empty.
        """
        if not nameserver or not nameserver.strip():
            raise ValueError("NS nameserver cannot be empty")
        clean_ns = nameserver.strip()
        object.__setattr__(self, "nameserver", clean_ns)
        super().__init__(name=name, rtype="NS", rdata=clean_ns, ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize NS record to dictionary.

        Returns:
            Dictionary containing record fields including nameserver.
        """
        d = super().to_dict()
        d["nameserver"] = self.nameserver
        return d


@dataclass(frozen=True, init=False)
class SOARecord(Record):
    """Start of Authority Resource Record.

    Attributes:
        mname: Primary master nameserver FQDN.
        rname: Mailbox of responsible person (as domain name).
        serial: Zone serial number.
        refresh: Refresh interval in seconds.
        retry: Retry interval in seconds.
        expire: Expire limit in seconds.
        minimum: Minimum/negative caching TTL in seconds.
        ttl: Time-to-live for SOA record in seconds (default: 3600).
    """

    mname: str = ""
    rname: str = ""
    serial: int = 1
    refresh: int = 3600
    retry: int = 900
    expire: int = 604800
    minimum: int = 300

    def __init__(
        self,
        mname: str,
        rname: str,
        serial: int = 1,
        refresh: int = 3600,
        retry: int = 900,
        expire: int = 604800,
        minimum: int = 300,
        ttl: int = 3600,
        name: str = "@",
    ) -> None:
        """Initialize an SOARecord.

        Args:
            mname: Primary master nameserver FQDN.
            rname: Mailbox of responsible person.
            serial: Zone serial number.
            refresh: Refresh interval in seconds.
            retry: Retry interval in seconds.
            expire: Expire limit in seconds.
            minimum: Minimum/negative caching TTL in seconds.
            ttl: Time-to-live for SOA record in seconds.
            name: Owner name (default '@').
        """
        object.__setattr__(self, "mname", mname.strip())
        object.__setattr__(self, "rname", rname.strip())
        object.__setattr__(self, "serial", int(serial))
        object.__setattr__(self, "refresh", int(refresh))
        object.__setattr__(self, "retry", int(retry))
        object.__setattr__(self, "expire", int(expire))
        object.__setattr__(self, "minimum", int(minimum))
        rdata = f"{mname} {rname} {serial} {refresh} {retry} {expire} {minimum}"
        super().__init__(name=name, rtype="SOA", rdata=rdata, ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize SOA record to dictionary.

        Returns:
            Dictionary containing all SOA parameters and timers.
        """
        d = super().to_dict()
        d.update({
            "mname": self.mname,
            "rname": self.rname,
            "serial": self.serial,
            "refresh": self.refresh,
            "retry": self.retry,
            "expire": self.expire,
            "minimum": self.minimum,
        })
        return d


@dataclass(frozen=True, init=False)
class DSRecord(Record):
    """Delegation Signer Resource Record (RFC 4034).

    Attributes:
        name: Owner name / delegated child zone apex.
        key_tag: 16-bit key identifier (0 to 65535).
        algorithm: Cryptographic algorithm number (0 to 255).
        digest_type: Digest algorithm identifier (0 to 255).
        digest: Hexadecimal digest string.
        ttl: Time-to-live in seconds (default: 300).
    """

    key_tag: int = 0
    algorithm: int = 0
    digest_type: int = 0
    digest: str = ""

    def __init__(
        self,
        name: str,
        key_tag: int,
        algorithm: int,
        digest_type: int,
        digest: str,
        ttl: int = 300,
    ) -> None:
        """Initialize a DSRecord.

        Args:
            name: Owner name.
            key_tag: Key tag integer (0 to 65535).
            algorithm: Algorithm number (0 to 255).
            digest_type: Digest type number (0 to 255).
            digest: Hexadecimal digest string.
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If key_tag, algorithm, digest_type are out of bounds or digest is invalid.
        """
        if not isinstance(key_tag, int) or not (0 <= key_tag <= 65535):
            raise ValueError(f"DS key_tag must be between 0 and 65535, got {key_tag}")
        if not isinstance(algorithm, int) or not (0 <= algorithm <= 255):
            raise ValueError(f"DS algorithm must be between 0 and 255, got {algorithm}")
        if not isinstance(digest_type, int) or not (0 <= digest_type <= 255):
            raise ValueError(f"DS digest_type must be between 0 and 255, got {digest_type}")
        if not digest or not isinstance(digest, str):
            raise ValueError("DS digest cannot be empty")
        clean_digest = digest.strip().replace(" ", "").lower()
        if not clean_digest:
            raise ValueError("DS digest cannot be empty")
        try:
            bytes.fromhex(clean_digest)
        except ValueError as exc:
            raise ValueError(f"Invalid DS digest hex string '{digest}': {exc}") from exc

        object.__setattr__(self, "key_tag", int(key_tag))
        object.__setattr__(self, "algorithm", int(algorithm))
        object.__setattr__(self, "digest_type", int(digest_type))
        object.__setattr__(self, "digest", clean_digest)
        rdata = f"{key_tag} {algorithm} {digest_type} {clean_digest}"
        super().__init__(name=name, rtype="DS", rdata=rdata, ttl=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize DS record to dictionary.

        Returns:
            Dictionary containing all DS fields.
        """
        d = super().to_dict()
        d.update({
            "key_tag": self.key_tag,
            "algorithm": self.algorithm,
            "digest_type": self.digest_type,
            "digest": self.digest,
        })
        return d


RECORD_TYPES: Dict[str, type[Record]] = {
    "A": ARecord,
    "AAAA": AAAARecord,
    "CNAME": CNAMERecord,
    "PTR": PTRRecord,
    "TXT": TXTRecord,
    "MX": MXRecord,
    "NS": NSRecord,
    "SOA": SOARecord,
    "DS": DSRecord,
}


class Zone:
    """A hosted DNS Zone rooted at an apex domain name.

    Attributes:
        name: Zone apex FQDN ending with '.' (e.g. 'example.com.').
        records: List of Resource Records in the zone.
        default_ttl: Default TTL in seconds for zone records.
        host_node: Optional name of the serving Node.
        allow_transfer: Explicit Zone Transfer allow-list, or None to derive it
            from the Secondaries declared for this apex.
    """

    def __init__(
        self,
        name: str,
        records: Optional[Iterable[Record]] = None,
        default_ttl: int = 300,
        host_node: Optional[str] = None,
        allow_transfer: Optional[Iterable[Any]] = None,
        dnssec: bool = False,
        nsec3: bool = False,
        nsec3_salt: Optional[str] = None,
    ) -> None:
        """Initialize a Zone.

        Args:
            name: Zone apex FQDN ending with '.'.
            records: Optional initial list of Record instances.
            default_ttl: Default TTL in seconds.
            host_node: Optional node name to use for default nameserver targets.
            allow_transfer: Optional Nodes or IP address strings permitted to
                pull this Zone. When given it replaces the ACL derived from the
                declared Secondaries outright, which is what stages a refused
                Zone Transfer.
            dnssec: Whether to enable DNSSEC signing for this zone.
            nsec3: Whether to use NSEC3 instead of plaintext NSEC.
            nsec3_salt: Optional hexadecimal salt string for NSEC3.

        Raises:
            ValueError: If name does not end with a trailing dot or is empty.
        """
        if not name or not name.endswith("."):
            raise ValueError(
                f"Zone apex '{name}' must be a fully-qualified domain name ending with '.'"
            )

        self.name: str = name
        self.default_ttl: int = default_ttl
        self.host_node: Optional[str] = host_node
        self.allow_transfer: Optional[List[Any]] = (
            list(allow_transfer) if allow_transfer is not None else None
        )
        self.dnssec: bool = bool(dnssec)
        self.nsec3: bool = bool(nsec3)
        self.nsec3_salt: Optional[str] = nsec3_salt
        self.keys_dir: Optional[str] = None

        self.records: List[Record] = list(records) if records is not None else []

        self._auto_soa: bool = False
        self._auto_ns: bool = False

        self._ensure_defaults()

    def _qualify(self, label: str) -> str:
        """Return a label as an FQDN under this Zone's apex.

        The root zone's apex is already the trailing dot, so a label there is
        qualified by appending the dot rather than the apex.

        Args:
            label: A single label or relative name.

        Returns:
            The label as an FQDN.
        """
        if self.name == ".":
            return f"{label}."
        return f"{label}.{self.name}"

    def _default_nameserver_target(self, node_name: Optional[str]) -> str:
        """Derive standard default nameserver target.

        Args:
            node_name: Optional node name.

        Returns:
            FQDN target for default SOA and NS records.
        """
        if not node_name:
            return self._qualify("ns1")
        if self.name.endswith(".arpa.") or self.name == ".":
            if "." in node_name:
                return node_name if node_name.endswith(".") else f"{node_name}."
            return f"{node_name}."
        return f"{node_name}.{self.name}"

    def _ensure_defaults(self) -> None:
        """Auto-generate default standard SOA and apex NS records if omitted."""
        has_soa = any(r.rtype == "SOA" for r in self.records)
        if not has_soa:
            self._auto_soa = True
            ns_target = self._default_nameserver_target(self.host_node)
            soa = SOARecord(
                mname=ns_target,
                rname=self._qualify("hostmaster"),
                serial=1,
                refresh=AUTO_SOA_REFRESH,
                retry=AUTO_SOA_RETRY,
                expire=AUTO_SOA_EXPIRE,
                minimum=AUTO_SOA_MINIMUM,
                ttl=3600,
                name="@",
            )
            self.records.insert(0, soa)

        has_apex_ns = any(
            r.rtype == "NS" and r.name in ("@", self.name, "")
            for r in self.records
        )
        if not has_apex_ns:
            self._auto_ns = True
            ns_target = self._default_nameserver_target(self.host_node)
            ns = NSRecord(name="@", nameserver=ns_target, ttl=self.default_ttl)
            # Place apex NS after SOA
            idx = 1 if self._auto_soa else 0
            self.records.insert(idx, ns)

    def bind_host_node(self, node_name: str) -> None:
        """Bind this zone to a serving host Node.

        If SOA and apex NS were auto-generated without an explicit host_node,
        this updates their targets to point to the host node. Explicitly
        provided author records are never overwritten.

        Args:
            node_name: The name of the Node hosting this zone.
        """
        self.host_node = node_name
        target = self._default_nameserver_target(node_name)

        if self._auto_soa:
            for idx, r in enumerate(self.records):
                if isinstance(r, SOARecord):
                    new_soa = SOARecord(
                        mname=target,
                        rname=r.rname,
                        serial=r.serial,
                        refresh=r.refresh,
                        retry=r.retry,
                        expire=r.expire,
                        minimum=r.minimum,
                        ttl=r.ttl,
                        name=r.name,
                    )
                    self.records[idx] = new_soa
                    break

        if self._auto_ns:
            for idx, r in enumerate(self.records):
                if isinstance(r, NSRecord) and r.name in ("@", self.name, ""):
                    new_ns = NSRecord(name=r.name, nameserver=target, ttl=r.ttl)
                    self.records[idx] = new_ns
                    break

    def ds_record(self, digest_type: int = 2) -> DSRecord:
        """Extract the generated KSK delegation signer record for this zone.

        Args:
            digest_type: Digest algorithm identifier (default 2 for SHA-256).

        Returns:
            A DSRecord for the zone's provisioned KSK.

        Raises:
            ValueError: If the zone is not configured with dnssec=True.
        """
        if not self.dnssec:
            raise ValueError(
                f"Zone '{self.name}' is not configured with dnssec=True"
            )
        from mniplab.dns.provisioner import DNSProvisioner
        return DNSProvisioner.get_zone_ds_record(self, digest_type=digest_type)

    @property
    def soa_record(self) -> Optional[SOARecord]:
        """Return the SOARecord in this zone, if present.

        Returns:
            The SOARecord instance or None.
        """
        for r in self.records:
            if isinstance(r, SOARecord):
                return r
        return None

    @property
    def ns_records(self) -> List[NSRecord]:
        """Return all NSRecords in this zone.

        Returns:
            List of NSRecord instances in this zone.
        """
        return [r for r in self.records if isinstance(r, NSRecord)]

    def add_record(self, record: Record) -> None:
        """Add a Resource Record to this zone.

        Args:
            record: The Record to append.
        """
        self.records.append(record)

    def remove_record(self, record: Record) -> None:
        """Remove a Resource Record from this zone.

        Args:
            record: The Record to remove.
        """
        if record in self.records:
            self.records.remove(record)

    def get_records(self, rtype: Optional[str] = None) -> List[Record]:
        """Get records in this zone, optionally filtered by record type.

        Args:
            rtype: Optional record type string (e.g. 'A', 'PTR').

        Returns:
            List of matching records.
        """
        if rtype is None:
            return list(self.records)
        target_type = rtype.upper()
        return [r for r in self.records if r.rtype == target_type]

    def to_zone_file(self) -> str:
        """Render this Zone into RFC 1035 master file syntax.

        Returns:
            String content of the zone file.
        """
        lines = [
            f"$ORIGIN {self.name}",
            f"$TTL {self.default_ttl}",
        ]
        for record in self.records:
            owner = record.name if record.name else "@"
            lines.append(f"{owner} {record.ttl} IN {record.rtype} {record.rdata}")

        return "\n".join(lines) + "\n"

    def clone(self) -> Zone:
        """Create a deep copy of this Zone for snapshotting.

        Returns:
            A new Zone instance with an independent copy of records.
        """
        new_zone = Zone.__new__(Zone)
        new_zone.name = self.name
        new_zone.default_ttl = self.default_ttl
        new_zone.host_node = self.host_node
        new_zone.allow_transfer = (
            list(self.allow_transfer) if self.allow_transfer is not None else None
        )
        new_zone._auto_soa = self._auto_soa
        new_zone._auto_ns = self._auto_ns
        new_zone.dnssec = self.dnssec
        new_zone.nsec3 = self.nsec3
        new_zone.nsec3_salt = self.nsec3_salt
        new_zone.keys_dir = self.keys_dir
        new_zone.records = list(self.records)
        return new_zone

    def to_dict(self) -> Dict[str, Any]:
        """Serialize Zone to dictionary.

        Returns:
            Dictionary containing zone metadata and serialized records.
        """
        return {
            "name": self.name,
            "default_ttl": self.default_ttl,
            "dnssec": self.dnssec,
            "nsec3": self.nsec3,
            "nsec3_salt": self.nsec3_salt,
            "records": [r.to_dict() for r in self.records],
        }


def transfer_target_addresses(target: Any) -> List[str]:
    """Return the addresses a declared transfer peer can be reached at.

    A peer is declared either as a Node — the common case, read for its
    interface addresses once they are up — or as a bare IP address string,
    which may name something outside this Lab. This follows the precedent the
    Resolver Service's Root Hints set.

    Args:
        target: A Node, or an IPv4 or IPv6 address string.

    Returns:
        The peer's usable addresses, without loopback and without duplicates.
        Empty when the target names neither.
    """
    if isinstance(target, str):
        try:
            address = ipaddress.ip_address(target.strip())
        except ValueError:
            return []
        return [] if address.is_loopback else [str(address)]

    found: List[str] = []
    for intf in getattr(target, "intfList", lambda: [])():
        for candidate in (getattr(intf, "ip", None), getattr(intf, "ip6", None)):
            if not candidate or candidate in found:
                continue
            try:
                if ipaddress.ip_address(candidate).is_loopback:
                    continue
            except ValueError:
                continue
            found.append(str(candidate))
    return found


def transfer_target_label(target: Any) -> str:
    """Return a human-readable name for a declared transfer peer.

    Args:
        target: A Node, or an IP address string.

    Returns:
        The Node's name, or the address string itself.
    """
    if isinstance(target, str):
        return target.strip()
    return str(getattr(target, "name", target))


class SecondaryZone:
    """A Zone a Node replicates from a Primary rather than serves from a file.

    A Secondary carries an apex and its Primary and nothing else: its Records
    arrive over the wire, so it has none to seed, no default TTL to apply, and
    neither an auto-generated SOA nor an auto-generated apex NS. Declaring the
    two authority types as separate types rather than one type with a role flag
    is what makes seeding a Secondary unrepresentable instead of silently
    ignored.

    Attributes:
        name: Zone apex FQDN ending with '.'.
        primary: The declared Primary, a Node or an IP address string.
        host_node: Optional name of the Node serving this Secondary.
        records: Always empty; a Secondary's contents live in Knot's storage.
    """

    def __init__(
        self,
        name: str,
        primary: Any,
        host_node: Optional[str] = None,
        records: Any = None,
    ) -> None:
        """Initialize a Secondary Zone.

        Args:
            name: Zone apex FQDN ending with '.'.
            primary: The Primary to replicate from, either the Node itself or
                an IP address string.
            host_node: Optional name of the serving Node.
            records: Accepted only to reject it — a Secondary's Records are
                acquired by Zone Transfer and cannot be declared.

        Raises:
            ValueError: If name is not an FQDN, if primary is missing, or if
                records are supplied.
        """
        if not name or not name.endswith("."):
            raise ValueError(
                f"Zone apex '{name}' must be a fully-qualified domain name ending with '.'"
            )
        if primary is None or (isinstance(primary, str) and not primary.strip()):
            raise ValueError(
                f"Secondary zone '{name}' must name a primary, either a Node or an IP address"
            )
        if records:
            raise ValueError(
                f"Secondary zone '{name}' cannot be seeded with records: its contents "
                f"arrive by Zone Transfer from '{transfer_target_label(primary)}'. "
                f"Declare the records on the primary's Zone instead."
            )

        self.name: str = name
        self.primary: Any = primary
        self.host_node: Optional[str] = host_node
        self.records: List[Record] = []

    @property
    def primary_label(self) -> str:
        """The Primary's Node name, or its address when declared as one."""
        return transfer_target_label(self.primary)

    @property
    def soa_record(self) -> Optional[SOARecord]:
        """Always None: a Secondary's SOA is the Primary's, acquired over the wire.

        Returns:
            None. Read-side callers share one accessor across both Zone types,
            so a Secondary answers the question rather than lacking the
            attribute.
        """
        return None

    def primary_addresses(self) -> List[str]:
        """Return the addresses this Secondary transfers from.

        A Node Primary becomes addresses here, once its interfaces are up.

        Returns:
            The Primary's usable IP addresses, IPv4 before IPv6.
        """
        return transfer_target_addresses(self.primary)

    def bind_host_node(self, node_name: str) -> None:
        """Bind this Secondary to its serving Node.

        Args:
            node_name: The name of the Node hosting this Secondary.
        """
        self.host_node = node_name

    def clone(self) -> SecondaryZone:
        """Create a copy of this Secondary for snapshotting.

        Returns:
            A new SecondaryZone naming the same apex and Primary.
        """
        return SecondaryZone(
            self.name, primary=self.primary, host_node=self.host_node
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this Secondary to a dictionary.

        Returns:
            A dict naming the apex, the role, and the Primary.
        """
        return {
            "name": self.name,
            "role": "secondary",
            "primary": self.primary_label,
            "primary_addresses": self.primary_addresses(),
            "records": [],
        }

    def __repr__(self) -> str:
        """Return an unambiguous representation naming apex and Primary."""
        return f"SecondaryZone({self.name!r}, primary={self.primary_label!r})"
