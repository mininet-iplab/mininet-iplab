"""Unit tests for the Lab key on root-namespace objects.

A switch's bridge and veths, a NAT Gateway's veths and a Container Host's
container all live in the host's one namespace, so each carries the Lab
key; the Node name a Learner sees is unchanged.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pytest

from mniplab import kernel
from mniplab.errors import ConfigurationError
from mniplab.net import mnIPLab
from mniplab.tests.labs import unstarted_lab
from mniplab.tests.test_switch import FakeSwitch


@pytest.mark.unit
class SwitchPortAliasTests(unittest.TestCase):
    """A Guide's ``s1-ethN`` names the keyed port ``<key>-s1-ethN``."""

    def _switch(self) -> FakeSwitch:
        sw = FakeSwitch('s1')
        sw.bridge_name = 'k1-s1'
        sw.nameToIntf['k1-s1-eth1'] = object()
        return sw

    def test_unkeyed_port_name_resolves_to_the_keyed_interface(self) -> None:
        sw = self._switch()
        self.assertEqual(sw.resolve_port('s1-eth1'), 'k1-s1-eth1')
        self.assertEqual(sw.resolve_port('k1-s1-eth1'), 'k1-s1-eth1')

    def test_unknown_port_is_left_for_the_caller_to_report(self) -> None:
        sw = self._switch()
        self.assertEqual(sw.resolve_port('s1-eth9'), 's1-eth9')

    def test_vlan_set_through_the_alias_lands_on_the_keyed_port(self) -> None:
        sw = self._switch()
        sw.set_access('s1-eth1', 10)
        self.assertIn('k1-s1-eth1', sw._port_vlans)
        self.assertTrue(any('set port k1-s1-eth1 tag=10' in c for c in sw.recorder.commands))


@pytest.mark.requires_root
class KeyedSwitchTests(unittest.TestCase):
    """The switch keeps its Node name; its bridge and veths carry the key."""

    def test_add_switch_refuses_a_name_that_cannot_fit_with_the_key(self) -> None:
        with unstarted_lab() as lab, \
                patch('mniplab.net.ensure_ovs_available'), \
                patch('mininet.net.Mininet.addSwitch', return_value=Mock()) as parent:
            limit = lab.max_root_node_name_len()
            with self.assertRaises(ConfigurationError) as ctx:
                lab.addSwitch('s' * (limit + 1))

        parent.assert_not_called()
        message = str(ctx.exception)
        self.assertIn('15', message)
        self.assertIn(str(limit), message)
        self.assertIn(lab.lab_key, message)

    def test_switch_node_name_is_unchanged_and_bridge_is_keyed(self) -> None:
        with unstarted_lab() as lab, patch('mniplab.net.ensure_ovs_available'):
            sw = lab.addSwitch('s1')
            self.assertEqual(sw.name, 's1')
            self.assertIs(lab.get('s1'), sw)
            self.assertEqual(str(sw), lab.bridge_name('s1'))
            self.assertEqual(f'{sw}', f'{lab.lab_key}-s1')
            sw.terminate()

    def test_link_keys_the_switch_side_only(self) -> None:
        with unstarted_lab() as lab, patch('mniplab.net.ensure_ovs_available'):
            sw = lab.addSwitch('s1')
            h1 = lab.addHost('h1')
            link = lab.addLink(h1, sw, params1={'ip': '10.0.0.1/24'})
            self.assertEqual(link.intf1.name, 'h1-eth0')
            self.assertEqual(link.intf2.name, lab.root_intf_name('s1', 1))
            self.assertIn('h1-eth0', h1.cmd('ip -o link'))
            link.delete()
            sw.terminate()


@pytest.mark.requires_root
class KeyedNATTests(unittest.TestCase):
    """A NAT Gateway's mark is the Lab's, and its veth carries the key."""

    def test_add_nat_refuses_a_name_that_cannot_fit_with_the_key(self) -> None:
        with unstarted_lab() as lab:
            limit = lab.max_root_node_name_len()
            with self.assertRaises(ConfigurationError) as ctx:
                lab.addNAT('n' * (limit + 1))
        self.assertIn('15', str(ctx.exception))
        self.assertIn(str(limit), str(ctx.exception))

    def test_marks_differ_across_labs_and_within_a_lab(self) -> None:
        net1 = mnIPLab(topo=None, controller=None, lab_key='nk1')
        net2 = mnIPLab(topo=None, controller=None, lab_key='nk2')
        try:
            a1 = net1.addNAT('nat1')
            a2 = net1.addNAT('nat2')
            b1 = net2.addNAT('nat1')
            self.assertNotEqual(a1._mark, a2._mark)
            self.assertNotEqual(a1._mark, b1._mark)
            self.assertGreater(min(a1._mark, a2._mark, b1._mark), 0)
        finally:
            for net in (net1, net2):
                for node in net.hosts:
                    node.terminate()
                net.stop()

    def test_nat_veth_is_keyed_and_the_router_side_is_not(self) -> None:
        with unstarted_lab() as lab:
            nat = lab.addNAT('nat1')
            r1 = lab.addRouter('r1')
            link = lab.addLink(nat, r1, params1={'ip': '100.64.0.1/24'},
                               params2={'ip': '100.64.0.2/24'})
            self.assertEqual(link.intf1.name, lab.root_intf_name('nat1', 0))
            self.assertEqual(link.intf2.name, 'r1-eth0')
            link.delete()


@pytest.mark.unit
class RootIPForwardLeaseTests(unittest.TestCase):
    """Root ``ip_forward`` is held by every NAT Gateway on the host, restored by the last."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.proc = os.path.join(self.tmp, 'ip_forward')
        with open(self.proc, 'w', encoding='utf-8') as f:
            f.write('0\n')
        lease_dir = os.path.join(self.tmp, 'lease')
        self._patches = [
            patch.object(kernel, 'IP_FORWARD_FILE', self.proc),
            patch.object(kernel, 'IP_FORWARD_LEASE_DIR', lease_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    def _value(self) -> str:
        with open(self.proc, encoding='utf-8') as f:
            return f.read().strip()

    def test_first_holder_enables_and_last_holder_restores(self) -> None:
        kernel.acquire_ip_forward_lease('lab1.nat1')
        self.assertEqual(self._value(), '1')
        kernel.acquire_ip_forward_lease('lab2.nat1')
        kernel.release_ip_forward_lease('lab1.nat1')
        self.assertEqual(self._value(), '1', 'the first Lab to stop must not restore under the other')
        kernel.release_ip_forward_lease('lab2.nat1')
        self.assertEqual(self._value(), '0')
        self.assertEqual(kernel._ip_forward_holders(), [])

    def test_an_already_enabled_host_stays_enabled(self) -> None:
        with open(self.proc, 'w', encoding='utf-8') as f:
            f.write('1\n')
        kernel.acquire_ip_forward_lease('lab1.nat1')
        kernel.release_ip_forward_lease('lab1.nat1')
        self.assertEqual(self._value(), '1')

    def test_release_without_acquire_is_a_no_op(self) -> None:
        kernel.release_ip_forward_lease('lab9.nat1')
        self.assertEqual(self._value(), '0')

    def test_host_wide_release_restores_and_clears_every_holder(self) -> None:
        kernel.acquire_ip_forward_lease('lab1.nat1')
        kernel.acquire_ip_forward_lease('lab2.nat1')
        kernel.release_all_ip_forward_leases()
        self.assertEqual(self._value(), '0')
        self.assertEqual(kernel._ip_forward_holders(), [])

    def test_a_labs_holders_can_be_released_by_key(self) -> None:
        kernel.acquire_ip_forward_lease('lab1.nat1')
        kernel.acquire_ip_forward_lease('lab1.nat2')
        kernel.acquire_ip_forward_lease('lab2.nat1')
        kernel.release_ip_forward_leases_of('lab1')
        self.assertEqual(kernel._ip_forward_holders(), ['lab2.nat1'])
        self.assertEqual(self._value(), '1')
        kernel.release_ip_forward_leases_of('lab2')
        self.assertEqual(self._value(), '0')


if __name__ == '__main__':
    unittest.main()
