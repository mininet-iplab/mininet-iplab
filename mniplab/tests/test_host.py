"""Unit tests for mnIPLabHost IPv6 address and default route methods."""

import unittest
from unittest.mock import patch

from mniplab.errors import LinkError
from mniplab.host import mnIPLabHost
from mniplab.tests.fakes import CommandRecorder, FakeIntf


class _StubHost(mnIPLabHost):
    """A host that records commands instead of running them.

    Bypasses Node.__init__ so the tests need no network namespace.
    """

    def __init__(self, name='h1', intf=None):
        self.name = name
        self.recorder = CommandRecorder()
        self._intf = intf

    def cmd(self, *args, **kwargs):
        return self.recorder(*args, **kwargs)

    def defaultIntf(self):
        return self._intf


class _PlainIntf:
    """An interface with no IPv6 state, like stock Mininet's."""

    def __init__(self, name='h1-eth0'):
        self.name = name


class HostSetIP6Tests(unittest.TestCase):
    """A Host sets its IPv6 address through a method on the Host."""

    def test_delegates_to_an_ipv6_capable_interface(self):
        node = _StubHost()
        intf = FakeIntf('h1-eth0')
        intf.node = node
        # Borrow the real interface implementation for delegation.
        from mniplab.link import IPv6Intf
        real = object.__new__(IPv6Intf)
        real.name = 'h1-eth0'
        real.node = node
        real.ip6 = None
        real.prefixLen6 = None
        node._intf = real

        node.setIP6('2001:db8::1/64')

        self.assertEqual(real.IP6(), '2001:db8::1')
        self.assertTrue(node.recorder.issued_any('ip -6 addr add'))

    def test_applies_address_on_a_plain_interface(self):
        node = _StubHost(intf=_PlainIntf())

        node.setIP6('2001:db8::1/64')

        issued = node.recorder.issued('ip -6 addr add')
        self.assertEqual(len(issued), 1)
        self.assertIn('2001:db8::1/64', issued[0])

    def test_accepts_a_separate_prefix_length(self):
        node = _StubHost(intf=_PlainIntf())

        node.setIP6('2001:db8::1', 48)

        self.assertTrue(node.recorder.issued_any('2001:db8::1/48'))

    def test_missing_prefix_length_raises(self):
        node = _StubHost(intf=_PlainIntf())

        with self.assertRaises(LinkError) as ctx:
            node.setIP6('2001:db8::1')

        self.assertIn('prefix length', str(ctx.exception))

    def test_failure_raises_naming_the_host(self):
        node = _StubHost(intf=_PlainIntf())
        node.recorder.fail_on('ip -6 addr add', 'Error: permission denied')

        with self.assertRaises(LinkError) as ctx:
            node.setIP6('2001:db8::1/64')

        self.assertIn('h1', str(ctx.exception))

    def test_no_interface_is_a_no_op(self):
        node = _StubHost(intf=None)

        self.assertEqual(node.setIP6('2001:db8::1/64'), '')


class HostDefaultRoute6Tests(unittest.TestCase):
    """A Host sets its IPv6 default route through a method on the Host."""

    def test_applies_the_route(self):
        node = _StubHost()

        node.setDefaultRoute6('via 2001:db8::1')

        self.assertTrue(node.recorder.issued_any('ip -6 route add default via 2001:db8::1'))

    def test_failure_raises_naming_the_host(self):
        node = _StubHost()
        node.recorder.fail_on('ip -6 route add', 'Error: network unreachable')

        with self.assertRaises(LinkError) as ctx:
            node.setDefaultRoute6('via 2001:db8::1')

        self.assertIn('h1', str(ctx.exception))


class HostConfigErrorPolicyTests(unittest.TestCase):
    """Lab startup logs a gateway failure rather than refusing to start."""

    def test_config_logs_and_continues_when_route_fails(self):
        node = _StubHost(intf=_PlainIntf())
        node.recorder.fail_on('ip -6 route add', 'Error: unreachable')

        with patch('mniplab.host.error') as logged:
            with patch('mniplab.utils.disable_icmp_rate_limiting'):
                with patch('mininet.node.Node.config', return_value={}):
                    node.config(defaultRoute6='via 2001:db8::1')

        self.assertTrue(logged.called)
        self.assertIn('IPv6 default route', logged.call_args[0][0])


class HostInitParamsTests(unittest.TestCase):
    """__init__ reads a Host's nameservers."""

    def _init_with(self, **params):
        captured = {}

        def fake_node_init(node_self, name, **kwargs):
            captured.update(kwargs)

        with patch('mininet.node.Node.__init__', fake_node_init):
            host = mnIPLabHost('h1', **params)
        return host, captured

    def test_mniplabhost_accepts_nameservers_list(self):
        host, captured = self._init_with(nameservers=['10.0.0.1', '10.0.0.2'])
        self.assertEqual(host.nameservers, ['10.0.0.1', '10.0.0.2'])

    def test_mniplabhost_accepts_single_nameserver_string(self):
        host, captured = self._init_with(nameservers='10.0.0.1')
        self.assertEqual(host.nameservers, ['10.0.0.1'])

    def test_mniplabhost_defaults_nameservers_to_empty_list(self):
        host, captured = self._init_with()
        self.assertEqual(host.nameservers, [])
