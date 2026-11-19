"""Unit tests for Lab-level IPv6 address reconciliation.

FRR clears IPv6 addresses on router interfaces when its daemons start, so
start() reconciles interface state against the kernel afterwards. These tests
drive that pass without privileges.
"""

import unittest
from unittest.mock import patch

from mniplab.link import IPv6Intf
from mniplab.tests.fakes import FakeNode
from mniplab.tests.labs import unstarted_lab


def _intf(node: FakeNode, name: str, ip6=None, prefix=None) -> IPv6Intf:
    """Build an IPv6Intf without running Mininet's constructor."""
    intf = object.__new__(IPv6Intf)
    intf.name = name
    intf.node = node
    intf.ip6 = ip6
    intf.prefixLen6 = prefix
    return intf


def _reapply(*hosts) -> None:
    """Run a Lab's IPv6 reconciliation over these hosts."""
    with unstarted_lab() as lab:
        lab.hosts = list(hosts)
        lab._reapply_ipv6_addresses()


class ReapplyIPv6AddressesTests(unittest.TestCase):
    """Addresses cleared during startup are restored from interface state."""

    def test_restores_an_address_the_kernel_lost(self):
        node = FakeNode('r1')
        intf = _intf(node, 'r1-eth0', '2001:db8::1', '64')
        node.intfs = {0: intf}
        node.recorder.respond_to('ip -6 addr show', '')

        _reapply(node)

        issued = node.recorder.issued('ip -6 addr add')
        self.assertEqual(len(issued), 1)
        self.assertIn('2001:db8::1/64', issued[0])
        self.assertEqual(intf.ip6, '2001:db8::1')

    def test_leaves_a_surviving_address_alone(self):
        node = FakeNode('r1')
        intf = _intf(node, 'r1-eth0', '2001:db8::1', '64')
        node.intfs = {0: intf}
        node.recorder.respond_to('ip -6 addr show', 'inet6 2001:db8::1/64 scope global')

        _reapply(node)

        self.assertFalse(node.recorder.issued_any('ip -6 addr add'))

    def test_ignores_interfaces_without_an_ipv6_address(self):
        node = FakeNode('h1')
        node.intfs = {0: _intf(node, 'h1-eth0')}

        _reapply(node)

        self.assertEqual(node.recorder.commands, [])

    def test_restores_every_interface_on_every_host(self):
        r1 = FakeNode('r1')
        r1.intfs = {
            0: _intf(r1, 'r1-eth0', '2001:db8:1::1', '64'),
            1: _intf(r1, 'r1-eth1', '2001:db8:2::1', '64'),
        }
        r2 = FakeNode('r2')
        r2.intfs = {0: _intf(r2, 'r2-eth0', '2001:db8:2::2', '64')}
        for node in (r1, r2):
            node.recorder.respond_to('ip -6 addr show', '')

        _reapply(r1, r2)

        self.assertEqual(len(r1.recorder.issued('ip -6 addr add')), 2)
        self.assertEqual(len(r2.recorder.issued('ip -6 addr add')), 1)

    def test_restore_failure_is_logged_not_raised(self):
        node = FakeNode('r1')
        node.intfs = {0: _intf(node, 'r1-eth0', '2001:db8::1', '64')}
        node.recorder.respond_to('ip -6 addr show', '')
        node.recorder.fail_on('ip -6 addr add', 'Error: permission denied')

        with patch('mniplab.net.error') as logged:
            _reapply(node)

        self.assertTrue(logged.called)
        self.assertIn('r1-eth0', logged.call_args[0][0])

    def test_a_failing_interface_does_not_stop_the_others(self):
        node = FakeNode('r1')
        node.intfs = {
            0: _intf(node, 'r1-eth0', '2001:db8:1::1', '64'),
            1: _intf(node, 'r1-eth1', '2001:db8:2::1', '64'),
        }
        node.recorder.respond_to('ip -6 addr show', '')
        node.recorder.fail_on('2001:db8:1::1', 'Error: permission denied')

        with patch('mniplab.net.error'):
            _reapply(node)

        self.assertTrue(node.recorder.issued_any('2001:db8:2::1/64'))

    def test_reads_the_kernel_before_deciding_to_restore(self):
        node = FakeNode('r1')
        node.intfs = {0: _intf(node, 'r1-eth0', '2001:db8::1', '64')}
        node.recorder.respond_to('ip -6 addr show', '')

        _reapply(node)

        self.assertTrue(node.recorder.issued_any('ip -6 addr show'))
