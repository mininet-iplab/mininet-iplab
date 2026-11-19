"""A Host's declared gateway is installed through the Interface it selects.

Without a ``dev``, ``defaultRoute`` and ``defaultRoute6`` leave by the Host's
lowest-numbered Interface; a ``dev`` names another. The selection waits for
every Link, so fixed ports give the same route whichever Link is created
first, and a DHCP client flag elsewhere or of the other family leaves the
gateway in place. A Host that declares no gateway gets none: DHCP, the
Router Advertisement or the Learner supplies it.
"""

from __future__ import annotations

import pytest
from mininet.node import Node

from mniplab.tests.labs import LabCase


def _default_route(node: Node, family: int) -> str:
    """A Node's default route of one family, up to its device; empty when it has none.

    ``ip route`` is read whole and filtered here, so the same read works with
    a Container Host's BusyBox ``ip``.
    """
    output = node.cmd('ip -6 route' if family == 6 else 'ip route')
    for line in output.splitlines():
        words = line.split()
        if words[:1] == ['default'] and 'dev' in words[:-1]:
            return ' '.join(words[:words.index('dev') + 2])
    return ''


class HostDefaultRouteTests(LabCase):
    """What a Host declares as its gateway is the route it holds after start."""

    def test_a_gateway_without_dev_leaves_by_the_lowest_numbered_interface(self) -> None:
        net = self.lab()
        h1 = net.addHost('h1', defaultRoute='via 192.168.1.1', defaultRoute6='via 2001:db8:1::1')
        h2 = net.addHost('h2')
        h3 = net.addHost('h3')
        # Port 1 is created first: the selection waits for every Link.
        net.addLink(h1, h3, port1=1, params1={'ip': '192.168.2.2/24', 'ip6': '2001:db8:2::2/64'},
                    params2={'ip': '192.168.2.1/24', 'ip6': '2001:db8:2::1/64'})
        net.addLink(h1, h2, port1=0, params1={'ip': '192.168.1.2/24', 'ip6': '2001:db8:1::2/64'},
                    params2={'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'})
        self.start(net)

        self.assertEqual(_default_route(h1, 4), 'default via 192.168.1.1 dev h1-eth0')
        self.assertEqual(_default_route(h1, 6), 'default via 2001:db8:1::1 dev h1-eth0')

    def _start_probe(self, dhcp_link_first: bool) -> Node:
        """Start the probe Lab: ``dhcp6`` on port 0, the declared gateway through port 1.

        Args:
            dhcp_link_first: Create the DHCPv6 client Link before the static one.

        Returns:
            The probe's Host, started.
        """
        net = self.lab()
        h1 = net.addHost('h1', defaultRoute6='via 2001:db8:2::1 dev h1-eth1')
        h2 = net.addHost('h2')
        h3 = net.addHost('h3')
        links = [
            lambda: net.addLink(h1, h2, port1=0, params1={'dhcp6': True},
                                params2={'ip6': '2001:db8:1::1/64'}),
            lambda: net.addLink(h1, h3, port1=1, params1={'ip6': '2001:db8:2::2/64'},
                                params2={'ip6': '2001:db8:2::1/64'}),
        ]
        for add_link in links if dhcp_link_first else reversed(links):
            add_link()
        self.start(net)
        return h1

    def test_a_gateway_beside_a_dhcp6_link_created_first_is_kept(self) -> None:
        """Creating the DHCPv6 client Link first used to drop the declaration."""
        h1 = self._start_probe(dhcp_link_first=True)

        self.assertEqual(_default_route(h1, 6), 'default via 2001:db8:2::1 dev h1-eth1')

    def test_a_gateway_beside_a_dhcp6_link_created_last_is_kept(self) -> None:
        h1 = self._start_probe(dhcp_link_first=False)

        self.assertEqual(_default_route(h1, 6), 'default via 2001:db8:2::1 dev h1-eth1')

    def test_an_explicit_dev_selects_a_non_default_interface(self) -> None:
        """Mininet's single-word IPv4 form names the Interface too."""
        net = self.lab()
        h1 = net.addHost('h1', defaultRoute='via 192.168.2.1 dev h1-eth1',
                         defaultRoute6='via 2001:db8:2::1 dev h1-eth1')
        h4 = net.addHost('h4', defaultRoute='h4-eth1')
        h2 = net.addHost('h2')
        h3 = net.addHost('h3')
        net.addLink(h1, h2, params1={'ip': '192.168.1.2/24', 'ip6': '2001:db8:1::2/64'},
                    params2={'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'})
        net.addLink(h1, h3, params1={'ip': '192.168.2.2/24', 'ip6': '2001:db8:2::2/64'},
                    params2={'ip': '192.168.2.1/24', 'ip6': '2001:db8:2::1/64'})
        net.addLink(h4, h2, params1={'ip': '192.168.3.2/24'}, params2={'ip': '192.168.3.1/24'})
        net.addLink(h4, h3, params1={'ip': '192.168.4.2/24'}, params2={'ip': '192.168.4.1/24'})
        self.start(net)

        self.assertEqual(_default_route(h1, 4), 'default via 192.168.2.1 dev h1-eth1')
        self.assertEqual(_default_route(h1, 6), 'default via 2001:db8:2::1 dev h1-eth1')
        self.assertEqual(_default_route(h4, 4), 'default dev h4-eth1')

    def test_an_ipv6_link_local_gateway_leaves_by_its_selected_interface(self) -> None:
        """A link-local next hop means nothing without its device, so it gets one."""
        net = self.lab()
        h1 = net.addHost('h1', defaultRoute6='via fe80::1')
        h2 = net.addHost('h2', defaultRoute6='via fe80::2 dev h2-eth1')
        h3 = net.addHost('h3')
        net.addLink(h1, h2, params1={'ip6': '2001:db8:1::1/64'},
                    params2={'ip6': '2001:db8:1::2/64'})
        net.addLink(h2, h3, params1={'ip6': '2001:db8:2::2/64'},
                    params2={'ip6': '2001:db8:2::3/64'})
        self.start(net)

        self.assertEqual(_default_route(h1, 6), 'default via fe80::1 dev h1-eth0')
        self.assertEqual(_default_route(h2, 6), 'default via fe80::2 dev h2-eth1')

    def test_a_dhcp_client_on_another_interface_or_family_leaves_the_gateway_in_place(self) -> None:
        """h1 and h2 differ only in which Link is created first."""
        net = self.lab()
        gateways = {'defaultRoute': 'via 192.168.1.1', 'defaultRoute6': 'via 2001:db8:1::1'}
        h1 = net.addHost('h1', **gateways)
        h2 = net.addHost('h2', **gateways)
        v4 = net.addHost('v4', defaultRoute='via 192.168.5.1')
        v6 = net.addHost('v6', defaultRoute6='via 2001:db8:6::1')
        p1 = net.addHost('p1')
        p2 = net.addHost('p2')
        static = ({'ip': '192.168.1.2/24', 'ip6': '2001:db8:1::2/64'},
                  {'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'})
        dhcp = ({'dhcp4': True, 'dhcp6': True}, {'ip': '192.168.9.1/24', 'ip6': '2001:db8:9::1/64'})
        net.addLink(h1, p1, port1=1, params1=dhcp[0], params2=dhcp[1])
        net.addLink(h1, p1, port1=0, params1=static[0], params2=static[1])
        net.addLink(h2, p2, port1=0, params1=static[0], params2=static[1])
        net.addLink(h2, p2, port1=1, params1=dhcp[0], params2=dhcp[1])
        net.addLink(v4, p1, params1={'ip': '192.168.5.2/24', 'dhcp6': True},
                    params2={'ip': '192.168.5.1/24', 'ip6': '2001:db8:5::1/64'})
        net.addLink(v6, p1, params1={'ip6': '2001:db8:6::2/64', 'dhcp4': True},
                    params2={'ip': '192.168.6.1/24', 'ip6': '2001:db8:6::1/64'})
        self.start(net)

        for host in (h1, h2):
            with self.subTest(host.name):
                self.assertEqual(_default_route(host, 4),
                                 f'default via 192.168.1.1 dev {host.name}-eth0')
                self.assertEqual(_default_route(host, 6),
                                 f'default via 2001:db8:1::1 dev {host.name}-eth0')
        self.assertEqual(_default_route(v4, 4), 'default via 192.168.5.1 dev v4-eth0')
        self.assertEqual(_default_route(v6, 6), 'default via 2001:db8:6::1 dev v6-eth0')

    def test_a_host_declaring_no_gateway_gets_none(self) -> None:
        """A Router on the Link is not taken for the Host's gateway."""
        net = self.lab()
        h1 = net.addHost('h1')
        r1 = net.addRouter('r1')
        net.addLink(h1, r1, params1={'ip': '192.168.1.2/24', 'ip6': '2001:db8:1::2/64'},
                    params2={'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'})
        self.start(net)

        self.assertEqual(_default_route(h1, 4), '')
        self.assertEqual(_default_route(h1, 6), '')

    def test_a_nat_gateways_route_for_its_peer_is_installed_as_written(self) -> None:
        """The NAT is on port 1, so its route does not take the lowest-numbered Interface."""
        net = self.lab()
        h1 = net.addHost('h1')
        h2 = net.addHost('h2')
        nat1 = net.addNAT('nat1', subnet='100.64.0.0/24')
        net.addLink(h1, h2, params1={'ip': '192.168.1.2/24'}, params2={'ip': '192.168.1.1/24'})
        net.addLink(h1, nat1, params1={'ip': '100.64.0.2/24'}, params2={'ip': '100.64.0.1/24'})
        self.start(net)

        self.assertEqual(_default_route(h1, 4), 'default via 100.64.0.1 dev h1-eth1')


@pytest.mark.requires_docker
class ContainerHostDefaultRouteTests(LabCase):
    """A Container Host's gateways follow the same rule as a Host's."""

    def test_a_container_hosts_gateways_leave_by_their_selected_interfaces(self) -> None:
        net = self.lab()
        d1 = net.addContainer('d1', dimage='alpine:3.20',
                              defaultRoute='via 192.168.2.1 dev d1-eth1',
                              defaultRoute6='via fe80::1')
        h2 = net.addHost('h2')
        h3 = net.addHost('h3')
        net.addLink(d1, h3, port1=1, params1={'ip': '192.168.2.2/24'},
                    params2={'ip': '192.168.2.1/24'})
        net.addLink(d1, h2, port1=0, params1={'ip': '192.168.1.2/24', 'ip6': '2001:db8:1::2/64'},
                    params2={'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'})
        self.start(net)

        self.assertEqual(_default_route(d1, 4), 'default via 192.168.2.1 dev d1-eth1')
        self.assertEqual(_default_route(d1, 6), 'default via fe80::1 dev d1-eth0')
