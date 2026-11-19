"""NAT Table: what a translating Router remembers about the connections it rewrote.

NAT's defining property is that it is stateful, and state is invisible on
the wire: a packet carries one side of an entry or the other, never both
and never how long the entry has left.  So this reads the
Router's connection tracking table instead of its packets.

The table is read inside the translating Router's namespace, entered by
its Namespace Anchor exactly as a Packet Capture is.  Connection tracking
is per network namespace, so what is read is that Router's own table and
its Lab's by construction: neither the NAT Gateway's translation in the
root namespace nor a second Lab's can appear, and there is no zone to
scope by.

It is read with ``conntrack -L`` (conntrack-tools).  The proc-filesystem
table, ``/proc/net/nf_conntrack``, is not an alternative: kernels built
without ``CONFIG_NF_CONNTRACK_PROCFS`` — the dev container's among them —
have no such file, in a Router's namespace or out of it, while a NAT Lab
runs or not.

:func:`parse_nat_table` is pure and holds every decision about what the
text means; :class:`NATTableReader` is the only code here that starts
a process.
"""

from __future__ import annotations

import ipaddress
import shlex
import subprocess
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, List, Optional, Set, Tuple

from mniplab.labhandle import NodeAttachment
from mniplab.namespace import build_nsenter_argv

# Seconds one read of a table may take before it is abandoned.  A table a
# Lab fills holds a handful of connections; listing one takes milliseconds.
READ_TIMEOUT = 5.0

# The keys of one direction's tuple in a ``conntrack -L`` line.
_TUPLE_KEYS = frozenset({"src", "dst", "sport", "dport", "type", "code", "id"})

# The address families ``-o extended`` puts before the protocol.
_FAMILIES = frozenset({"ipv4", "ipv6"})


@dataclass(frozen=True)
class NATEntry:
    """One entry of a Router's NAT Table: an inside pair, the outside pair substituted for it.

    Attributes:
        protocol: The connection's protocol as conntrack names it (``tcp``,
            ``udp``, ``icmp``).
        inside_address: The source address the connection had on arrival.
        inside_port: Its source port, or for ICMP its echo identifier.
        outside_address: The source address the Router substituted.
        outside_port: The source port (or ICMP identifier) substituted.
        destination: Where the connection is going, which is not rewritten.
        destination_port: The destination port; None for ICMP.
        port_kind: ``"port"`` for TCP and UDP, ``"id"`` for ICMP, None for
            a protocol with neither.
        state: The TCP state (``ESTABLISHED``, ``TIME_WAIT`` …); for a
            protocol without one, ``UNREPLIED`` until the far end answers
            and ``REPLIED`` after.
        lifetime: Seconds the entry has left unless more traffic renews it.
        kind: ``"source"`` when the Router rewrote the source (a masquerade,
            or the outbound side of a static mapping): the inside pair is the
            client, and destination is the server it reached. ``"destination"``
            when it rewrote only the destination (a port forward, or the inbound
            side of a static mapping): the inside pair is the server reached,
            the outside pair the address the client targeted, and destination
            the client.
    """

    protocol: str
    inside_address: str
    inside_port: Optional[str]
    outside_address: str
    outside_port: Optional[str]
    destination: str
    destination_port: Optional[str]
    port_kind: Optional[str]
    state: str
    lifetime: int
    kind: str

    def as_dict(self) -> Dict[str, Any]:
        """Return the entry as the JSON the NAT Table pane reads."""
        return asdict(self)


def _port_kind(tuple_: Dict[str, str]) -> Optional[str]:
    if "sport" in tuple_:
        return "port"
    if "id" in tuple_:
        return "id"
    return None


def _source_port(tuple_: Dict[str, str]) -> Optional[str]:
    return tuple_.get("sport", tuple_.get("id"))


def _destination_port(tuple_: Dict[str, str]) -> Optional[str]:
    return tuple_.get("dport", tuple_.get("id"))


def _parse_line(line: str) -> Optional[NATEntry]:
    """Read one ``conntrack -L`` line; None unless it is a translated connection.

    A line reads ``<proto> <protonum> <timeout> [STATE] <original tuple>
    [FLAGS] <reply tuple> [FLAGS] <more key=value>``.  The reply tuple is
    what the far end sends back, so it is addressed to whatever source the
    Router substituted: the connection was translated when the reply goes
    to a different address or port than the original came from.
    """
    words = line.split()
    if words and words[0] in _FAMILIES:
        words = words[2:]
    if len(words) < 4 or not words[1].isdigit() or not words[2].isdigit():
        return None
    protocol, lifetime = words[0], int(words[2])

    state: Optional[str] = None
    unreplied = False
    tuples: List[Dict[str, str]] = []
    for word in words[3:]:
        if word.startswith("[") and word.endswith("]"):
            unreplied = unreplied or word == "[UNREPLIED]"
            continue
        key, sep, value = word.partition("=")
        if not sep:
            if not tuples:
                state = word
            continue
        if key == "src":
            tuples.append({})
        if key in _TUPLE_KEYS and tuples and len(tuples) <= 2:
            tuples[-1].setdefault(key, value)
    if len(tuples) != 2 or not all("src" in t and "dst" in t for t in tuples):
        return None
    original, reply = tuples
    port_kind = _port_kind(original)
    state_text = state or ("UNREPLIED" if unreplied else "REPLIED")

    # The reply comes back to whatever source the Router substituted, so a
    # source rewrite shows as the reply's destination differing from the
    # original source. A destination rewrite shows as the reply's source
    # differing from the original destination.
    if (original["src"], _source_port(original)) != (reply["dst"], _destination_port(reply)):
        return NATEntry(
            protocol=protocol,
            inside_address=original["src"],
            inside_port=_source_port(original),
            outside_address=reply["dst"],
            outside_port=_destination_port(reply),
            destination=original["dst"],
            destination_port=original.get("dport"),
            port_kind=port_kind,
            state=state_text,
            lifetime=lifetime,
            kind="source",
        )
    if (original["dst"], _destination_port(original)) != (reply["src"], _source_port(reply)):
        return NATEntry(
            protocol=protocol,
            inside_address=reply["src"],
            inside_port=_source_port(reply),
            outside_address=original["dst"],
            outside_port=_destination_port(original),
            destination=original["src"],
            destination_port=original.get("sport"),
            port_kind=port_kind,
            state=state_text,
            lifetime=lifetime,
            kind="destination",
        )
    return None


def _sort_key(entry: NATEntry) -> Tuple[Any, ...]:
    try:
        address: Any = ipaddress.ip_address(entry.inside_address)
        family = address.version
    except ValueError:
        address, family = None, 0
    port = entry.inside_port or ""
    return (
        family,
        int(address) if address is not None else 0,
        entry.protocol,
        int(port) if port.isdigit() else -1,
        entry.destination,
        entry.kind,
    )


def parse_nat_table(text: str) -> List[NATEntry]:
    """Read a ``conntrack -L`` table into the NAT Table entries it holds.

    Only connections the Router rewrote are entries, whether the source
    (``kind="source"``) or only the destination (``kind="destination"``, a
    port forward). A connection the Router forwards untranslated, or one that
    starts or ends at the Router itself, is left out.  Lines that are not table entries —
    blank lines, conntrack's own summary — are ignored, as is the address
    family ``-o extended`` puts first.

    Args:
        text: What ``conntrack -L`` printed.

    Returns:
        The entries, ordered by inside address, protocol and inside
        port, so a table read again lists them in the same order — the
        kernel's own order is its hash table's, which reshuffles.
    """
    entries = [e for e in map(_parse_line, text.splitlines()) if e is not None]
    return sorted(entries, key=_sort_key)


@dataclass(frozen=True)
class NATRule:
    """One rule of a Router's NAT, as the rules strip shows it.

    Attributes:
        kind: ``"masquerade"``, ``"port_forward"``, ``"static"`` or ``"other"``.
        interface: The outside Interface the rule applies to. None for a port
            forward whose DNAT names no Interface, and for ``"other"``.
        protocol: ``tcp`` or ``udp`` for a port forward.
        outside_address: The address a port forward or static mapping is
            reached at from outside.
        outside_port: The port a port forward is reached at.
        inside_address: The inside server a port forward reaches, or the inside
            Host a static mapping maps.
        inside_port: The port a port forward reaches on its inside server.
        hairpin: True when inside clients reach the forward through the
            outside address too.
        raw: For ``"other"``, the line as iptables printed it.
    """

    kind: str
    interface: Optional[str] = None
    protocol: Optional[str] = None
    outside_address: Optional[str] = None
    outside_port: Optional[str] = None
    inside_address: Optional[str] = None
    inside_port: Optional[str] = None
    hairpin: bool = False
    raw: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        """Return the rule as the JSON the NAT Table pane reads."""
        return asdict(self)


# The options a rule this module reads may carry, each with one value.
_RULE_OPTIONS = frozenset({
    "-A", "-i", "-o", "-s", "-d", "-p", "-j", "--dport", "--to-destination",
    "--to-source", "--ctstate",
})
# The match modules iptables prints for the protocols and state a rule names.
_MODULES = frozenset({"tcp", "udp", "conntrack"})
_FORWARD_OPTIONS = frozenset({"-A", "-i", "-d", "-p", "--dport", "-j", "--to-destination"})
_HAIRPIN_OPTIONS = frozenset({"-A", "-s", "-d", "-p", "--dport", "-j", "--ctstate"})
# Lines that describe the table rather than a rule.
_TABLE_LINES = frozenset({"-P", "-N"})


@dataclass
class _Line:
    """One rule line split into its options and the match modules it names.

    Attributes:
        options: The value of each option the line carried.
        modules: The names given after ``-m``.
    """

    options: Dict[str, str]
    modules: Set[str]


def _read_line(line: str) -> Optional[_Line]:
    """Split one rule line; None unless every token is one this module reads."""
    try:
        tokens = iter(shlex.split(line))
    except ValueError:
        return None
    options: Dict[str, str] = {}
    modules: Set[str] = set()
    for token in tokens:
        if token == "-m":
            module = next(tokens, None)
            if module is None:
                return None
            modules.add(module)
        elif token in _RULE_OPTIONS and token not in options:
            value = next(tokens, None)
            if value is None:
                return None
            options[token] = value
        else:
            return None
    return _Line(options, modules) if "-A" in options else None


def _host(value: str) -> Optional[str]:
    """The IPv4 address a rule names as one host (``a.b.c.d`` or ``a.b.c.d/32``), or None."""
    address = value[:-3] if value.endswith("/32") else value
    try:
        ipaddress.IPv4Address(address)
    except ValueError:
        return None
    return address


def _masquerade(line: _Line) -> Optional[str]:
    """The outside Interface a plain masquerade rule names, or None."""
    o = line.options
    if (set(o) == {"-A", "-o", "-j"} and not line.modules
            and o["-A"] == "POSTROUTING" and o["-j"] == "MASQUERADE"):
        return o["-o"]
    return None


def _hairpin_masquerade(line: _Line) -> Optional[Tuple[str, str, str]]:
    """The inside server, protocol and port a hairpin masquerade serves, or None."""
    o = line.options
    if (o["-A"] != "POSTROUTING" or o.get("-j") != "MASQUERADE"
            or o.get("--ctstate") != "DNAT" or "-o" in o
            or not {"-d", "-p", "--dport"} <= set(o) or not set(o) <= _HAIRPIN_OPTIONS
            or not line.modules <= _MODULES):
        return None
    server = _host(o["-d"])
    return None if server is None else (server, o["-p"], o["--dport"])


def _port_forward(line: _Line) -> Optional[NATRule]:
    """The port forward a DNAT rule declares, its hairpin not yet settled, or None."""
    o = line.options
    if (o["-A"] != "PREROUTING" or o.get("-j") != "DNAT"
            or not {"-d", "-p", "--dport", "--to-destination"} <= set(o)
            or not set(o) <= _FORWARD_OPTIONS or o["-p"] not in ("tcp", "udp")
            or not line.modules <= {"tcp", "udp"} or not o["--dport"].isdigit()):
        return None
    outside = _host(o["-d"])
    host, sep, port = o["--to-destination"].rpartition(":")
    inside = _host(host) if sep and port.isdigit() else None
    if outside is None or inside is None:
        return None
    return NATRule(kind="port_forward", interface=o.get("-i"), protocol=o["-p"],
                   outside_address=outside, outside_port=o["--dport"],
                   inside_address=inside, inside_port=port)


def _static_dnat(line: _Line) -> Optional[Tuple[str, str, str]]:
    """The Interface, inside Host and mapped address of a static mapping's DNAT, or None."""
    o = line.options
    if (set(o) != {"-A", "-i", "-d", "-j", "--to-destination"} or line.modules
            or o["-A"] != "PREROUTING" or o["-j"] != "DNAT"):
        return None
    outside = _host(o["-d"])
    inside = _host(o["--to-destination"])
    if outside is None or inside is None:
        return None
    return (o["-i"], inside, outside)


def _static_snat(line: _Line) -> Optional[Tuple[str, str, str]]:
    """The Interface, inside Host and mapped address of a static mapping's SNAT, or None."""
    o = line.options
    if (set(o) != {"-A", "-o", "-s", "-j", "--to-source"} or line.modules
            or o["-A"] != "POSTROUTING" or o["-j"] != "SNAT"):
        return None
    inside = _host(o["-s"])
    outside = _host(o["--to-source"])
    if inside is None or outside is None:
        return None
    return (o["-o"], inside, outside)


def parse_nat_rules(text: str) -> List[NATRule]:
    """Read ``iptables -t nat -S`` into the rules a Router runs.

    A rule this module knows becomes an item: a masquerade out of an
    Interface, a port forward, or a static 1:1 mapping. A forward's hairpin
    masquerade and a static mapping's SNAT belong to those items and are not
    listed on their own. Any other rule is an ``"other"`` item holding its
    raw line, so nothing the Router runs is left out. Policy and chain lines
    are not rules.

    Args:
        text: What ``iptables -t nat -S`` printed.

    Returns:
        The items, in the order iptables lists their rules.
    """
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and line.split()[0] not in _TABLE_LINES]
    reads = [_read_line(line) for line in lines]

    built: Dict[int, NATRule] = {}
    claimed: Set[int] = set()

    # A forward whose DNAT names no Interface is the one a Learner's inside
    # clients reach, so it is hairpin, and its address and port are reachable
    # from inside. A forward that names an Interface is hairpin only when such
    # a DNAT serves the same address and port.
    forwards: Dict[int, NATRule] = {}
    unnamed: Set[int] = set()
    for index, read in enumerate(reads):
        if read is None:
            continue
        forward = _port_forward(read)
        if forward is not None:
            forwards[index] = forward
            if "-i" not in read.options:
                unnamed.add(index)
    reachable = {
        (forwards[i].outside_address, forwards[i].protocol, forwards[i].outside_port)
        for i in unnamed
    }
    # The inside server, protocol and port each hairpin forward reaches; the
    # masquerade for those belongs to the forward.
    served = {
        (forwards[i].inside_address, forwards[i].protocol, forwards[i].inside_port)
        for i in unnamed
    }
    for index, forward in forwards.items():
        hairpin = index in unnamed or (
            forward.outside_address, forward.protocol, forward.outside_port) in reachable
        built[index] = replace(forward, hairpin=hairpin)
    for index, read in enumerate(reads):
        if read is not None and _hairpin_masquerade(read) in served:
            claimed.add(index)

    snats: Dict[int, Tuple[str, str, str]] = {}
    for index, read in enumerate(reads):
        key = _static_snat(read) if read is not None else None
        if key is not None:
            snats[index] = key
    for index, read in enumerate(reads):
        mapping = _static_dnat(read) if read is not None else None
        if mapping is None:
            continue
        pair = next((j for j, key in snats.items() if key == mapping and j not in claimed), None)
        if pair is None:
            continue
        claimed.add(pair)
        interface, inside, outside = mapping
        built[index] = NATRule(kind="static", interface=interface,
                               inside_address=inside, outside_address=outside)

    for index, read in enumerate(reads):
        egress = _masquerade(read) if read is not None else None
        if egress is not None and index not in claimed:
            built[index] = NATRule(kind="masquerade", interface=egress)

    rules: List[NATRule] = []
    for index, line in enumerate(lines):
        if index in claimed:
            continue
        rules.append(built[index] if index in built else NATRule(kind="other", raw=line))
    return rules


def _namespace_command(attachment: NodeAttachment, command: List[str]) -> List[str]:
    """Build ``command`` to run inside a translating Node's namespaces.

    Raises:
        ValueError: The Node is a Container Host, which never translates
            for a Lab.
    """
    if attachment.container_name is not None:
        raise ValueError(f"{attachment.node} is a Container Host and holds no NAT Table")
    return build_nsenter_argv(attachment.pid, command)


def build_nat_table_command(attachment: NodeAttachment) -> List[str]:
    """Build the command that lists one Node's connection tracking table.

    Args:
        attachment: The translating Node's name and Namespace Anchor pid.

    Returns:
        The full command, starting with ``nsenter``.

    Raises:
        ValueError: The Node is a Container Host, which never translates
            for a Lab.
    """
    return _namespace_command(attachment, ["conntrack", "-L"])


def build_nat_rules_command(attachment: NodeAttachment) -> List[str]:
    """Build the command that lists one Node's NAT rules.

    Args:
        attachment: The translating Node's name and Namespace Anchor pid.

    Returns:
        The full command, starting with ``nsenter``.

    Raises:
        ValueError: The Node is a Container Host, which never translates
            for a Lab.
    """
    return _namespace_command(attachment, ["iptables", "-t", "nat", "-S"])


class NATTableReader:
    """Reads a Node's connection tracking table and its NAT rules."""

    def read(self, attachment: NodeAttachment) -> str:
        """List the table inside the Node and return it as text.

        Blocks, so the registry calls it from an executor.

        Args:
            attachment: The translating Node's name and Namespace Anchor pid.

        Returns:
            What ``conntrack -L`` printed.

        Raises:
            ValueError: conntrack is not installed, or could not list the
                table.
        """
        return self._run(
            build_nat_table_command(attachment), attachment, "NAT Table",
            "conntrack is not installed on this host; install conntrack-tools "
            "(the 'conntrack' package) to read NAT Tables",
        )

    def read_rules(self, attachment: NodeAttachment) -> str:
        """List the NAT rules inside the Node and return them as text.

        Blocks, so the registry calls it from an executor.

        Args:
            attachment: The translating Node's name and Namespace Anchor pid.

        Returns:
            What ``iptables -t nat -S`` printed.

        Raises:
            ValueError: iptables is not installed, or could not list the
                rules.
        """
        return self._run(
            build_nat_rules_command(attachment), attachment, "NAT rules",
            "iptables is not installed on this host; install iptables to read NAT rules",
        )

    def _run(self, argv: List[str], attachment: NodeAttachment, what: str, missing: str) -> str:
        """Run one listing and return what it printed.

        Raises:
            ValueError: The tool is not installed, did not finish in time, or
                failed; ``missing`` is the message when it is not installed.
        """
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=READ_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"Reading {attachment.node}'s {what} timed out") from exc
        if result.returncode == 0:
            return result.stdout
        if result.returncode == 127 or "No such file" in result.stderr:
            raise ValueError(missing)
        reason = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "no reason given"
        raise ValueError(f"Could not read {attachment.node}'s {what}: {reason}")
