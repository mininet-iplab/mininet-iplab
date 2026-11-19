"""An address is declared once, on its Interface, with one key per family.

A Node-level address was a second place to declare one, and it disagreed
with the Link end silently — verified 2026-10-18: ``addHost('h1',
ip='10.9.9.9/24')`` with Link end ``192.168.1.2/24`` came up ``10.9.9.9/24``,
and ``addHost('h1', ip6='2001:db8:99::9/64')`` with Link end
``2001:db8:1::2/64`` came up holding both. Every such declaration, and every
address written under the other family's key, is refused while the Lab is
being built, naming the Node, the value and the line to write instead.
"""

from __future__ import annotations

import unittest

import pytest
from mininet.node import Host
from mininet.topo import Topo

from mniplab import mnIPLab
from mniplab.errors import ConfigurationError, LinkError
from mniplab.tests.labs import unstarted_lab


@pytest.mark.requires_root
class NodeLevelAddressRefusalTests(unittest.TestCase):
    """No ``add*`` call takes an address; the Link end does."""

    def test_a_node_level_ipv4_address_is_refused_with_the_link_end_line(self) -> None:
        with unstarted_lab() as lab:
            with self.assertRaises(ConfigurationError) as ctx:
                lab.addHost('h1', ip='10.9.9.9/24')

        message = str(ctx.exception)
        self.assertIn('h1', message)
        self.assertIn("ip='10.9.9.9/24'", message)
        self.assertIn("net.addLink(h1, …, params1={'ip': '10.9.9.9/24'})", message)

    def test_a_node_level_ipv6_address_is_refused_with_the_link_end_line(self) -> None:
        with unstarted_lab() as lab:
            with self.assertRaises(ConfigurationError) as ctx:
                lab.addHost('h1', ip6='2001:db8:99::9/64')

        message = str(ctx.exception)
        self.assertIn('h1', message)
        self.assertIn("ip6='2001:db8:99::9/64'", message)
        self.assertIn("params1={'ip6': '2001:db8:99::9/64'}", message)

    def test_every_way_of_adding_a_node_refuses_an_address(self) -> None:
        ways = {
            'addRouter': lambda lab: lab.addRouter('n1', ip='10.9.9.9/24'),
            'addNAT': lambda lab: lab.addNAT('n1', ip='10.9.9.9/24'),
            'addTransit': lambda lab: lab.addTransit('n1', ip='10.9.9.9/24'),
            'addContainer': lambda lab: lab.addContainer('n1', dimage='alpine:3.20', ip='10.9.9.9/24'),
            'addExaBGP': lambda lab: lab.addExaBGP('n1', ip='10.9.9.9/24'),
            'addNode': lambda lab: lab.addNode('n1', cls=Host, ip='10.9.9.9/24'),
        }
        for way, add in ways.items():
            with self.subTest(way), unstarted_lab() as lab:
                with self.assertRaises(ConfigurationError) as ctx:
                    add(lab)
                self.assertIn("n1: ip='10.9.9.9/24'", str(ctx.exception))
                self.assertEqual(lab.hosts, [])

    def test_a_topo_host_with_an_address_is_refused(self) -> None:
        topo = Topo()
        topo.addHost('h1', ip='10.9.9.9/24')
        with self.assertRaises(ConfigurationError) as ctx:
            mnIPLab(topo=topo, controller=None)

        self.assertIn("h1: ip='10.9.9.9/24'", str(ctx.exception))

    def test_a_node_added_without_an_address_or_with_ip_none_is_accepted(self) -> None:
        with unstarted_lab() as lab:
            lab.addHost('h1')
            lab.addHost('h2', ip=None)
            lab.addNode('n1', cls=Host, ip=None)

        self.assertEqual([node.name for node in lab.hosts], ['h1', 'h2', 'n1'])


@pytest.mark.requires_root
class WrongFamilyRefusalTests(unittest.TestCase):
    """``ip`` and ``defaultRoute`` are IPv4; ``ip6`` and ``defaultRoute6`` are IPv6."""

    def test_an_ipv6_address_as_a_link_ends_ip_is_refused_naming_ip6(self) -> None:
        with unstarted_lab() as lab:
            h1 = lab.addHost('h1')
            h2 = lab.addHost('h2')
            with self.assertRaises(LinkError) as ctx:
                lab.addLink(h1, h2, params1={'ip': '2001:db8:1::2/64'})
            self.assertEqual(lab.links, [])

        message = str(ctx.exception)
        self.assertIn('h1', message)
        self.assertIn("ip='2001:db8:1::2/64'", message)
        self.assertIn("{'ip6': '2001:db8:1::2/64'}", message)

    def test_an_ipv4_address_as_a_link_ends_ip6_is_refused_naming_ip(self) -> None:
        with unstarted_lab() as lab:
            h1 = lab.addHost('h1')
            h2 = lab.addHost('h2')
            with self.assertRaises(LinkError) as ctx:
                lab.addLink(h1, h2, params2={'ip6': '192.168.1.1/24'})
            self.assertEqual(lab.links, [])

        message = str(ctx.exception)
        self.assertIn('h2', message)
        self.assertIn("ip6='192.168.1.1/24'", message)
        self.assertIn("{'ip': '192.168.1.1/24'}", message)

    def test_an_ipv6_gateway_as_default_route_is_refused_naming_default_route6(self) -> None:
        with unstarted_lab() as lab:
            with self.assertRaises(ConfigurationError) as ctx:
                lab.addHost('h1', defaultRoute='via 2001:db8:1::1')
            self.assertEqual(lab.hosts, [])

        message = str(ctx.exception)
        self.assertIn('h1', message)
        self.assertIn("defaultRoute='via 2001:db8:1::1'", message)
        self.assertIn("defaultRoute6='via 2001:db8:1::1'", message)

    def test_an_ipv4_gateway_as_default_route6_is_refused_naming_default_route(self) -> None:
        with unstarted_lab() as lab:
            with self.assertRaises(ConfigurationError) as ctx:
                lab.addHost('h1', defaultRoute6='via 192.168.1.1')
            self.assertEqual(lab.hosts, [])

        message = str(ctx.exception)
        self.assertIn("defaultRoute6='via 192.168.1.1'", message)
        self.assertIn("defaultRoute='via 192.168.1.1'", message)

    def test_gateways_of_the_right_family_and_an_interface_route_are_accepted(self) -> None:
        with unstarted_lab() as lab:
            lab.addHost('h1', defaultRoute='via 192.168.1.1', defaultRoute6='via 2001:db8:1::1')
            lab.addHost('h2', defaultRoute='h2-eth0')

        self.assertEqual([node.name for node in lab.hosts], ['h1', 'h2'])


@pytest.mark.requires_root
class DhcpClientAddressRefusalTests(unittest.TestCase):
    """A DHCP client Interface starts bare, so it takes no static address of its family."""

    def test_a_static_address_beside_its_familys_dhcp_flag_is_refused(self) -> None:
        cases = (
            ('ip', '10.0.0.5/24', 'dhcp4', {'ip': '10.0.0.1/24'}),
            ('ip6', '2001:db8:1::5/64', 'dhcp6', {'ip6': '2001:db8:1::1/64'}),
        )
        for key, address, flag, router_end in cases:
            with self.subTest(flag), unstarted_lab() as lab:
                h1 = lab.addHost('h1')
                r1 = lab.addRouter('r1')
                with self.assertRaises(LinkError) as ctx:
                    lab.addLink(r1, h1, params1=router_end, params2={key: address, flag: True})
                self.assertEqual(lab.links, [])

                message = str(ctx.exception)
                self.assertIn('h1', message)
                self.assertIn(f"{key}='{address}'", message)
                self.assertIn(flag, message)
                self.assertIn('a DHCP client Interface starts bare', message)
                self.assertIn('remove the address or the flag', message)
