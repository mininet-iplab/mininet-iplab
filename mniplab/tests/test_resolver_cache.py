"""Tests for Resolver cache inspection, negative caching, and Access Control.

Unit coverage (unprivileged) parses real ``dump_cache`` and ``stats_noreset``
text captured from Unbound 1.17 in this repository's own hierarchy Lab,
exercises the query and Access Control services against a stub control
socket, and checks the CLI's rendering.

Privileged integration tests verify the behaviour those readings describe:
that a warm query is answered from cache, that an NXDOMAIN survives as a
negative entry, that flushing sends the next query back across the
hierarchy, that a hot Access Control reload turns a client away with
REFUSED without dropping what the cache already holds, and that the reload
lands inside its documented latency budget.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock
from typing import Dict, List, Optional

import pytest

from mniplab.errors import ResolverControlError
from mniplab.resolver.access import ResolverAccessService
from mniplab.resolver.cache_dump import parse_cache_dump
from mniplab.resolver.control_client import UnboundControlClient
from mniplab.resolver.models import AccessRule, CacheEntry, ResolverMetrics
from mniplab.resolver.provisioner import ResolverProvisioner
from mniplab.resolver.query import ResolverQueryService
from mniplab.resolver.service import ResolverService
from mniplab.tests.fakes import FakeLab, FakeNode, cli_output

# Captured verbatim from Unbound 1.17.1 in the Root -> TLD -> Authoritative
# Lab below, after one resolved name, one NXDOMAIN, and one NODATA query.
CACHE_DUMP = """START_RRSET_CACHE
;rrset 300 1 0 2 0
example.com.\t300\tIN\tNS\tns.example.com.
;rrset 300 1 0 8 0
web.example.com.\t300\tIN\tA\t10.0.5.2
;rrset 60 1 0 7 0
example.com.\t60\tIN\tSOA\tauth1.example.com. hostmaster.example.com. 1 3600 900 604800 300
;rrset 300 1 0 1 0
ns.example.com.\t300\tIN\tA\t10.0.3.2
;rrset 300 1 0 8 0
.\t300\tIN\tNS\troot1.
;rrset 60 1 0 7 0
.\t60\tIN\tSOA\troot1. hostmaster. 1 3600 900 604800 300
END_RRSET_CACHE
START_MSG_CACHE
msg web.example.com. IN A 32896 1 300 0 1 0 0
web.example.com. IN A 0
msg web.example.com. IN AAAA 32896 1 60 0 0 1 0
example.com. IN SOA 4
msg nosuch.example.com. IN A 32899 1 45 0 0 1 0
example.com. IN SOA 4
msg . IN NS 32896 1 300 0 1 0 1
. IN NS 0
root1. IN A 0
END_MSG_CACHE
EOF
"""

STATS = {
    "total.num.queries": 8,
    "total.num.cachehits": 6,
    "total.num.cachemiss": 2,
    "total.recursion.time.avg": 0.00042,
}


class StubControlClient:
    """An Unbound control socket stand-in recording the commands it received."""

    def __init__(
        self,
        dump: str = CACHE_DUMP,
        stats: Optional[Dict[str, float]] = None,
        reload_error: Optional[str] = None,
    ) -> None:
        self.dump = dump
        self.stats = dict(stats if stats is not None else STATS)
        self.reload_error = reload_error
        self.commands: List[str] = []

    def dump_cache(self) -> str:
        """Return the canned cache dump."""
        self.commands.append("dump_cache")
        return self.dump

    def stats_noreset(self) -> Dict[str, float]:
        """Return the canned counters."""
        self.commands.append("stats_noreset")
        return dict(self.stats)

    def reload_keep_cache(self) -> None:
        """Record the hot reload, or fail as the daemon would."""
        self.commands.append("reload_keep_cache")
        if self.reload_error:
            raise ResolverControlError(self.reload_error)

    def flush_zone(self, zone: str) -> str:
        """Record a subtree eviction."""
        self.commands.append(f"flush_zone {zone}")
        return "ok removed 4 rrsets, 2 messages"

    def flush_name(self, name: str) -> str:
        """Record a single-name eviction."""
        self.commands.append(f"flush {name}")
        return "ok"

    def is_running(self) -> bool:
        """Report the daemon as answering."""
        return True


class ResolverServiceTestCase(unittest.TestCase):
    """Base wiring a stub control socket in front of one resolving Node."""

    def setUp(self) -> None:
        self.service = ResolverService(root_hints=["10.0.1.2"])
        self.node = FakeNode("res1", services=[self.service])
        self.net = FakeLab(self.node)
        self.client = StubControlClient()

        patcher = mock.patch.object(
            ResolverQueryService,
            "get_client",
            classmethod(
                lambda cls, net, node_name: (self.node, self.service, self.client)
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


@pytest.mark.unit
class CacheDumpParsingTests(unittest.TestCase):
    """Tests for turning Unbound's cache dump into Cache Entries."""

    def setUp(self) -> None:
        self.entries = parse_cache_dump(CACHE_DUMP)

    def _find(self, name: str, rtype: str) -> CacheEntry:
        """Return the single entry for one name and type."""
        matches = [
            entry
            for entry in self.entries
            if entry.name == name and entry.rtype == rtype
        ]
        self.assertEqual(len(matches), 1, f"expected one {name} {rtype}")
        return matches[0]

    def test_positive_rrset_carries_remaining_ttl_and_data(self) -> None:
        """A cached answer is reported under its own name with its TTL left."""
        entry = self._find("web.example.com.", "A")

        self.assertEqual(entry.ttl, 300)
        self.assertEqual(entry.data, "10.0.5.2")
        self.assertEqual(entry.status, "positive")
        self.assertFalse(entry.negative)

    def test_delegation_records_learned_on_the_walk_are_cached(self) -> None:
        """The referrals the Iterative Walk collected are visible too."""
        self.assertEqual(self._find(".", "NS").data, "root1.")
        self.assertEqual(self._find("ns.example.com.", "A").data, "10.0.3.2")

    def test_nxdomain_is_filed_under_the_queried_name(self) -> None:
        """A cached NXDOMAIN appears where the Learner asked, not under the SOA."""
        entry = self._find("nosuch.example.com.", "A")

        self.assertEqual(entry.status, "NXDOMAIN")
        self.assertTrue(entry.negative)
        self.assertEqual(entry.ttl, 45)

    def test_nxdomain_is_correlated_with_the_soa_bounding_it(self) -> None:
        """RFC 2308 caches the denial against an SOA, so the SOA is shown."""
        entry = self._find("nosuch.example.com.", "A")

        self.assertEqual(entry.soa, "example.com.")
        self.assertIn("auth1.example.com.", entry.data)

    def test_nodata_is_distinguished_from_nxdomain(self) -> None:
        """A name that exists without the queried type caches as NODATA."""
        entry = self._find("web.example.com.", "AAAA")

        self.assertEqual(entry.status, "NODATA")
        self.assertTrue(entry.negative)
        self.assertEqual(entry.soa, "example.com.")

    def test_answered_messages_do_not_become_negative_entries(self) -> None:
        """A message with an answer section is already in the RRset cache."""
        self.assertEqual(
            [entry.status for entry in self.entries if entry.name == "web.example.com."
             and entry.rtype == "A"],
            ["positive"],
        )

    def test_entries_are_stably_ordered(self) -> None:
        """Unbound dumps in hash order; a ticking table must not reshuffle."""
        keys = [entry.sort_key for entry in self.entries]

        self.assertEqual(keys, sorted(keys))

    def test_empty_dump_yields_no_entries(self) -> None:
        """A resolver that has answered nothing holds nothing."""
        empty = "START_RRSET_CACHE\nEND_RRSET_CACHE\nSTART_MSG_CACHE\nEND_MSG_CACHE\nEOF\n"

        self.assertEqual(parse_cache_dump(empty), [])

    def test_a_cached_referral_is_not_read_as_nodata(self) -> None:
        """A referral is NOERROR with no answer too, but names no SOA.

        RFC 2308 defines NODATA by the SOA in the authority section. Without
        that test a delegation would surface to a Learner as a phantom
        NODATA for a name that resolves perfectly well.
        """
        referral = (
            "START_RRSET_CACHE\n"
            ";rrset 300 1 0 2 0\n"
            "com.\t300\tIN\tNS\tns.com.\n"
            "END_RRSET_CACHE\n"
            "START_MSG_CACHE\n"
            "msg deep.com. IN A 32896 1 300 0 0 1 0\n"
            "com. IN NS 0\n"
            "END_MSG_CACHE\n"
            "EOF\n"
        )

        entries = parse_cache_dump(referral)

        self.assertEqual([entry.name for entry in entries], ["com."])
        self.assertFalse(any(entry.negative for entry in entries))

    def test_dnssec_validation_status_and_ad_flag(self) -> None:
        """Cache dump parser extracts secure/bogus/insecure status and AD flag."""
        dump = (
            "START_RRSET_CACHE\n"
            ";rrset 300 1 1 12 5\n"
            "web.example.com.\t300\tIN\tA\t10.0.5.2\n"
            ";rrset 300 1 0 8 1\n"
            "bad.example.com.\t300\tIN\tA\t10.0.5.99\n"
            ";rrset 300 1 0 8 0\n"
            "plain.example.com.\t300\tIN\tA\t10.0.5.3\n"
            "END_RRSET_CACHE\n"
            "START_MSG_CACHE\n"
            "msg web.example.com. IN A 32928 1 300 5 1 0 0\n"
            "web.example.com. IN A 0\n"
            "msg nosuch.example.com. IN A 32931 1 45 5 0 1 0\n"
            "example.com. IN SOA 4\n"
            "END_MSG_CACHE\n"
            "EOF\n"
        )
        entries = parse_cache_dump(dump)
        by_name = {e.name: e for e in entries}

        # web.example.com. is secure with AD=True
        web = by_name["web.example.com."]
        self.assertEqual(web.security, "secure")
        self.assertTrue(web.authenticated_data)

        # bad.example.com. is bogus with AD=False
        bad = by_name["bad.example.com."]
        self.assertEqual(bad.security, "bogus")
        self.assertFalse(bad.authenticated_data)

        # plain.example.com. is insecure with AD=False
        plain = by_name["plain.example.com."]
        self.assertEqual(plain.security, "insecure")
        self.assertFalse(plain.authenticated_data)

        # nosuch.example.com. negative NXDOMAIN is secure with AD=True
        nosuch = by_name["nosuch.example.com."]
        self.assertEqual(nosuch.status, "NXDOMAIN")
        self.assertEqual(nosuch.security, "secure")
        self.assertTrue(nosuch.authenticated_data)

    def test_a_validated_rrset_carries_ad_without_a_cached_message(self) -> None:
        """AD follows the validation status, not the cached message's flags.

        Unbound computes the AD bit when it answers, so the flags word it
        stores with a cached message never carries it — and a validated RRset
        often has no cached message beside it at all. This is the shape a real
        ``dump_cache`` has after one validated query, and it is the shape the
        Resolver panel's AD marker depends on.
        """
        dump = (
            "START_RRSET_CACHE\n"
            ";rrset 32 1 1 11 5\n"
            "web.example.com.\t32\tIN\tA\t10.0.6.100\n"
            ";rrset 32 1 0 1 0\n"
            "ns1.example.com.\t32\tIN\tA\t10.0.3.2\n"
            "END_RRSET_CACHE\n"
            "START_MSG_CACHE\n"
            "END_MSG_CACHE\n"
            "EOF\n"
        )

        by_name = {entry.name: entry for entry in parse_cache_dump(dump)}

        web = by_name["web.example.com."]
        self.assertEqual(web.security, "secure")
        self.assertTrue(web.authenticated_data)

        glue = by_name["ns1.example.com."]
        self.assertEqual(glue.security, "insecure")
        self.assertFalse(glue.authenticated_data)


@pytest.mark.unit
class ResolverMetricsTests(unittest.TestCase):
    """Tests for the aggregate query counters."""

    def test_metrics_parses_dnssec_counters(self) -> None:
        """ResolverMetrics holds and exposes DNSSEC security counters."""
        metrics = ResolverMetrics(
            queries=10,
            hits=5,
            misses=5,
            dnssec_secure=4,
            dnssec_bogus=1,
            dnssec_insecure=2,
        )
        self.assertEqual(metrics.dnssec_secure, 4)
        self.assertEqual(metrics.dnssec_bogus, 1)
        self.assertEqual(metrics.dnssec_insecure, 2)
        d = metrics.to_dict()
        self.assertEqual(d["dnssec_secure"], 4)
        self.assertEqual(d["dnssec_bogus"], 1)
        self.assertEqual(d["dnssec_insecure"], 2)


    def test_hit_ratio_is_a_percentage_of_all_queries(self) -> None:
        """Six hits in eight queries is 75%."""
        self.assertEqual(ResolverMetrics(queries=8, hits=6, misses=2).hit_ratio, 75.0)

    def test_hit_ratio_before_any_query_is_zero(self) -> None:
        """A fresh Resolver reports 0%, not a division by zero."""
        self.assertEqual(ResolverMetrics().hit_ratio, 0.0)

    def test_to_dict_carries_every_displayed_field(self) -> None:
        """The Web UI's metric cards read these keys."""
        self.assertEqual(
            ResolverMetrics(queries=4, hits=1, misses=3, recursion_time_ms=0.42).to_dict(),
            {
                "queries": 4,
                "hits": 1,
                "misses": 3,
                "hit_ratio": 25.0,
                "recursion_time_ms": 0.42,
                "dnssec_secure": 0,
                "dnssec_bogus": 0,
                "dnssec_insecure": 0,
            },
        )


@pytest.mark.unit
class ResolverQueryServiceTests(ResolverServiceTestCase):
    """Tests for reading and invalidating a Resolver's cache."""

    def test_get_cache_returns_parsed_entries(self) -> None:
        """The cache is read over the control socket and parsed."""
        entries = ResolverQueryService.get_cache(self.net, "res1")

        self.assertIn("dump_cache", self.client.commands)
        self.assertTrue(any(entry.negative for entry in entries))

    def test_get_metrics_converts_recursion_time_to_milliseconds(self) -> None:
        """Unbound counts seconds; a sub-millisecond hit needs finer units."""
        metrics = ResolverQueryService.get_metrics(self.net, "res1")

        self.assertEqual(metrics.queries, 8)
        self.assertEqual(metrics.hit_ratio, 75.0)
        self.assertEqual(metrics.recursion_time_ms, 0.42)

    def test_flush_without_a_target_clears_the_whole_cache(self) -> None:
        """Every cached name sits beneath the root, so flushing it clears all."""
        result = ResolverQueryService.flush_cache(self.net, "res1")

        self.assertEqual(self.client.commands, ["flush_zone ."])
        self.assertEqual(result["scope"], "zone")
        self.assertEqual(result["target"], ".")

    def test_flush_with_a_name_evicts_only_that_name(self) -> None:
        """Flushing one name leaves the rest of the walk cached."""
        ResolverQueryService.flush_cache(self.net, "res1", "web.example.com")

        self.assertEqual(self.client.commands, ["flush web.example.com"])

    def test_flush_with_zone_scope_evicts_the_subtree(self) -> None:
        """Zone scope evicts the name and everything beneath it."""
        ResolverQueryService.flush_cache(
            self.net, "res1", "example.com", scope="zone"
        )

        self.assertEqual(self.client.commands, ["flush_zone example.com"])

    def test_unknown_flush_scope_is_rejected(self) -> None:
        """Only name and zone scopes exist."""
        with self.assertRaises(ValueError):
            ResolverQueryService.flush_cache(self.net, "res1", "x", scope="everything")

    def test_control_failures_surface_as_value_errors(self) -> None:
        """The CLI and Web API both catch ValueError, so faults arrive as one."""
        self.client.dump_cache = mock.Mock(  # type: ignore[method-assign]
            side_effect=ResolverControlError("socket gone")
        )

        with self.assertRaises(ValueError):
            ResolverQueryService.get_cache(self.net, "res1")

    def test_missing_node_is_reported(self) -> None:
        """Naming a Node the Lab does not have is an error, not an empty cache."""
        with self.assertRaises(ValueError):
            ResolverQueryService.find_service(self.net, "nope")

    def test_node_without_a_resolver_is_reported(self) -> None:
        """A Node offering no Resolver Service has no cache to show."""
        net = FakeLab(FakeNode("h1"))

        with self.assertRaises(ValueError):
            ResolverQueryService.find_service(net, "h1")


@pytest.mark.unit
class ResolverInfoTests(ResolverServiceTestCase):
    """Tests for the Resolver's reported mode, hierarchy, and policy."""

    def test_info_reports_mode_hierarchy_and_rules(self) -> None:
        """show_resolver and the Web API read one dict."""
        with mock.patch("os.path.exists", return_value=False):
            info = ResolverQueryService.get_resolver_info(self.net, "res1")

        self.assertEqual(info["mode"], "iterative")
        self.assertEqual(info["root_hints"][0]["addresses"], ["10.0.1.2"])
        self.assertEqual(info["forwarders"], [])
        self.assertEqual(
            [rule["subnet"] for rule in info["access_control"]],
            ["0.0.0.0/0", "::/0"],
        )

    def test_info_reports_a_stopped_resolver(self) -> None:
        """No socket on disk means the daemon never came up."""
        with mock.patch("os.path.exists", return_value=False):
            info = ResolverQueryService.get_resolver_info(self.net, "res1")

        self.assertFalse(info["running"])
        self.assertEqual(info["socket_state"], "inactive")



@pytest.mark.unit
class ResolverAccessServiceTests(ResolverServiceTestCase):
    """Tests for Learner-authored Access Control applied without a cache drop."""

    def _rendered_config(self) -> str:
        """Return the unbound.conf now on disk for the resolving Node."""
        path = os.path.join(self.net.resolver_dir("res1", "etc"), "unbound.conf")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_adding_a_rule_rewrites_the_config_and_reloads_hot(self) -> None:
        """A new rule reaches the daemon without invalidating the cache."""
        ResolverAccessService.add_rule(self.net, "res1", "10.0.2.0/24", "refuse")

        self.assertIn("access-control: 10.0.2.0/24 refuse", self._rendered_config())
        self.assertEqual(self.client.commands, ["reload_keep_cache"])

    def test_adding_a_rule_for_a_known_subnet_replaces_its_action(self) -> None:
        """A subnet gets one rule, so re-declaring it changes the verdict."""
        ResolverAccessService.add_rule(self.net, "res1", "10.0.2.0/24", "refuse")
        rules = ResolverAccessService.add_rule(
            self.net, "res1", "10.0.2.0/24", "allow"
        )

        matching = [rule for rule in rules if rule["subnet"] == "10.0.2.0/24"]
        self.assertEqual(matching, [{"subnet": "10.0.2.0/24", "action": "allow", "family": 4}])

    def test_host_address_is_normalized_to_its_subnet(self) -> None:
        """A Learner naming a host address means the subnet containing it."""
        rules = ResolverAccessService.add_rule(self.net, "res1", "10.0.2.7/24", "deny")

        self.assertIn(
            {"subnet": "10.0.2.0/24", "action": "deny", "family": 4}, rules
        )

    def test_removing_a_rule_reloads_the_remaining_policy(self) -> None:
        """Deleting a rule is applied the same hot way as adding one."""
        ResolverAccessService.add_rule(self.net, "res1", "10.0.2.0/24", "refuse")
        rules = ResolverAccessService.remove_rule(self.net, "res1", "10.0.2.0/24")

        self.assertNotIn("10.0.2.0/24", [rule["subnet"] for rule in rules])
        self.assertNotIn("access-control: 10.0.2.0/24", self._rendered_config())

    def test_removing_an_unknown_rule_is_rejected(self) -> None:
        """Deleting a subnet the Resolver never had is a mistake worth naming."""
        with self.assertRaises(ValueError):
            ResolverAccessService.remove_rule(self.net, "res1", "192.168.9.0/24")

    def test_invalid_action_is_rejected_before_the_daemon_sees_it(self) -> None:
        """A typo must not reach unbound.conf and stop the daemon reloading."""
        with self.assertRaises(ValueError):
            ResolverAccessService.add_rule(self.net, "res1", "10.0.2.0/24", "block")

        self.assertEqual(self.client.commands, [])

    def test_a_rejected_reload_leaves_the_previous_rules_in_force(self) -> None:
        """If the daemon will not take the rules, the Lab keeps the old ones."""
        self.client.reload_error = "reload failed"

        with self.assertRaises(ValueError):
            ResolverAccessService.add_rule(self.net, "res1", "10.0.2.0/24", "refuse")

        self.assertEqual(
            [rule.subnet for rule in self.service.access_control],
            ["0.0.0.0/0", "::/0"],
        )

    def test_a_rejected_reload_also_rolls_the_config_file_back(self) -> None:
        """A rollback that only restored memory would arm the next restart.

        The rejected rules reach unbound.conf before the reload is attempted,
        so leaving them there would let a later restart apply a policy the
        Lab reports as not in force.
        """
        self.client.reload_error = "reload failed"

        with self.assertRaises(ValueError):
            ResolverAccessService.add_rule(self.net, "res1", "10.0.2.0/24", "refuse")

        self.assertNotIn("10.0.2.0/24", self._rendered_config())

    def test_listing_rules_reads_without_touching_the_daemon(self) -> None:
        """Showing the policy is not a reason to reload it."""
        rules = ResolverAccessService.list_rules(self.net, "res1")

        self.assertEqual(
            [rule["subnet"] for rule in rules], ["0.0.0.0/0", "::/0"]
        )
        self.assertEqual(self.client.commands, [])

    def test_rules_reset_when_the_lab_stops(self) -> None:
        """Learner rules are Ephemeral, so the next class starts from the Lab."""
        ResolverAccessService.add_rule(self.net, "res1", "10.0.2.0/24", "refuse")

        self.service.reset_access_control()

        self.assertEqual(
            [rule.subnet for rule in self.service.access_control],
            ["0.0.0.0/0", "::/0"],
        )


@pytest.mark.unit
class ResolverCLITests(ResolverServiceTestCase):
    """Tests for the terminal parity commands."""

    def _run(self, command: str, line: str) -> str:
        """Run one CLI command against the stub network and capture its output."""
        return cli_output(command, line, net=self.net)

    def test_show_cache_banners_metrics_then_lists_entries(self) -> None:
        """The metrics banner is what makes the cache table mean something."""
        output = self._run("show_cache", "res1")

        self.assertIn("Hit ratio: 75.0%", output)
        self.assertIn("web.example.com.", output)
        self.assertIn("10.0.5.2", output)

    def test_show_cache_marks_negative_entries(self) -> None:
        """A denial must not read as an answer."""
        output = self._run("show_cache", "res1")

        self.assertIn("NXDOMAIN", output)
        self.assertIn("NODATA", output)

    def test_show_cache_without_a_node_prints_usage(self) -> None:
        """A bare command explains itself rather than failing."""
        self.assertIn("Usage: show_cache", self._run("show_cache", ""))

    def test_show_cache_reports_an_empty_cache(self) -> None:
        """An empty table needs saying so, not a blank screen."""
        self.client.dump = "START_RRSET_CACHE\nEND_RRSET_CACHE\nEOF\n"

        self.assertIn("Cache is empty", self._run("show_cache", "res1"))

    def test_flush_cache_without_a_target_clears_everything(self) -> None:
        """The bare command is the "start cold again" gesture."""
        output = self._run("flush_cache", "res1")

        self.assertEqual(self.client.commands, ["flush_zone ."])
        self.assertIn("Flushed zone .", output)

    def test_flush_cache_with_a_name_evicts_that_name(self) -> None:
        """One name goes cold while the rest of the walk stays warm."""
        self._run("flush_cache", "res1 web.example.com")

        self.assertEqual(self.client.commands, ["flush web.example.com"])

    def test_flush_cache_zone_flag_evicts_the_subtree(self) -> None:
        """--zone is how a Learner clears a whole branch of the hierarchy."""
        self._run("flush_cache", "res1 example.com --zone")

        self.assertEqual(self.client.commands, ["flush_zone example.com"])

    def test_show_resolver_reports_mode_hints_and_rules(self) -> None:
        """One command answers "how is this Resolver set up?"."""
        output = self._run("show_resolver", "res1")

        self.assertIn("Mode: iterative", output)
        self.assertIn("10.0.1.2", output)
        self.assertIn("0.0.0.0/0", output)

    def test_resolver_access_add_prints_the_new_policy(self) -> None:
        """The Learner sees what is now in force, not just an acknowledgement."""
        output = self._run("resolver_access", "res1 add 10.0.2.0/24 refuse")

        self.assertIn("10.0.2.0/24", output)
        self.assertIn("refuse", output)
        self.assertIn("reload_keep_cache", self.client.commands)

    def test_resolver_access_del_removes_the_rule(self) -> None:
        """Deleting is the other half of authoring."""
        self._run("resolver_access", "res1 add 10.0.2.0/24 refuse")
        output = self._run("resolver_access", "res1 del 10.0.2.0/24")

        self.assertNotIn("10.0.2.0/24", output)

    def test_resolver_access_reports_an_invalid_action(self) -> None:
        """A typo is named rather than silently ignored."""
        self.assertIn(
            "Error:", self._run("resolver_access", "res1 add 10.0.2.0/24 block")
        )

    def test_resolver_access_with_only_a_node_lists_the_policy(self) -> None:
        """The bare form is the terminal parity for reading the rules."""
        output = self._run("resolver_access", "res1")

        self.assertIn("0.0.0.0/0", output)
        self.assertIn("allow", output)
        self.assertEqual(self.client.commands, [])

    def test_resolver_access_without_a_node_prints_usage(self) -> None:
        """All three forms of the command are shown."""
        output = self._run("resolver_access", "")

        self.assertIn("resolver_access <node>", output)
        self.assertIn("add <cidr>", output)
        self.assertIn("del <cidr>", output)


if __name__ == "__main__":
    unittest.main()
