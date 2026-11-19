"""The web server's half of a Packet Capture's PDML stream.

tshark's ``-T pdml -l`` flushes each ``</packet>`` as the packet is
dissected, so the stream is cut into whole packets here and forwarded
untouched: the browser parses the field tree, because parsing it here
would cost fifty times what splitting does and be paid in the process
every Lab shares.  The one thing read out of a packet on this side is its
summary row — cheap, by pattern rather than by XML parse — which is all
the pane gets while the rate guard is on.

PDML carries no Info column, and tshark cannot add one without flattening
the field tree, so a summary's description is read out of the last protocol
layer by one rule for every protocol: the message it carries — "Solicit",
"Echo (ping) request", "UPDATE Message" — as Wireshark's Info column leads
with, since the Protocol column already names the protocol.  A protocol
with no message to name is described by its layer's label.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

PACKET_OPEN = b"<packet>"
PACKET_CLOSE = b"</packet>"

# The protocol elements PDML emits that are bookkeeping rather than a layer
# of the packet: tshark wraps stray fields in the first, and names its own
# pseudo-protocols (malformed, expert info) under the second.
_NOT_A_LAYER = ("fake-field-wrapper",)
_PSEUDO_PREFIX = "_ws."

_PROTO_TAG = re.compile(r"<proto\s([^>]*)>")
_NAME_ATTR = re.compile(r'\bname="([^"]*)"')
_SHOWNAME_ATTR = re.compile(r'\bshowname="([^"]*)"')

# The address fields a row's ends are read from, most specific first: a
# packet with an IP header is shown by its IP ends, one without (ARP, a bare
# frame) by its link-layer ends — Wireshark's own packet list does the same.
_SOURCE_FIELDS = ("ip.src", "ipv6.src", "eth.src")
_DESTINATION_FIELDS = ("ip.dst", "ipv6.dst", "eth.dst")

# The field that names a layer's message, for the protocols the Lab
# Examples teach.  A layer not listed here is named by the parenthesised
# ending of its label when it has one — "Domain Name System (query)" — and
# by the whole label when it has not.
_MESSAGE_FIELDS = {
    "arp": "arp.opcode",
    "bgp": "bgp.type",
    "dhcp": "dhcp.option.dhcp",
    "dhcpv6": "dhcpv6.msgtype",
    "icmp": "icmp.type",
    "icmpv6": "icmpv6.type",
    "ospf": "ospf.msg",
}

# A named value as tshark labels it: the name before its code, or after it.
_NAME_THEN_CODE = re.compile(r"(.+) \(\d+\)")
_CODE_THEN_NAME = re.compile(r"\d+ \((.+)\)")
_LABEL_ENDING = re.compile(r".+ \(([^()]+)\)")


class PacketSplitter:
    """Cut tshark's PDML stream into whole ``<packet>`` elements.

    Reads from a pipe land wherever they land — mid-tag, mid-character —
    so bytes are held until a packet closes, and a packet is released the
    moment its ``</packet>`` arrives rather than when the next one does.
    """

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> List[str]:
        """Take one read from the stream and return the packets it completed.

        Args:
            data: Bytes as read from tshark's stdout.

        Returns:
            Every packet closed by this read, each from ``<packet>`` to
            ``</packet>`` inclusive, decoded as UTF-8.  The document's
            preamble and anything between packets is dropped.
        """
        self._buffer += data
        packets = []
        while True:
            end = self._buffer.find(PACKET_CLOSE)
            if end < 0:
                break
            end += len(PACKET_CLOSE)
            chunk = self._buffer[:end]
            self._buffer = self._buffer[end:]
            start = chunk.find(PACKET_OPEN)
            if start >= 0:
                packets.append(chunk[start:].decode("utf-8", errors="replace"))
        return packets


def summarize_packet(packet: str) -> Dict[str, Any]:
    """Read one packet's summary row out of its PDML, without parsing it.

    Args:
        packet: One ``<packet>`` element as :class:`PacketSplitter` yields it.

    Returns:
        ``number`` and ``length`` (ints), ``time`` (capture time as epoch
        seconds, a float), ``source``, ``destination``, ``protocol`` (the
        top layer's name, upper-cased) and ``description`` (the message
        that layer carries, or its label).
    """
    protocol, description = _top_layer(packet)
    return {
        "number": _as_int(_field(packet, "num")),
        "time": _as_float(_field(packet, "timestamp", attribute="value")),
        "length": _as_int(_field(packet, "len")),
        "source": _first_field(packet, _SOURCE_FIELDS),
        "destination": _first_field(packet, _DESTINATION_FIELDS),
        "protocol": protocol,
        "description": description,
    }


def _top_layer(packet: str) -> "tuple[str, str]":
    """Return the name of the packet's last real protocol layer and its message."""
    for tag in reversed(list(_PROTO_TAG.finditer(packet))):
        name_match = _NAME_ATTR.search(tag.group(1))
        if name_match is None:
            continue
        name = name_match.group(1)
        if name in _NOT_A_LAYER or name.startswith(_PSEUDO_PREFIX):
            continue
        label_match = _SHOWNAME_ATTR.search(tag.group(1))
        label = html.unescape(label_match.group(1)) if label_match else name
        return name.upper(), _message(packet[tag.end():], name, label)
    return "", ""


def _message(layer: str, name: str, label: str) -> str:
    """Name the message a layer carries, by its message field or its label.

    Args:
        layer: The packet's text from the layer's opening tag on; the layer
            is the last, so every field that follows is its own.
        name: The layer's protocol name, e.g. ``dhcpv6``.
        label: The layer's own label, e.g. ``Domain Name System (query)``.
    """
    field = _MESSAGE_FIELDS.get(name)
    shown = _field(layer, field, attribute="showname") if field else None
    if shown:
        value = shown.split(": ", 1)[-1]
        named = _CODE_THEN_NAME.fullmatch(value) or _NAME_THEN_CODE.fullmatch(value)
        message = named.group(1) if named else value
    else:
        ending = _LABEL_ENDING.fullmatch(label)
        message = ending.group(1) if ending else label
    return message[:1].upper() + message[1:]


def _field(packet: str, name: str, attribute: str = "show") -> Optional[str]:
    """Return one attribute of the first field with this name, or None."""
    pattern = r'<field name="%s"[^>]*?\s%s="([^"]*)"' % (re.escape(name), attribute)
    match = re.search(pattern, packet)
    return html.unescape(match.group(1)) if match else None


def _first_field(packet: str, names: "tuple[str, ...]") -> str:
    """Return the shown value of the first of these fields the packet has."""
    for name in names:
        value = _field(packet, name)
        if value:
            return value
    return ""


def _as_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _as_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
