"""Unit tests for reading a Speaker through ``SpeakerQueryService``.

The answers scripted here are what ExaBGP 5.0.13 wrote on the Speaker Test
Lab's pipe, verbatim, so the parsers are pinned to the daemon's own output:
one JSON array for the Neighbor listing, and one whitespace-separated line
per route in the extensive RIB listings.
"""

from __future__ import annotations

import json
import unittest

from mniplab.errors import ExaBGPControlError
from mniplab.exabgp.query import SpeakerNotFoundError, SpeakerQueryService
from mniplab.tests.fakes import FakeLab, FakeNode, SpeakerClientStubMixin, cli_output

NEIGHBOR_V4 = {
    "state": "up", "duration": 30, "fsm": "ESTABLISHED",
    "local": {"families": {"ipv4 unicast": True}, "address": "10.0.1.2", "as": 65001,
              "id": "1.1.1.1", "hold": 180},
    "peer": {"families": {"ipv4 unicast": True}, "address": "10.0.1.1", "as": 65000,
             "id": "10.0.1.1", "hold": 180},
    "messages": {"sent": {"update": 2, "total": 4}, "received": {"update": 3, "total": 5}},
    "families": ["ipv4 unicast"],
}

NEIGHBOR_V6 = {
    "state": "up", "duration": 28, "fsm": "ESTABLISHED",
    "local": {"families": {"ipv6 unicast": True}, "address": "2001:db8:1::2", "as": 65001,
              "id": "1.1.1.1", "hold": 180},
    "peer": {"families": {"ipv6 unicast": True}, "address": "2001:db8:1::1", "as": 65000,
             "id": "10.0.1.1", "hold": 180},
    "messages": {"sent": {"update": 2, "total": 4}, "received": {"update": 2, "total": 4}},
    "families": ["ipv6 unicast"],
}

NEIGHBOR_V4_DOWN = {
    "state": "down", "duration": 12, "fsm": "ACTIVE",
    "local": {"families": {"ipv4 unicast": True}, "address": "10.0.1.2", "as": 65001,
              "id": "1.1.1.1", "hold": 180},
    "peer": {"families": {}, "address": "10.0.1.1", "as": 65000, "id": None, "hold": None},
    "messages": {"sent": {"update": 0, "total": 0}, "received": {"update": 0, "total": 0}},
    "families": [],
}

V4_HEAD = ("neighbor 10.0.1.1 local-ip 10.0.1.2 local-as 65001 peer-as 65000 "
           "router-id 1.1.1.1 family-allowed in-open ipv4 unicast")
V6_HEAD = ("neighbor 2001:db8:1::1 local-ip 2001:db8:1::2 local-as 65001 peer-as 65000 "
           "router-id 1.1.1.1 family-allowed in-open ipv6 unicast")

ADJ_RIB_OUT = [
    f"{V4_HEAD} 203.0.113.0/24 next-hop 10.0.1.2 as-path ( 65001 )",
    f"{V6_HEAD} 2001:db8:100::/48 next-hop 2001:db8:1::2 as-path ( 65001 )",
]

ADJ_RIB_IN = [
    f"{V4_HEAD} 198.51.100.0/24 next-hop 10.0.1.1 origin igp as-path ( 65000 ) med 0",
    f"{V4_HEAD} 203.0.113.0/24 next-hop 10.0.1.1 origin igp as-path ( 65000 65001 )",
    f"{V6_HEAD} 2001:db8:100::/48 next-hop fe80::5c9f:23ff:fe4f:2122 origin igp as-path ( 65000 65001 )",
]


def speaker_lab() -> FakeLab:
    """A fake Lab with one Speaker, one Router and one Host."""
    sp1 = FakeNode("sp1", display_type="bgp-speaker")
    sp1.params = {"local_as": 65001}
    return FakeLab(sp1, FakeNode("r1", display_type="router"), FakeNode("h1"))


class SpeakerLookupTests(SpeakerClientStubMixin, unittest.TestCase):
    """Which Nodes ``get_speaker`` will read at all."""

    def test_an_unknown_node_raises_the_not_found_error(self) -> None:
        with self.assertRaises(SpeakerNotFoundError) as ctx:
            SpeakerQueryService.get_speaker(speaker_lab(), "sp9")
        self.assertIn("Node not found: sp9", str(ctx.exception))

    def test_a_node_that_is_not_a_speaker_raises_the_not_found_error(self) -> None:
        with self.assertRaises(SpeakerNotFoundError) as ctx:
            SpeakerQueryService.get_speaker(speaker_lab(), "r1")
        self.assertIn("r1 is not a Speaker", str(ctx.exception))

    def test_the_not_found_error_is_the_family_standard_value_error(self) -> None:
        """The CLI catches ValueError for every family; this one is no different."""
        self.assertTrue(issubclass(SpeakerNotFoundError, ValueError))


class GetSpeakerTests(SpeakerClientStubMixin, unittest.TestCase):
    """One dictionary: the Speaker, its Neighbors, and both RIBs per Neighbor."""

    def setUp(self) -> None:
        super().setUp()
        self.answer({
            "show neighbor json": [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])],
            "show adj-rib out extensive": ADJ_RIB_OUT,
            "show adj-rib in extensive": ADJ_RIB_IN,
        })

    def test_the_speaker_is_named_with_its_local_as(self) -> None:
        speaker = SpeakerQueryService.get_speaker(speaker_lab(), "sp1")
        self.assertEqual(speaker["node"], "sp1")
        self.assertEqual(speaker["local_as"], 65001)

    def test_every_neighbor_is_listed_with_its_identity_state_and_uptime(self) -> None:
        neighbors = SpeakerQueryService.get_speaker(speaker_lab(), "sp1")["neighbors"]
        self.assertEqual(
            [(n["address"], n["peer_as"], n["local_address"], n["local_as"], n["family"],
              n["state"], n["up"], n["uptime"]) for n in neighbors],
            [
                ("10.0.1.1", 65000, "10.0.1.2", 65001, "ipv4", "established", True, 30),
                ("2001:db8:1::1", 65000, "2001:db8:1::2", 65001, "ipv6", "established", True, 28),
            ],
        )

    def test_a_declared_route_appears_under_its_own_neighbor_only(self) -> None:
        neighbors = SpeakerQueryService.get_speaker(speaker_lab(), "sp1")["neighbors"]
        by_address = {n["address"]: n for n in neighbors}
        self.assertEqual(
            [r["prefix"] for r in by_address["10.0.1.1"]["announced"]], ["203.0.113.0/24"]
        )
        self.assertEqual(
            [r["prefix"] for r in by_address["2001:db8:1::1"]["announced"]], ["2001:db8:100::/48"]
        )

    def test_an_announced_route_carries_its_attributes(self) -> None:
        announced = SpeakerQueryService.get_speaker(speaker_lab(), "sp1")["neighbors"][0]["announced"]
        self.assertEqual(announced, [{
            "prefix": "203.0.113.0/24",
            "next_hop": "10.0.1.2",
            "as_path": [65001],
            "community": [],
            "large_community": [],
            "med": None,
        }])

    def test_received_routes_are_what_the_neighbor_sent(self) -> None:
        received = SpeakerQueryService.get_speaker(speaker_lab(), "sp1")["neighbors"][0]["received"]
        self.assertEqual([(r["prefix"], r["next_hop"], r["as_path"], r["med"]) for r in received], [
            ("198.51.100.0/24", "10.0.1.1", [65000], 0),
            ("203.0.113.0/24", "10.0.1.1", [65000, 65001], None),
        ])

    def test_a_down_neighbor_is_listed_as_down_with_no_uptime(self) -> None:
        """Configured but never connected: present, down, family still known."""
        self.answer({
            "show neighbor json": [json.dumps([NEIGHBOR_V4_DOWN, NEIGHBOR_V6])],
            "show adj-rib out extensive": ADJ_RIB_OUT,
            "show adj-rib in extensive": ADJ_RIB_IN[2:],
        })
        down = SpeakerQueryService.get_speaker(speaker_lab(), "sp1")["neighbors"][0]
        self.assertEqual((down["address"], down["family"], down["state"], down["up"], down["uptime"]),
                         ("10.0.1.1", "ipv4", "active", False, None))
        # What was injected for a down Neighbor is still listed beside it.
        self.assertEqual([r["prefix"] for r in down["announced"]], ["203.0.113.0/24"])
        self.assertEqual(down["received"], [])

    def test_communities_large_communities_and_med_are_parsed_bare_or_bracketed(self) -> None:
        """ExaBGP prints one community bare and several in brackets."""
        self.answer({
            "show neighbor json": [json.dumps([NEIGHBOR_V4])],
            "show adj-rib out extensive": [
                f"{V4_HEAD} 203.0.113.0/24 next-hop 10.0.1.2 origin igp med 50 "
                "local-preference 200 community [ 65001:200 no-advertise ] "
                "extended-community target:65001:1 large-community 65001:3:4",
                f"{V4_HEAD} 192.0.2.0/24 next-hop 10.0.1.2 as-path ( 65010 65011 ) "
                "community 65001:100 large-community [ 65001:1:2 65001:5:6 ]",
            ],
            "show adj-rib in extensive": [],
        })
        announced = SpeakerQueryService.get_speaker(speaker_lab(), "sp1")["neighbors"][0]["announced"]
        self.assertEqual(announced, [
            {"prefix": "203.0.113.0/24", "next_hop": "10.0.1.2", "as_path": [],
             "community": ["65001:200", "no-advertise"], "large_community": ["65001:3:4"],
             "med": 50},
            {"prefix": "192.0.2.0/24", "next_hop": "10.0.1.2", "as_path": [65010, 65011],
             "community": ["65001:100"], "large_community": ["65001:1:2", "65001:5:6"],
             "med": None},
        ])

    def test_a_daemon_that_does_not_answer_raises_with_the_reason(self) -> None:
        self.refuse("no answer from sp1 to '{command}' within 5 s: is the ExaBGP daemon running?")
        with self.assertRaises(ExaBGPControlError) as ctx:
            SpeakerQueryService.get_speaker(speaker_lab(), "sp1")
        self.assertEqual(ctx.exception.command, "show neighbor json")

    def test_a_listing_that_is_not_json_is_reported_as_the_daemon_misanswering(self) -> None:
        """A read never leaks a bare parse error: the surfaces expect the control error."""
        self.answer({"show neighbor json": ["Peer AS up/down state"]})
        with self.assertRaises(ExaBGPControlError) as ctx:
            SpeakerQueryService.get_speaker(speaker_lab(), "sp1")
        self.assertEqual(ctx.exception.command, "show neighbor json")
        self.assertIn("sp1", str(ctx.exception))


class ShowSpeakerCommandTests(SpeakerClientStubMixin, unittest.TestCase):
    """``show_speaker <node>`` prints what ``get_speaker`` reads."""

    def setUp(self) -> None:
        super().setUp()
        self.answer({
            "show neighbor json": [json.dumps([NEIGHBOR_V4_DOWN, NEIGHBOR_V6])],
            "show adj-rib out extensive": ADJ_RIB_OUT,
            "show adj-rib in extensive": ADJ_RIB_IN,
        })

    def test_requires_a_node_argument(self) -> None:
        self.assertIn("Usage: show_speaker <node>", cli_output("show_speaker", "", net=speaker_lab()))

    def test_prints_neighbors_with_state_then_routes_per_neighbor(self) -> None:
        printed = cli_output("show_speaker", "sp1", net=speaker_lab())
        self.assertIn("Speaker sp1 (AS 65001)", printed)
        lines = [line.strip() for line in printed.splitlines()]
        # Neighbors first, each with its AS, state and uptime.
        self.assertTrue(any("10.0.1.1" in l and "AS 65000" in l and "active" in l and "down" in l
                            for l in lines), printed)
        self.assertTrue(any("2001:db8:1::1" in l and "AS 65000" in l and "established" in l and "28s" in l
                            for l in lines), printed)
        # Then, per Neighbor, what was announced and what came back.
        self.assertLess(printed.index("Neighbors"), printed.index("Announced to 10.0.1.1"))
        self.assertLess(printed.index("Announced to 10.0.1.1"), printed.index("Received from 10.0.1.1"))
        self.assertLess(printed.index("Received from 10.0.1.1"), printed.index("Announced to 2001:db8:1::1"))
        self.assertIn("203.0.113.0/24", printed[printed.index("Announced to 10.0.1.1"):printed.index("Received from 10.0.1.1")])
        self.assertIn("198.51.100.0/24", printed[printed.index("Received from 10.0.1.1"):printed.index("Announced to 2001:db8:1::1")])
        self.assertIn("as-path 65000 65001", printed)
        self.assertIn("med 0", printed)

    def test_route_attributes_print_in_exabgp_words(self) -> None:
        self.answer({
            "show neighbor json": [json.dumps([NEIGHBOR_V4])],
            "show adj-rib out extensive": [
                f"{V4_HEAD} 203.0.113.0/24 next-hop 10.0.1.2 as-path ( 65001 ) "
                "community [ 65001:200 no-advertise ] large-community 65001:3:4 med 50",
            ],
            "show adj-rib in extensive": [],
        })
        printed = cli_output("show_speaker", "sp1", net=speaker_lab())
        self.assertIn("203.0.113.0/24  next-hop 10.0.1.2  as-path 65001  "
                      "community 65001:200 no-advertise  large-community 65001:3:4  med 50", printed)
        self.assertIn("(none)", printed)

    def test_an_unknown_or_non_speaker_node_prints_the_family_standard_message(self) -> None:
        self.assertIn("Error: Node not found: sp9", cli_output("show_speaker", "sp9", net=speaker_lab()))
        self.assertIn("Error: r1 is not a Speaker", cli_output("show_speaker", "r1", net=speaker_lab()))

    def test_a_daemon_that_does_not_answer_is_reported_with_its_reason(self) -> None:
        self.refuse("no answer from sp1 to '{command}' within 5 s: is the ExaBGP daemon running?")
        printed = cli_output("show_speaker", "sp1", net=speaker_lab())
        self.assertIn("Error: no answer from sp1 to 'show neighbor json' within 5 s", printed)


if __name__ == "__main__":
    unittest.main()
