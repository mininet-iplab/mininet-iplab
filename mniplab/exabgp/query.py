"""SpeakerQueryService: read a Speaker's Neighbors and both of its RIBs.

Shared by the CLI (``show_speaker``) and the Web API (``GET /speakers/{node}``),
the way :class:`mniplab.dns.query.DNSQueryService` is shared for a DNS
Service. A Speaker is its own Node kind rather than a Service on a Node,
so the lookup asks the Node what kind it is instead of searching its
Services.

One read is three commands on the Speaker's pipe: the JSON Neighbor listing
— every configured Neighbor, up or down, with its state and uptime — and
the outgoing and incoming RIBs in extensive form. A RIB line is parsed on
whitespace, never on column offsets, because an IPv6 peer overflows the
summary column.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from mniplab.errors import ExaBGPControlError
from mniplab.exabgp.control_client import SpeakerControlClient
from mniplab.exabgp.models import BGPRoute
from mniplab.exabgp.node import ExaBGPNode

SHOW_NEIGHBORS = "show neighbor json"
SHOW_ADJ_RIB_OUT = "show adj-rib out extensive"
SHOW_ADJ_RIB_IN = "show adj-rib in extensive"

#: The brackets ExaBGP groups a multi-token attribute value in, opener → closer.
_BRACKETS = {"(": ")", "[": "]", "{": "}"}

#: ExaBGP's attribute word → the ``BGPRoute`` field it fills, and how its
#: value tokens become that field. The same words render the route back
#: (the template, ``show_speaker``), so one table names them.
ROUTE_ATTRIBUTES: Dict[str, Tuple[str, Callable[[List[str]], Any]]] = {
    "next-hop": ("next_hop", lambda values: values[0] if values else None),
    "as-path": ("as_path", lambda values: [int(v) for v in values if v.isdigit()]),
    "community": ("community", list),
    "large-community": ("large_community", list),
    "med": ("med", lambda values: int(values[0]) if values and values[0].isdigit() else None),
}


class SpeakerNotFoundError(ValueError):
    """Raised when a Node named as a Speaker is absent or is not one.

    A ``ValueError`` like every family's not-found error, so the CLI prints
    it the same way; its own class so the Web API can answer 404.
    """


class SpeakerQueryService:
    """Query service for reading a running Speaker."""

    @staticmethod
    def find_speaker(net: Any, node_name: str) -> Any:
        """Resolve a Node and check that it is a Speaker.

        The kind is read from the Node's display type, the mark the Web UI
        already recognises a Speaker by, so a fake Lab can stand in for a
        real one in the contract tests.

        Args:
            net: The Lab.
            node_name: The Speaker's name.

        Returns:
            The Node.

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
        """
        # Mininet's get raises KeyError for a name the Lab lacks; the
        # caller is promised the not-found error either way.
        try:
            node = net.get(node_name)
        except KeyError:
            node = None
        if node is None:
            raise SpeakerNotFoundError(f"Node not found: {node_name}")
        if getattr(node, "display_type", None) != ExaBGPNode.display_type:
            raise SpeakerNotFoundError(f"{node_name} is not a Speaker")
        return node

    @classmethod
    def get_client(cls, net: Any, node_name: str) -> Tuple[Any, SpeakerControlClient]:
        """Resolve a Speaker and its control client.

        The client is the Speaker's own, held for the Lab's lifetime, so
        every surface shares the one lock that serialises its pipe.

        Args:
            net: The Lab.
            node_name: The Speaker's name.

        Returns:
            Tuple of (node, client).

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
        """
        node = cls.find_speaker(net, node_name)
        return node, node.control_client()

    @staticmethod
    def list_neighbors(client: SpeakerControlClient, node_name: str) -> List[Dict[str, Any]]:
        """Read every configured Neighbor of a Speaker, up or down.

        One pipe round trip, shared by a read of the whole Speaker and by a
        write that must know which Neighbors exist and which family each
        carries.

        Args:
            client: The Speaker's control client.
            node_name: The Speaker's name, for messages.

        Returns:
            One entry per Neighbor, as :func:`_neighbor_entry` shapes it.

        Raises:
            ExaBGPControlError: The daemon did not answer, answered
                ``error``, or answered something other than JSON.
        """
        listing = "".join(client.execute(SHOW_NEIGHBORS)) or "[]"
        try:
            return [_neighbor_entry(entry) for entry in json.loads(listing)]
        except ValueError as exc:
            raise ExaBGPControlError(
                f"ExaBGP on {node_name} answered '{SHOW_NEIGHBORS}' with something "
                f"other than JSON: {exc}",
                command=SHOW_NEIGHBORS,
            ) from exc

    @classmethod
    def get_speaker(cls, net: Any, node_name: str) -> Dict[str, Any]:
        """Return a Speaker's Neighbors with what each was sent and sent back.

        Args:
            net: The Lab.
            node_name: The Speaker's name.

        Returns:
            Dict with ``node``, ``local_as`` and ``neighbors``; each Neighbor
            carries ``address``, ``peer_as``, ``local_address``, ``local_as``,
            ``family``, ``state``, ``up``, ``uptime`` (seconds, or None when
            down), ``announced`` and ``received``. A route carries
            ``prefix``, ``next_hop``, ``as_path``, ``community``,
            ``large_community`` and ``med``.

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
            ExaBGPControlError: The daemon did not answer, or answered
                ``error``; the message names the reason.
        """
        node, client = cls.get_client(net, node_name)
        neighbors = cls.list_neighbors(client, node_name)
        announced = _routes_by_neighbor(client.execute(SHOW_ADJ_RIB_OUT))
        received = _routes_by_neighbor(client.execute(SHOW_ADJ_RIB_IN))
        for neighbor in neighbors:
            neighbor["announced"] = [asdict(r) for r in announced.get(neighbor["address"], [])]
            neighbor["received"] = [asdict(r) for r in received.get(neighbor["address"], [])]
        # A Speaker added without local_as still has one: its Neighbors' own.
        local_as = node.params.get("local_as")
        if local_as is None and neighbors:
            local_as = neighbors[0]["local_as"]
        return {"node": node_name, "local_as": local_as, "neighbors": neighbors}


def _neighbor_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Shape one element of ExaBGP's ``show neighbor json`` array.

    The family is read from the configured (local) side so a Neighbor that
    never opened a session — whose negotiated family list is empty — still
    says which family it carries.

    Args:
        entry: One Neighbor as the daemon reported it.

    Returns:
        The Neighbor's identity, state and uptime.
    """
    local = entry.get("local", {})
    peer = entry.get("peer", {})
    up = entry.get("state") == "up"
    families = [name for name, on in local.get("families", {}).items() if on]
    family = families[0].split()[0] if families else None
    return {
        "address": peer.get("address"),
        "peer_as": peer.get("as"),
        "local_address": local.get("address"),
        "local_as": local.get("as"),
        "family": family,
        "state": str(entry.get("fsm", "")).lower(),
        "up": up,
        "uptime": entry.get("duration") if up else None,
    }


def _routes_by_neighbor(lines: List[str]) -> Dict[str, List[BGPRoute]]:
    """Group the routes of an extensive RIB listing by Neighbor address.

    Args:
        lines: The listing, one route per line.

    Returns:
        Neighbor address → its routes, in listing order.
    """
    grouped: Dict[str, List[BGPRoute]] = {}
    for line in lines:
        parsed = _parse_rib_line(line)
        if parsed is not None:
            address, route = parsed
            grouped.setdefault(address, []).append(route)
    return grouped


def _parse_rib_line(line: str) -> Optional[Tuple[str, BGPRoute]]:
    """Parse one line of ``show adj-rib out|in extensive``.

    The line is ExaBGP's ``Neighbor.name()`` — ``neighbor <ip> local-ip …
    family-allowed in-open`` — then the family, the prefix, and attributes
    as ``<word> <value>`` pairs where a value is one token or a bracketed
    group (``as-path ( 65001 65002 )``, ``community [ a b ]``); a single
    community prints bare, and ``atomic-aggregate`` has no value at all.

    Args:
        line: The line, terminator excluded.

    Returns:
        The Neighbor's address and the route — the same ``BGPRoute`` a Lab
        declares, so declared and observed routes share one shape — or None
        for a line that is not a route (ExaBGP writes nothing else here,
        but the listing is not ours to trust).
    """
    tokens = line.split()
    if len(tokens) < 2 or tokens[0] != "neighbor" or "family-allowed" not in tokens:
        return None
    address = tokens[1]
    # After 'family-allowed <session>' come the AFI, the SAFI and the prefix.
    position = tokens.index("family-allowed") + 4
    if position >= len(tokens):
        return None
    fields: Dict[str, Any] = {"prefix": tokens[position], "next_hop": None}
    for word, values in _attribute_pairs(tokens[position + 1:]):
        if word in ROUTE_ATTRIBUTES:
            field, convert = ROUTE_ATTRIBUTES[word]
            fields[field] = convert(values)
    return address, BGPRoute(**fields)


def _attribute_pairs(tokens: List[str]) -> Iterator[Tuple[str, List[str]]]:
    """Yield ``(word, values)`` for each attribute on a RIB line.

    Args:
        tokens: The tokens after the prefix.

    Yields:
        The attribute's word and its value tokens, brackets stripped; every
        group of a multi-segment AS-PATH lands in the one list.
    """
    index = 0
    while index < len(tokens):
        word = tokens[index]
        index += 1
        values: List[str] = []
        if index < len(tokens) and tokens[index] in _BRACKETS:
            while index < len(tokens) and tokens[index] in _BRACKETS:
                closer = _BRACKETS[tokens[index]]
                index += 1
                while index < len(tokens) and tokens[index] != closer:
                    values.append(tokens[index])
                    index += 1
                index += 1
        elif word != "atomic-aggregate" and index < len(tokens):
            values.append(tokens[index])
            index += 1
        yield word, values
