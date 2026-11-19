"""Unit tests for mniplab.ipv6 — IPv6 address handling and DAD."""

import os
import unittest
from unittest.mock import Mock, patch

from mniplab.errors import LinkError
from mniplab.ipv6 import (
    DAD_MODE_FAST,
    DAD_MODE_STRICT,
    DAD_VALID_MODES,
    get_dad_mode,
    validate_ipv6_address,
    normalize_ipv6_address,
    is_ipv6_address,
)


class GetDadModeTests(unittest.TestCase):
    """get_dad_mode extracts and validates DAD mode from params or environment."""

    def test_default_fast(self):
        self.assertEqual(get_dad_mode(), DAD_MODE_FAST)

    def test_from_params(self):
        self.assertEqual(get_dad_mode({'dad_mode': 'strict'}), DAD_MODE_STRICT)

    def test_from_environment(self):
        with patch.dict(os.environ, {'MNIPLAB_IPV6_DAD_MODE': 'strict'}):
            self.assertEqual(get_dad_mode(), DAD_MODE_STRICT)

    def test_params_override_env(self):
        with patch.dict(os.environ, {'MNIPLAB_IPV6_DAD_MODE': 'strict'}):
            self.assertEqual(get_dad_mode({'dad_mode': 'fast'}), DAD_MODE_FAST)

    def test_invalid_raises(self):
        with self.assertRaises(LinkError) as ctx:
            get_dad_mode({'dad_mode': 'invalid'})
        self.assertIn("Invalid IPv6 DAD mode", str(ctx.exception))

    def test_case_insensitive(self):
        self.assertEqual(get_dad_mode({'dad_mode': 'STRICT'}), DAD_MODE_STRICT)
        self.assertEqual(get_dad_mode({'dad_mode': 'Fast'}), DAD_MODE_FAST)


class ValidateIPv6AddressTests(unittest.TestCase):
    """validate_ipv6_address checks format and prefix length."""

    def test_valid_with_prefix(self):
        result = validate_ipv6_address('2001:db8::1/64')
        self.assertEqual(result, '2001:db8::1/64')

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError) as ctx:
            validate_ipv6_address('not-an-ipv6')
        self.assertIn("Invalid IPv6 address format", str(ctx.exception))

    def test_missing_prefix_raises(self):
        with self.assertRaises(ValueError) as ctx:
            validate_ipv6_address('2001:db8::1')
        self.assertIn("must include prefix length", str(ctx.exception))

    def test_node_name_in_error(self):
        with self.assertRaises(ValueError) as ctx:
            validate_ipv6_address('bad', node_name='r1')
        self.assertIn("r1", str(ctx.exception))


class NormalizeIPv6AddressTests(unittest.TestCase):
    """normalize_ipv6_address normalizes and validates IPv6."""

    def test_valid_address(self):
        result = normalize_ipv6_address('2001:db8::1/64')
        self.assertEqual(result, '2001:db8::1/64')

    def test_bare_address_becomes_a_host_route(self):
        self.assertEqual(normalize_ipv6_address('2001:db8::1'), '2001:db8::1/128')

    def test_none_returns_none(self):
        self.assertIsNone(normalize_ipv6_address(None))

    def test_empty_returns_none(self):
        self.assertIsNone(normalize_ipv6_address(''))

    def test_invalid_raises_link_error(self):
        with self.assertRaises(LinkError) as ctx:
            normalize_ipv6_address('bad-address')
        self.assertIn("Invalid IPv6 address format", str(ctx.exception))


class IsIPv6AddressTests(unittest.TestCase):
    """is_ipv6_address detects IPv6 by colon separator."""

    def test_ipv6_true(self):
        self.assertTrue(is_ipv6_address('2001:db8::1'))

    def test_ipv6_with_prefix_true(self):
        self.assertTrue(is_ipv6_address('2001:db8::1/64'))

    def test_ipv4_false(self):
        self.assertFalse(is_ipv6_address('10.0.0.1'))

    def test_none_false(self):
        self.assertFalse(is_ipv6_address(None))

    def test_empty_false(self):
        self.assertFalse(is_ipv6_address(''))


class PureHelperTests(unittest.TestCase):
    """Address helpers take no node or interface — only strings."""

    def test_config_error_ignores_existing_address(self):
        from mniplab.ipv6 import is_ipv6_config_error
        self.assertFalse(is_ipv6_config_error('RTNETLINK answers: File exists'))
        self.assertTrue(is_ipv6_config_error('Error: permission denied'))
        self.assertFalse(is_ipv6_config_error(''))
        self.assertFalse(is_ipv6_config_error('ok'))

    def test_addr_add_command_skips_detection_in_fast_mode(self):
        from mniplab.ipv6 import build_addr_add_command
        cmd = build_addr_add_command('h1-eth0', '2001:db8::1/64', 'fast')
        self.assertIn('ip -6 addr add', cmd)
        self.assertIn('nodad', cmd)

    def test_addr_add_command_performs_detection_in_strict_mode(self):
        from mniplab.ipv6 import build_addr_add_command
        cmd = build_addr_add_command('h1-eth0', '2001:db8::1/64', 'strict')
        self.assertNotIn('nodad', cmd)

    def test_addr_add_command_quotes_interface_names(self):
        from mniplab.ipv6 import build_addr_add_command
        cmd = build_addr_add_command('weird name', '2001:db8::1/64', 'fast')
        self.assertIn("'weird name'", cmd)

    def test_parse_addresses_returns_global_addresses(self):
        from mniplab.ipv6 import parse_addresses
        output = (
            '    inet6 2001:db8::1/64 scope global\n'
            '    inet6 fe80::a00:27ff:fe4e:66a1/64 scope link\n'
        )
        self.assertEqual(parse_addresses(output), ['2001:db8::1/64'])

    def test_parse_addresses_can_include_link_local(self):
        from mniplab.ipv6 import parse_addresses
        output = '    inet6 fe80::1/64 scope link\n'
        self.assertEqual(parse_addresses(output), [])
        self.assertEqual(
            parse_addresses(output, include_link_local=True), ['fe80::1/64']
        )

    def test_parse_addresses_handles_empty_output(self):
        from mniplab.ipv6 import parse_addresses
        self.assertEqual(parse_addresses(''), [])

    def test_parse_addresses_preserves_order(self):
        from mniplab.ipv6 import parse_addresses
        output = (
            '    inet6 2001:db8::1/64 scope global\n'
            '    inet6 2001:db8:2::9/48 scope global\n'
        )
        self.assertEqual(
            parse_addresses(output), ['2001:db8::1/64', '2001:db8:2::9/48']
        )

    def test_dad_settled_requires_address_present_and_not_tentative(self):
        from mniplab.ipv6 import dad_settled
        self.assertTrue(dad_settled('inet6 2001:db8::1/64 scope global', '2001:db8::1/64'))
        self.assertFalse(
            dad_settled('inet6 2001:db8::1/64 scope global tentative', '2001:db8::1/64')
        )
        self.assertFalse(dad_settled('', '2001:db8::1/64'))
        self.assertFalse(dad_settled('inet6 2001:db8::9/64', '2001:db8::1/64'))

    def test_dad_failed_detects_duplicate(self):
        from mniplab.ipv6 import dad_failed
        self.assertTrue(dad_failed('inet6 2001:db8::1/64 tentative dadfailed'))
        self.assertFalse(dad_failed('inet6 2001:db8::1/64 scope global'))
        self.assertFalse(dad_failed(''))
