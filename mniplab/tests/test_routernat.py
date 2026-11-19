"""Router NAT without a Node: declarations, build-time refusals, apply errors and the translator list.

Every case runs on fakes, so nothing is built and no root is needed. What a
Router actually translates is proven by behaviour in the privileged Router
NAT Test Lab, not here.
"""

from __future__ import annotations

import unittest
from typing import Any, Optional

from mniplab import PortForward, StaticNAT
from mniplab.errors import ConfigurationError, RouterError
from mniplab.link import IPv6Intf
from mniplab.nodelib import MasqueradeNAT, TransitNAT
from mniplab.routernat import apply_network, validate_network
from mniplab.tests.fakes import FakeIntf, FakeLab, FakeLink, FakeNode
from mniplab.topology import TopologyBuilder

FORWARD = PortForward("tcp", 8080, "192.168.1.10", 80)
MAPPING = StaticNAT("192.168.1.11", "203.0.113.5")


def exited(status: int, output: str = "") -> str:
    """What a Node's shell answers for a command that printed ``output`` and exited with ``status``."""
    return f"{output}\n__mniplab_status={status}\n"


def wire(end1: FakeIntf, end2: FakeIntf) -> FakeLink:
    """Join two Interfaces on one Link, as Mininet's Link does, and return it."""
    link = FakeLink(end1, end2)
    end1.link = end2.link = link
    return link


def router(*entries: Any) -> FakeNode:
    """A Router named r1 carrying NAT entries, as ``addRouter(nat=...)`` leaves it."""
    node = FakeNode("r1", display_type="router")
    node.nat = list(entries)
    return node


def interface(node: FakeNode, name: str, ip: Optional[str] = None,
              nat: Optional[str] = None) -> FakeIntf:
    """Add an Interface to ``node`` the way a Link end declares it: address and mark."""
    intf = FakeIntf(name, node)
    if ip is not None:
        intf.params["ip"] = ip
    if nat is not None:
        intf.params["nat"] = nat
    node.addIntf(intf)
    return intf


def gateway(cls: type, name: str, *ends: FakeIntf) -> Any:
    """A NAT Gateway of the real class, with its Interfaces, and no Node built under it."""
    node: Any = object.__new__(cls)
    node.name = name
    node.intfs = {}
    for port, end in enumerate(ends):
        node.intfs[port] = end
        end.node = node
    return node


class RouterNATCase(unittest.TestCase):
    """Shared helpers: a refused Lab's message, and a Lab with one outside Router."""

    def refusal(self, *nodes: Any) -> str:
        """Validate a Lab of these Nodes, expecting a refusal; return its message."""
        with self.assertRaises(ConfigurationError) as caught:
            validate_network(FakeLab(*nodes))
        return str(caught.exception)

    @staticmethod
    def isp(name: str = "isp1", ip: str = "203.0.113.1/30") -> FakeNode:
        """An upstream Node with one Interface, ready to be wired to a Router's outside one."""
        node = FakeNode(name)
        interface(node, f"{name}-eth0", ip=ip)
        return node


class DeclarationTests(unittest.TestCase):
    """The entry types hold what a Learner's Lab says, and refuse what it cannot mean."""

    def test_a_port_forward_keeps_its_protocol_ports_address_and_options(self) -> None:
        forward = PortForward("udp", 5353, "192.168.1.10", 53, hairpin=True, interface="r1-eth0")

        self.assertEqual(
            (forward.protocol, forward.outside_port, forward.inside,
             forward.inside_port, forward.hairpin, forward.interface),
            ("udp", 5353, "192.168.1.10", 53, True, "r1-eth0"),
        )

    def test_a_port_forward_is_refused_for_a_protocol_other_than_tcp_or_udp(self) -> None:
        with self.assertRaises(ValueError):
            PortForward("icmp", 8080, "192.168.1.10", 80)

    def test_a_port_forward_is_refused_for_a_port_outside_one_to_65535(self) -> None:
        for outside_port, inside_port in ((0, 80), (65536, 80), (8080, 0), (8080, 65536)):
            with self.subTest(outside_port=outside_port, inside_port=inside_port):
                with self.assertRaises(ValueError):
                    PortForward("tcp", outside_port, "192.168.1.10", inside_port)

    def test_an_entry_is_refused_for_an_address_that_is_not_ipv4(self) -> None:
        with self.assertRaises(ValueError):
            PortForward("tcp", 8080, "2001:db8::10", 80)
        with self.assertRaises(ValueError):
            StaticNAT("192.168.1.11", "not-an-address")

    def test_both_entry_types_are_exported_beside_the_other_lab_primitives(self) -> None:
        import mniplab

        self.assertIn("PortForward", mniplab.__all__)
        self.assertIn("StaticNAT", mniplab.__all__)


class ValidationTests(RouterNATCase):
    """What the Lab refuses before anything starts, each message naming its Router and Interface."""

    def test_a_declaration_on_the_outside_interface_is_accepted(self) -> None:
        r1 = router(FORWARD, MAPPING)
        isp = self.isp()
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])

        validate_network(FakeLab(r1, isp))

    def test_a_masquerade_only_router_is_accepted(self) -> None:
        r1 = router()
        isp = self.isp()
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])

        validate_network(FakeLab(r1, isp))

    def test_an_entry_on_a_router_with_no_outside_interface_is_refused(self) -> None:
        r1 = router(FORWARD)
        interface(r1, "r1-eth0", ip="192.168.1.1/24")

        message = self.refusal(r1)

        self.assertIn("r1", message)
        self.assertIn("outside", message)

    def test_an_entry_naming_an_interface_that_is_not_outside_is_refused(self) -> None:
        r1 = router(PortForward("tcp", 8080, "192.168.1.10", 80, interface="r1-eth0"))
        interface(r1, "r1-eth0", ip="192.168.1.1/24")
        isp = self.isp()
        wire(interface(r1, "r1-eth1", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])

        message = self.refusal(r1, isp)

        self.assertIn("r1", message)
        self.assertIn("r1-eth0", message)

    def test_an_entry_naming_no_interface_of_its_router_is_refused(self) -> None:
        r1 = router(PortForward("tcp", 8080, "192.168.1.10", 80, interface="r1-eth9"))
        isp = self.isp()
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])

        message = self.refusal(r1, isp)

        self.assertIn("r1", message)
        self.assertIn("r1-eth9", message)

    def test_an_entry_without_an_interface_is_refused_on_a_router_with_two_outside_interfaces(self) -> None:
        r1 = router(MAPPING)
        isp1, isp2 = self.isp("isp1"), self.isp("isp2", ip="198.51.100.1/30")
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp1.intfs[0])
        wire(interface(r1, "r1-eth1", ip="198.51.100.2/30", nat="outside"), isp2.intfs[0])

        message = self.refusal(r1, isp1, isp2)

        self.assertIn("r1", message)
        self.assertIn("r1-eth0", message)
        self.assertIn("r1-eth1", message)

    def test_an_entry_without_an_interface_applies_to_the_only_outside_interface(self) -> None:
        r1 = router(FORWARD)
        isp = self.isp()
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])

        validate_network(FakeLab(r1, isp))

    def test_an_entry_naming_an_outside_interface_is_accepted_on_a_router_with_two(self) -> None:
        r1 = router(PortForward("tcp", 8080, "192.168.1.10", 80, interface="r1-eth1"))
        isp1, isp2 = self.isp("isp1"), self.isp("isp2", ip="198.51.100.1/30")
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp1.intfs[0])
        wire(interface(r1, "r1-eth1", ip="198.51.100.2/30", nat="outside"), isp2.intfs[0])

        validate_network(FakeLab(r1, isp1, isp2))

    def test_an_outside_interface_facing_an_internet_gateway_is_refused(self) -> None:
        r1 = router()
        nat_end = FakeIntf("nat1-eth0")
        nat1 = gateway(MasqueradeNAT, "nat1", nat_end)
        wire(interface(r1, "r1-eth0", ip="100.64.0.2/24", nat="outside"), nat_end)

        message = self.refusal(r1, nat1)

        self.assertIn("r1-eth0", message)
        self.assertIn("nat1", message)

    def test_an_outside_interface_facing_the_speaker_of_a_transit_gateway_is_refused(self) -> None:
        r1 = router()
        speaker = FakeNode("sp1", display_type="bgp-speaker")
        wire(interface(r1, "r1-eth0", ip="100.64.51.2/30", nat="outside"),
             interface(speaker, "sp1-eth0", ip="100.64.51.1/30"))
        transit_end = FakeIntf("nat2-eth0")
        nat2 = gateway(TransitNAT, "nat2", transit_end)
        wire(interface(speaker, "sp1-eth1", ip="100.64.50.2/30"), transit_end)

        message = self.refusal(r1, speaker, nat2)

        self.assertIn("r1-eth0", message)
        self.assertIn("nat2", message)

    def test_router_nat_is_accepted_on_another_interface_of_a_gateway_peer(self) -> None:
        r1 = router(MAPPING)
        nat_end = FakeIntf("nat1-eth0")
        nat1 = gateway(MasqueradeNAT, "nat1", nat_end)
        wire(interface(r1, "r1-eth0", ip="100.64.0.2/24"), nat_end)
        isp = self.isp()
        wire(interface(r1, "r1-eth1", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])

        validate_network(FakeLab(r1, nat1, isp))

    def test_an_outside_mark_on_a_node_that_is_not_a_router_is_refused(self) -> None:
        host = FakeNode("h1")
        interface(host, "h1-eth0", ip="10.0.0.2/24", nat="outside")

        message = self.refusal(host)

        self.assertIn("h1-eth0", message)
        self.assertIn("Router", message)

    def test_an_outside_mark_on_a_switch_is_refused_rather_than_ignored(self) -> None:
        switch = FakeNode("s1")
        interface(switch, "s1-eth0", nat="outside")

        with self.assertRaises(ConfigurationError) as caught:
            validate_network(FakeLab(switches=[switch]))

        self.assertIn("s1-eth0", str(caught.exception))

    def test_a_port_forward_on_an_outside_interface_without_an_ipv4_address_is_refused(self) -> None:
        r1 = router(FORWARD)
        isp = self.isp()
        wire(interface(r1, "r1-eth0", nat="outside"), isp.intfs[0])

        message = self.refusal(r1, isp)

        self.assertIn("r1-eth0", message)
        self.assertIn("IPv4", message)

    def test_a_nat_mark_other_than_outside_is_refused(self) -> None:
        r1 = router()
        interface(r1, "r1-eth0", ip="10.0.0.1/24", nat="inside")

        message = self.refusal(r1)

        self.assertIn("r1-eth0", message)
        self.assertIn("inside", message)

    def test_a_nat_entry_that_is_not_a_port_forward_or_mapping_is_refused(self) -> None:
        r1 = router({"protocol": "tcp"})

        message = self.refusal(r1)

        self.assertIn("r1", message)


class ApplyTests(RouterNATCase):
    """A rule the Router refuses to take is an error at start, never a silent gap."""

    def test_a_router_without_router_nat_is_left_alone(self) -> None:
        r1 = router()
        interface(r1, "r1-eth0", ip="192.168.1.1/24")

        apply_network(FakeLab(r1))

        self.assertEqual(r1.recorder.commands, [])

    def test_a_rule_the_router_refuses_is_an_error_naming_the_router(self) -> None:
        r1 = router()
        isp = self.isp()
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])
        r1.recorder.fail_on("iptables", exited(2, "iptables v1.8.9 (nf_tables): Couldn't load match"))

        with self.assertRaisesRegex(RouterError, "r1") as caught:
            apply_network(FakeLab(r1, isp))

        self.assertIn("Couldn't load match", str(caught.exception))

    def test_a_warning_printed_with_exit_status_zero_does_not_refuse_the_rule(self) -> None:
        r1 = router()
        isp = self.isp()
        wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])
        r1.recorder.respond_to("iptables", exited(0, "iptables-legacy tables present, use iptables-legacy to see them"))

        apply_network(FakeLab(r1, isp))

        self.assertTrue(r1.recorder.issued_any("-j MASQUERADE"))


class TranslatorTests(RouterNATCase):
    """A Router with Router NAT joins the Topology's translator list, one row per outside Interface."""

    def test_a_router_with_an_outside_interface_joins_the_translator_list(self) -> None:
        r1 = router(MAPPING)
        isp = self.isp()
        link = wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp.intfs[0])

        topology = TopologyBuilder.build(FakeLab(r1, isp, links=[link]))

        self.assertEqual(topology["translators"], [{"node": "r1", "interface": "r1-eth0"}])

    def test_each_outside_interface_of_a_router_is_a_translator_row(self) -> None:
        r1 = router()
        isp1, isp2 = self.isp("isp1"), self.isp("isp2", ip="198.51.100.1/30")
        link1 = wire(interface(r1, "r1-eth0", ip="203.0.113.2/30", nat="outside"), isp1.intfs[0])
        link2 = wire(interface(r1, "r1-eth1", ip="198.51.100.2/30", nat="outside"), isp2.intfs[0])

        topology = TopologyBuilder.build(FakeLab(r1, isp1, isp2, links=[link1, link2]))

        self.assertEqual(
            topology["translators"],
            [{"node": "r1", "interface": "r1-eth0"}, {"node": "r1", "interface": "r1-eth1"}],
        )

    def test_a_router_without_an_outside_interface_is_not_a_translator(self) -> None:
        r1 = router(FORWARD)
        interface(r1, "r1-eth0", ip="192.168.1.1/24")

        self.assertEqual(TopologyBuilder.build(FakeLab(r1))["translators"], [])


class OutsideMarkTests(unittest.TestCase):
    """The mark a Link end declares reaches its Interface and leaves the Interface's config alone."""

    def test_an_ipv6_interface_built_with_the_mark_keeps_it(self) -> None:
        r1 = router()

        intf = IPv6Intf("r1-eth0", node=r1, nat="outside")

        self.assertEqual(intf.params.get("nat"), "outside")
