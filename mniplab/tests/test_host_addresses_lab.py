"""An Interface carries exactly the addresses the Lab declares.

Mininet numbers every Host from ``ipBase`` (``10.0.0.N/8``) when it is given
no ``ip``, and applies that number to the Host's default Interface at
``start()`` — after ``addLink`` has set the address the Lab declared,
silently replacing it (verified 2026-10-03: ``h1`` declared
``192.168.1.2/24`` on its Link end and came up ``10.0.0.1/8``). An mnIPLab
Host, Container Host or Node gets no automatic address; ``autoSetMacs``,
which numbers MACs the same way, is untouched.
"""

from __future__ import annotations

import shlex
from typing import List

import pytest
from mininet.node import Host, Node

from mniplab import mnIPLab
from mniplab.tests.labs import LabCase


def _ipv4(node: Node, intf: str) -> str:
    """The IPv4 addresses on one of a Node's Interfaces, as ``ip -o`` lists them."""
    return node.cmd(f'ip -o -4 addr show dev {shlex.quote(intf)}').strip()


def _global_ipv6(node: Node, intf: str) -> List[str]:
    """The global IPv6 addresses on one of a Node's Interfaces, one per entry."""
    output = node.cmd(f'ip -o -6 addr show dev {shlex.quote(intf)} scope global')
    return [line.split()[3] for line in output.strip().splitlines()]


def _ipv4_list(node: Node, intf: str) -> List[str]:
    """The IPv4 addresses on one of a Node's Interfaces, one per entry."""
    return [line.split()[3] for line in _ipv4(node, intf).splitlines()]


class HostAddressTests(LabCase):
    """What a Host's Link end declares is what it has after start."""

    def test_a_host_addressed_only_on_its_link_end_keeps_that_address(self) -> None:
        net = self.lab()
        h1 = net.addHost('h1')
        r1 = net.addRouter('r1')
        net.addLink(h1, r1, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
        self.start(net)

        addresses = _ipv4(h1, 'h1-eth0')
        self.assertIn('192.168.1.2/24', addresses)
        self.assertNotIn('10.0.0.', addresses)

    def test_a_host_and_a_node_declared_without_an_address_have_none(self) -> None:
        net = self.lab()
        h1 = net.addHost('h1')
        n1 = net.addNode('n1', cls=Host)
        r1 = net.addRouter('r1')
        net.addLink(h1, r1, params2={'ip': '192.168.1.1/24'})
        net.addLink(n1, r1, params2={'ip': '192.168.2.1/24'})
        self.start(net)

        self.assertEqual(_ipv4(h1, 'h1-eth0'), '')
        self.assertEqual(_ipv4(n1, 'n1-eth0'), '')

    def test_link_ends_hold_exactly_one_address_per_declared_family(self) -> None:
        net = self.lab()
        h4 = net.addHost('h4')
        h6 = net.addHost('h6')
        hd = net.addHost('hd')
        r1 = net.addRouter('r1')
        net.addLink(h4, r1, params1={'ip': '192.168.4.2/24'}, params2={'ip': '192.168.4.1/24'})
        net.addLink(h6, r1, params1={'ip6': '2001:db8:6::2/64'},
                    params2={'ip6': '2001:db8:6::1/64'})
        net.addLink(hd, r1, params1={'ip': '192.168.5.2/24', 'ip6': '2001:db8:5::2/64'},
                    params2={'ip': '192.168.5.1/24', 'ip6': '2001:db8:5::1/64'})
        self.start(net)

        self.assertEqual(_ipv4_list(h4, 'h4-eth0'), ['192.168.4.2/24'])
        self.assertEqual(_global_ipv6(h4, 'h4-eth0'), [])
        self.assertEqual(_ipv4_list(h6, 'h6-eth0'), [])
        self.assertEqual(_global_ipv6(h6, 'h6-eth0'), ['2001:db8:6::2/64'])
        self.assertEqual(_ipv4_list(hd, 'hd-eth0'), ['192.168.5.2/24'])
        self.assertEqual(_global_ipv6(hd, 'hd-eth0'), ['2001:db8:5::2/64'])
        self.assertEqual(hd.defaultIntf().updateIP6(), '2001:db8:5::2')

    def test_a_dhcp_client_interface_starts_bare(self) -> None:
        """Nothing pre-empts the Learner's DHCP client."""
        net = self.lab()
        h1 = net.addHost('h1')
        r1 = net.addRouter('r1')
        net.addLink(h1, r1, params1={'dhcp4': True}, params2={'ip': '10.0.0.1/24'})
        self.start(net)

        self.assertEqual(_ipv4(h1, 'h1-eth0'), '')

    def test_a_static_address_beside_the_other_familys_dhcp_client_holds(self) -> None:
        """A Lab can serve one family statically and the other by DHCP."""
        net = self.lab()
        h4 = net.addHost('h4')
        h6 = net.addHost('h6')
        r1 = net.addRouter('r1')
        net.addLink(h4, r1, params1={'ip': '192.168.4.2/24', 'dhcp6': True},
                    params2={'ip': '192.168.4.1/24', 'ip6': '2001:db8:4::1/64'})
        net.addLink(h6, r1, params1={'ip6': '2001:db8:6::2/64', 'dhcp4': True},
                    params2={'ip': '192.168.6.1/24', 'ip6': '2001:db8:6::1/64'})
        self.start(net)

        self.assertEqual(_ipv4_list(h4, 'h4-eth0'), ['192.168.4.2/24'])
        self.assertEqual(_global_ipv6(h4, 'h4-eth0'), [])
        self.assertEqual(_ipv4_list(h6, 'h6-eth0'), [])
        self.assertEqual(_global_ipv6(h6, 'h6-eth0'), ['2001:db8:6::2/64'])

    def test_auto_set_macs_still_numbers_host_macs(self) -> None:
        net = mnIPLab(topo=None, controller=None, autoSetMacs=True)
        h1 = net.addHost('h1')
        h2 = net.addHost('h2')
        net.addLink(h1, h2, params1={'ip': '192.168.1.1/24'}, params2={'ip': '192.168.1.2/24'})
        self.start(net)

        self.assertEqual(h1.MAC('h1-eth0'), '00:00:00:00:00:01')
        self.assertEqual(h2.MAC('h2-eth0'), '00:00:00:00:00:02')
        self.assertIn('192.168.1.1/24', _ipv4(h1, 'h1-eth0'))


@pytest.mark.requires_docker
class ContainerHostAddressTests(LabCase):
    """A Container Host takes the same path, so it gets no automatic address either."""

    def test_a_container_host_declared_without_an_address_has_none(self) -> None:
        net = self.lab()
        d1 = net.addContainer('d1', dimage='alpine:3.20')
        r1 = net.addRouter('r1')
        net.addLink(d1, r1, params2={'ip': '192.168.1.1/24'})
        self.start(net)

        self.assertEqual(_ipv4(d1, 'd1-eth0'), '')
