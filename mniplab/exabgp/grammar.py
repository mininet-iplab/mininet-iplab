"""The one route grammar every Speaker surface accepts.

A Learner announces a route in ExaBGP's own words on the CLI —
``<prefix> next-hop <ip> [as-path …] [community …] [large-community …]
[med N]`` — and as the same fields in a REST body; :func:`parse_route` and
:func:`build_route` turn both into one :class:`BGPRoute`, refusing the same
things and naming the offending word or field, so CLI, REST and the panel
accept exactly the same input. :func:`route_command`
renders a route back into the words the pipe takes. A burst of generated
routes is asked for the same way — ``generate <count> [sub-prefixes N]
[prefix-seed S] [as-path-seed S]`` on the CLI, the same fields over REST —
through :func:`parse_generate` and :func:`build_generate`.

Validation is ours and comes before any write: the prefix
and next-hop must parse and be the same family, because ExaBGP accepts a
cross-family route and puts the session into a permanent flap.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from mniplab.exabgp.models import BGPRoute
from mniplab.exabgp.query import ROUTE_ATTRIBUTES

#: The community names ExaBGP's parser knows, as a Learner may type them.
WELL_KNOWN_COMMUNITIES = ("no-export", "no-advertise", "blackhole", "no-peer")

#: The characters ExaBGP groups a multi-token value in, stripped off a word
#: so ``[ 65001 65002 ]`` and ``[65001 65002]`` read the same.
_BRACKET_CHARS = "[]{}()"

#: The route fields that hold a list of values.
_LIST_FIELDS = {"as_path", "community", "large_community"}

#: The most routes one ``generate`` may announce: each is a pipe round trip,
#: and a burst past this is a Lab of its own, not a lesson's lever.
MAX_GENERATE_COUNT = 1000

#: ``generate``'s option words → the ``GenerateOptions`` field each fills.
GENERATE_OPTIONS = {
    "sub-prefixes": "sub_prefixes",
    "prefix-seed": "prefix_seed",
    "as-path-seed": "as_path_seed",
}

_MAX_ASN = 0xFFFFFFFF
_MAX_COMMUNITY_PART = 0xFFFF
_MAX_MED = 0xFFFFFFFF
_MAX_SEED = 2**63 - 1


@dataclass(frozen=True)
class GenerateOptions:
    """What a ``generate`` asks for, validated.

    Attributes:
        count: Base prefixes to announce.
        sub_prefixes: Sub-prefixes per base prefix, or None for none.
        prefix_seed: Seed for the prefixes, or None for a fresh draw.
        as_path_seed: Seed for the AS-PATHs, or None for a fresh draw.
    """
    count: int
    sub_prefixes: Optional[int] = None
    prefix_seed: Optional[int] = None
    as_path_seed: Optional[int] = None


def parse_route(tokens: Sequence[str]) -> BGPRoute:
    """Parse ``<prefix> next-hop <ip> [attribute value…]…`` into a route.

    Attribute order is free, as it is for ExaBGP. A multi-token value may be
    bracketed the way ExaBGP writes it (``as-path [ 65001 65002 ]``), with
    the brackets glued to the values, or given bare; it runs until the next
    attribute word.

    Args:
        tokens: The line's words, split on whitespace.

    Returns:
        The route, validated.

    Raises:
        ValueError: The prefix is missing; a word is not a supported
            attribute (named); ``next-hop`` is missing; an attribute has no
            value or a malformed one (named).
    """
    words = [t.strip(_BRACKET_CHARS) for t in tokens]
    words = [w for w in words if w]
    if not words:
        raise ValueError("a prefix is required: <prefix> next-hop <ip> [attributes…]")
    fields: Dict[str, Any] = {"prefix": words[0]}
    index = 1
    while index < len(words):
        word = words[index]
        if word not in ROUTE_ATTRIBUTES:
            raise ValueError(
                f"unknown attribute '{word}': supported are "
                + ", ".join(ROUTE_ATTRIBUTES)
            )
        field = ROUTE_ATTRIBUTES[word][0]
        if field in fields:
            raise ValueError(f"'{word}' is given twice")
        index += 1
        values: List[str] = []
        # A list value runs until the next attribute word; a single value
        # is one token, so whatever follows it must be an attribute word.
        while index < len(words) and words[index] not in ROUTE_ATTRIBUTES:
            values.append(words[index])
            index += 1
            if field not in _LIST_FIELDS:
                break
        if not values:
            raise ValueError(f"'{word}' needs a value")
        fields[field] = values if field in _LIST_FIELDS else values[0]
    return build_route(**fields)


def build_route(
    prefix: Optional[str] = None,
    next_hop: Optional[str] = None,
    as_path: Optional[Iterable[Union[int, str]]] = None,
    community: Optional[Iterable[str]] = None,
    large_community: Optional[Iterable[str]] = None,
    med: Union[int, str, None] = None,
) -> BGPRoute:
    """Build a validated route from its fields, as a REST body carries them.

    Args:
        prefix: The prefix with its length, e.g. ``203.0.113.0/24``.
        next_hop: The next-hop address, the prefix's family.
        as_path: AS numbers, ints or their decimal spelling.
        community: ``<asn>:<value>`` communities or well-known names.
        large_community: ``<n>:<n>:<n>`` large communities.
        med: The multi-exit discriminator, an int or its decimal spelling.

    Returns:
        The route.

    Raises:
        ValueError: A field is missing or does not parse, or the next-hop
            is not the prefix's family; the message names the field and,
            for a family mismatch, both families.
    """
    if prefix is None or str(prefix).strip() == "":
        raise ValueError("prefix is required")
    network = parse_prefix(prefix)
    if next_hop is None or str(next_hop).strip() == "":
        raise ValueError("next-hop is required")
    hop = _parse_next_hop(next_hop)
    if hop.version != network.version:
        raise ValueError(
            f"next-hop {hop} is IPv{hop.version} but prefix {network} is "
            f"IPv{network.version}; a route's next-hop must be the prefix's family"
        )
    return BGPRoute(
        prefix=str(network),
        next_hop=str(hop),
        as_path=[_parse_asn(v) for v in _values(as_path)],
        community=[_parse_community(v) for v in _values(community)],
        large_community=[_parse_large_community(v) for v in _values(large_community)],
        med=None if med is None else _parse_med(med),
    )


def parse_generate(tokens: Sequence[str]) -> GenerateOptions:
    """Parse ``<count> [sub-prefixes N] [prefix-seed S] [as-path-seed S]``.

    Args:
        tokens: The words after ``generate``, split on whitespace.

    Returns:
        The options, validated.

    Raises:
        ValueError: The count is missing or not a number in range; a word
            is not a supported option (named); an option has no value or a
            malformed one (named).
    """
    if not tokens:
        raise ValueError("generate needs a count: generate <count> [sub-prefixes N] "
                         "[prefix-seed S] [as-path-seed S]")
    fields: Dict[str, Any] = {"count": tokens[0]}
    index = 1
    while index < len(tokens):
        word = tokens[index]
        if word not in GENERATE_OPTIONS:
            raise ValueError(
                f"unknown generate option '{word}': supported are " + ", ".join(GENERATE_OPTIONS)
            )
        field = GENERATE_OPTIONS[word]
        if field in fields:
            raise ValueError(f"'{word}' is given twice")
        if index + 1 >= len(tokens):
            raise ValueError(f"'{word}' needs a value")
        fields[field] = tokens[index + 1]
        index += 2
    return build_generate(**fields)


def build_generate(
    count: Union[int, str, None] = None,
    sub_prefixes: Union[int, str, None] = None,
    prefix_seed: Union[int, str, None] = None,
    as_path_seed: Union[int, str, None] = None,
) -> GenerateOptions:
    """Build validated generate options from their fields, as a REST body carries them.

    Args:
        count: Base prefixes to announce, 1 to ``MAX_GENERATE_COUNT``.
        sub_prefixes: Sub-prefixes per base prefix, at least 1, or None.
        prefix_seed: Seed for the prefixes, or None.
        as_path_seed: Seed for the AS-PATHs, or None.

    Returns:
        The options.

    Raises:
        ValueError: The count is missing, or a field is not a number in its
            range; the message names the field and the value.
    """
    if count is None or str(count).strip() == "":
        raise ValueError("count is required")
    return GenerateOptions(
        count=_parse_count("count", count, 1, MAX_GENERATE_COUNT),
        sub_prefixes=None if sub_prefixes is None else _parse_count(
            "sub-prefixes", sub_prefixes, 1, MAX_GENERATE_COUNT),
        prefix_seed=None if prefix_seed is None else _parse_count("prefix-seed", prefix_seed, 0, _MAX_SEED),
        as_path_seed=None if as_path_seed is None else _parse_count(
            "as-path-seed", as_path_seed, 0, _MAX_SEED),
    )


def route_command(route: BGPRoute) -> str:
    """Render a route as ExaBGP's ``announce route …`` words.

    Args:
        route: The route.

    Returns:
        ``announce route <prefix> next-hop <ip> [as-path [ … ]] …``; an
        attribute the route lacks is left out.
    """
    parts = ["announce", "route", route.prefix, "next-hop", str(route.next_hop)]
    for word, (field, _) in ROUTE_ATTRIBUTES.items():
        if field == "next_hop":
            continue
        value = getattr(route, field)
        if value is None or value == []:
            continue
        if isinstance(value, list):
            parts.append(f"{word} [ {' '.join(str(v) for v in value)} ]")
        else:
            parts.append(f"{word} {value}")
    return " ".join(parts)


def _values(values: Optional[Iterable[Any]]) -> List[Any]:
    return [] if values is None else list(values)


def parse_prefix(prefix: str) -> Union[ipaddress.IPv4Network, ipaddress.IPv6Network]:
    """Parse a prefix with its length, naming the field when it does not.

    Args:
        prefix: The prefix, e.g. ``203.0.113.0/24``.

    Returns:
        The network.

    Raises:
        ValueError: The length is missing or the text is not a network.
    """
    text = str(prefix).strip()
    if "/" not in text:
        raise ValueError(f"prefix '{text}' needs a length, e.g. {text}/24")
    try:
        return ipaddress.ip_network(text)
    except ValueError as exc:
        raise ValueError(f"prefix '{text}' is not a network: {exc}") from exc


def _parse_next_hop(next_hop: str) -> Union[ipaddress.IPv4Address, ipaddress.IPv6Address]:
    text = str(next_hop).strip()
    try:
        return ipaddress.ip_address(text)
    except ValueError as exc:
        raise ValueError(f"next-hop '{text}' is not an IP address") from exc


def _parse_asn(value: Union[int, str]) -> int:
    text = str(value).strip()
    if not text.isdigit() or int(text) > _MAX_ASN:
        raise ValueError(f"as-path '{text}' is not an AS number")
    return int(text)


def _parse_community(value: str) -> str:
    text = str(value).strip()
    if text.lower() in WELL_KNOWN_COMMUNITIES:
        return text.lower()
    parts = text.split(":")
    if len(parts) == 2 and all(p.isdigit() and int(p) <= _MAX_COMMUNITY_PART for p in parts):
        return text
    raise ValueError(
        f"community '{text}' is not <asn>:<value> or a well-known name "
        f"({', '.join(WELL_KNOWN_COMMUNITIES)})"
    )


def _parse_large_community(value: str) -> str:
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) == 3 and all(p.isdigit() and int(p) <= _MAX_ASN for p in parts):
        return text
    raise ValueError(f"large-community '{text}' is not <asn>:<n>:<n>")


def _parse_count(field: str, value: Union[int, str], low: int, high: int) -> int:
    text = str(value).strip()
    if not text.isdigit() or not low <= int(text) <= high:
        raise ValueError(f"{field} '{text}' is not a number between {low} and {high}")
    return int(text)


def _parse_med(value: Union[int, str]) -> int:
    return _parse_count("med", value, 0, _MAX_MED)
