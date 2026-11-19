"""A Host's gateway is checked against its Interface once every Link exists.

``defaultRoute`` and ``defaultRoute6`` are Host-wide settings that leave by
one Interface: the one their ``dev`` names, or else the Host's
lowest-numbered. Which Interface that is cannot be known while Links are
still being added, so the check runs at ``start()``, for every Host, before
any of them installs a route. A gateway with no such Interface, or one
leaving by a DHCP client Interface of its family, is refused there.

The refusal replaces a silent deletion: with ``dhcp6`` on port 0, static
IPv6 on port 1 and ``defaultRoute6`` through port 1, creating port 0 first
dropped the declaration and creating port 1 first kept it (verified
2026-10-18 on the suppression this check replaces).
"""

from __future__ import annotations

import unittest

import pytest
from mininet.node import Node

from mniplab.errors import ConfigurationError
from mniplab.tests.labs import unstarted_lab


def _default_routes(node: Node) -> str:
    """Every default route a Node holds, IPv4 then IPv6; empty when it has none."""
    return (node.cmd('ip route show default') + node.cmd('ip -6 route show default')).strip()


@pytest.mark.requires_root
class HostGatewayRefusalTests(unittest.TestCase):
    """A gateway that cannot leave by its Interface stops ``start()`` before any route is set."""

    def test_a_gateway_leaving_by_its_familys_dhcp_client_interface_is_refused(self) -> None:
        """The other family's gateway is valid, and is withheld all the same."""
        peer_end = {'ip': '192.168.1.1/24', 'ip6': '2001:db8:1::1/64'}
        cases = (
            ('defaultRoute', 'via 192.168.1.1', 'dhcp4', {'ip6': '2001:db8:1::2/64'}),
            ('defaultRoute6', 'via 2001:db8:1::1', 'dhcp6', {'ip': '192.168.1.2/24'}),
        )
        for key, route, flag, static_end in cases:
            with self.subTest(flag), unstarted_lab() as lab:
                h0 = lab.addHost('h0', defaultRoute='via 192.168.0.1')
                h1 = lab.addHost('h1', defaultRoute='via 192.168.1.1',
                                 defaultRoute6='via 2001:db8:1::1')
                h2 = lab.addHost('h2')
                lab.addLink(h0, h2, params1={'ip': '192.168.0.2/24'},
                            params2={'ip': '192.168.0.1/24'})
                lab.addLink(h1, h2, params1={flag: True, **static_end}, params2=peer_end)

                with self.assertRaises(ConfigurationError) as ctx:
                    lab.start()

                message = str(ctx.exception)
                self.assertIn('h1', message)
                self.assertIn(f"{key}='{route}'", message)
                self.assertIn('h1-eth0', message)
                self.assertIn(flag, message)
                self.assertIn('remove the static default or the flag', message)
                self.assertEqual(_default_routes(h1), '')
                self.assertEqual(_default_routes(h0), '')

    def test_an_explicit_dev_on_a_dhcp_client_interface_is_refused_at_start_only(self) -> None:
        """Adding the Links decides nothing; the selection is checked once they all exist."""
        with unstarted_lab() as lab:
            h1 = lab.addHost('h1', defaultRoute6='via 2001:db8:2::1 dev h1-eth1')
            h2 = lab.addHost('h2')
            h3 = lab.addHost('h3')
            lab.addLink(h1, h3, port1=1, params1={'dhcp6': True},
                        params2={'ip6': '2001:db8:2::1/64'})
            lab.addLink(h1, h2, port1=0, params1={'ip6': '2001:db8:1::2/64'},
                        params2={'ip6': '2001:db8:1::1/64'})
            self.assertEqual(len(lab.links), 2)

            with self.assertRaises(ConfigurationError) as ctx:
                lab.start()

            message = str(ctx.exception)
            self.assertIn("defaultRoute6='via 2001:db8:2::1 dev h1-eth1'", message)
            self.assertIn('h1-eth1', message)
            self.assertIn('dhcp6', message)
            self.assertEqual(_default_routes(h1), '')

    def test_a_gateway_without_an_interface_to_leave_by_is_refused(self) -> None:
        with self.subTest('no Link'), unstarted_lab() as lab:
            lab.addHost('h1', defaultRoute='via 192.168.1.1')

            with self.assertRaises(ConfigurationError) as ctx:
                lab.start()

            message = str(ctx.exception)
            self.assertIn("h1: defaultRoute='via 192.168.1.1'", message)
            self.assertIn('no Interface', message)

        with self.subTest('a dev the Host lacks'), unstarted_lab() as lab:
            h1 = lab.addHost('h1', defaultRoute6='via 2001:db8:1::1 dev h1-eth3')
            h2 = lab.addHost('h2')
            lab.addLink(h1, h2, params1={'ip6': '2001:db8:1::2/64'},
                        params2={'ip6': '2001:db8:1::1/64'})

            with self.assertRaises(ConfigurationError) as ctx:
                lab.start()

            message = str(ctx.exception)
            self.assertIn("defaultRoute6='via 2001:db8:1::1 dev h1-eth3'", message)
            self.assertIn('h1-eth3', message)
            self.assertIn('h1-eth0', message)
            self.assertEqual(_default_routes(h1), '')


if __name__ == '__main__':
    unittest.main()
