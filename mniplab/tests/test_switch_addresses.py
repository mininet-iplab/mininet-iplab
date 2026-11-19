"""A Switch is layer 2: its Interfaces carry no addresses.

A Switch is a bridge in the host's one namespace, so an address put on one of
its Interfaces lands on the host machine itself — verified 2026-10-03:
``10.9.9.1/24`` on a Switch end appeared in the host's root namespace with a
kernel route. Every path that would put one there is refused, naming the
Link and pointing to a Host or Internet Gateway; Mininet's other addressing forms
stay accepted.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import pytest

from mniplab.errors import ConfigurationError, LinkError
from mniplab.tests.labs import unstarted_lab

LEAKED = '10.9.9.1'


def _root_namespace_addresses() -> str:
    return subprocess.run(['ip', '-o', 'addr'], capture_output=True, text=True, check=True).stdout


@pytest.mark.requires_root
class SwitchAddressRefusalTests(unittest.TestCase):
    """No address reaches a Switch Interface, by ``addLink`` or by ``setIP``."""

    def test_an_ipv4_address_on_a_switch_end_is_refused_and_never_reaches_the_host(self) -> None:
        with unstarted_lab() as lab, patch('mniplab.net.ensure_ovs_available'):
            sw = lab.addSwitch('s1')
            h1 = lab.addHost('h1')
            try:
                with self.assertRaises(LinkError) as ctx:
                    lab.addLink(h1, sw, params1={'ip': '10.9.9.2/24'},
                                params2={'ip': f'{LEAKED}/24'})
                self.assertEqual(lab.links, [])
                self.assertNotIn(LEAKED, _root_namespace_addresses())
            finally:
                sw.terminate()

        message = str(ctx.exception)
        self.assertIn('h1', message)
        self.assertIn('s1', message)
        self.assertIn('Host or Internet Gateway', message)

    def test_an_ipv6_address_on_a_switch_end_is_refused(self) -> None:
        with unstarted_lab() as lab, patch('mniplab.net.ensure_ovs_available'):
            sw = lab.addSwitch('s1')
            h1 = lab.addHost('h1')
            try:
                with self.assertRaises(LinkError) as ctx:
                    lab.addLink(sw, h1, params1={'ip6': '2001:db8:9::1/64'})
            finally:
                sw.terminate()

        self.assertIn('s1', str(ctx.exception))

    def test_an_address_set_on_a_switch_interface_directly_is_refused(self) -> None:
        with unstarted_lab() as lab, patch('mniplab.net.ensure_ovs_available'):
            sw = lab.addSwitch('s1')
            h1 = lab.addHost('h1')
            link = lab.addLink(h1, sw, params1={'ip': '10.9.9.2/24'})
            try:
                with self.assertRaises(ConfigurationError) as ctx:
                    sw.setIP(f'{LEAKED}/24', intf=link.intf2)
                self.assertNotIn(LEAKED, _root_namespace_addresses())
            finally:
                link.delete()
                sw.terminate()

        self.assertIn('h1–s1', str(ctx.exception))
        self.assertIn('Host or Internet Gateway', str(ctx.exception))

    def test_a_switch_named_by_string_is_refused_too(self) -> None:
        with unstarted_lab() as lab, patch('mniplab.net.ensure_ovs_available'):
            sw = lab.addSwitch('s1')
            lab.addHost('h1')
            try:
                with self.assertRaises(LinkError) as ctx:
                    lab.addLink('h1', 's1', params2={'ip': f'{LEAKED}/24'})
                self.assertEqual(lab.links, [])
                self.assertNotIn(LEAKED, _root_namespace_addresses())
            finally:
                sw.terminate()

        self.assertIn('h1–s1', str(ctx.exception))

    def test_a_switch_end_without_an_address_still_links(self) -> None:
        with unstarted_lab() as lab, patch('mniplab.net.ensure_ovs_available'):
            sw = lab.addSwitch('s1')
            h1 = lab.addHost('h1')
            link = lab.addLink(h1, sw, params1={'ip': '10.9.9.2/24'}, params2={'vlan': 10})
            try:
                self.assertIn('10.9.9.2/24', h1.cmd('ip -o addr show h1-eth0'))
            finally:
                link.delete()
                sw.terminate()
