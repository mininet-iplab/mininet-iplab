"""Unit tests for mniplab.kernel — kernel network configuration."""

import unittest
from unittest.mock import Mock, patch

from mniplab.errors import RouterError
from mniplab.kernel import KernelConfigManager


class KernelConfigManagerApplySysctlTests(unittest.TestCase):
    """apply_sysctl runs sysctl and optionally raises on failure."""

    def _make_manager(self, cmd_output=''):
        node = Mock()
        node.name = 'r1'
        node.cmd.return_value = cmd_output
        return KernelConfigManager(node), node

    def test_success_non_critical(self):
        mgr, node = self._make_manager('net.ipv4.ip_forward = 1')
        mgr.apply_sysctl('net.ipv4.ip_forward', '1')
        node.cmd.assert_called_once_with('sysctl -w net.ipv4.ip_forward=1')

    def test_critical_success(self):
        mgr, node = self._make_manager('net.ipv4.ip_forward = 1')
        mgr.apply_sysctl('net.ipv4.ip_forward', '1', critical=True)
        node.cmd.assert_called_once_with('sysctl -w net.ipv4.ip_forward=1')

    def test_critical_failure_raises(self):
        mgr, node = self._make_manager('sysctl: permission denied')
        with self.assertRaises(RouterError) as ctx:
            mgr.apply_sysctl('net.ipv4.ip_forward', '1', critical=True)
        self.assertIn("Failed to set", str(ctx.exception))
        self.assertIn("r1", str(ctx.exception))

    def test_critical_failure_missing_expected_output(self):
        mgr, node = self._make_manager('some unrelated output')
        with self.assertRaises(RouterError) as ctx:
            mgr.apply_sysctl('net.ipv4.ip_forward', '1', critical=True)
        self.assertIn("Failed to set", str(ctx.exception))

    def test_non_critical_failure_does_not_raise(self):
        mgr, node = self._make_manager('sysctl: error')
        mgr.apply_sysctl('net.ipv4.ip_forward', '1', critical=False)
        # Should not raise

    def test_error_context_included(self):
        mgr, node = self._make_manager('sysctl: error')
        with self.assertRaises(RouterError) as ctx:
            mgr.apply_sysctl('net.ipv4.ip_forward', '1', critical=True,
                             error_context="Check root privileges")
        self.assertIn("Check root privileges", str(ctx.exception))


class KernelConfigManagerEnableForwardingTests(unittest.TestCase):
    """enable_forwarding sets IPv4 and IPv6 forwarding."""

    def test_calls_both_forwarding_sysctls(self):
        node = Mock()
        node.name = 'r1'
        node.cmd.side_effect = [
            'net.ipv4.ip_forward = 1',
            'net.ipv6.conf.all.forwarding = 1',
        ]
        mgr = KernelConfigManager(node)

        mgr.enable_forwarding()

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('sysctl -w net.ipv4.ip_forward=1', calls)
        self.assertIn('sysctl -w net.ipv6.conf.all.forwarding=1', calls)


class KernelConfigManagerDisableIcmpRateLimitingTests(unittest.TestCase):
    """disable_icmp_rate_limiting applies sysctl settings."""

    def test_applies_icmp_settings(self):
        node = Mock()
        node.name = 'r1'
        mgr = KernelConfigManager(node)

        mgr.disable_icmp_rate_limiting()

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('sysctl -w net.ipv4.icmp_ratelimit=0', calls)
        self.assertIn('sysctl -w net.ipv4.icmp_ratemask=0', calls)
        self.assertIn('sysctl -w net.ipv6.icmp.ratelimit=0', calls)
        self.assertIn('sysctl -w net.ipv6.icmp.ratemask=', calls)


class KernelConfigManagerEnableMultipathTests(unittest.TestCase):
    """enable_multipath sets hash policy sysctls."""

    def test_applies_multipath_settings(self):
        node = Mock()
        node.name = 'r1'
        mgr = KernelConfigManager(node)

        mgr.enable_multipath()

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('sysctl -w net.ipv4.fib_multipath_hash_policy=1', calls)
        self.assertIn('sysctl -w net.ipv6.fib_multipath_hash_policy=1', calls)
        self.assertIn('sysctl -w net.ipv4.fib_multipath_hash_fields=3', calls)
        self.assertIn('sysctl -w net.ipv6.fib_multipath_hash_fields=3', calls)


class KernelConfigManagerSetupVRFsTests(unittest.TestCase):
    """setup_vrfs creates VRF devices and attaches interfaces."""

    def test_creates_vrf_devices(self):
        node = Mock()
        node.name = 'r1'
        mgr = KernelConfigManager(node)

        mgr.setup_vrfs({'vrf-red': 100}, {})

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('ip link add vrf-red type vrf table 100', calls)
        self.assertIn('ip link set vrf-red up', calls)

    def test_attaches_interface_preserving_ip(self):
        node = Mock()
        node.name = 'r1'
        node.cmd.side_effect = lambda cmd: (
            'inet 10.0.0.1/24 scope global eth0' if 'ip addr show' in cmd else ''
        )
        mgr = KernelConfigManager(node)

        mgr.setup_vrfs({'vrf-red': 100}, {'eth0': 'vrf-red'})

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('ip link set eth0 master vrf-red', calls)
        self.assertIn('ip addr add 10.0.0.1/24 dev eth0', calls)

    def test_skips_unknown_vrf(self):
        node = Mock()
        node.name = 'r1'
        mgr = KernelConfigManager(node)

        with patch('mniplab.kernel.error') as mock_error:
            mgr.setup_vrfs({}, {'eth0': 'vrf-missing'})

        mock_error.assert_called_once()
        self.assertIn("vrf-missing", str(mock_error.call_args))

    def test_attaches_without_ip_when_none_found(self):
        node = Mock()
        node.name = 'r1'
        node.cmd.return_value = ''  # No IP address found
        mgr = KernelConfigManager(node)

        mgr.setup_vrfs({'vrf-blue': 200}, {'eth1': 'vrf-blue'})

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('ip link set eth1 master vrf-blue', calls)
        # Should not add IP since none was found
        ip_add_calls = [c for c in calls if 'ip addr add' in c]
        self.assertEqual(len(ip_add_calls), 0)

    def test_attaches_interface_preserving_dual_stack_and_secondary_ips(self):
        node = Mock()
        node.name = 'r1'
        ip_addr_show_output = (
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default\n"
            "    link/ether 02:42:0a:00:00:01 brd ff:ff:ff:ff:ff:ff\n"
            "    inet 10.0.0.1/24 scope global eth0\n"
            "       valid_lft forever preferred_lft forever\n"
            "    inet 10.0.0.2/24 scope global secondary eth0\n"
            "       valid_lft forever preferred_lft forever\n"
            "    inet6 2001:db8::1/64 scope global\n"
            "       valid_lft forever preferred_lft forever\n"
            "    inet6 2001:db8:1::1/64 scope global\n"
            "       valid_lft forever preferred_lft forever\n"
            "    inet6 fe80::42:aff:fe00:1/64 scope link\n"
            "       valid_lft forever preferred_lft forever\n"
        )
        node.cmd.side_effect = lambda cmd: (
            ip_addr_show_output if cmd == 'ip addr show eth0' else ''
        )
        mgr = KernelConfigManager(node)

        with patch('mniplab.kernel.info') as mock_info:
            mgr.setup_vrfs({'vrf-red': 100}, {'eth0': 'vrf-red'})

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('ip link set eth0 master vrf-red', calls)
        ip_add_calls = [c for c in calls if c.startswith('ip addr add ')]
        self.assertEqual(ip_add_calls, [
            'ip addr add 10.0.0.1/24 dev eth0',
            'ip addr add 10.0.0.2/24 dev eth0',
            'ip addr add 2001:db8::1/64 dev eth0',
            'ip addr add 2001:db8:1::1/64 dev eth0',
        ])
        mock_info.assert_called_once_with(
            "*** Attaching eth0 to VRF vrf-red on r1 (preserving 10.0.0.1/24, 10.0.0.2/24, 2001:db8::1/64, 2001:db8:1::1/64)\n"
        )

    def test_attaches_interface_skipping_malformed_ips(self):
        node = Mock()
        node.name = 'r1'
        ip_addr_show_output = (
            "    inet 10.0.0.1/24 scope global eth0\n"
            "    inet 999.999.999.999/24 scope global eth0\n"
            "    inet6 2001:db8::1/64 scope global\n"
            "    inet6 2001:db8:::1/64 scope global\n"
        )
        node.cmd.side_effect = lambda cmd: (
            ip_addr_show_output if cmd == 'ip addr show eth0' else ''
        )
        mgr = KernelConfigManager(node)

        with patch('mniplab.kernel.info') as mock_info:
            mgr.setup_vrfs({'vrf-red': 100}, {'eth0': 'vrf-red'})

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn('ip link set eth0 master vrf-red', calls)
        ip_add_calls = [c for c in calls if c.startswith('ip addr add ')]
        self.assertEqual(ip_add_calls, [
            'ip addr add 10.0.0.1/24 dev eth0',
            'ip addr add 2001:db8::1/64 dev eth0',
        ])
        mock_info.assert_called_once_with(
            "*** Attaching eth0 to VRF vrf-red on r1 (preserving 10.0.0.1/24, 2001:db8::1/64)\n"
        )

    def test_setup_vrfs_quotes_shell_arguments(self):
        node = Mock()
        node.name = 'r1'
        node.cmd.return_value = 'inet 10.0.0.1/24 scope global eth 0'
        mgr = KernelConfigManager(node)

        mgr.setup_vrfs({'vrf red': 100}, {'eth 0': 'vrf red'})

        calls = [c[0][0] for c in node.cmd.call_args_list]
        self.assertIn("ip link add 'vrf red' type vrf table 100", calls)
        self.assertIn("ip link set 'vrf red' up", calls)
        self.assertIn("ip addr show 'eth 0'", calls)
        self.assertIn("ip link set 'eth 0' master 'vrf red'", calls)
        self.assertIn("ip addr add 10.0.0.1/24 dev 'eth 0'", calls)



if __name__ == '__main__':
    unittest.main()
