"""Tests for DHCP Relay: Relay declaration, address derivation, resolution, and lifecycle.

Unit coverage (unprivileged) covers:
- Relay declaration and validation
- Separate attachment point on Node (ServiceMixin.addRelay)
- Local address derivation per covered Subnet
- Relayed branch of resolution (returns None, contributes no local interface)
- Server listening interface set including the relay-facing interface
- Generated dnsmasq command arguments

Privileged integration coverage (@pytest.mark.integration) starts a real Lab
with a DHCP Service on one Node and a client behind a relaying Router, runs
the real dhcpcd client, and asserts a real Lease appears in the server's table.
"""

from __future__ import annotations

import os
import shutil
import time
import unittest
from typing import Any, List
from unittest.mock import Mock, patch

import pytest

from mniplab.dhcp import Pool, Reservation, DHCPService, Relay, DHCPRelayDiagnostics
from mniplab.dhcp.leases import LeaseQueryService
from mniplab.dhcp.pool_service import PoolService
from mniplab.dhcp.relay_activity import RelayActivityQueryService
from mniplab.errors import ConfigurationError
from mniplab.host import mnIPLabHost
from mniplab.net import mnIPLab
from mniplab.provisioners import DHCPProvisioner, DHCPRelayProvisioner
from mniplab.router import FRRouter
from mniplab.service import ServiceMixin
from mniplab.tests.fakes import (
    CommandRecorder,
    FakeLab,
    FakeNode,
    KeaClientStubMixin,
    cli_output,
)



class RelayDeclarationTests(unittest.TestCase):
    """Relay is a plain declaration of a Node's offer to forward DHCP."""

    def test_accepts_single_subnet_and_server(self) -> None:
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        self.assertEqual(relay.subnets, ("10.50.0.0/24",))
        self.assertEqual(relay.server, "10.0.0.2")
        self.assertIsNone(relay.node)
        self.assertEqual(relay.local_addresses, {})

    def test_accepts_multiple_subnets(self) -> None:
        relay = Relay("10.50.0.0/24", "10.51.0.0/24", server="10.0.0.2")
        self.assertEqual(relay.subnets, ("10.50.0.0/24", "10.51.0.0/24"))
        self.assertEqual(relay.server, "10.0.0.2")

    def test_accepts_ipv6_subnet_and_server(self) -> None:
        relay = Relay("2001:db8:50::/64", server="2001:db8:0::2")
        self.assertEqual(relay.subnets, ("2001:db8:50::/64",))
        self.assertEqual(relay.server, "2001:db8:0::2")
        self.assertEqual(relay.families, (6,))
        self.assertEqual(relay.server_for_family(6), "2001:db8:0::2")

    def test_accepts_dual_stack_subnets_and_servers(self) -> None:
        relay = Relay(
            "10.50.0.0/24",
            "2001:db8:50::/64",
            server=["10.0.0.2", "2001:db8:0::2"],
        )
        self.assertEqual(relay.subnets, ("10.50.0.0/24", "2001:db8:50::/64"))
        self.assertEqual(relay.families, (4, 6))
        self.assertEqual(relay.server_for_family(4), "10.0.0.2")
        self.assertEqual(relay.server_for_family(6), "2001:db8:0::2")

    def test_infers_families_from_subnets(self) -> None:
        v4_relay = Relay("10.50.0.0/24", server="10.0.0.2")
        self.assertEqual(v4_relay.families, (4,))
        v6_relay = Relay("2001:db8:50::/64", server="2001:db8:0::2")
        self.assertEqual(v6_relay.families, (6,))
        dual_relay = Relay("10.50.0.0/24", "2001:db8:50::/64", server="10.0.0.2")
        self.assertEqual(dual_relay.families, (4, 6))

    def test_accepts_subnets_as_sequence(self) -> None:
        relay = Relay(["10.50.0.0/24", "10.51.0.0/24"], server="10.0.0.2")
        self.assertEqual(relay.subnets, ("10.50.0.0/24", "10.51.0.0/24"))

    def test_requires_at_least_one_subnet(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Relay(server="10.0.0.2")
        self.assertIn("at least one Subnet", str(ctx.exception))

    def test_requires_server_address(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Relay("10.50.0.0/24")
        self.assertIn("server address", str(ctx.exception))

    def test_rejects_invalid_cidr(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Relay("not-a-cidr", server="10.0.0.2")
        self.assertIn("Invalid Subnet CIDR", str(ctx.exception))

    def test_rejects_invalid_server_ip(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Relay("10.50.0.0/24", server="not-an-ip")
        self.assertIn("Invalid server address", str(ctx.exception))

    def test_find_on_returns_attached_relay(self) -> None:
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        node = Mock(name="r1", relays=[relay])
        self.assertIs(Relay.find_on(node), relay)

    def test_find_on_raises_when_no_relay_attached(self) -> None:
        node = Mock(name="r1", relays=[])
        node.name = "r1"
        with self.assertRaises(ValueError) as ctx:
            Relay.find_on(node)
        self.assertIn("r1 has no Relay attached", str(ctx.exception))


class RelayAttachmentTests(unittest.TestCase):
    """The Relay attachment point sits beside ServiceMixin, but is separate."""

    def test_add_relay_returns_relay(self) -> None:
        class DummyNode(ServiceMixin):
            pass

        node = DummyNode()
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        self.assertIs(node.addRelay(relay), relay)

    def test_add_relay_sets_relays_node(self) -> None:
        class DummyNode(ServiceMixin):
            pass

        node = DummyNode()
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        node.addRelay(relay)
        self.assertIs(relay.node, node)

    def test_relays_property_lists_attached_relay(self) -> None:
        class DummyNode(ServiceMixin):
            name = "r1"

        node = DummyNode()
        r1 = Relay("10.50.0.0/24", server="10.0.0.2")
        node.addRelay(r1)
        self.assertEqual(node.relays, [r1])

    def test_adding_second_relay_raises_configuration_error(self) -> None:
        class DummyNode(ServiceMixin):
            name = "r1"

        node = DummyNode()
        node.addRelay(Relay("10.50.0.0/24", server="10.0.0.2"))
        with self.assertRaises(ConfigurationError) as ctx:
            node.addRelay(Relay("10.51.0.0/24", server="10.0.0.2"))
        self.assertIn("already has a Relay attached", str(ctx.exception))

    def test_relay_is_not_appended_to_services(self) -> None:
        class DummyNode(ServiceMixin):
            pass

        node = DummyNode()
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        node.addRelay(relay)
        self.assertEqual(node.services, [])
        self.assertEqual(node.relays, [relay])

    def test_a_fresh_node_has_no_relays(self) -> None:
        class DummyNode(ServiceMixin):
            pass

        self.assertEqual(DummyNode().relays, [])

    def test_host_and_router_both_carry_the_relay_attachment_point(self) -> None:
        self.assertTrue(issubclass(mnIPLabHost, ServiceMixin))
        self.assertTrue(issubclass(FRRouter, ServiceMixin))


class RelayLocalAddressDerivationTests(unittest.TestCase):
    """The relay's local address is derived from recorded interface state."""

    def test_derives_local_address_from_recorded_interface(self) -> None:
        node = FakeNode.with_addresses("r1", "10.50.0.1/24", "10.0.0.1/30")
        local_ip = DHCPRelayProvisioner.resolve_local_address(node, "10.50.0.0/24")
        self.assertEqual(local_ip, "10.50.0.1")

    def test_derives_local_ipv6_address_from_recorded_state(self) -> None:
        node = FakeNode.with_addresses(
            "r1", ("10.50.0.1", "2001:db8:50::1/64"), ("10.0.0.1", "2001:db8:0::1/64")
        )
        local_v6 = DHCPRelayProvisioner.resolve_local_address(node, "2001:db8:50::/64")
        self.assertEqual(local_v6, "2001:db8:50::1")

    def test_derives_local_address_without_prefix_length(self) -> None:
        node = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        local_ip = DHCPRelayProvisioner.resolve_local_address(node, "10.50.0.0/24")
        self.assertEqual(local_ip, "10.50.0.1")

    def test_raises_configuration_error_when_no_interface_matches(self) -> None:
        node = FakeNode.with_addresses("r1", "10.0.0.1")
        with self.assertRaises(ConfigurationError) as ctx:
            DHCPRelayProvisioner.resolve_local_address(node, "10.50.0.0/24")
        self.assertIn("no attached Interface found", str(ctx.exception))
        self.assertIn("10.50.0.0/24", str(ctx.exception))

    def test_resolve_relay_populates_local_addresses_dict(self) -> None:
        node = FakeNode.with_addresses("r1", "10.50.0.1", "10.51.0.1")
        relay = Relay("10.50.0.0/24", "10.51.0.0/24", server="10.0.0.2")
        resolved = DHCPRelayProvisioner.resolve_relay(node, relay)
        expected = {"10.50.0.0/24": "10.50.0.1", "10.51.0.0/24": "10.51.0.1"}
        self.assertEqual(resolved, expected)
        self.assertEqual(relay.local_addresses, expected)
        self.assertEqual(relay.local_address("10.50.0.0/24"), "10.50.0.1")
        self.assertEqual(relay.local_address("10.51.0.0/24"), "10.51.0.1")


class RelayedResolutionTests(unittest.TestCase):
    """Resolution handles direct, relayed, and un-relayed remote pools."""

    def test_pool_matching_remote_interface_is_legal_when_relay_covers_it(self) -> None:
        srv = FakeNode.with_addresses("srv", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        intf = DHCPProvisioner.resolve_interface(srv, pool, net)
        self.assertIsNone(intf)

    def test_ipv6_relayed_pool_resolves_to_none(self) -> None:
        srv = FakeNode.with_addresses("srv", ("10.0.0.2", "2001:db8:0::2/64"))
        r1 = FakeNode.with_addresses(
            "r1", ("10.50.0.1", "2001:db8:50::1/64"), ("10.0.0.1", "2001:db8:0::1/64")
        )
        relay = Relay("2001:db8:50::/64", server="2001:db8:0::2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        pool = Pool(cidr="2001:db8:50::/64", start="2001:db8:50::100", end="2001:db8:50::200")
        intf = DHCPProvisioner.resolve_interface(srv, pool, net)
        self.assertIsNone(intf)

    def test_refuses_subnet_that_is_both_directly_served_and_relayed(self) -> None:
        srv = FakeNode.with_addresses("srv", "10.50.0.2", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interface(srv, pool, net)
        msg = str(ctx.exception)
        self.assertIn("either directly served or relayed, never both", msg)
        self.assertIn("srv", msg)
        self.assertIn("r1", msg)

    def test_relayed_pool_contributes_no_local_interface_to_service(self) -> None:
        srv = FakeNode.with_addresses("srv", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        service = DHCPService(pool)
        interfaces = DHCPProvisioner.resolve_interfaces(srv, service, net)
        self.assertEqual(interfaces, {})

    def test_direct_and_relayed_pools_resolve_together(self) -> None:
        srv = FakeNode.with_addresses("srv", "192.168.1.1", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        direct_pool = Pool(cidr="192.168.1.0/24", start="192.168.1.100", end="192.168.1.200")
        relayed_pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        service = DHCPService(direct_pool, relayed_pool)

        interfaces = DHCPProvisioner.resolve_interfaces(srv, service, net)
        self.assertEqual(interfaces, {"192.168.1.0/24": "srv-eth0"})

    def test_remote_subnet_without_relay_remains_fatal(self) -> None:
        srv = FakeNode.with_addresses("srv", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        r1.relays = []
        net = FakeLab(srv, r1)

        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interface(srv, pool, net)
        msg = str(ctx.exception)
        self.assertIn("relay", msg.lower())
        self.assertNotIn("out of scope", msg)
        self.assertIn("r1", msg)
        self.assertIn("10.50.0.0/24", msg)
        self.assertIn("r1.addRelay", msg)


class ServerListeningInterfaceTests(unittest.TestCase):
    """The server listens on the interface facing the relay for relayed pools."""

    def test_service_listens_on_interface_carrying_relay_server_address(self) -> None:
        srv = FakeNode.with_addresses("srv", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        service = DHCPService(pool)
        service.node = srv
        service.interface_names = DHCPProvisioner.resolve_interfaces(srv, service, net)

        intfs = DHCPProvisioner.listening_interfaces(srv, service, family=4, net=net)
        self.assertEqual(intfs, ["srv-eth0"])

    def test_ipv6_relayed_pool_listens_on_interface_with_unicast_ip_syntax(self) -> None:
        srv = FakeNode.with_addresses("srv", ("10.0.0.2", "2001:db8:0::2/64"))
        r1 = FakeNode.with_addresses(
            "r1", ("10.50.0.1", "2001:db8:50::1/64"), ("10.0.0.1", "2001:db8:0::1/64")
        )
        relay = Relay("2001:db8:50::/64", server="2001:db8:0::2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        pool = Pool(cidr="2001:db8:50::/64", start="2001:db8:50::100", end="2001:db8:50::200")
        service = DHCPService(pool)
        service.node = srv
        service.interface_names = DHCPProvisioner.resolve_interfaces(srv, service, net)

        intfs = DHCPProvisioner.listening_interfaces(srv, service, family=6, net=net)
        self.assertEqual(intfs, ["srv-eth0/2001:db8:0::2"])

    def test_listening_interfaces_combines_direct_and_relay_facing(self) -> None:
        srv = FakeNode.with_addresses("srv", "192.168.1.1", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(srv, r1)

        direct_pool = Pool(cidr="192.168.1.0/24", start="192.168.1.100", end="192.168.1.200")
        relayed_pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        service = DHCPService(direct_pool, relayed_pool)
        service.node = srv
        service.interface_names = DHCPProvisioner.resolve_interfaces(srv, service, net)

        intfs = DHCPProvisioner.listening_interfaces(srv, service, family=4, net=net)
        self.assertEqual(intfs, ["srv-eth0", "srv-eth1"])

    def test_no_relay_block_in_kea_configuration(self) -> None:
        from mniplab.dhcp.config import KeaConfigBuilder

        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        config = KeaConfigBuilder.build(
            pools=[pool],
            interface_names=["srv-eth0"],
            control_socket_path="/var/run/kea/kea4-ctrl-socket",
            lease_database_path="/var/lib/kea/kea-leases4.csv",
            family=4,
        )
        dhcp4 = config["Dhcp4"]
        self.assertEqual(dhcp4["interfaces-config"]["interfaces"], ["srv-eth0"])
        subnet = dhcp4["subnet4"][0]
        self.assertEqual(subnet["subnet"], "10.50.0.0/24")
        self.assertNotIn("relay", subnet)

    def test_kea_config_for_relayed_ipv6_pool_has_no_interface_key_in_subnet6(self) -> None:
        from mniplab.dhcp.config import KeaConfigBuilder

        pool = Pool(cidr="2001:db8:50::/64", start="2001:db8:50::100", end="2001:db8:50::200")
        config = KeaConfigBuilder.build(
            pools=[pool],
            interface_names=["srv-eth0/2001:db8:0::2"],
            control_socket_path="/var/run/kea/kea6-ctrl-socket",
            lease_database_path="/var/lib/kea/kea-leases6.csv",
            family=6,
            subnet_interfaces={},
        )
        dhcp6 = config["Dhcp6"]
        self.assertEqual(dhcp6["interfaces-config"]["interfaces"], ["srv-eth0/2001:db8:0::2"])
        subnet = dhcp6["subnet6"][0]
        self.assertEqual(subnet["subnet"], "2001:db8:50::/64")
        self.assertNotIn("interface", subnet)
        self.assertNotIn("relay", subnet)


class RelayInvocationTests(unittest.TestCase):
    """The relay process runs dnsmasq with -d, --port=0, --log-dhcp, and --dhcp-relay."""

    def test_build_command_args_suppresses_dns_and_enables_logging(self) -> None:
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        relay.local_addresses = {"10.50.0.0/24": "10.50.0.1"}
        args = DHCPRelayProvisioner.build_command_args(relay)
        self.assertIn("-d", args)
        self.assertIn("--port=0", args)
        self.assertIn("--log-dhcp", args)
        self.assertIn("--dhcp-relay=10.50.0.1,10.0.0.2", args)

    def test_build_command_args_carries_both_families_in_one_invocation(self) -> None:
        relay = Relay(
            "10.50.0.0/24",
            "2001:db8:50::/64",
            server=["10.0.0.2", "2001:db8:0::2"],
        )
        relay.local_addresses = {
            "10.50.0.0/24": "10.50.0.1",
            "2001:db8:50::/64": "2001:db8:50::1",
        }
        args = DHCPRelayProvisioner.build_command_args(relay)
        self.assertIn("-d", args)
        self.assertIn("--port=0", args)
        self.assertIn("--log-dhcp", args)
        self.assertIn("--dhcp-relay=10.50.0.1,10.0.0.2", args)
        self.assertIn("--dhcp-relay=2001:db8:50::1,2001:db8:0::2", args)

    def test_build_command_args_carries_one_relay_argument_per_subnet(self) -> None:
        relay = Relay("10.50.0.0/24", "10.51.0.0/24", server="10.0.0.2")
        relay.local_addresses = {
            "10.50.0.0/24": "10.50.0.1",
            "10.51.0.0/24": "10.51.0.1",
        }
        args = DHCPRelayProvisioner.build_command_args(relay)
        relay_args = [a for a in args if a.startswith("--dhcp-relay=")]
        self.assertEqual(len(relay_args), 2)
        self.assertIn("--dhcp-relay=10.50.0.1,10.0.0.2", relay_args)
        self.assertIn("--dhcp-relay=10.51.0.1,10.0.0.2", relay_args)

    def test_start_relay_issues_nohup_dnsmasq(self) -> None:
        recorder = CommandRecorder()
        node = Mock()
        node.name = "r1"
        node.cmd = recorder
        intf = Mock()
        intf.name = "r1-eth0"
        intf.ip = "10.50.0.1"
        intf.ip6 = None
        node.intfList.return_value = [intf]

        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        node.relays = [relay]
        FakeLab(node)

        DHCPRelayProvisioner.start_relay(node)

        dnsmasq_cmds = [cmd for cmd in recorder.issued("dnsmasq") if "nohup" in cmd]
        self.assertTrue(dnsmasq_cmds, "Expected dnsmasq command to be issued")
        cmd = dnsmasq_cmds[0]
        self.assertIn("nohup", cmd)
        self.assertIn("--port=0", cmd)
        self.assertIn("--log-dhcp", cmd)
        self.assertIn("--dhcp-relay=10.50.0.1,10.0.0.2", cmd)
        self.assertIn("dnsmasq.log", cmd)

    def test_stop_relay_issues_pkill_dnsmasq(self) -> None:
        recorder = CommandRecorder()
        node = Mock()
        node.name = "r1"
        node.cmd = recorder
        DHCPRelayProvisioner.stop_relay(node)
        pkill_cmds = recorder.issued("pkill")
        self.assertTrue(pkill_cmds)
        self.assertIn("dnsmasq", pkill_cmds[0])


class RelayDiagnosticsTests(unittest.TestCase):
    """Start-time diagnostic checks and error handling for DHCP Relay.

    Fatal when the configuration can never work, a warning when it works
    and might be the point.
    """

    def test_pool_matching_no_interface_anywhere_is_reported_as_typo(self) -> None:
        srv = FakeNode.with_addresses("srv", "10.0.0.2")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1")
        net = FakeLab(srv, r1)

        pool = Pool(cidr="192.168.99.0/24", start="192.168.99.100", end="192.168.99.200")
        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interface(srv, pool, net)
        msg = str(ctx.exception)
        self.assertIn("no attached Interface found for Pool CIDR", msg)
        self.assertNotIn("addRelay", msg)

    def test_relay_subnet_on_remote_node_raises_fatal_naming_fix(self) -> None:
        r1 = FakeNode.with_addresses("r1", "10.0.0.1")
        r2 = FakeNode.with_addresses("r2", "10.60.0.1")
        net = FakeLab(r1, r2)

        relay = Relay("10.60.0.0/24", server="10.0.0.2")
        r1.relays = [relay]

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPRelayProvisioner.resolve_local_address(r1, "10.60.0.0/24", net=net)
        msg = str(ctx.exception)
        self.assertIn("belongs to a Subnet attached to r2, not to r1", msg)
        self.assertIn("r2.addRelay", msg)

    def test_relay_subnet_matching_nothing_anywhere_raises_fatal_as_unmatched(self) -> None:
        r1 = FakeNode.with_addresses("r1", "10.0.0.1")
        net = FakeLab(r1)

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPRelayProvisioner.resolve_local_address(r1, "192.168.99.0/24", net=net)
        msg = str(ctx.exception)
        self.assertIn("no attached Interface found for Relay Subnet CIDR 192.168.99.0/24", msg)
        self.assertNotIn("belongs to a Subnet", msg)

    def test_relay_and_service_on_same_interface_raises_fatal(self) -> None:
        r1 = FakeNode.with_addresses("r1", "10.50.0.1", "10.0.0.1")
        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        service = DHCPService(pool)
        relay = Relay("10.50.0.0/24", server="10.0.0.2")

        r1.services = [service]
        r1.relays = [relay]

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPRelayProvisioner.validate_no_relay_service_collision(r1)
        msg = str(ctx.exception)
        self.assertIn("cannot declare both a Relay and a DHCP Service on the same Interface", msg)
        self.assertIn("r1-eth0", msg)
        self.assertIn("Remove either the Relay", msg)

        # Both start_relay and start_dhcp_service must refuse the collision
        with self.assertRaises(ConfigurationError):
            DHCPRelayProvisioner.start_relay(r1)
        with self.assertRaises(ConfigurationError):
            DHCPProvisioner.start_dhcp_service(r1, service, FakeLab(r1))

    def test_relay_naming_unassigned_service_address_is_warning_and_lab_starts(self) -> None:
        r1 = FakeNode.with_addresses("r1", "10.50.0.1")
        relay = Relay("10.50.0.0/24", server="10.99.99.99")
        r1.relays = [relay]
        net = FakeLab(r1)

        # Pure diagnostic check from recorded state
        warnings = DHCPRelayDiagnostics.check_relay(r1, relay, net)
        self.assertEqual(len(warnings), 1)
        self.assertIn("10.99.99.99", warnings[0])
        self.assertIn("not assigned to any Node in the Lab", warnings[0])
        self.assertIn("r1.addRelay", warnings[0])

        # At start time, a warning is emitted and no error is raised (Lab starts)
        recorder = CommandRecorder()
        r1.cmd = recorder  # type: ignore[method-assign]
        with patch("mniplab.provisioners.warn") as mock_warn:
            DHCPRelayProvisioner.start_relay(r1, net=net)
            mock_warn.assert_called()
            warn_msg = "".join(call.args[0] for call in mock_warn.call_args_list)
            self.assertIn("10.99.99.99", warn_msg)

    def test_relay_naming_node_without_service_is_warning_and_lab_starts(self) -> None:
        r1 = FakeNode.with_addresses("r1", "10.50.0.1")
        srv = FakeNode.with_addresses("srv", "10.0.0.2")
        srv.services = []
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(r1, srv)

        warnings = DHCPRelayDiagnostics.check_relay(r1, relay, net)
        self.assertEqual(len(warnings), 1)
        self.assertIn("srv has no DHCP Service attached", warnings[0])
        self.assertIn("srv.addService", warnings[0])

        recorder = CommandRecorder()
        r1.cmd = recorder  # type: ignore[method-assign]
        with patch("mniplab.provisioners.warn") as mock_warn:
            DHCPRelayProvisioner.start_relay(r1, net=net)
            mock_warn.assert_called()
            warn_msg = "".join(call.args[0] for call in mock_warn.call_args_list)
            self.assertIn("srv has no DHCP Service", warn_msg)

    def test_relay_naming_reachable_service_emits_no_warnings(self) -> None:
        r1 = FakeNode.with_addresses("r1", "10.50.0.1")
        srv = FakeNode.with_addresses("srv", "10.0.0.2")
        srv.services = [DHCPService(Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200"))]
        relay = Relay("10.50.0.0/24", server="10.0.0.2")
        r1.relays = [relay]
        net = FakeLab(r1, srv)

        warnings = DHCPRelayDiagnostics.check_relay(r1, relay, net)
        self.assertEqual(warnings, [])

    def test_directly_attached_ipv6_pool_with_mac_reservation_warns(self) -> None:
        r1 = FakeNode.with_addresses("r1", (None, "2001:db8:1::1/64"))
        pool = Pool(
            cidr="2001:db8:1::/64",
            start="2001:db8:1::100",
            end="2001:db8:1::200",
            reservations=(
                Reservation(mac="00:00:00:00:00:02", ip="2001:db8:1::50"),
            ),
        )
        service = DHCPService(pool)
        r1.services = [service]
        net = FakeLab(r1)

        warnings = DHCPRelayDiagnostics.diagnose_reservations(service, net)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Reservation for 2001:db8:1::50 identifies client by hardware address", warnings[0])
        self.assertIn("not reliable without a Relay", warnings[0])

    def test_relayed_ipv6_pool_with_mac_reservation_emits_no_warning(self) -> None:
        r1 = FakeNode.with_addresses("r1", (None, "2001:db8:50::1/64"))
        r1.relays = [Relay("2001:db8:50::/64", server="2001:db8:0::2")]
        srv = FakeNode.with_addresses("srv", (None, "2001:db8:0::2/64"))
        pool = Pool(
            cidr="2001:db8:50::/64",
            start="2001:db8:50::100",
            end="2001:db8:50::200",
            reservations=(
                Reservation(mac="00:00:00:00:00:02", ip="2001:db8:50::50"),
            ),
        )
        service = DHCPService(pool)
        srv.services = [service]
        net = FakeLab(r1, srv)

        warnings = DHCPRelayDiagnostics.diagnose_reservations(service, net)
        self.assertEqual(warnings, [])

    def test_directly_attached_ipv6_pool_with_duid_reservation_emits_no_warning(self) -> None:
        r1 = FakeNode.with_addresses("r1", (None, "2001:db8:1::1/64"))
        pool = Pool(
            cidr="2001:db8:1::/64",
            start="2001:db8:1::100",
            end="2001:db8:1::200",
            reservations=(
                Reservation(duid="00:01:00:01:aa:bb:cc:dd:ee:ff", ip="2001:db8:1::50"),
            ),
        )
        service = DHCPService(pool)
        r1.services = [service]
        net = FakeLab(r1)

        warnings = DHCPRelayDiagnostics.diagnose_reservations(service, net)
        self.assertEqual(warnings, [])

    def test_ipv4_pool_with_mac_reservation_emits_no_warning(self) -> None:
        r1 = FakeNode.with_addresses("r1", "10.50.0.1")
        pool = Pool(
            cidr="10.50.0.0/24",
            start="10.50.0.100",
            end="10.50.0.200",
            reservations=(
                Reservation(mac="00:00:00:00:00:02", ip="10.50.0.50"),
            ),
        )
        service = DHCPService(pool)
        r1.services = [service]
        net = FakeLab(r1)

        warnings = DHCPRelayDiagnostics.diagnose_reservations(service, net)
        self.assertEqual(warnings, [])

    def test_relay_fails_when_dnsmasq_not_installed(self) -> None:
        with patch("shutil.which", return_value=None):
            with self.assertRaises(ConfigurationError) as ctx:
                DHCPRelayProvisioner.ensure_available()
            self.assertIn("apt-get install dnsmasq-base", str(ctx.exception))

    def test_relay_process_startup_command_error_reported(self) -> None:
        recorder = CommandRecorder()
        recorder.fail_on("dnsmasq", "dnsmasq: failed to bind DHCP server socket: Address already in use")
        r1 = FakeNode.with_addresses("r1", "10.50.0.1")
        r1.cmd = recorder  # type: ignore[method-assign]
        r1.relays = [Relay("10.50.0.0/24", server="10.0.0.2")]
        FakeLab(r1)

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPRelayProvisioner.start_relay(r1)
        msg = str(ctx.exception)
        self.assertIn("DHCP Relay process failed to start", msg)
        self.assertIn("Address already in use", msg)
        self.assertIn("/var/log/dnsmasq/dnsmasq.log", msg)

    def test_relay_process_startup_log_error_reported(self) -> None:
        recorder = CommandRecorder()
        r1 = FakeNode.with_addresses("r1", "10.50.0.1")
        r1.cmd = recorder  # type: ignore[method-assign]
        r1.relays = [Relay("10.50.0.0/24", server="10.0.0.2")]
        net = FakeLab(r1)

        log_path = DHCPRelayProvisioner.log_path(r1.name, net=net)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("dnsmasq: bad command line options: try --help\n")

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPRelayProvisioner.start_relay(r1)
        msg = str(ctx.exception)
        self.assertIn("DHCP Relay process failed to start", msg)
        self.assertIn("bad command line options", msg)


class RelayedLeaseLabelTests(KeaClientStubMixin, unittest.TestCase):
    """Ticket 09: the Lease table says which Leases were relayed.

    A Lease carries no record of the Relay that forwarded it — Kea's lease
    document has no such field — so "relayed" is derived by joining the
    Lease's subnet identifier back to the Pool it came from. These tests
    cover that join: a relayed Pool, a directly served Pool, and a Lease
    whose subnet identifier matches no Pool the framework knows about,
    which is absent data, not an error.
    """

    @staticmethod
    def _relay_node(name: str, *subnets: str) -> FakeNode:
        return FakeNode(name, relays=[Relay(list(subnets), server="10.0.0.2")])

    @staticmethod
    def _service_node(*pools: Pool) -> FakeNode:
        return FakeNode("srv", services=[DHCPService(*pools)])

    @staticmethod
    def _net(service_node: FakeNode, *hosts: FakeNode) -> FakeLab:
        return FakeLab(service_node, *hosts)

    def _get_leases(self, net: FakeLab, leases: List[dict]) -> dict:
        self.client.get_all_leases.return_value = leases
        return LeaseQueryService.get_leases(net, "srv")

    def test_a_lease_from_a_relayed_pool_is_labelled_relayed(self) -> None:
        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        node = self._service_node(pool)
        net = self._net(node, self._relay_node("r1", "10.50.0.0/24"))
        lease = {"ip-address": "10.50.0.100", "hw-address": "aa:bb:cc:dd:ee:ff", "subnet-id": 1}

        result = self._get_leases(net, [lease])

        self.assertIs(result["v4"][0]["relayed"], True)

    def test_a_direct_lease_in_a_lab_with_a_relay_is_labelled_direct(self) -> None:
        """The comparison the feature exists for: one Service serving a
        relayed Subnet and a directly attached one, labelled side by side."""
        relayed_pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        direct_pool = Pool(cidr="10.60.0.0/24", start="10.60.0.100", end="10.60.0.200")
        node = self._service_node(relayed_pool, direct_pool)
        net = self._net(node, self._relay_node("r1", "10.50.0.0/24"))
        leases = [
            {"ip-address": "10.50.0.100", "subnet-id": 1},
            {"ip-address": "10.60.0.100", "subnet-id": 2},
        ]

        result = self._get_leases(net, leases)

        self.assertIs(result["v4"][0]["relayed"], True)
        self.assertIs(result["v4"][1]["relayed"], False)

    def test_a_lease_whose_subnet_id_matches_no_pool_carries_no_label(self) -> None:
        """Absent data, not an error: the Lease renders exactly as before."""
        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        node = self._service_node(pool)
        net = self._net(node, self._relay_node("r1", "10.50.0.0/24"))
        lease = {"ip-address": "10.99.0.10", "subnet-id": 42}

        result = self._get_leases(net, [lease])

        self.assertNotIn("relayed", result["v4"][0])
        self.assertEqual(result["v4"][0], {"ip-address": "10.99.0.10", "subnet-id": 42})

    def test_a_lab_with_no_relay_shows_no_relay_labelling_at_all(self) -> None:
        """Not a column reading "direct" on every row: no label whatsoever."""
        pool = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        node = self._service_node(pool)
        net = self._net(node)  # no Relay anywhere in the Lab
        lease = {"ip-address": "10.50.0.100", "subnet-id": 1}

        result = self._get_leases(net, [lease])

        self.assertNotIn("relayed", result["v4"][0])

    def test_the_label_splits_by_address_family_not_by_a_new_axis(self) -> None:
        """A dual-stack Service with only the v4 Subnet relayed labels the v4
        table and leaves the v6 table exactly as it was."""
        pool_v4 = Pool(cidr="10.50.0.0/24", start="10.50.0.100", end="10.50.0.200")
        pool_v6 = Pool(
            cidr="2001:db8:50::/64", start="2001:db8:50::100", end="2001:db8:50::200"
        )
        node = self._service_node(pool_v4, pool_v6)
        net = self._net(node, self._relay_node("r1", "10.50.0.0/24"))
        lease_v4 = {"ip-address": "10.50.0.100", "subnet-id": 1}
        lease_v6 = {"ip-address": "2001:db8:50::100", "subnet-id": 1}

        self.client.get_all_leases.return_value = [lease_v4]
        self.client6.get_all_leases.return_value = [lease_v6]

        result = LeaseQueryService.get_leases(net, "srv")

        self.assertIs(result["v4"][0]["relayed"], True)
        self.assertNotIn("relayed", result["v6"][0])


class ShowLeasesRelayLabelTests(unittest.TestCase):
    """mnIPLabCLI.do_show_leases: the relayed/direct label on the CLI surface."""

    def _printed(self, result):
        with patch(
            "mniplab.dhcp.leases.LeaseQueryService.get_leases", return_value=result
        ):
            return cli_output("show_leases", "srv")

    def test_relayed_lease_is_marked_relayed(self):
        printed = self._printed(
            {"success": True, "v4": [{"ip-address": "10.50.0.100", "subnet-id": 1, "relayed": True}]}
        )
        self.assertIn("10.50.0.100", printed)
        self.assertIn("relayed", printed)

    def test_direct_lease_is_marked_direct_when_the_lab_stages_a_relay(self):
        printed = self._printed(
            {"success": True, "v4": [{"ip-address": "10.60.0.100", "subnet-id": 2, "relayed": False}]}
        )
        self.assertIn("direct", printed)

    def test_unlabelled_lease_renders_exactly_as_before(self):
        """A Lab with no Relay: no relay labelling at all on the CLI either."""
        printed = self._printed(
            {"success": True, "v4": [{"ip-address": "10.50.0.100", "hw-address": "aa:bb:cc:dd:ee:ff", "valid-lft": 3600}]}
        )
        self.assertIn("10.50.0.100", printed)
        self.assertNotIn("relayed", printed)
        self.assertNotIn("direct", printed)

    def test_v6_relayed_lease_is_marked_relayed(self):
        printed = self._printed(
            {"success": True, "v6": [{"ip-address": "2001:db8:50::100", "duid": "00:01", "subnet-id": 1, "relayed": True}]}
        )
        self.assertIn("2001:db8:50::100", printed)
        self.assertIn("relayed", printed)
