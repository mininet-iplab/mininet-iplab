"""SpeakerRouteService: announce and withdraw routes on a running Speaker.

Shared by the CLI (``announce``, ``withdraw``) and the Web API
(``POST /speakers/{node}/announce`` and ``…/withdraw``), the way
:class:`mniplab.exabgp.query.SpeakerQueryService` is shared for the read.
Nothing is stored: ExaBGP's memory is the only record,
and the panel re-reads it on its next poll.

Every write is one command per Neighbor — ``neighbor <ip> announce route
…``, ``neighbor <ip> withdraw route <prefix>``, ``neighbor <ip> clear
adj-rib out`` — because ExaBGP's bare ``announce`` answers ``done`` and
silently skips a Neighbor that lacks the route's family.
"All Neighbors" is expanded here into the family-matching ones, and a
route's family is checked against its Neighbor before anything is written:
ExaBGP accepts the cross-family route and puts the session into a permanent
flap.
"""

from __future__ import annotations

import ipaddress
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple, Union

from mniplab.exabgp.control_client import SpeakerControlClient
from mniplab.exabgp.grammar import build_generate, build_route, parse_prefix, route_command
from mniplab.exabgp.models import BGPRoute
from mniplab.exabgp.query import SpeakerQueryService
from mniplab.exabgp.utils import make_v4_peer_routes, make_v6_peer_routes


#: A parsed prefix of either family.
Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


class SpeakerRouteService:
    """Route service for driving a running Speaker."""

    @classmethod
    def announce(
        cls, net: Any, node_name: str, route: BGPRoute, neighbor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Announce a route to one Neighbor, or to every Neighbor of its family.

        Args:
            net: The Lab.
            node_name: The Speaker's name.
            route: The route; validated here whoever built it.
            neighbor: The Neighbor's address, or None for all.

        Returns:
            Dict with ``node``, ``route`` (the route as announced) and
            ``neighbors`` (the addresses written to, in order).

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
            ValueError: The route does not validate, the Neighbor is unknown,
                or it does not carry the prefix's family; the message names
                the field and both families.
            ExaBGPControlError: The daemon rejected the announce or did not
                answer; the message names the command.
        """
        route = build_route(**asdict(route))
        client, targets = cls._targets(net, node_name, parse_prefix(route.prefix), neighbor)
        for address in targets:
            _announce_to(client, address, route)
        return {"node": node_name, "route": asdict(route), "neighbors": targets}

    @classmethod
    def withdraw(
        cls, net: Any, node_name: str, prefix: str, neighbor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Withdraw a prefix from one Neighbor, or from every Neighbor of its family.

        A withdraw needs only the prefix; a prefix the Neighbor was never
        sent is answered ``done`` by ExaBGP and reported the same way here.

        Args:
            net: The Lab.
            node_name: The Speaker's name.
            prefix: The prefix with its length.
            neighbor: The Neighbor's address, or None for all.

        Returns:
            Dict with ``node``, ``prefix`` and ``neighbors`` written to.

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
            ValueError: The prefix does not parse, the Neighbor is unknown,
                or it does not carry the prefix's family.
            ExaBGPControlError: The daemon rejected the withdraw or did not
                answer.
        """
        network = parse_prefix(prefix)
        client, targets = cls._targets(net, node_name, network, neighbor)
        for address in targets:
            client.execute(f"neighbor {address} withdraw route {network}")
        return {"node": node_name, "prefix": str(network), "neighbors": targets}

    @classmethod
    def withdraw_all(
        cls, net: Any, node_name: str, neighbor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Withdraw everything announced to one Neighbor, or to every Neighbor.

        This is ExaBGP's ``clear adj-rib out``: what the Lab Example shipped
        goes with what was announced since.

        Args:
            net: The Lab.
            node_name: The Speaker's name.
            neighbor: The Neighbor's address, or None for all.

        Returns:
            Dict with ``node``, ``all`` (True) and ``neighbors`` cleared.

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
            ValueError: The Neighbor is unknown.
            ExaBGPControlError: The daemon rejected the clear or did not answer.
        """
        client, targets = cls._targets(net, node_name, None, neighbor)
        for address in targets:
            client.execute(f"neighbor {address} clear adj-rib out")
        return {"node": node_name, "all": True, "neighbors": targets}

    @classmethod
    def generate(
        cls, net: Any, node_name: str, neighbor: Optional[str], count: Union[int, str],
        sub_prefixes: Union[int, str, None] = None,
        prefix_seed: Union[int, str, None] = None,
        as_path_seed: Union[int, str, None] = None,
    ) -> Dict[str, Any]:
        """Announce a burst of random routes to one Neighbor.

        The routes are the kind the Lab Examples ship — the per-family
        route generator with the Neighbor's local AS as origin, its local
        address as next-hop and its family — announced one by one through
        the same validated path as :meth:`announce`. No burst is
        tracked: the list returned is what a Learner withdraws by hand, and
        :meth:`withdraw_all` is the clear.

        Args:
            net: The Lab.
            node_name: The Speaker's name.
            neighbor: The Neighbor's address; None is accepted only when the
                Speaker has exactly one Neighbor.
            count: Base prefixes to announce.
            sub_prefixes: Sub-prefixes per base prefix, for longest-match
                lessons, or None.
            prefix_seed: Seed for the prefixes, so a burst can be regenerated.
            as_path_seed: Seed for the AS-PATHs.

        Returns:
            Dict with ``node``, ``neighbor`` and ``announced`` (the routes,
            in the order they were announced).

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
            ValueError: An option is out of range, the Neighbor is unknown,
                or none was named and the Speaker has several.
            ExaBGPControlError: The daemon rejected an announce or did not
                answer; the message names the command.
        """
        options = build_generate(count, sub_prefixes, prefix_seed, as_path_seed)
        _node, client = SpeakerQueryService.get_client(net, node_name)
        neighbors = SpeakerQueryService.list_neighbors(client, node_name)
        chosen = cls._choose_neighbor(node_name, neighbors, neighbor)
        make_routes = make_v6_peer_routes if chosen["family"] == "ipv6" else make_v4_peer_routes
        routes = make_routes(
            options.count, chosen["local_as"], chosen["local_address"],
            prefix_seed=options.prefix_seed, as_path_seed=options.as_path_seed,
            num_sub_prefixes=options.sub_prefixes,
        )
        announced = []
        for generated in routes:
            route = build_route(**asdict(generated))
            _announce_to(client, chosen["address"], route)
            announced.append(asdict(route))
        return {"node": node_name, "neighbor": chosen["address"], "announced": announced}

    @staticmethod
    def _choose_neighbor(
        node_name: str, neighbors: List[Dict[str, Any]], neighbor: Optional[str]
    ) -> Dict[str, Any]:
        """Pick the Neighbor a generate goes to.

        Args:
            node_name: The Speaker's name, for messages.
            neighbors: The Speaker's Neighbors as ExaBGP lists them.
            neighbor: The address asked for, or None.

        Returns:
            The Neighbor's entry.

        Raises:
            ValueError: The address is unknown, or none was given and the
                Speaker has several Neighbors; the message names them.
        """
        known = ", ".join(n["address"] for n in neighbors) or "none"
        if neighbor is None:
            if len(neighbors) == 1:
                return neighbors[0]
            raise ValueError(f"generate needs a Neighbor on {node_name}: choose one of {known}")
        for entry in neighbors:
            if entry["address"] == str(neighbor).strip():
                return entry
        raise ValueError(f"{node_name} has no Neighbor {neighbor} (its Neighbors: {known})")

    @staticmethod
    def _targets(
        net: Any, node_name: str, network: Optional[Network], neighbor: Optional[str]
    ) -> Tuple[SpeakerControlClient, List[str]]:
        """Resolve the Speaker's client and the Neighbors a write goes to.

        Args:
            net: The Lab.
            node_name: The Speaker's name.
            network: The prefix whose family the Neighbors must carry, or
                None when the write is family-neutral.
            neighbor: One Neighbor's address, or None for all.

        Returns:
            Tuple of (client, peer addresses).

        Raises:
            SpeakerNotFoundError: The Node does not exist or is not a Speaker.
            ValueError: The Neighbor is unknown, or lacks the prefix's family.
        """
        _node, client = SpeakerQueryService.get_client(net, node_name)
        neighbors = SpeakerQueryService.list_neighbors(client, node_name)
        family = None if network is None else f"ipv{network.version}"
        if neighbor is not None:
            chosen = [n for n in neighbors if n["address"] == str(neighbor).strip()]
            if not chosen:
                known = ", ".join(n["address"] for n in neighbors) or "none"
                raise ValueError(f"{node_name} has no Neighbor {neighbor} (its Neighbors: {known})")
            if family is not None and chosen[0]["family"] != family:
                raise ValueError(
                    f"neighbor {neighbor} carries {_family_label(chosen[0]['family'])} but "
                    f"prefix {network} is {_family_label(family)}"
                )
            return client, [chosen[0]["address"]]
        targets: List[str] = [
            n["address"] for n in neighbors if family is None or n["family"] == family
        ]
        if not targets:
            raise ValueError(
                f"{node_name} has no Neighbor carrying {_family_label(family)} for prefix {network}"
            )
        return client, targets



def _announce_to(client: SpeakerControlClient, address: str, route: BGPRoute) -> None:
    """Write one validated route to one Neighbor — the write path every announce shares.

    Args:
        client: The Speaker's control client.
        address: The Neighbor's address.
        route: The route, already validated by ``build_route``.

    Raises:
        ExaBGPControlError: The daemon rejected the announce or did not answer.
    """
    client.execute(f"neighbor {address} {route_command(route)}")


def _family_label(family: Optional[str]) -> str:
    """``'ipv4'`` → ``'IPv4'``, the way the family is written everywhere else."""
    return str(family).replace("ipv", "IPv") if family else "an unknown family"
