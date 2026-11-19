"""Unit tests for announcing and withdrawing through ``SpeakerRouteService``.

The pipe is the scripted stub, so what these tests pin is the service's own
part: which Neighbors a write goes to, and what is refused before anything
is written — the family checks ExaBGP does not make and
the Neighbor lookups. What lands on the Router is the mechanism suite's
business (``test_bgp_speaker_scenario.py``).
"""

from __future__ import annotations

import ipaddress
import json
import unittest
from typing import List

from mniplab.errors import ExaBGPControlError
from mniplab.exabgp.models import BGPRoute
from mniplab.exabgp.query import SpeakerNotFoundError
from mniplab.cli import mnIPLabCLI
from mniplab.exabgp.routes import SpeakerRouteService
from mniplab.tests.fakes import SpeakerClientStubMixin, cli_output
from mniplab.tests.test_speaker_query import NEIGHBOR_V4, NEIGHBOR_V6, speaker_lab

V4_ROUTE = BGPRoute("203.0.113.0/24", "10.0.1.2", as_path=[65001], community=["no-export"])
V6_ROUTE = BGPRoute("2001:db8:200::/48", "2001:db8:1::2")


class RouteServiceCase(SpeakerClientStubMixin, unittest.TestCase):
    """A Speaker with one IPv4 and one IPv6 Neighbor behind the stub."""

    def setUp(self) -> None:
        super().setUp()
        self.net = speaker_lab()
        self.writes: List[str] = []

        def execute(command: str) -> List[str]:
            if command == "show neighbor json":
                return [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])]
            self.writes.append(command)
            return []

        self.pipe.execute.side_effect = execute


class AnnounceTests(RouteServiceCase):
    """Which Neighbors an announce reaches, and what is refused first."""

    def test_an_announce_to_one_neighbor_names_that_neighbor(self) -> None:
        result = SpeakerRouteService.announce(self.net, "sp1", V4_ROUTE, neighbor="10.0.1.1")
        self.assertEqual(result["neighbors"], ["10.0.1.1"])
        self.assertEqual(result["route"]["prefix"], "203.0.113.0/24")
        self.assertEqual(result["route"]["community"], ["no-export"])
        self.assertEqual(len(self.writes), 1)

    def test_an_announce_to_all_reaches_the_family_matching_neighbors_only(self) -> None:
        """ExaBGP's bare ``announce`` would answer ``done`` and skip the IPv6
        Neighbor silently; the service writes per Neighbor instead."""
        result = SpeakerRouteService.announce(self.net, "sp1", V4_ROUTE)
        self.assertEqual(result["neighbors"], ["10.0.1.1"])
        self.assertEqual(len(self.writes), 1)

    def test_a_prefix_whose_family_the_neighbor_lacks_is_refused_before_any_write(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.announce(self.net, "sp1", V6_ROUTE, neighbor="10.0.1.1")
        message = str(ctx.exception)
        self.assertIn("10.0.1.1", message)
        self.assertIn("IPv4", message)
        self.assertIn("IPv6", message)
        self.assertEqual(self.writes, [])

    def test_a_cross_family_next_hop_is_refused_before_any_write(self) -> None:
        for prefix, next_hop in (("2001:db8:200::/48", "10.0.1.2"), ("203.0.113.0/24", "2001:db8:1::2")):
            with self.assertRaises(ValueError) as ctx:
                SpeakerRouteService.announce(self.net, "sp1", BGPRoute(prefix, next_hop))
            self.assertIn("IPv4", str(ctx.exception))
            self.assertIn("IPv6", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_an_announce_to_all_without_a_next_hop_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.announce(self.net, "sp1", BGPRoute("203.0.113.0/24", None))  # type: ignore[arg-type]
        self.assertIn("next-hop", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_an_unknown_neighbor_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.announce(self.net, "sp1", V4_ROUTE, neighbor="10.9.9.9")
        self.assertIn("10.9.9.9", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_a_node_that_is_not_a_speaker_is_not_found(self) -> None:
        with self.assertRaises(SpeakerNotFoundError):
            SpeakerRouteService.announce(self.net, "r1", V4_ROUTE)

    def test_a_rejected_announce_names_the_command_that_failed(self) -> None:
        def refuse(command: str) -> List[str]:
            if command == "show neighbor json":
                return [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])]
            raise ExaBGPControlError(f"ExaBGP on sp1 rejected '{command}'", command=command)

        self.pipe.execute.side_effect = refuse
        with self.assertRaises(ExaBGPControlError) as ctx:
            SpeakerRouteService.announce(self.net, "sp1", V4_ROUTE, neighbor="10.0.1.1")
        self.assertIn("203.0.113.0/24", ctx.exception.command or "")
        self.assertIn("rejected", str(ctx.exception))


class WithdrawTests(RouteServiceCase):
    """Withdrawing one prefix, or everything a Neighbor was sent."""

    def test_a_withdraw_from_one_neighbor_names_that_neighbor(self) -> None:
        result = SpeakerRouteService.withdraw(self.net, "sp1", "203.0.113.0/24", neighbor="10.0.1.1")
        self.assertEqual(result["neighbors"], ["10.0.1.1"])
        self.assertEqual(result["prefix"], "203.0.113.0/24")
        self.assertEqual(len(self.writes), 1)

    def test_a_withdraw_from_all_reaches_the_family_matching_neighbors_only(self) -> None:
        result = SpeakerRouteService.withdraw(self.net, "sp1", "2001:db8:100::/48")
        self.assertEqual(result["neighbors"], ["2001:db8:1::1"])

    def test_a_withdraw_of_a_prefix_the_neighbor_cannot_carry_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.withdraw(self.net, "sp1", "2001:db8:100::/48", neighbor="10.0.1.1")
        self.assertIn("IPv6", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_a_malformed_prefix_is_refused_naming_the_field(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.withdraw(self.net, "sp1", "203.0.113.0")
        self.assertIn("prefix", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_withdraw_all_clears_one_neighbor(self) -> None:
        result = SpeakerRouteService.withdraw_all(self.net, "sp1", "10.0.1.1")
        self.assertEqual(result["neighbors"], ["10.0.1.1"])
        self.assertTrue(result["all"])
        self.assertEqual(len(self.writes), 1)

    def test_withdraw_all_without_a_neighbor_clears_every_neighbor(self) -> None:
        result = SpeakerRouteService.withdraw_all(self.net, "sp1")
        self.assertEqual(result["neighbors"], ["10.0.1.1", "2001:db8:1::1"])
        self.assertEqual(len(self.writes), 2)


class GenerateTests(RouteServiceCase):
    """A burst of the Lab Examples' kind of routes, to one Neighbor."""

    def test_a_burst_is_announced_to_the_neighbor_and_returned(self) -> None:
        result = SpeakerRouteService.generate(self.net, "sp1", "10.0.1.1", 5)
        self.assertEqual(result["neighbor"], "10.0.1.1")
        announced = result["announced"]
        self.assertEqual(len(announced), 5)
        self.assertEqual(len(self.writes), 5)
        for route in announced:
            # The Lab Examples' shape: a public IPv4 prefix, the Neighbor's
            # local address as next hop, an AS-PATH originating at the local AS.
            self.assertRegex(route["prefix"], r"^\d+\.\d+\.\d+\.\d+/\d+$")
            self.assertEqual(route["next_hop"], "10.0.1.2")
            self.assertEqual(route["as_path"][0], 65001)

    def test_the_ipv6_neighbor_gets_ipv6_prefixes_from_its_own_address(self) -> None:
        announced = SpeakerRouteService.generate(self.net, "sp1", "2001:db8:1::1", 3)["announced"]
        self.assertEqual(len(announced), 3)
        for route in announced:
            self.assertIn(":", route["prefix"])
            self.assertEqual(route["next_hop"], "2001:db8:1::2")

    def test_the_same_seeds_produce_the_same_burst(self) -> None:
        first = SpeakerRouteService.generate(self.net, "sp1", "10.0.1.1", 4, prefix_seed=1, as_path_seed=2)
        second = SpeakerRouteService.generate(self.net, "sp1", "10.0.1.1", 4, prefix_seed=1, as_path_seed=2)
        self.assertEqual(first["announced"], second["announced"])
        other = SpeakerRouteService.generate(self.net, "sp1", "10.0.1.1", 4, prefix_seed=3, as_path_seed=2)
        self.assertNotEqual(first["announced"], other["announced"])

    def test_sub_prefixes_accompany_each_base_prefix(self) -> None:
        announced = SpeakerRouteService.generate(
            self.net, "sp1", "10.0.1.1", 2, sub_prefixes=2, prefix_seed=5
        )["announced"]
        networks = [ipaddress.ip_network(r["prefix"]) for r in announced]
        bases = [n for n in networks if not any(n != o and n.subnet_of(o) for o in networks)]  # type: ignore[arg-type]
        subs = [n for n in networks if n not in bases]
        self.assertEqual(len(bases), 2)
        self.assertGreaterEqual(len(subs), 2)
        for sub in subs:
            self.assertEqual(sum(1 for b in bases if sub.subnet_of(b)), 1, sub)  # type: ignore[arg-type,misc]
        self.assertEqual(len(self.writes), len(announced))

    def test_a_missing_neighbor_is_refused_naming_the_choices(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.generate(self.net, "sp1", None, 5)
        self.assertIn("10.0.1.1", str(ctx.exception))
        self.assertIn("2001:db8:1::1", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_a_bad_count_is_refused_before_any_write(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.generate(self.net, "sp1", "10.0.1.1", 0)
        self.assertIn("count", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_an_unknown_neighbor_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SpeakerRouteService.generate(self.net, "sp1", "10.9.9.9", 5)
        self.assertIn("10.9.9.9", str(ctx.exception))


class AnnounceCommandTests(RouteServiceCase):
    """``announce <node> [neighbor <ip>] <prefix> next-hop <ip> [attrs…]``."""

    def test_an_announce_to_one_neighbor_reports_what_went_where(self) -> None:
        printed = cli_output(
            "announce",
            "sp1 neighbor 10.0.1.1 198.18.0.0/24 next-hop 10.0.1.2 as-path [ 65001 65002 ] med 50",
            net=self.net,
        )
        self.assertIn("198.18.0.0/24", printed)
        self.assertIn("10.0.1.1", printed)
        self.assertIn("as-path 65001 65002", printed)
        self.assertEqual(len(self.writes), 1)

    def test_an_announce_to_all_names_every_neighbor_written(self) -> None:
        printed = cli_output("announce", "sp1 2001:db8:200::/48 next-hop 2001:db8:1::2", net=self.net)
        self.assertIn("2001:db8:1::1", printed)
        self.assertNotIn("10.0.1.1", printed)

    def test_an_invalid_line_prints_the_parsers_message(self) -> None:
        printed = cli_output(
            "announce", "sp1 198.18.0.0/24 next-hop 10.0.1.2 local-preference 200", net=self.net
        )
        self.assertIn("Error: unknown attribute 'local-preference'", printed)
        self.assertEqual(self.writes, [])

    def test_a_cross_family_route_prints_both_families(self) -> None:
        printed = cli_output("announce", "sp1 2001:db8:200::/48 next-hop 10.0.1.2", net=self.net)
        self.assertIn("Error:", printed)
        self.assertIn("IPv4", printed)
        self.assertIn("IPv6", printed)

    def test_too_few_words_prints_usage(self) -> None:
        for line in ("", "sp1", "sp1 neighbor 10.0.1.1"):
            self.assertIn("Usage: announce <node>", cli_output("announce", line, net=self.net), line)

    def test_a_rejected_announce_prints_the_command_that_failed(self) -> None:
        def refuse(command: str) -> List[str]:
            if command == "show neighbor json":
                return [json.dumps([NEIGHBOR_V4, NEIGHBOR_V6])]
            raise ExaBGPControlError(f"ExaBGP on sp1 rejected '{command}'", command=command)

        self.pipe.execute.side_effect = refuse
        printed = cli_output("announce", "sp1 198.18.0.0/24 next-hop 10.0.1.2", net=self.net)
        self.assertIn("Error: ExaBGP on sp1 rejected 'neighbor 10.0.1.1 announce route 198.18.0.0/24", printed)

    def test_help_shows_both_forms_with_examples(self) -> None:
        """``help announce`` prints the command's docstring, as Cmd does."""
        doc = mnIPLabCLI.do_announce.__doc__ or ""
        self.assertIn("announce <node> [neighbor <ip>] <prefix> next-hop <ip>", doc)
        self.assertIn("[as-path", doc)
        self.assertIn("[community", doc)
        self.assertIn("[large-community", doc)
        self.assertIn("[med", doc)
        self.assertIn("announce <node> [neighbor <ip>] generate <count>", doc)
        self.assertIn("[sub-prefixes N] [prefix-seed S] [as-path-seed S]", doc)
        self.assertIn("Examples:", doc)
        self.assertIn("announce sp1 neighbor 10.0.1.1", doc)
        self.assertIn("generate 20", doc)

    def test_generate_announces_a_burst_and_prints_every_prefix(self) -> None:
        printed = cli_output(
            "announce", "sp1 neighbor 10.0.1.1 generate 4 sub-prefixes 1 prefix-seed 3 as-path-seed 5",
            net=self.net,
        )
        self.assertIn("announced", printed)
        self.assertIn("10.0.1.1", printed)
        prefixes = [line.split()[1] for line in printed.splitlines() if line.strip().startswith("- ")]
        self.assertEqual(len(prefixes), len(self.writes))
        self.assertGreaterEqual(len(prefixes), 4)
        for prefix in prefixes:
            self.assertRegex(prefix, r"^\d+\.\d+\.\d+\.\d+/\d+$")

    def test_generate_without_a_neighbor_names_the_choices(self) -> None:
        printed = cli_output("announce", "sp1 generate 4", net=self.net)
        self.assertIn("Error: generate needs a Neighbor on sp1", printed)
        self.assertIn("2001:db8:1::1", printed)

    def test_a_bad_generate_option_prints_the_parsers_message(self) -> None:
        printed = cli_output("announce", "sp1 neighbor 10.0.1.1 generate 4 seed 9", net=self.net)
        self.assertIn("Error: unknown generate option 'seed'", printed)
        printed = cli_output("announce", "sp1 neighbor 10.0.1.1 generate lots", net=self.net)
        self.assertIn("Error: count 'lots'", printed)
        self.assertEqual(self.writes, [])


class WithdrawCommandTests(RouteServiceCase):
    """``withdraw <node> [neighbor <ip>] (<prefix> | all)``."""

    def test_a_withdraw_reports_the_prefix_and_neighbors(self) -> None:
        printed = cli_output("withdraw", "sp1 neighbor 10.0.1.1 203.0.113.0/24", net=self.net)
        self.assertIn("203.0.113.0/24", printed)
        self.assertIn("10.0.1.1", printed)
        self.assertEqual(len(self.writes), 1)

    def test_withdraw_all_reports_every_neighbor_cleared(self) -> None:
        printed = cli_output("withdraw", "sp1 all", net=self.net)
        self.assertIn("10.0.1.1", printed)
        self.assertIn("2001:db8:1::1", printed)
        self.assertEqual(len(self.writes), 2)

    def test_withdraw_all_to_one_neighbor(self) -> None:
        printed = cli_output("withdraw", "sp1 neighbor 2001:db8:1::1 all", net=self.net)
        self.assertIn("2001:db8:1::1", printed)
        self.assertNotIn("10.0.1.1", printed)
        self.assertEqual(len(self.writes), 1)

    def test_a_bad_prefix_prints_the_parsers_message(self) -> None:
        printed = cli_output("withdraw", "sp1 203.0.113.0", net=self.net)
        self.assertIn("Error: prefix '203.0.113.0' needs a length", printed)

    def test_too_few_or_too_many_words_prints_usage(self) -> None:
        for line in ("", "sp1", "sp1 neighbor 10.0.1.1", "sp1 203.0.113.0/24 extra"):
            self.assertIn("Usage: withdraw <node>", cli_output("withdraw", line, net=self.net), line)

    def test_help_shows_both_forms_with_examples(self) -> None:
        doc = mnIPLabCLI.do_withdraw.__doc__ or ""
        self.assertIn("withdraw <node> [neighbor <ip>] <prefix>", doc)
        self.assertIn("withdraw <node> [neighbor <ip>] all", doc)
        self.assertIn("Examples:", doc)


if __name__ == "__main__":
    unittest.main()
