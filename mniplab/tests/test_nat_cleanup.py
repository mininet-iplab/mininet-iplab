"""Unit tests for NAT idempotent cleanup.

Tests verify that MasqueradeNAT and TransitNAT cleanup methods handle
missing rules gracefully without failing.
"""

import unittest
from typing import Any, Callable, List, Optional
from unittest.mock import Mock, patch, MagicMock, PropertyMock

from mniplab.nodelib import RULE_PRESENT_SENTINEL, MasqueradeNAT, TransitNAT


class MasqueradeNATCleanupTests(unittest.TestCase):
    """Test MasqueradeNAT cleanup is idempotent."""

    def _make_nat(self):
        """Create a MasqueradeNAT instance with mocked attributes."""
        nat = object.__new__(MasqueradeNAT)
        nat.name = 'nat1'
        nat._mark = 100
        nat._local_intf_name = 'eth0'
        nat._local_subnet = '10.0.0.0/24'
        nat._terminated = False
        nat._peer_node = Mock()
        nat._peer_node.name = 'peer1'
        nat._peer_node.shell = True
        nat._peer_node.cmd = Mock(return_value='')
        nat._peer_intf_name = 'peer1-eth0'
        nat._ip_forward_holder = None
        return nat

    def test_remove_masquerade_rule_skips_when_not_found(self):
        """_remove_masquerade_rule should skip if rule doesn't exist."""
        nat = self._make_nat()
        peer_node = Mock()
        peer_node.name = 'peer1'
        # Return empty string to simulate rule not found
        peer_node.cmd = Mock(return_value='')
        
        nat._remove_masquerade_rule(peer_node, 'peer1-eth0')
        
        # Should check if rule exists, but not delete (because check returned empty)
        self.assertEqual(peer_node.cmd.call_count, 1)
        check_cmd = peer_node.cmd.call_args[0][0]
        self.assertIn('-C', check_cmd)
        self.assertIn('nat', check_cmd)

    def test_remove_masquerade_rule_deletes_when_found(self):
        """_remove_masquerade_rule should delete if rule exists."""
        nat = self._make_nat()
        peer_node = Mock()
        peer_node.name = 'peer1'
        # Return non-empty to simulate rule found, then empty for delete
        peer_node.cmd = Mock(side_effect=[RULE_PRESENT_SENTINEL, ''])
        
        nat._remove_masquerade_rule(peer_node, 'peer1-eth0')
        
        # Should check and then delete
        self.assertEqual(peer_node.cmd.call_count, 2)
        check_cmd = peer_node.cmd.call_args_list[0][0][0]
        delete_cmd = peer_node.cmd.call_args_list[1][0][0]
        self.assertIn('-C', check_cmd)
        self.assertIn('-D', delete_cmd)

    def test_remove_mark_rule_skips_when_not_found(self):
        """_remove_mark_rule should skip if rule doesn't exist."""
        nat = self._make_nat()
        # Return empty string to simulate rule not found
        nat.cmd = Mock(return_value='')
        
        nat._remove_mark_rule('eth0')
        
        # Should check if rule exists - the check command contains '-C'
        self.assertEqual(nat.cmd.call_count, 1)
        check_cmd = nat.cmd.call_args[0][0]
        self.assertIn('-C', check_cmd)
        self.assertIn('mangle', check_cmd)

    def test_remove_marked_masquerade_rule_skips_when_not_found(self):
        """_remove_marked_masquerade_rule should skip if rule doesn't exist."""
        nat = self._make_nat()
        # Return empty string to simulate rule not found
        nat.cmd = Mock(return_value='')
        
        nat._remove_marked_masquerade_rule()
        
        # Should check if rule exists - the check command contains '-C'
        self.assertEqual(nat.cmd.call_count, 1)
        check_cmd = nat.cmd.call_args[0][0]
        self.assertIn('-C', check_cmd)
        self.assertIn('nat', check_cmd)

    def test_cleanup_rules_is_idempotent(self):
        """cleanup_rules should be safe to call multiple times."""
        nat = self._make_nat()
        nat._terminated = False
        nat.cmd = Mock(return_value='')
        
        # First call
        nat.cleanup_rules()
        self.assertTrue(nat._terminated)
        
        # Second call should be a no-op
        nat.cleanup_rules()
        # Should not fail


class TransitNATCleanupTests(unittest.TestCase):
    """Test TransitNAT cleanup is idempotent."""

    def _make_transit_nat(self):
        """Create a TransitNAT instance with mocked attributes."""
        nat = object.__new__(TransitNAT)
        nat.name = 'transit1'
        nat._mark = 100
        nat._local_intf_name = 'eth0'
        nat._local_subnet = '10.0.0.0/24'
        nat._terminated = False
        nat._peer_node = Mock()
        nat._peer_node.name = 'peer1'
        nat._peer_node.shell = True
        nat._peer_node.cmd = Mock(return_value='')
        nat._peer_intf_name = 'peer1-eth0'
        nat._ip_forward_holder = None
        nat._downstream_states = []
        nat.cmd = Mock(return_value='')
        return nat

    def test_cleanup_skips_downstream_when_rule_not_found(self):
        """cleanup_rules should skip downstream cleanup when rules don't exist."""
        nat = self._make_transit_nat()
        downstream_node = Mock()
        downstream_node.name = 'downstream1'
        downstream_node.shell = True
        # Return empty for check (rule not found), empty for route check
        downstream_node.cmd = Mock(return_value='')
        
        nat._downstream_states = [{
            'node': downstream_node,
            'intf': 'downstream1-eth0',
            'gw': '10.0.0.1',
        }]
        
        nat.cleanup_rules()
        
        # Should check for rules but not try to delete
        # First call is the check command
        self.assertGreaterEqual(downstream_node.cmd.call_count, 1)
        check_cmd = downstream_node.cmd.call_args_list[0][0][0]
        self.assertIn('-C', check_cmd)
        # Should NOT delete because check returned empty
        delete_calls = [c[0][0] for c in downstream_node.cmd.call_args_list if '-D' in c[0][0]]
        self.assertEqual(len(delete_calls), 0)

    def test_cleanup_deletes_downstream_when_rule_found(self):
        """cleanup_rules should delete downstream rules when they exist."""
        nat = self._make_transit_nat()
        downstream_node = Mock()
        downstream_node.name = 'downstream1'
        downstream_node.shell = True
        # Return non-empty for check (rule found), empty for delete, non-empty for route
        downstream_node.cmd = Mock(
            side_effect=[RULE_PRESENT_SENTINEL, '', 'default via 10.0.0.1', '']
        )
        
        nat._downstream_states = [{
            'node': downstream_node,
            'intf': 'downstream1-eth0',
            'gw': '10.0.0.1',
        }]
        
        nat.cleanup_rules()
        
        # Should check, delete, check route, delete route
        self.assertGreaterEqual(downstream_node.cmd.call_count, 2)
        check_cmd = downstream_node.cmd.call_args_list[0][0][0]
        delete_cmd = downstream_node.cmd.call_args_list[1][0][0]
        self.assertIn('-C', check_cmd)
        self.assertIn('-D', delete_cmd)


if __name__ == '__main__':
    unittest.main()


class RuleExistenceContractTests(unittest.TestCase):
    """The existence check must read exit status, not stdout.

    'iptables -C' prints nothing to stdout whether or not the rule exists —
    only its exit status differs. A check that inspects stdout alone can never
    distinguish the two, and silently skips every deletion.
    """

    def _nat(self):
        nat = object.__new__(MasqueradeNAT)
        nat.name = 'nat1'
        nat._mark = 100
        return nat

    def test_check_command_converts_exit_status_into_output(self):
        cmd = MasqueradeNAT._build_rule_check_cmd('nat', 'POSTROUTING', '-o eth0 -j MASQUERADE')

        self.assertIn('-C', cmd)
        self.assertIn('&&', cmd)
        self.assertIn(RULE_PRESENT_SENTINEL, cmd)

    def test_check_command_discards_stdout_so_only_the_sentinel_remains(self):
        cmd = MasqueradeNAT._build_rule_check_cmd('nat', 'POSTROUTING', '-o eth0 -j MASQUERADE')

        self.assertIn('>/dev/null', cmd)

    def test_missing_rule_reports_absent(self):
        node = Mock()
        node.cmd = Mock(return_value='')

        self.assertFalse(self._nat()._rule_exists(node, 'nat', 'POSTROUTING', '-o eth0 -j MASQUERADE'))

    def test_present_rule_reports_present(self):
        node = Mock()
        node.cmd = Mock(return_value=f'{RULE_PRESENT_SENTINEL}\n')

        self.assertTrue(self._nat()._rule_exists(node, 'nat', 'POSTROUTING', '-o eth0 -j MASQUERADE'))

    def test_present_rule_is_actually_deleted(self):
        node = Mock()
        node.cmd = Mock(side_effect=[RULE_PRESENT_SENTINEL, ''])

        self._nat()._remove_rule_if_present(
            node, 'nat', 'POSTROUTING', '-o eth0 -j MASQUERADE', 'test rule'
        )

        issued = [call[0][0] for call in node.cmd.call_args_list]
        self.assertTrue(any('-D' in cmd for cmd in issued))

    def test_absent_rule_is_not_deleted(self):
        node = Mock()
        node.cmd = Mock(return_value='')

        self._nat()._remove_rule_if_present(
            node, 'nat', 'POSTROUTING', '-o eth0 -j MASQUERADE', 'test rule'
        )

        issued = [call[0][0] for call in node.cmd.call_args_list]
        self.assertFalse(any('-D' in cmd for cmd in issued))


class MasqueradeNATSealingRuleTests(unittest.TestCase):
    """The NAT Gateway seals its Lab with policy routing and a conntrack zone.

    A Gateway with a given mark renders the full rule set — the raw CT zone
    rule, the mangle mark rule, the three CONNMARK rules, the masquerade
    rule, the ``ip rule`` and the table's link route — with the table id
    equal to the mark and the zone equal to the mark's Lab base
    (``mark >> 8``); the keyed sweep and the host-wide cleanup derive the
    deletions for all of them from the one mangle line.
    """

    MARK = 0x123401  # Lab base 0x1234
    ZONE = MARK >> 8
    INTF = 'k01-nat1-eth0'

    def _nat(self, subnet: Optional[str] = '100.64.0.0/24') -> MasqueradeNAT:
        nat = object.__new__(MasqueradeNAT)
        nat.name = 'nat1'
        nat._mark = self.MARK
        nat._local_intf_name = self.INTF
        nat._local_subnet = subnet
        nat.nat_subnet = subnet
        nat._terminated = False
        nat._ip_forward_holder = None
        nat._peer_node = None
        nat._peer_intf_name = None
        nat.cmd = Mock(return_value='')
        return nat

    def _sealing_rules(self, action: str) -> List[str]:
        mark, zone, intf = self.MARK, self.ZONE, self.INTF
        return [
            f'iptables -t raw -{action} PREROUTING -i {intf} -j CT --zone-orig {zone}',
            f'iptables -t mangle -{action} PREROUTING -i {intf} -j MARK --set-mark {mark}',
            f'iptables -t mangle -{action} PREROUTING -i {intf} -j CONNMARK --save-mark',
            f'iptables -t mangle -{action} PREROUTING ! -i {intf} -m connmark --mark {mark} -j CONNMARK --restore-mark',
            f'iptables -t mangle -{action} OUTPUT -m connmark --mark {mark} -j CONNMARK --restore-mark',
            f'iptables -t nat -{action} POSTROUTING -m mark --mark {mark} -j MASQUERADE',
        ]

    def test_stage2_installs_and_removes_sealing_rules(self) -> None:
        nat = self._nat()

        nat._configure_stage2_masquerade()
        installed = [call[0][0] for call in nat.cmd.call_args_list]

        for rule in self._sealing_rules('A'):
            self.assertIn(rule, installed)
        self.assertIn(f'ip rule add fwmark {self.MARK} lookup {self.MARK}', installed)
        self.assertIn(f'ip route add 100.64.0.0/24 dev {self.INTF} table {self.MARK}', installed)

        nat.cmd = Mock(side_effect=lambda cmd: RULE_PRESENT_SENTINEL if '-C' in cmd else '')
        nat._remove_stage2_masquerade()
        removed = [call[0][0] for call in nat.cmd.call_args_list]

        for rule in self._sealing_rules('D'):
            self.assertTrue(any(rule in cmd for cmd in removed), rule)
        self.assertIn(f'ip rule del fwmark {self.MARK} lookup {self.MARK}', removed)
        self.assertIn(f'ip route flush table {self.MARK}', removed)

    def test_no_link_subnet_installs_no_policy_rule(self) -> None:
        """Without a link route there is no table to send marked replies to."""
        nat = self._nat(subnet=None)

        with self.assertLogs('mniplab.nodelib', level='WARNING') as logs:
            nat._configure_stage2_masquerade()
        issued = [call[0][0] for call in nat.cmd.call_args_list]

        self.assertFalse(any(cmd.startswith('ip rule') for cmd in issued), issued)
        self.assertFalse(any(cmd.startswith('ip route') for cmd in issued), issued)
        self.assertTrue(any('policy route' in line for line in logs.output), logs.output)

    def _derived_deletions(self) -> List[str]:
        mark, zone, intf = self.MARK, self.ZONE, self.INTF
        return [
            f'iptables -t nat -D POSTROUTING -m mark --mark {mark} -j MASQUERADE',
            f'iptables -t raw -D PREROUTING -i {intf} -j CT --zone-orig {zone}',
            f'iptables -t mangle -D PREROUTING -i {intf} -j CONNMARK --save-mark',
            f'iptables -t mangle -D PREROUTING ! -i {intf} -m connmark --mark {mark} -j CONNMARK --restore-mark',
            f'iptables -t mangle -D OUTPUT -m connmark --mark {mark} -j CONNMARK --restore-mark',
            f'ip rule del fwmark {mark} lookup {mark}',
            f'ip route flush table {mark}',
        ]

    def _run_cleanup_against(self, mangle_lines: List[str], sweep: Callable[[], None]) -> List[str]:
        commands_run: List[str] = []

        def fake_run(cmd: List[str], **kwargs: Any) -> Mock:
            commands_run.append(' '.join(cmd))
            res = Mock()
            res.returncode = 0
            res.stdout = ''
            if cmd[:5] == ['iptables', '-t', 'mangle', '-S', 'PREROUTING']:
                res.stdout = ''.join(f'{line}\n' for line in mangle_lines)
            return res

        with patch('mniplab.cleanup._run_command_silent', side_effect=fake_run), \
             patch('mniplab.kernel.release_ip_forward_leases_of'):
            sweep()
        return commands_run

    def test_sweep_root_namespace_deletes_all_rules_from_one_mangle_line(self) -> None:
        from mniplab.cleanup import sweep_root_namespace_by_key

        mangle_line = f'-A PREROUTING -i {self.INTF} -j MARK --set-xmark {hex(self.MARK)}/0xffffffff'

        commands_run = self._run_cleanup_against([mangle_line], lambda: sweep_root_namespace_by_key('k01'))

        self.assertIn(f'iptables -t mangle -D {mangle_line[len("-A "):]}', commands_run)
        for deletion in self._derived_deletions():
            self.assertIn(deletion, commands_run)

    def test_host_wide_cleanup_touches_only_keyed_mark_rules(self) -> None:
        """A host's own mangle and fwmark rules are not the cleanup's to delete."""
        from mniplab.cleanup import cleanup_nat_rules

        ours = f'-A PREROUTING -i {self.INTF} -j MARK --set-xmark {hex(self.MARK)}/0xffffffff'
        foreign = '-A PREROUTING -i eth0 -j MARK --set-xmark 0x1/0xffffffff'
        save = f'-A PREROUTING -i {self.INTF} -j CONNMARK --save-mark'

        commands_run = self._run_cleanup_against([foreign, ours, save], lambda: cleanup_nat_rules(verbose=False))

        self.assertIn(f'iptables -t mangle -D {ours[len("-A "):]}', commands_run)
        for deletion in self._derived_deletions():
            self.assertIn(deletion, commands_run)
        self.assertFalse(any(' -i eth0 ' in cmd for cmd in commands_run), commands_run)
        self.assertNotIn('ip rule show', commands_run)
        self.assertNotIn('iptables -t raw -S PREROUTING', commands_run)
