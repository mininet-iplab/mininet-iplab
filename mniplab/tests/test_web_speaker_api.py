"""Contract tests for ``/api/labs/fake/speakers/{node}`` and its writes, against a fake Lab.

The app's registry holds a :class:`FakeLab` with one Speaker and one Host,
and the Speaker's control client is the scripted stub, so the whole path
from route to ``SpeakerQueryService`` and ``SpeakerRouteService`` runs in
the fast suite: the documented shape on 200, 404 for a Node that is not a
Speaker, 400 naming the field for a bad body, the error mapping when the
daemon answers ``error`` or falls silent, and that a Learner's Session is
enough.
"""

from __future__ import annotations

import json
import unittest
from typing import List

from mniplab.errors import ExaBGPControlError
from mniplab.tests.fakes import FakeTerminalCloserMixin, LoginClientMixin, SpeakerClientStubMixin
from mniplab.tests.test_speaker_query import (
    ADJ_RIB_IN,
    ADJ_RIB_OUT,
    NEIGHBOR_V4,
    NEIGHBOR_V6,
    speaker_lab,
)
from mniplab.webserver import registry


class SpeakerRouteTests(SpeakerClientStubMixin, LoginClientMixin, FakeTerminalCloserMixin,
                        unittest.TestCase):
    """What the browser gets from ``/api/labs/fake/speakers/{node}``."""

    def setUp(self) -> None:
        super().setUp()
        registry.lab_service.lab = speaker_lab()
        self.addCleanup(setattr, registry.lab_service, "lab", None)
        self.answer({
            "show neighbor json": [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])],
            "show adj-rib out extensive": ADJ_RIB_OUT,
            "show adj-rib in extensive": ADJ_RIB_IN,
        })
        self.open_session(role="learner")

    def test_a_speaker_is_returned_with_its_neighbors_and_both_ribs(self) -> None:
        response = self.client.get("/api/labs/fake/speakers/sp1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["node"], payload["local_as"]), ("sp1", 65001))
        neighbors = {n["address"]: n for n in payload["neighbors"]}
        self.assertEqual(set(neighbors), {"10.0.1.1", "2001:db8:1::1"})
        v4 = neighbors["10.0.1.1"]
        self.assertEqual((v4["peer_as"], v4["family"], v4["state"], v4["up"], v4["uptime"]),
                         (65000, "ipv4", "established", True, 30))
        self.assertEqual([r["prefix"] for r in v4["announced"]], ["203.0.113.0/24"])
        self.assertEqual([r["prefix"] for r in v4["received"]],
                         ["198.51.100.0/24", "203.0.113.0/24"])
        self.assertEqual(v4["received"][0]["as_path"], [65000])

    def test_a_learner_session_is_enough(self) -> None:
        """Reading a Speaker is a lever in a running Lab, gated by login alone."""
        self.assertEqual(self.client.get("/api/labs/fake/speakers/sp1").status_code, 200)

    def test_without_a_session_the_route_is_refused(self) -> None:
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/labs/fake/speakers/sp1").status_code, 401)

    def test_a_host_is_not_found_under_speakers(self) -> None:
        response = self.client.get("/api/labs/fake/speakers/h1")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "h1 is not a Speaker")

    def test_an_unknown_node_is_not_found_under_speakers(self) -> None:
        response = self.client.get("/api/labs/fake/speakers/sp9")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Node not found: sp9")

    def test_a_rejected_command_is_a_bad_gateway_naming_the_command(self) -> None:
        """ExaBGP's ``error`` gives no reason, so the command is the reason."""
        self.refuse("ExaBGP on sp1 rejected '{command}' (the reason is in the daemon's log)")

        response = self.client.get("/api/labs/fake/speakers/sp1")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["command"], "show neighbor json")
        self.assertIn("rejected 'show neighbor json'", response.json()["detail"])

    def test_a_silent_daemon_is_a_bad_gateway_naming_the_timeout(self) -> None:
        self.refuse("no answer from sp1 to '{command}' within 5 s: is the ExaBGP daemon running?")

        response = self.client.get("/api/labs/fake/speakers/sp1")

        self.assertEqual(response.status_code, 502)
        self.assertIn("within 5 s", response.json()["detail"])
        self.assertEqual(response.json()["command"], "show neighbor json")

    def test_without_a_lab_the_route_says_so(self) -> None:
        registry.lab_service.lab = None
        response = self.client.get("/api/labs/fake/speakers/sp1")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "No lab running")


class SpeakerWriteRouteCase(SpeakerClientStubMixin, LoginClientMixin, FakeTerminalCloserMixin,
                            unittest.TestCase):
    """The two POST routes, with the Neighbor listing answered and writes recorded."""

    def setUp(self) -> None:
        super().setUp()
        registry.lab_service.lab = speaker_lab()
        self.addCleanup(setattr, registry.lab_service, "lab", None)
        self.writes: List[str] = []

        def execute(command: str) -> List[str]:
            if command == "show neighbor json":
                return [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])]
            self.writes.append(command)
            return []

        self.pipe.execute.side_effect = execute
        self.open_session(role="learner")


class AnnounceRouteTests(SpeakerWriteRouteCase):
    """``POST /api/speakers/{node}/announce``."""

    def test_an_announce_returns_the_route_and_the_neighbors_written(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "neighbor": "10.0.1.1", "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
            "as_path": [65001, 65002], "community": ["65001:100", "no-export"],
            "large_community": ["65001:1:2"], "med": 50,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["node"], "sp1")
        self.assertEqual(payload["neighbors"], ["10.0.1.1"])
        self.assertEqual(payload["route"], {
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2", "as_path": [65001, 65002],
            "community": ["65001:100", "no-export"], "large_community": ["65001:1:2"], "med": 50,
        })
        self.assertEqual(len(self.writes), 1)

    def test_an_announce_to_all_reaches_the_family_matching_neighbors(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "2001:db8:200::/48", "next_hop": "2001:db8:1::2",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["neighbors"], ["2001:db8:1::1"])

    def test_a_learner_session_is_enough(self) -> None:
        """Announcing is the Learner's lever, gated by login alone."""
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
        })
        self.assertEqual(response.status_code, 200)

    def test_without_a_session_the_route_is_refused(self) -> None:
        self.client.cookies.clear()
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
        })
        self.assertEqual(response.status_code, 401)

    def test_a_host_is_not_found_under_speakers(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/h1/announce", json={
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "h1 is not a Speaker")

    def test_a_cross_family_next_hop_is_a_400_naming_both_families(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "2001:db8:200::/48", "next_hop": "10.0.1.2",
        })
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("next-hop", detail)
        self.assertIn("IPv4", detail)
        self.assertIn("IPv6", detail)
        self.assertEqual(self.writes, [])

    def test_a_malformed_community_is_a_400_naming_the_field(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2", "community": ["65001-100"],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("community '65001-100'", response.json()["detail"])

    def test_a_mistyped_as_number_or_med_is_a_400_naming_the_word(self) -> None:
        """What the CLI refuses by name, REST refuses by name too (decision 19)."""
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2", "as_path": [65001, "AS65002"],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("as-path 'AS65002' is not an AS number", response.json()["detail"])

        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2", "med": "high",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("med 'high'", response.json()["detail"])
        self.assertEqual(self.writes, [])

    def test_a_missing_prefix_is_a_400_naming_the_field(self) -> None:
        """Not pydantic's "Field required": the grammar names the field, as it
        does for every other bad value."""
        for body in ({"next_hop": "10.0.1.2"}, {"prefix": "", "next_hop": "10.0.1.2"}):
            response = self.client.post("/api/labs/fake/speakers/sp1/announce", json=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.json()["detail"], "prefix is required")
        self.assertEqual(self.writes, [])

    def test_a_missing_next_hop_is_a_400_naming_the_field(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={"prefix": "198.18.0.0/24"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("next-hop is required", response.json()["detail"])

    def test_a_neighbor_lacking_the_family_is_a_400(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "neighbor": "10.0.1.1", "prefix": "2001:db8:200::/48", "next_hop": "2001:db8:1::2",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("10.0.1.1", response.json()["detail"])

    def test_a_rejected_announce_is_a_bad_gateway_naming_the_command(self) -> None:
        def refuse(command: str) -> List[str]:
            if command == "show neighbor json":
                return [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])]
            raise ExaBGPControlError(
                f"ExaBGP on sp1 rejected '{command}' (the reason is in the daemon's log)",
                command=command,
            )

        self.pipe.execute.side_effect = refuse
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "neighbor": "10.0.1.1", "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
        })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["command"],
                         "neighbor 10.0.1.1 announce route 198.18.0.0/24 next-hop 10.0.1.2")
        self.assertIn("rejected", response.json()["detail"])

    def test_a_silent_daemon_is_a_bad_gateway_naming_the_timeout(self) -> None:
        self.refuse("no answer from sp1 to '{command}' within 5 s: is the ExaBGP daemon running?")
        response = self.client.post("/api/labs/fake/speakers/sp1/announce", json={
            "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
        })
        self.assertEqual(response.status_code, 502)
        self.assertIn("within 5 s", response.json()["detail"])


class WithdrawRouteTests(SpeakerWriteRouteCase):
    """``POST /api/speakers/{node}/withdraw``."""

    def test_a_withdraw_returns_the_prefix_and_the_neighbors_written(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/withdraw", json={
            "neighbor": "10.0.1.1", "prefix": "203.0.113.0/24",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "node": "sp1", "prefix": "203.0.113.0/24", "neighbors": ["10.0.1.1"],
        })

    def test_withdraw_all_clears_the_neighbor(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/withdraw", json={
            "neighbor": "10.0.1.1", "all": True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"node": "sp1", "all": True, "neighbors": ["10.0.1.1"]})
        self.assertEqual(len(self.writes), 1)

    def test_a_body_with_neither_prefix_nor_all_is_a_400_naming_the_fields(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/withdraw", json={"neighbor": "10.0.1.1"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("prefix", response.json()["detail"])
        self.assertIn("all", response.json()["detail"])
        self.assertEqual(self.writes, [])

    def test_a_host_is_not_found_under_speakers(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/h1/withdraw", json={"prefix": "203.0.113.0/24"})
        self.assertEqual(response.status_code, 404)

    def test_a_rejected_withdraw_is_a_bad_gateway_naming_the_command(self) -> None:
        self.refuse("ExaBGP on sp1 rejected '{command}' (the reason is in the daemon's log)")
        response = self.client.post("/api/labs/fake/speakers/sp1/withdraw", json={"prefix": "203.0.113.0/24"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["command"], "show neighbor json")


class GenerateRouteTests(SpeakerWriteRouteCase):
    """``POST /api/speakers/{node}/generate``."""

    def test_a_burst_returns_the_routes_it_announced(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/generate", json={
            "neighbor": "10.0.1.1", "count": 5, "sub_prefixes": 1, "prefix_seed": 3, "as_path_seed": 4,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["node"], payload["neighbor"]), ("sp1", "10.0.1.1"))
        self.assertGreaterEqual(len(payload["announced"]), 5)
        self.assertEqual(len(payload["announced"]), len(self.writes))
        first = payload["announced"][0]
        self.assertEqual(set(first), {"prefix", "next_hop", "as_path", "community", "large_community", "med"})
        self.assertEqual(first["next_hop"], "10.0.1.2")
        self.assertEqual(first["as_path"][0], 65001)

    def test_the_same_seeds_answer_the_same_burst(self) -> None:
        body = {"neighbor": "10.0.1.1", "count": 3, "prefix_seed": 8, "as_path_seed": 9}
        first = self.client.post("/api/labs/fake/speakers/sp1/generate", json=body).json()
        second = self.client.post("/api/labs/fake/speakers/sp1/generate", json=body).json()
        self.assertEqual(first["announced"], second["announced"])

    def test_a_learner_session_is_enough(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/generate", json={"neighbor": "10.0.1.1", "count": 1})
        self.assertEqual(response.status_code, 200)

    def test_a_host_is_not_found_under_speakers(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/h1/generate", json={"neighbor": "10.0.1.1", "count": 1})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "h1 is not a Speaker")

    def test_a_bad_count_is_a_400_naming_the_field(self) -> None:
        for count in (0, "lots", 5000):
            response = self.client.post("/api/labs/fake/speakers/sp1/generate", json={"neighbor": "10.0.1.1", "count": count})
            self.assertEqual(response.status_code, 400, count)
            self.assertIn(f"count '{count}'", response.json()["detail"])
        self.assertEqual(self.writes, [])

    def test_a_missing_count_is_a_400_naming_the_field(self) -> None:
        for body in ({"neighbor": "10.0.1.1"}, {"neighbor": "10.0.1.1", "count": ""}):
            response = self.client.post("/api/labs/fake/speakers/sp1/generate", json=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.json()["detail"], "count is required")
        self.assertEqual(self.writes, [])

    def test_a_bad_seed_is_a_400_naming_the_field(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/generate", json={
            "neighbor": "10.0.1.1", "count": 2, "as_path_seed": "lucky",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("as-path-seed 'lucky'", response.json()["detail"])
        self.assertEqual(self.writes, [])

    def test_without_a_neighbor_on_a_speaker_with_several_the_choices_are_named(self) -> None:
        response = self.client.post("/api/labs/fake/speakers/sp1/generate", json={"count": 2})
        self.assertEqual(response.status_code, 400)
        self.assertIn("choose one of 10.0.1.1, 2001:db8:1::1", response.json()["detail"])

    def test_a_rejected_announce_is_a_bad_gateway_naming_the_command(self) -> None:
        def refuse(command: str) -> List[str]:
            if command == "show neighbor json":
                return [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])]
            raise ExaBGPControlError(
                f"ExaBGP on sp1 rejected '{command}' (the reason is in the daemon's log)",
                command=command,
            )

        self.pipe.execute.side_effect = refuse
        response = self.client.post("/api/labs/fake/speakers/sp1/generate", json={"neighbor": "10.0.1.1", "count": 2})
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.json()["command"].startswith("neighbor 10.0.1.1 announce route "))

    def test_a_silent_daemon_is_a_bad_gateway_naming_the_timeout(self) -> None:
        self.refuse("no answer from sp1 to '{command}' within 5 s: is the ExaBGP daemon running?")
        response = self.client.post("/api/labs/fake/speakers/sp1/generate", json={"neighbor": "10.0.1.1", "count": 2})
        self.assertEqual(response.status_code, 502)
        self.assertIn("within 5 s", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
