"""Unit tests for IPv6 interface handling."""

import unittest
from unittest.mock import patch

from mniplab.link import IPv6Intf, LinkError


class IPv6AddressStateTests(unittest.TestCase):
    """An Interface stores and reports the IPv6 address it carries."""

    def _intf(self, **params):
        from mniplab.link import IPv6Intf
        from mniplab.tests.fakes import FakeNode
        node = FakeNode('h1')
        intf = IPv6Intf('h1-eth0', node=node, **params)
        return intf, node

    def test_new_interface_has_no_ipv6_address(self):
        intf, _ = self._intf()
        self.assertIsNone(intf.ip6)
        self.assertIsNone(intf.prefixLen6)
        self.assertIsNone(intf.IP6())

    def test_set_ip6_records_address_and_prefix(self):
        intf, _ = self._intf()
        intf.setIP6('2001:db8::1/64')
        self.assertEqual(intf.ip6, '2001:db8::1')
        self.assertEqual(intf.prefixLen6, '64')
        self.assertEqual(intf.IP6(), '2001:db8::1')

    def test_set_ip6_applies_address_to_the_node(self):
        intf, node = self._intf()
        intf.setIP6('2001:db8::1/64')
        issued = node.recorder.issued('ip -6 addr add')
        self.assertEqual(len(issued), 1)
        self.assertIn('2001:db8::1/64', issued[0])
        self.assertIn('dev h1-eth0', issued[0])

    def test_set_ip6_keeps_the_address_across_a_link_down_like_ipv4(self):
        """The kernel drops an IPv6 address when its Interface goes down and
        keeps an IPv4 one; keep_addr_on_down makes the two behave alike, so
        a Link brought down and up (or a MAC set at start) keeps its address."""
        intf, node = self._intf()
        intf.setIP6('2001:db8::1/64')
        issued = node.recorder.issued('keep_addr_on_down')
        self.assertEqual(len(issued), 1)
        self.assertIn('net.ipv6.conf.h1-eth0.keep_addr_on_down=1', issued[0])
        # Set after the address exists, so a failed add never leaves a
        # setting behind on an Interface that carries nothing.
        commands = node.recorder.commands
        self.assertLess(commands.index(node.recorder.issued('ip -6 addr add')[0]), commands.index(issued[0]))

    def test_set_ip6_accepts_separate_prefix_length(self):
        intf, _ = self._intf()
        intf.setIP6('2001:db8::5', 48)
        self.assertEqual(intf.ip6, '2001:db8::5')
        self.assertEqual(intf.prefixLen6, '48')

    def test_set_ip6_without_prefix_length_raises(self):
        intf, _ = self._intf()
        with self.assertRaises(LinkError) as ctx:
            intf.setIP6('2001:db8::5')
        self.assertIn('prefix length', str(ctx.exception))

    def test_set_ip6_rejects_malformed_address(self):
        intf, _ = self._intf()
        with self.assertRaises(LinkError):
            intf.setIP6('not-an-address/64')

    def test_failed_apply_raises_naming_node_interface_and_address(self):
        intf, node = self._intf()
        node.recorder.fail_on('ip -6 addr add', 'Error: permission denied')
        with self.assertRaises(LinkError) as ctx:
            intf.setIP6('2001:db8::1/64')
        message = str(ctx.exception)
        self.assertIn('h1', message)
        self.assertIn('h1-eth0', message)
        self.assertIn('2001:db8::1/64', message)

    def test_failed_apply_leaves_no_recorded_address(self):
        intf, node = self._intf()
        node.recorder.fail_on('ip -6 addr add', 'Error: permission denied')
        with self.assertRaises(LinkError):
            intf.setIP6('2001:db8::1/64')
        self.assertIsNone(intf.ip6)

    def test_address_already_existing_is_not_an_error(self):
        intf, node = self._intf()
        node.recorder.respond_to('ip -6 addr add', 'RTNETLINK answers: File exists')
        intf.setIP6('2001:db8::1/64')
        self.assertEqual(intf.ip6, '2001:db8::1')


class IPv6ReadBackTests(unittest.TestCase):
    """An Interface reports the address the kernel actually accepted."""

    def _intf(self):
        from mniplab.link import IPv6Intf
        from mniplab.tests.fakes import FakeNode
        node = FakeNode('h1')
        return IPv6Intf('h1-eth0', node=node), node

    def test_update_ip6_reads_global_address(self):
        intf, node = self._intf()
        node.recorder.respond_to(
            'ip -6 addr show',
            'inet6 2001:db8::1/64 scope global\n    inet6 fe80::1/64 scope link',
        )
        self.assertEqual(intf.updateIP6(), '2001:db8::1')
        self.assertEqual(intf.prefixLen6, '64')

    def test_update_ip6_ignores_link_local_only(self):
        intf, node = self._intf()
        node.recorder.respond_to('ip -6 addr show', 'inet6 fe80::1/64 scope link')
        self.assertIsNone(intf.updateIP6())

    def test_update_ip6_clears_state_when_address_is_gone(self):
        intf, node = self._intf()
        intf.setIP6('2001:db8::1/64')
        node.recorder.respond_to('ip -6 addr show', '')
        self.assertIsNone(intf.updateIP6())
        self.assertIsNone(intf.ip6)

    def test_update_ip6_reports_kernel_truth_over_configured_value(self):
        intf, node = self._intf()
        intf.setIP6('2001:db8::1/64')
        node.recorder.respond_to('ip -6 addr show', 'inet6 2001:db8::99/64 scope global')
        self.assertEqual(intf.updateIP6(), '2001:db8::99')


class IPv6ConfigPathTests(unittest.TestCase):
    """Interface config() records IPv6 state from ip6."""

    def _intf(self, **params):
        from mniplab.link import IPv6Intf
        from mniplab.tests.fakes import FakeNode
        node = FakeNode('h1')
        return IPv6Intf('h1-eth0', node=node, **params), node

    def test_config_with_ip6_records_state(self):
        intf, _ = self._intf(ip6='2001:db8::1/64')
        self.assertEqual(intf.IP6(), '2001:db8::1')

    def test_config_without_ipv6_leaves_state_empty(self):
        intf, _ = self._intf()
        self.assertIsNone(intf.IP6())

    def test_strict_dad_waits_for_the_address_to_settle(self):
        intf, node = self._intf()
        node.recorder.respond_to('ip -6 addr show', 'inet6 2001:db8::1/64 scope global')
        intf.setIP6('2001:db8::1/64', dad_mode='strict')
        self.assertTrue(node.recorder.issued_any('ip -6 addr show'))

    def test_strict_dad_raises_on_duplicate_address(self):
        intf, node = self._intf()
        node.recorder.respond_to('ip -6 addr show', 'inet6 2001:db8::1/64 dadfailed')
        with self.assertRaises(LinkError) as ctx:
            intf.setIP6('2001:db8::1/64', dad_mode='strict')
        self.assertIn('DAD failed', str(ctx.exception))

    def test_fast_dad_does_not_poll(self):
        intf, node = self._intf()
        intf.setIP6('2001:db8::1/64', dad_mode='fast')
        self.assertFalse(node.recorder.issued_any('ip -6 addr show'))

    def test_duplicate_addresses_across_nodes_are_allowed(self):
        first, _ = self._intf(ip6='2001:db8::1/64')
        second, _ = self._intf(ip6='2001:db8::1/64')
        self.assertEqual(first.IP6(), second.IP6())
