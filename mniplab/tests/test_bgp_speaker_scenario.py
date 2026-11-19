"""Mechanism tests for reading and driving a Speaker, on the BGP Test Lab.

Each test drives :class:`SpeakerQueryService` and :class:`SpeakerRouteService`
the way the CLI and the Web API do and asserts what a Learner observes: the
Router's BGP table and the Speaker's own report of its Neighbors and RIBs.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any, Dict, List, Tuple

import pytest

from mniplab.exabgp.models import BGPRoute
from mniplab.exabgp.query import SpeakerQueryService
from mniplab.exabgp.routes import SpeakerRouteService
from mniplab.tests.fakes import cli_output
from mniplab.tests.labs import (
    SPEAKER_R1_NETWORK,
    SPEAKER_V4_ROUTE,
    SPEAKER_V6_ROUTE,
    LabCase,
    build_bgp_test_lab,
)
from mniplab.utils import wait_until

#: How long a session may take to establish, or to be noticed gone.
SESSION_TIMEOUT = 40.0


def neighbors_by_address(net: Any) -> Dict[str, Dict[str, Any]]:
    """Read ``sp1`` and key its Neighbors by peer address.

    Args:
        net: The started BGP Test Lab.

    Returns:
        Peer address → the Neighbor as ``get_speaker`` reported it.
    """
    return {n["address"]: n for n in SpeakerQueryService.get_speaker(net, "sp1")["neighbors"]}


def split_bases_and_sub_prefixes(prefixes: List[str]) -> Tuple[List[Any], List[Any]]:
    """Split a generated burst into its base prefixes and the sub-prefixes inside them.

    A base is a prefix no other prefix of the burst contains; every other
    prefix must lie inside exactly one base, which is what a
    longest-prefix-match lesson needs.

    Args:
        prefixes: The burst's prefixes.

    Returns:
        ``(bases, sub_prefixes)`` as networks.

    Raises:
        AssertionError: A prefix lies inside no base, or inside more than one.
    """
    networks = [ipaddress.ip_network(p) for p in prefixes]
    bases = [n for n in networks if not any(n != o and n.subnet_of(o) for o in networks)]  # type: ignore[arg-type]
    subs = [n for n in networks if n not in bases]
    for sub in subs:
        assert sum(1 for b in bases if sub.subnet_of(b)) == 1, f"{sub} is not inside exactly one base"  # type: ignore[arg-type,misc]
    return bases, subs


def all_established(net: Any) -> bool:
    """Whether every Neighbor of ``sp1`` reads as up.

    Args:
        net: The started BGP Test Lab.

    Returns:
        True once all three sessions are Established.
    """
    return all(n["up"] for n in neighbors_by_address(net).values())


class SpeakerScenarioTests(LabCase):
    """End-to-end scenario exercising reading, driving, and recovering a Speaker."""

    def test_speaker_lifecycle_routing_generation_and_recovery(self) -> None:
        net = self.lab()
        nodes = build_bgp_test_lab(net)
        self.start(net)
        self.assertTrue(
            wait_until(lambda: all_established(net), SESSION_TIMEOUT),
            "every session should establish",
        )
        r1 = nodes["r1"]

        # ==============================================================
        # 1. Peering & Route Exchange Verification
        # ==============================================================
        self.assertTrue(
            wait_until(
                lambda: SPEAKER_V4_ROUTE in r1.cmd('vtysh -c "show bgp ipv4 unicast"'),
                10.0,
            )
        )
        self.assertTrue(
            wait_until(
                lambda: SPEAKER_V6_ROUTE in r1.cmd('vtysh -c "show bgp ipv6 unicast"'),
                10.0,
            )
        )
        self.assertTrue(
            wait_until(
                lambda: SPEAKER_R1_NETWORK in {
                    r["prefix"]
                    for n in SpeakerQueryService.get_speaker(net, "sp1")["neighbors"]
                    if n["address"] == "10.0.1.1"
                    for r in n["received"]
                },
                10.0,
            ),
            "r1 network should be received by sp1",
        )

        speaker = SpeakerQueryService.get_speaker(net, "sp1")
        self.assertEqual((speaker["node"], speaker["local_as"]), ("sp1", 65001))
        neighbors = {n["address"]: n for n in speaker["neighbors"]}
        self.assertEqual(set(neighbors), {"10.0.1.1", "2001:db8:1::1", "10.0.2.1"})

        for address, family in (("10.0.1.1", "ipv4"), ("2001:db8:1::1", "ipv6")):
            neighbor = neighbors[address]
            self.assertEqual(neighbor["state"], "established", neighbor)
            self.assertGreaterEqual(neighbor["uptime"], 0)
            self.assertEqual(
                (neighbor["peer_as"], neighbor["local_as"], neighbor["family"]),
                (65000, 65001, family),
            )

        self.assertEqual(
            [r["prefix"] for r in neighbors["10.0.1.1"]["announced"]],
            [SPEAKER_V4_ROUTE],
        )
        self.assertEqual(
            [r["prefix"] for r in neighbors["2001:db8:1::1"]["announced"]],
            [SPEAKER_V6_ROUTE],
        )
        self.assertEqual(neighbors["10.0.1.1"]["announced"][0]["as_path"], [65001])

        received = {r["prefix"]: r for r in neighbors["10.0.1.1"]["received"]}
        self.assertIn(SPEAKER_R1_NETWORK, received)
        self.assertEqual(received[SPEAKER_R1_NETWORK]["as_path"], [65000])
        self.assertEqual(received[SPEAKER_R1_NETWORK]["next_hop"], "10.0.1.1")

        printed = cli_output("show_speaker", "sp1", net=net)
        self.assertIn("Speaker sp1 (AS 65001)", printed)
        self.assertIn("established", printed)
        self.assertIn("Received from 10.0.1.1", printed)
        self.assertIn(SPEAKER_R1_NETWORK, printed)

        # ==============================================================
        # 2. Runtime Writes (Announce, Attributes, Withdraw)
        # ==============================================================
        route = BGPRoute(
            "198.18.0.0/24",
            "10.0.1.2",
            as_path=[65001, 65002],
            community=["65001:100", "no-export"],
            med=50,
        )
        result = SpeakerRouteService.announce(net, "sp1", route, neighbor="10.0.1.1")
        self.assertEqual(result["neighbors"], ["10.0.1.1"])
        self.assertTrue(
            wait_until(
                lambda: "198.18.0.0/24" in r1.cmd('vtysh -c "show bgp ipv4 unicast"'),
                10.0,
            )
        )
        detail = r1.cmd('vtysh -c "show bgp ipv4 unicast 198.18.0.0/24"')
        self.assertIn("65001 65002", detail)
        self.assertIn("65001:100", detail)
        self.assertIn("no-export", detail)
        self.assertIn("metric 50", detail)

        neighbors = neighbors_by_address(net)
        announced_v4 = {r["prefix"]: r for r in neighbors["10.0.1.1"]["announced"]}
        self.assertIn("198.18.0.0/24", announced_v4)
        self.assertEqual(announced_v4["198.18.0.0/24"]["as_path"], [65001, 65002])
        self.assertEqual(
            announced_v4["198.18.0.0/24"]["community"], ["65001:100", "no-export"]
        )
        self.assertEqual(announced_v4["198.18.0.0/24"]["med"], 50)
        self.assertNotIn(
            "198.18.0.0/24",
            [r["prefix"] for r in neighbors["2001:db8:1::1"]["announced"]],
        )

        result = SpeakerRouteService.announce(
            net, "sp1", BGPRoute("198.18.1.0/24", "10.0.1.2")
        )
        self.assertEqual(result["neighbors"], ["10.0.1.1", "10.0.2.1"])
        self.assertTrue(
            wait_until(
                lambda: "198.18.1.0/24" in r1.cmd('vtysh -c "show bgp ipv4 unicast"'),
                10.0,
            )
        )

        SpeakerRouteService.withdraw(net, "sp1", "198.18.0.0/24", neighbor="10.0.1.1")
        self.assertTrue(
            wait_until(
                lambda: "198.18.0.0/24"
                not in r1.cmd('vtysh -c "show bgp ipv4 unicast"'),
                10.0,
            )
        )
        self.assertIn("198.18.1.0/24", r1.cmd('vtysh -c "show bgp ipv4 unicast"'))

        SpeakerRouteService.withdraw_all(net, "sp1", "10.0.1.1")

        def v4_paths(prefix: str) -> List[str]:
            detail = json.loads(r1.cmd(f'vtysh -c "show bgp ipv4 unicast {prefix} json"'))
            return [path["aspath"]["string"] for path in detail.get("paths", [])]

        # r2 still holds sp1's route, over the longer path, and now hands it
        # to r1. 198.18.1.0/24 leaves r1 entirely: sp1 sent r2 that route
        # too, but with next hop 10.0.1.2, which r2 cannot reach.
        self.assertTrue(
            wait_until(
                lambda: v4_paths(SPEAKER_V4_ROUTE) == ["65001 65002"]
                and "198.18.1.0/24" not in r1.cmd('vtysh -c "show bgp ipv4 unicast"'),
                10.0,
            ),
            v4_paths(SPEAKER_V4_ROUTE),
        )
        neighbors = neighbors_by_address(net)
        self.assertEqual(neighbors["10.0.1.1"]["announced"], [])
        self.assertEqual(
            [r["prefix"] for r in neighbors["2001:db8:1::1"]["announced"]],
            [SPEAKER_V6_ROUTE],
        )

        printed = cli_output(
            "announce",
            "sp1 neighbor 10.0.1.1 198.18.2.0/24 next-hop 10.0.1.2 med 7",
            net=net,
        )
        self.assertIn("198.18.2.0/24", printed)
        self.assertTrue(
            wait_until(
                lambda: "198.18.2.0/24" in r1.cmd('vtysh -c "show bgp ipv4 unicast"'),
                10.0,
            )
        )
        printed = cli_output("withdraw", "sp1 198.18.2.0/24", net=net)
        self.assertIn("198.18.2.0/24", printed)
        self.assertTrue(
            wait_until(
                lambda: "198.18.2.0/24"
                not in r1.cmd('vtysh -c "show bgp ipv4 unicast"'),
                10.0,
            )
        )

        # ==============================================================
        # 3. Burst Generation and Longest Prefix Match
        # ==============================================================
        def v4_prefixes() -> set:
            table = r1.cmd('vtysh -c "show bgp ipv4 unicast json"')
            return set(json.loads(table).get("routes", {}))

        before = v4_prefixes()
        result = SpeakerRouteService.generate(
            net, "sp1", "10.0.1.1", 20, prefix_seed=11, as_path_seed=12
        )
        announced = [r["prefix"] for r in result["announced"]]
        self.assertEqual(len(announced), 20)
        self.assertTrue(
            wait_until(lambda: set(announced) <= v4_prefixes(), 20.0),
            "every generated prefix should reach r1",
        )
        self.assertEqual(len(v4_prefixes() - before), 20)

        for prefix in announced:
            detail = json.loads(
                r1.cmd(f'vtysh -c "show bgp ipv4 unicast {prefix} json"')
            )
            path = detail["paths"][0]
            self.assertEqual(path["aspath"]["segments"][0]["list"][0], 65001, prefix)
            self.assertEqual(path["nexthops"][0]["ip"], "10.0.1.2", prefix)

        again = SpeakerRouteService.generate(
            net, "sp1", "10.0.1.1", 20, prefix_seed=11, as_path_seed=12
        )
        self.assertEqual([r["prefix"] for r in again["announced"]], announced)

        lpm = SpeakerRouteService.generate(
            net, "sp1", "10.0.1.1", 2, sub_prefixes=2, prefix_seed=21
        )
        bases, subs = split_bases_and_sub_prefixes(
            [r["prefix"] for r in lpm["announced"]]
        )
        self.assertEqual(len(bases), 2)
        self.assertGreaterEqual(len(subs), 2)
        self.assertTrue(
            wait_until(
                lambda: {r["prefix"] for r in lpm["announced"]} <= v4_prefixes(), 20.0
            )
        )

        v6 = SpeakerRouteService.generate(
            net, "sp1", "2001:db8:1::1", 3, prefix_seed=31
        )
        self.assertEqual(
            {r["next_hop"] for r in v6["announced"]}, {"2001:db8:1::2"}
        )
        self.assertTrue(
            wait_until(
                lambda: all(
                    r["prefix"] in r1.cmd('vtysh -c "show bgp ipv6 unicast"')
                    for r in v6["announced"]
                ),
                20.0,
            )
        )

        printed = cli_output(
            "announce", "sp1 neighbor 10.0.1.1 generate 3 prefix-seed 41", net=net
        )
        self.assertIn("announced 3", printed)
        self.assertEqual(printed.count("/"), 3)

        # ==============================================================
        # 4. Link Degradation & Recovery
        # ==============================================================
        nodes["sp1"].intfs[0].ifconfig("down")

        def v4_down_v6_up() -> bool:
            neighbors_now = neighbors_by_address(net)
            return (
                not neighbors_now["10.0.1.1"]["up"]
                and neighbors_now["2001:db8:1::1"]["up"]
            )

        self.assertTrue(
            wait_until(v4_down_v6_up, SESSION_TIMEOUT),
            "the IPv4 Neighbor should read down while the IPv6 one stays up",
        )
        neighbors_now = neighbors_by_address(net)
        self.assertIsNone(neighbors_now["10.0.1.1"]["uptime"])
        self.assertNotEqual(neighbors_now["10.0.1.1"]["state"], "established")
        self.assertEqual(neighbors_now["2001:db8:1::1"]["state"], "established")

        nodes["sp1"].intfs[0].ifconfig("up")
        self.assertTrue(
            wait_until(lambda: neighbors_by_address(net)["10.0.1.1"]["up"], SESSION_TIMEOUT),
            "the IPv4 Neighbor should recover when link is up",
        )

