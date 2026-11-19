"""Tests for the Recursive Caching Resolver Service.

Unit coverage (unprivileged) covers Access Control rule and Root Hint models,
ResolverService declaration and Ephemeral reset, unbound.conf and Root Hints
file rendering, and start-time diagnostics.

Privileged integration tests start Unbound inside real Node namespaces and
verify the control socket, an Iterative Walk across an in-lab Root -> TLD ->
Authoritative hierarchy, forwarding mode, cold-versus-warm cache behaviour,
and teardown.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock
from typing import Any, Dict, List, Tuple

import pytest

from mniplab.errors import ConfigurationError, ResolverControlError
from mniplab.resolver.control_client import UnboundControlClient
from mniplab.resolver.diagnostics import ResolverDiagnostics
from mniplab.resolver.models import AccessRule, RootHint
from mniplab.resolver.provisioner import ResolverProvisioner
from mniplab.resolver.service import ResolverService
from mniplab.service import Service
from mniplab.tests.fakes import FakeIntf, FakeLab, FakeNode, ServicesReadyMixin
from mniplab.tests.labs import LabCase, build_dns_test_lab


class StubRootZoneService(Service):
    """An Authoritative DNS Service stand-in hosting one Zone apex."""

    name = "dns"

    def __init__(self, apex: str = ".") -> None:
        self.zones = {apex: object()}


@pytest.mark.unit
class AccessRuleTests(unittest.TestCase):
    """Tests for the AccessRule model."""

    def test_normalizes_subnet_and_action(self) -> None:
        """A host address inside a subnet is stored as the subnet itself."""
        rule = AccessRule("10.0.2.5/24", "REFUSE")
        self.assertEqual(rule.subnet, "10.0.2.0/24")
        self.assertEqual(rule.action, "refuse")
        self.assertEqual(rule.family, 4)

    def test_ipv6_rule(self) -> None:
        """IPv6 subnets are accepted and report family 6."""
        rule = AccessRule("2001:db8::/64", "allow")
        self.assertEqual(rule.subnet, "2001:db8::/64")
        self.assertEqual(rule.family, 6)

    def test_invalid_subnet_raises(self) -> None:
        """A subnet that is not CIDR is rejected at declaration."""
        with self.assertRaises(ValueError):
            AccessRule("not-a-subnet", "allow")

    def test_invalid_action_raises(self) -> None:
        """Only Unbound's three policies are accepted."""
        with self.assertRaises(ValueError):
            AccessRule("10.0.0.0/8", "drop")

    def test_to_dict_and_equality(self) -> None:
        """to_dict() carries the fields the CLI and Web API render."""
        rule = AccessRule("10.0.0.0/8", "deny")
        self.assertEqual(
            rule.to_dict(), {"subnet": "10.0.0.0/8", "action": "deny", "family": 4}
        )
        self.assertEqual(rule, AccessRule("10.0.0.1/8", "deny"))


@pytest.mark.unit
class RootHintTests(unittest.TestCase):
    """Tests for the RootHint model."""

    def test_address_hint_resolves_to_declared_address(self) -> None:
        """A hint declared as an address resolves to exactly that address."""
        hint = RootHint("hint1", addresses=["10.0.0.1"])
        self.assertEqual(hint.resolve_addresses(), ["10.0.0.1"])
        self.assertEqual(hint.fqdn, "hint1.root-servers.iplab.")

    def test_node_hint_reads_every_interface_address(self) -> None:
        """A hint declared as a Node resolves to all its interface addresses."""
        node = FakeNode(
            "root1",
            intfs=[
                FakeIntf("root1-eth0", ip="10.0.0.1", ip6="2001:db8::1"),
                FakeIntf("root1-eth1", ip="10.0.9.1"),
            ],
        )
        hint = RootHint("root1", node=node)
        self.assertEqual(
            hint.resolve_addresses(), ["10.0.0.1", "2001:db8::1", "10.0.9.1"]
        )

    def test_node_hint_skips_loopback(self) -> None:
        """Loopback addresses are useless as hints and are left out."""
        node = FakeNode(
            "root1",
            intfs=[FakeIntf("lo", ip="127.0.0.1"), FakeIntf("root1-eth0", ip="10.0.0.1")],
        )
        self.assertEqual(RootHint("root1", node=node).resolve_addresses(), ["10.0.0.1"])


@pytest.mark.unit
class ResolverServiceTests(unittest.TestCase):
    """Tests for ResolverService declaration and Ephemeral Access Control."""

    def test_defaults_allow_all_lab_traffic(self) -> None:
        """A Resolver with no declared rules accepts every in-lab client."""
        service = ResolverService(forwarders=["10.0.0.1"])
        self.assertEqual(
            [rule.to_dict()["subnet"] for rule in service.access_control],
            ["0.0.0.0/0", "::/0"],
        )
        self.assertTrue(all(r.action == "allow" for r in service.access_control))

    def test_default_ttl_caps(self) -> None:
        """TTL caps default to the classroom-scale 300s and 60s."""
        service = ResolverService(forwarders=["10.0.0.1"])
        self.assertEqual(service.max_ttl, 300)
        self.assertEqual(service.max_negative_ttl, 60)

    def test_badge_and_families(self) -> None:
        """A resolving Node wears a RESOLVER badge and serves both families."""
        service = ResolverService(forwarders=["10.0.0.1"])
        self.assertEqual(service.badge_text, "RESOLVER")
        self.assertEqual(service.families, [4, 6])

    def test_root_hints_accept_nodes_and_addresses(self) -> None:
        """Root Hints may be declared as Nodes, as IP strings, or as both."""
        root = FakeNode("root1", intfs=[FakeIntf("root1-eth0", ip="10.0.0.1")])
        service = ResolverService(root_hints=[root, "10.0.0.9"])

        self.assertEqual([h.label for h in service.root_hints], ["root1", "hint2"])
        self.assertEqual(service.root_hints[0].resolve_addresses(), ["10.0.0.1"])
        self.assertEqual(service.root_hints[1].resolve_addresses(), ["10.0.0.9"])

    def test_invalid_root_hint_address_raises(self) -> None:
        """A string hint that is not an IP address is rejected at declaration."""
        with self.assertRaises(ValueError):
            ResolverService(root_hints=["not-an-ip"])

    def test_invalid_forwarder_raises(self) -> None:
        """A forwarder that is not an IP address is rejected at declaration."""
        with self.assertRaises(ValueError):
            ResolverService(forwarders=["upstream.example.com"])

    def test_invalid_ttl_raises(self) -> None:
        """A TTL cap must be a positive whole number of seconds."""
        with self.assertRaises(ValueError):
            ResolverService(forwarders=["10.0.0.1"], max_ttl=0)

    def test_mode_reports_hierarchy_strategy(self) -> None:
        """mode names how the Resolver reaches the hierarchy."""
        self.assertEqual(ResolverService(forwarders=["10.0.0.1"]).mode, "forwarding")
        self.assertEqual(ResolverService(root_hints=["10.0.0.1"]).mode, "iterative")
        self.assertEqual(ResolverService().mode, "default")
        self.assertEqual(
            ResolverService(root_hints=["10.0.0.1"], forwarders=["10.0.0.2"]).mode,
            "forwarding",
        )

    def test_reset_access_control_discards_learner_rules(self) -> None:
        """Learner-authored rules vanish, restoring the Lab Example's own."""
        service = ResolverService(
            forwarders=["10.0.0.1"], access_control=[AccessRule("10.0.0.0/8", "allow")]
        )
        service.set_access_control(
            [AccessRule("10.0.0.0/8", "allow"), AccessRule("10.0.2.0/24", "refuse")]
        )
        self.assertEqual(len(service.access_control), 2)

        service.reset_access_control()
        self.assertEqual(service.access_control, [AccessRule("10.0.0.0/8", "allow")])

    def test_find_on_node(self) -> None:
        """find_on() locates the Resolver attached to a Node, or explains its absence."""
        service = ResolverService(forwarders=["10.0.0.1"])
        node = FakeNode("res1", services=[service])
        self.assertIs(ResolverService.find_on(node), service)

        with self.assertRaises(ValueError):
            ResolverService.find_on(FakeNode("h1"))


@pytest.mark.unit
class ResolverProvisionerRenderingTests(unittest.TestCase):
    """Tests for unbound.conf and Root Hints file rendering."""

    def test_root_hints_file_is_rfc1035(self) -> None:
        """The hints file carries an NS record per hint plus its glue records."""
        root = FakeNode(
            "root1", intfs=[FakeIntf("root1-eth0", ip="10.0.0.1", ip6="2001:db8::1")]
        )
        service = ResolverService(root_hints=[root])

        rendered = ResolverProvisioner.render_root_hints(service.root_hints)

        self.assertIn(".        3600000  IN  NS  root1.root-servers.iplab.", rendered)
        self.assertIn("root1.root-servers.iplab.  3600000  IN  A  10.0.0.1", rendered)
        self.assertIn(
            "root1.root-servers.iplab.  3600000  IN  AAAA  2001:db8::1", rendered
        )

    def test_iterative_config_loads_hints_and_omits_forwarding(self) -> None:
        """Iterative mode points Unbound at the hints file and forwards nothing."""
        service = ResolverService(root_hints=["10.0.0.1"])

        config = ResolverProvisioner.render_config(service)

        self.assertIn('root-hints: "/etc/unbound/root.hints"', config)
        self.assertNotIn("forward-zone:", config)
        self.assertIn('module-config: "iterator"', config)

    def test_trust_anchors_config_renders_validator_and_directives(self) -> None:
        """When trust anchors are configured, validator is loaded and directives are rendered."""
        anchor_rr = ". 300 IN DS 17782 13 2 7ab44252b42a2d2462f5b4c0e681393b04dc64d7a3a2c638d8642c1e510d014f"
        service = ResolverService(root_hints=["10.0.0.1"], trust_anchors=[anchor_rr])

        config = ResolverProvisioner.render_config(service)

        self.assertIn('module-config: "validator iterator"', config)
        self.assertIn("val-log-level: 2", config)
        self.assertIn("val-permissive-mode: no", config)
        self.assertIn("insecure-lan-zones: yes", config)
        self.assertIn(f'trust-anchor: "{anchor_rr}"', config)

    def test_trust_anchors_resolves_zone_and_node(self) -> None:
        """ResolverService resolves Zone and Node trust anchors into DS records."""
        from mniplab.dns.models import Zone
        from mniplab.dns.service import DNSService

        zone = Zone("example.com.", dnssec=True)
        node = FakeNode("auth1", services=[DNSService(zone)])

        service = ResolverService(root_hints=["10.0.0.1"], trust_anchors=[node, zone])
        config = ResolverProvisioner.render_config(service)

        self.assertIn('module-config: "validator iterator"', config)
        self.assertIn("trust-anchor: \"example.com.", config)

    def test_forwarding_config_renders_forward_zone(self) -> None:
        """Forwarding mode renders a root forward-zone with every upstream."""
        service = ResolverService(forwarders=["10.0.0.1", "2001:db8::1"])

        config = ResolverProvisioner.render_config(service)

        self.assertIn("forward-zone:", config)
        self.assertIn('name: "."', config)
        self.assertIn("forward-addr: 10.0.0.1", config)
        self.assertIn("forward-addr: 2001:db8::1", config)

    def test_config_renders_ttl_caps_and_access_control(self) -> None:
        """Declared TTL caps and Access Control rules reach unbound.conf."""
        service = ResolverService(
            forwarders=["10.0.0.1"],
            max_ttl=120,
            max_negative_ttl=30,
            access_control=[
                AccessRule("10.0.0.0/8", "allow"),
                AccessRule("10.0.2.0/24", "refuse"),
            ],
        )

        config = ResolverProvisioner.render_config(service)

        self.assertIn("cache-max-ttl: 120", config)
        self.assertIn("cache-max-negative-ttl: 30", config)
        self.assertIn("access-control: 10.0.0.0/8 allow", config)
        self.assertIn("access-control: 10.0.2.0/24 refuse", config)

    def test_config_enables_unix_socket_control(self) -> None:
        """Control is a UNIX socket path, which needs no certificate."""
        config = ResolverProvisioner.render_config(ResolverService(forwarders=["10.0.0.1"]))

        self.assertIn("control-enable: yes", config)
        self.assertIn("control-interface: /run/unbound/unbound.sock", config)
        self.assertNotIn("control-use-cert: yes", config)

    def test_config_listens_on_both_families(self) -> None:
        """The Resolver answers queries over IPv4 and IPv6 on port 53."""
        config = ResolverProvisioner.render_config(ResolverService(forwarders=["10.0.0.1"]))

        self.assertIn("interface: 0.0.0.0", config)
        self.assertIn("interface: ::", config)
        self.assertIn("port: 53", config)


@pytest.mark.unit
class ResolverProvisionerLifecycleTests(ServicesReadyMixin, unittest.TestCase):
    """Tests for directory preparation, config writing, and daemon commands."""

    def setUp(self) -> None:
        super().setUp()
        self.net = FakeLab()

    def _node(self, name: str, service: ResolverService) -> FakeNode:
        """Return a resolving Node the Lab holds."""
        node = FakeNode(name, services=[service])
        node.net = self.net
        return node

    def test_host_paths_are_scoped_per_node(self) -> None:
        """Each resolving Node gets its own tree in the Lab directory."""
        self.assertEqual(
            ResolverProvisioner.control_socket_host_path("res1", net=self.net),
            os.path.join(self.net.node_dir("res1"), "run", "unbound", "unbound.sock"),
        )
        self.assertNotEqual(
            ResolverProvisioner.host_dir("res1", net=self.net),
            ResolverProvisioner.host_dir("res2", net=self.net),
        )

    def test_ensure_available_names_the_node_missing_unbound(self) -> None:
        """A resolving Node with no unbound in PATH fails the preflight by name."""
        node = FakeNode("res1", services=[ResolverService()])

        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ConfigurationError) as ctx:
                ResolverProvisioner.ensure_available([node])
            ResolverProvisioner.ensure_available([FakeNode("h1")])

        err = str(ctx.exception)
        self.assertIn("res1", err)
        self.assertIn("unbound", err)

    def test_write_config_creates_config_and_hints(self) -> None:
        """Iterative mode writes both unbound.conf and the hints file."""
        service = ResolverService(root_hints=["10.0.0.1"])

        path = ResolverProvisioner.write_config("res1", service, net=self.net)

        self.assertTrue(os.path.exists(path))
        hints = os.path.join(self.net.resolver_dir("res1", "etc"), "root.hints")
        self.assertTrue(os.path.exists(hints))
        with open(hints, encoding="utf-8") as handle:
            self.assertIn("10.0.0.1", handle.read())

    def test_write_config_omits_hints_in_forwarding_mode(self) -> None:
        """Forwarding mode has no hierarchy to seed, so no hints file is written."""
        ResolverProvisioner.write_config(
            "res1", ResolverService(forwarders=["10.0.0.1"]), net=self.net
        )

        self.assertFalse(
            os.path.exists(os.path.join(self.net.resolver_dir("res1", "etc"), "root.hints"))
        )

    def test_start_binds_directories_and_launches_unbound(self) -> None:
        """Starting binds /etc/unbound and /run/unbound, then runs the daemon."""
        node = self._node("res1", ResolverService(forwarders=["10.0.0.1"]))

        ResolverProvisioner.start_node_services(node)

        etc_dir = self.net.resolver_dir("res1", "etc")
        run_dir = self.net.resolver_dir("res1", "run")
        self.assertIn("mount --bind", " ".join(node.recorder.commands))
        self.assertIn(f"mount --bind {etc_dir} /etc/unbound", node.recorder.commands)
        self.assertIn(f"mount --bind {run_dir} /run/unbound", node.recorder.commands)
        self.assertIn("unbound -c /etc/unbound/unbound.conf", node.recorder.commands)

    def test_stop_kills_daemon_removes_tree_and_resets_rules(self) -> None:
        """Stopping leaves no daemon, no ephemeral tree, and no Learner rules."""
        service = ResolverService(forwarders=["10.0.0.1"])
        node = self._node("res1", service)
        ResolverProvisioner.start_node_services(node)
        service.set_access_control([AccessRule("10.0.2.0/24", "refuse")])

        ResolverProvisioner.stop_node_services(node)

        self.assertIn("pkill -x unbound || true", node.recorder.commands)
        self.assertFalse(os.path.exists(self.net.resolver_dir("res1")))
        self.assertEqual(
            [rule.subnet for rule in service.access_control], ["0.0.0.0/0", "::/0"]
        )

    def test_start_all_ignores_nodes_without_a_resolver(self) -> None:
        """A Node with no Resolver Service is left alone."""
        plain = FakeNode("h1")

        ResolverProvisioner.start_all(FakeLab(plain), [plain])

        self.assertEqual(plain.recorder.commands, [])


@pytest.mark.unit
class ResolverServiceReadinessTests(unittest.TestCase):
    """start_resolver_service returns only once Unbound answers, or fails."""

    def _node(self) -> FakeNode:
        """Return a resolving Node held by a Lab of its own."""
        node = FakeNode("res1", services=[ResolverService(forwarders=["10.0.0.1"])])
        FakeLab(node)
        return node

    def test_start_waits_for_unbound_to_answer_on_its_control_socket(self) -> None:
        node = self._node()
        probed: list = []

        def is_running(client: Any) -> bool:
            probed.append(client.socket_path)
            return True

        with mock.patch(
            "mniplab.resolver.provisioner.UnboundControlClient.is_running", is_running
        ):
            ResolverProvisioner.start_node_services(node)

        self.assertEqual(
            probed, [ResolverProvisioner.control_socket_host_path("res1", net=node.net)]
        )

    def test_a_daemon_that_never_answers_fails_the_start_naming_its_log(self) -> None:
        node = self._node()

        with mock.patch.object(ResolverProvisioner, "READY_TIMEOUT", 0), mock.patch(
            "mniplab.resolver.provisioner.UnboundControlClient.is_running",
            return_value=False,
        ):
            with self.assertRaises(ConfigurationError) as ctx:
                ResolverProvisioner.start_node_services(node)

        message = str(ctx.exception)
        self.assertIn("res1", message)
        self.assertIn("unbound", message)
        self.assertIn("unbound.log", message)


@pytest.mark.unit
class ResolverDiagnosticsTests(unittest.TestCase):
    """Tests for fatal and teachable start-time diagnostics."""

    def _root_node(self, address: str = "10.0.0.1") -> FakeNode:
        return FakeNode(
            "root1",
            intfs=[FakeIntf("root1-eth0", ip=address)],
            services=[StubRootZoneService(".")],
        )

    def test_no_hierarchy_and_no_default_route_is_fatal(self) -> None:
        """Neither hints nor forwarders nor a default route can never resolve."""
        node = FakeNode(
            "res1", intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")], services=[ResolverService()]
        )

        with self.assertRaises(ConfigurationError) as ctx:
            ResolverDiagnostics.validate_network(FakeLab(node))

        self.assertIn("neither root_hints nor forwarders", str(ctx.exception))

    def test_no_hierarchy_with_default_route_starts(self) -> None:
        """A default route makes the built-in public hints reachable, so it starts."""
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService()],
        )
        node.params["defaultRoute"] = "via 10.0.0.1"

        self.assertEqual(ResolverDiagnostics.validate_network(FakeLab(node)), [])

    def test_unroutable_forwarder_is_fatal(self) -> None:
        """A forwarder off every attached subnet with no default route is fatal."""
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService(forwarders=["192.168.50.1"])],
        )

        with self.assertRaises(ConfigurationError) as ctx:
            ResolverDiagnostics.validate_network(FakeLab(node))

        self.assertIn("192.168.50.1", str(ctx.exception))

    def test_unroutable_root_hint_is_fatal(self) -> None:
        """A Root Hint address the Resolver has no route to is fatal."""
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService(root_hints=["192.168.50.1"])],
        )

        with self.assertRaises(ConfigurationError):
            ResolverDiagnostics.validate_network(FakeLab(node))

    def test_addressless_root_hint_node_is_fatal(self) -> None:
        """A hinted Node with no address can never be queried."""
        root = FakeNode("root1", intfs=[], services=[StubRootZoneService(".")])
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService(root_hints=[root])],
        )

        with self.assertRaises(ConfigurationError) as ctx:
            ResolverDiagnostics.validate_network(FakeLab(node))

        self.assertIn("resolves to no IP address", str(ctx.exception))

    def test_routable_hierarchy_on_root_zone_node_is_clean(self) -> None:
        """A hint at a Node hosting '.' on an attached subnet raises nothing."""
        root = self._root_node()
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService(root_hints=[root])],
        )

        warnings = ResolverDiagnostics.validate_network(FakeLab(node, root))

        self.assertEqual(warnings, [])

    def test_hint_at_node_without_root_zone_warns(self) -> None:
        """A hint at a Node that does not host '.' is teachable, so the Lab runs."""
        not_root = FakeNode("ns1", intfs=[FakeIntf("ns1-eth0", ip="10.0.0.1")])
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService(root_hints=[not_root])],
        )
        collected: List[str] = []

        warnings = ResolverDiagnostics.validate_network(
            FakeLab(node, not_root), warn_fn=collected.append
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("does not host the root zone", warnings[0])
        self.assertEqual(collected, warnings)

    def test_forwarder_that_serves_no_dns_warns(self) -> None:
        """A forwarder that is nobody's nameserver will time out; that is teachable."""
        neighbour = FakeNode("h9", intfs=[FakeIntf("h9-eth0", ip="10.0.0.9")])
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService(forwarders=["10.0.0.9"])],
        )

        warnings = ResolverDiagnostics.validate_network(
            FakeLab(node, neighbour), warn_fn=lambda _: None
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("not a Node offering DNS", warnings[0])

    def test_hints_shadowed_by_forwarders_warns(self) -> None:
        """Declaring both leaves the hints unused, which is worth saying out loud."""
        root = self._root_node()
        upstream = FakeNode(
            "up1",
            intfs=[FakeIntf("up1-eth0", ip="10.0.0.3")],
            services=[ResolverService(forwarders=["10.0.0.1"])],
        )
        node = FakeNode(
            "res1",
            intfs=[FakeIntf("res1-eth0", ip="10.0.0.2")],
            services=[ResolverService(root_hints=[root], forwarders=["10.0.0.3"])],
        )

        warnings = ResolverDiagnostics.validate_network(
            FakeLab(node, root, upstream), warn_fn=lambda _: None
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("Forwarding takes precedence", warnings[0])

    def test_router_hosting_a_resolver_is_not_called_unroutable(self) -> None:
        """A Router learns routes from its daemons, so an off-subnet hint is fine."""
        root = FakeNode(
            "root1",
            intfs=[FakeIntf("root1-eth0", ip="10.0.9.1")],
            services=[StubRootZoneService(".")],
        )
        router = FakeNode(
            "r1",
            intfs=[FakeIntf("r1-eth0", ip="10.0.0.1")],
            services=[ResolverService(root_hints=[root])],
        )
        router.is_router = True  # type: ignore[attr-defined]

        warnings = ResolverDiagnostics.validate_network(
            FakeLab(router, root), warn_fn=lambda _: None
        )

        self.assertEqual(warnings, [])

    def test_router_without_a_declared_hierarchy_still_starts(self) -> None:
        """A Router may learn a default route, so a bare Resolver on one can work."""
        router = FakeNode(
            "r1", intfs=[FakeIntf("r1-eth0", ip="10.0.0.1")], services=[ResolverService()]
        )
        router.is_router = True  # type: ignore[attr-defined]

        self.assertEqual(
            ResolverDiagnostics.validate_network(FakeLab(router)), []
        )

    def test_network_without_resolvers_is_skipped(self) -> None:
        """A Lab with no Resolver runs no Resolver diagnostics."""
        self.assertEqual(
            ResolverDiagnostics.validate_network(FakeLab(FakeNode("h1"))), []
        )


def _query_time_ms(dig_output: str) -> int:
    """Extract dig's reported query time in milliseconds.

    Args:
        dig_output: Full (non-short) dig output.

    Returns:
        The reported round-trip time in milliseconds.

    Raises:
        AssertionError: If dig reported no query time.
    """
    for line in dig_output.splitlines():
        if line.startswith(";; Query time:"):
            return int(line.split(":")[1].strip().split()[0])
    raise AssertionError(f"dig reported no query time:\n{dig_output}")


class ResolverIterativeWalkIntegrationTests(LabCase):
    """End-to-end resolution across the DNS Test Lab's hierarchy, no public internet."""

    def _resolving_lab(self) -> Tuple[Any, Dict[str, Any]]:
        """Build the DNS Test Lab with ``res1`` seeded from the in-lab Root."""
        net = self.lab()
        nodes = build_dns_test_lab(net)
        nodes["res1"].addService(ResolverService(root_hints=[nodes["root1"]]))
        return net, nodes

    def test_resolver_walks_lab_hierarchy_and_caches(self) -> None:
        """A Resolver seeded with in-lab Root Hints walks Root -> TLD -> Auth."""
        net, nodes = self._resolving_lab()
        client = nodes["client1"]

        self.start(net)
        control = UnboundControlClient(
            ResolverProvisioner.control_socket_host_path("res1", net=net)
        )
        self.assertTrue(control.is_running())

        cold = client.cmd("dig @10.0.4.2 web.example.com A")
        self.assertIn("status: NOERROR", cold)
        self.assertIn("10.0.5.2", cold)

        # A cold answer required an Iterative Walk; a warm one is served
        # from cache, which is the latency contrast the lab teaches.
        before = control.stats_noreset()
        warm = client.cmd("dig @10.0.4.2 web.example.com A")
        after = control.stats_noreset()

        self.assertIn("10.0.5.2", warm)
        self.assertGreater(
            after["total.num.cachehits"], before["total.num.cachehits"]
        )
        self.assertLess(_query_time_ms(warm), 20)

        # The cached RRset counts its TTL down rather than restarting it.
        cached_ttl = int(
            client.cmd("dig @10.0.4.2 web.example.com A +noall +answer").split()[1]
        )
        self.assertLessEqual(cached_ttl, 300)

        dump = control.dump_cache()
        self.assertIn("web.example.com.", dump)
        self.assertIn("com.", dump)

    def test_client_nameservers_resolve_through_the_resolver(self) -> None:
        """A client with nameservers set resolves and pings by name transparently."""
        net, nodes = self._resolving_lab()
        client = nodes["client1"]

        self.start(net)
        self.assertIn("nameserver 10.0.4.2", client.cmd("cat /etc/resolv.conf"))
        self.assertIn(
            "web.example.com has address 10.0.5.2",
            client.cmd("host web.example.com"),
        )
        ping = client.cmd("ping -c 2 -W 2 web.example.com")
        self.assertIn("2 packets transmitted, 2 received", ping)

    def test_teardown_removes_daemon_and_ephemeral_state(self) -> None:
        """net.stop() leaves no Unbound running and no ephemeral tree behind."""
        net, _nodes = self._resolving_lab()

        self.start(net)
        socket_path = ResolverProvisioner.control_socket_host_path("res1", net=net)
        net.stop()

        self.assertFalse(os.path.exists(socket_path))
        self.assertFalse(os.path.exists(ResolverProvisioner.host_dir("res1", net=net)))
        with self.assertRaises(ResolverControlError):
            UnboundControlClient(socket_path).status()


class ResolverForwardingIntegrationTests(LabCase):
    """Forwarding mode: the Resolver passes queries to a declared upstream."""

    def test_forwarder_answers_on_behalf_of_the_resolver(self) -> None:
        """A Resolver with forwarders resolves names its upstream is authoritative for."""
        net = self.lab()
        nodes = build_dns_test_lab(net)
        nodes["res1"].addService(ResolverService(forwarders=["10.0.3.2"]))
        client = nodes["client1"]

        self.start(net)
        control = UnboundControlClient(
            ResolverProvisioner.control_socket_host_path("res1", net=net)
        )
        self.assertTrue(control.is_running())

        answer = client.cmd("dig @10.0.4.2 web.example.com A +short")
        self.assertEqual(answer.strip(), "10.0.5.2")
        self.assertIn("web.example.com.", control.dump_cache())
