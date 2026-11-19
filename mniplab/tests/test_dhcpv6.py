"""Tests for the DHCPv6 Service: the IPv6 half of the DHCP feature.

Unit coverage (unprivileged): family inference
from a Pool's CIDR, family-aware validation, the generated DHCPv6
configuration (including the per-Subnet ``interface`` Kea 3.2 requires),
the family-aware control client, CIDR resolution against the address
*recorded* on an Interface (never the kernel's current state),
the ``dhcp6`` client marker suppressing a declared address and default
route, and the provisioner's family-paired lifecycle. The privileged suite
at the bottom starts a real Lab with an IPv6 Pool on a Router, an
FRR-sourced Router Advertisement, and a real dhcpcd taking a real Lease.
"""

import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
import unittest
from unittest.mock import Mock, patch

import pytest

from mniplab.dhcp.config import KeaConfigBuilder
from mniplab.dhcp.leases import LeaseQueryService
from mniplab.dhcp.pool import (
    Pool,
    Reservation,
    infer_family,
    validate_option_address,
    validate_pool_range,
    validate_preferred_lifetime,
    validate_reservation,
)
from mniplab.dhcp.pool_service import PoolService
from mniplab.dhcp.service import DHCPService
from mniplab.errors import ConfigurationError, LinkError
from mniplab.net import mnIPLab
from mniplab.provisioners import DHCPProvisioner
from mniplab.dhcp.router_advertisement import (
    InterfaceRouterAdvertisement,
    RouterAdvertisementAdapter,
    RouterAdvertisementDiagnostics,
)
from mniplab.tests.fakes import (
    FakeLab,
    FakeNode,
    KeaClientStubMixin,
    ServicesReadyMixin,
    cli_output,
)

V6_POOL: Dict[str, Any] = dict(
    cidr='2001:db8:1::/64', start='2001:db8:1::100', end='2001:db8:1::200'
)


class PoolFamilyTests(unittest.TestCase):
    """A Pool's family is inferred from its CIDR — no family argument exists."""

    def test_ipv4_cidr_infers_family_4(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        self.assertEqual(pool.family, 4)

    def test_ipv6_cidr_infers_family_6(self):
        pool = Pool(**V6_POOL)
        self.assertEqual(pool.family, 6)

    def test_infer_family_matches_ipaddress_version(self):
        self.assertEqual(infer_family('10.50.0.0/24'), 4)
        self.assertEqual(infer_family('2001:db8:1::/64'), 6)

    def test_infer_family_rejects_garbage(self):
        with self.assertRaises(ValueError):
            infer_family('not-a-cidr')


class PoolValidationTests(unittest.TestCase):
    """Pool dataclass validates its parameters on instantiation."""

    def test_pool_rejects_router_option_for_ipv6(self):
        expected_msg = (
            "A DHCPv6 Pool has no router option: the default gateway comes "
            "from the Router Advertisement, not from DHCPv6."
        )
        with self.assertRaises(ValueError) as ctx:
            Pool(**V6_POOL, router='2001:db8:1::1')
        self.assertEqual(str(ctx.exception), expected_msg)

    def test_pool_rejects_preferred_lifetime_for_ipv4(self):
        with self.assertRaises(ValueError) as ctx:
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200', preferred_lifetime=60)
        self.assertIn('IPv6', str(ctx.exception))

    def test_pool_accepts_valid_preferred_lifetime_for_ipv6(self):
        pool = Pool(**V6_POOL, lease_time=120, preferred_lifetime=60)
        self.assertEqual(pool.preferred_lifetime, 60)
        self.assertEqual(pool.lease_time, 120)

    def test_pool_rejects_preferred_lifetime_greater_than_valid_lifetime(self):
        with self.assertRaises(ValueError) as ctx:
            Pool(**V6_POOL, lease_time=120, preferred_lifetime=150)
        self.assertIn('cannot be greater than valid lifetime', str(ctx.exception))


class ValidatePoolRangeV6Tests(unittest.TestCase):
    """validate_pool_range accepts IPv6 Pools and validates them by the same rules."""

    def test_accepts_valid_v6_range_within_subnet(self):
        validate_pool_range('2001:db8:1::/64', '2001:db8:1::100', '2001:db8:1::200')
        validate_pool_range('2001:db8:1::/64', '2001:db8:1::100', '2001:db8:1::100')

    def test_rejects_ipv4_addresses_in_v6_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('2001:db8:1::/64', '10.50.0.100', '2001:db8:1::200')
        self.assertIn('IPv6', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('2001:db8:1::/64', '2001:db8:1::100', '10.50.0.200')
        self.assertIn('IPv6', str(ctx.exception))

    def test_rejects_start_address_outside_v6_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('2001:db8:1::/64', '2001:db8:2::100', '2001:db8:1::200')
        self.assertIn('not in subnet 2001:db8:1::/64', str(ctx.exception))

    def test_rejects_end_address_outside_v6_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('2001:db8:1::/64', '2001:db8:1::100', '2001:db8:2::200')
        self.assertIn('not in subnet 2001:db8:1::/64', str(ctx.exception))

    def test_rejects_start_address_after_end_address(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('2001:db8:1::/64', '2001:db8:1::200', '2001:db8:1::100')
        self.assertIn('cannot be after', str(ctx.exception))

    def test_rejects_subnet_router_anycast_address_as_pool_start(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('2001:db8:1::/64', '2001:db8:1::', '2001:db8:1::200')
        self.assertIn('Subnet-Router anycast address', str(ctx.exception))

    def test_ipv4_broadcast_guard_does_not_misfire_on_ipv6(self):
        """IPv6 has no broadcast addresses (RFC 4291 §2.6). An end address
        with all-ones interface ID must not be rejected by an IPv4 broadcast
        check."""
        validate_pool_range(
            '2001:db8:1::/64',
            '2001:db8:1::100',
            '2001:db8:1::ffff:ffff:ffff:ffff',
        )


class ValidateOptionAddressV6Tests(unittest.TestCase):
    """The DNS-server option validates against the Pool's family."""

    def test_accepts_ipv6_when_family_is_6(self):
        validate_option_address('DNS server', '2001:db8::53', family=6)

    def test_rejects_ipv4_when_family_is_6(self):
        with self.assertRaises(ValueError) as ctx:
            validate_option_address('DNS server', '8.8.8.8', family=6)
        self.assertIn('IPv6', str(ctx.exception))

    def test_rejects_ipv6_when_family_is_4(self):
        with self.assertRaises(ValueError) as ctx:
            validate_option_address('DNS server', '2001:db8::53', family=4)
        self.assertIn('IPv4', str(ctx.exception))

    def test_router_option_on_ipv6_pool_is_rejected_with_didactic_message(self):
        """Ticket 03: DHCPv6 has no router option; default route comes from RA."""
        expected_msg = (
            "A DHCPv6 Pool has no router option: the default gateway comes "
            "from the Router Advertisement, not from DHCPv6."
        )
        with self.assertRaises(ValueError) as ctx:
            validate_option_address('router', '2001:db8:1::1', family=6)
        self.assertEqual(str(ctx.exception), expected_msg)

        # Even with an IPv4 address, router on family 6 is still rejected with the same message
        with self.assertRaises(ValueError) as ctx:
            validate_option_address('router', '10.50.0.1', family=6)
        self.assertEqual(str(ctx.exception), expected_msg)

    def test_router_option_omitted_is_accepted_on_family_6(self):
        validate_option_address('router', '', family=6)

    def test_router_option_on_family_4_accepts_valid_ipv4(self):
        validate_option_address('router', '10.50.0.1', family=4)


class ValidateReservationV6Tests(unittest.TestCase):
    """A v6 Reservation's address must belong to the Subnet's family."""

    def test_accepts_v6_address_in_v6_subnet(self):
        validate_reservation('2001:db8:1::/64', 'aa:bb:cc:dd:ee:ff', '2001:db8:1::50')

    def test_rejects_v4_address_in_v6_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('2001:db8:1::/64', 'aa:bb:cc:dd:ee:ff', '10.50.0.50')
        self.assertIn('IPv6', str(ctx.exception))

    def test_rejects_v6_address_outside_the_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('2001:db8:1::/64', 'aa:bb:cc:dd:ee:ff', '2001:db8:2::50')
        self.assertIn('not in subnet', str(ctx.exception))

    def test_accepts_duid_identified_reservation(self):
        validate_reservation(
            '2001:db8:1::/64', '', '2001:db8:1::50',
            duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
        )

    def test_rejects_malformed_duid(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('2001:db8:1::/64', '', '2001:db8:1::50', duid='not-a-duid')
        self.assertIn('Invalid DUID', str(ctx.exception))

    def test_rejects_short_duid(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('2001:db8:1::/64', '', '2001:db8:1::50', duid='00:01')
        self.assertIn('Invalid DUID', str(ctx.exception))

    def test_rejects_both_identifiers(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation(
                '2001:db8:1::/64', 'aa:bb:cc:dd:ee:ff', '2001:db8:1::50',
                duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
            )
        self.assertIn('not both and not neither', str(ctx.exception))

    def test_rejects_neither_identifier(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('2001:db8:1::/64', '', '2001:db8:1::50')
        self.assertIn('not both and not neither', str(ctx.exception))

    def test_rejects_duid_on_v4_subnet_even_with_a_mac(self):
        """Family comes from the CIDR: a DUID never belongs to an IPv4 Pool."""
        with self.assertRaises(ValueError) as ctx:
            validate_reservation(
                '10.50.0.0/24', 'aa:bb:cc:dd:ee:ff', '10.50.0.50',
                duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
            )
        self.assertIn('DUID is an IPv6 client identifier', str(ctx.exception))


class ValidatePreferredLifetimeTests(unittest.TestCase):
    """validate_preferred_lifetime rejects non-positive or lifetimes exceeding valid lifetime."""

    def test_accepts_positive_int_within_valid_lifetime(self):
        validate_preferred_lifetime(60, 120)
        validate_preferred_lifetime(120, 120)
        validate_preferred_lifetime(60)

    def test_rejects_zero_and_negative(self):
        with self.assertRaises(ValueError) as ctx:
            validate_preferred_lifetime(0, 120)
        self.assertIn('positive', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_preferred_lifetime(-30, 120)
        self.assertIn('positive', str(ctx.exception))

    def test_rejects_non_integer_types(self):
        with self.assertRaises(ValueError) as ctx:
            validate_preferred_lifetime('60', 120)
        self.assertIn('whole number of seconds', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_preferred_lifetime(60.5, 120)
        self.assertIn('whole number of seconds', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_preferred_lifetime(True, 120)
        self.assertIn('whole number of seconds', str(ctx.exception))

    def test_rejects_preferred_lifetime_greater_than_valid_lifetime(self):
        with self.assertRaises(ValueError) as ctx:
            validate_preferred_lifetime(150, 120)
        self.assertIn('cannot be greater than valid lifetime', str(ctx.exception))


class DHCPServiceFamilyPartitionTests(unittest.TestCase):
    """One Service partitions its Pools by family; two servers is the provisioner's business."""

    def _v4_pool(self):
        return Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')

    def test_v4_only_service(self):
        service = DHCPService(self._v4_pool())
        self.assertEqual(service.pools_of_family(4), {'10.50.0.0/24': self._v4_pool()})
        self.assertEqual(service.pools_of_family(6), {})
        self.assertTrue(service.serves_ipv4)
        self.assertFalse(service.serves_ipv6)
        self.assertEqual(service.badge_text, 'DHCP')
        self.assertEqual(service.families, [4])

    def test_v6_only_service(self):
        pool = Pool(**V6_POOL)
        service = DHCPService(pool)
        self.assertEqual(service.pools_of_family(6), {'2001:db8:1::/64': pool})
        self.assertEqual(service.pools_of_family(4), {})
        self.assertFalse(service.serves_ipv4)
        self.assertTrue(service.serves_ipv6)
        self.assertEqual(service.badge_text, 'DHCPv6')
        self.assertEqual(service.families, [6])

    def test_dual_stack_service_holds_both(self):
        pool4, pool6 = self._v4_pool(), Pool(**V6_POOL)
        service = DHCPService(pool4, pool6)
        self.assertEqual(list(service.pools_of_family(4)), ['10.50.0.0/24'])
        self.assertEqual(list(service.pools_of_family(6)), ['2001:db8:1::/64'])
        self.assertTrue(service.serves_ipv4)
        self.assertTrue(service.serves_ipv6)
        self.assertEqual(service.badge_text, 'DHCP · DHCPv6')
        self.assertEqual(service.families, [4, 6])

    def test_empty_service_badge_text_and_families(self) -> None:
        service = DHCPService()
        self.assertEqual(service.badge_text, "")
        self.assertEqual(service.families, [])


class KeaConfigBuilderV6Tests(unittest.TestCase):
    """KeaConfigBuilder renders a Dhcp6 document kea-dhcp6 can accept."""

    def setUp(self):
        patcher = patch(
            'mniplab.dhcp.config.resolve_lease_cmds_hook',
            return_value='/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so',
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    _DEFAULT_INTERFACES = object()

    def _build(
        self,
        pools: Optional[Iterable[Pool]] = None,
        subnet_interfaces: Any = _DEFAULT_INTERFACES,
    ) -> Dict[str, Any]:
        return KeaConfigBuilder.build(
            pools=pools if pools is not None else [Pool(**V6_POOL)],
            interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea6-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases6.csv',
            family=6,
            subnet_interfaces=(
                {'2001:db8:1::/64': 'r1-eth0'}
                if subnet_interfaces is self._DEFAULT_INTERFACES
                else subnet_interfaces
            ),
        )

    def test_renders_a_dhcp6_document(self):
        config = self._build()
        self.assertEqual(list(config), ['Dhcp6'])
        self.assertEqual(
            config['Dhcp6']['interfaces-config']['interfaces'], ['r1-eth0']
        )

    def test_subnet6_names_its_serving_interface(self):
        """Ticket 01's spike: Kea 3.2 cannot select a directly-connected
        subnet6 from a link-local client source without this parameter."""
        config = self._build()
        subnet = config['Dhcp6']['subnet6'][0]
        self.assertEqual(subnet['id'], 1)
        self.assertEqual(subnet['subnet'], '2001:db8:1::/64')
        self.assertEqual(subnet['interface'], 'r1-eth0')
        self.assertEqual(subnet['pools'], [{'pool': '2001:db8:1::100 - 2001:db8:1::200'}])

    def test_subnet_interfaces_must_be_supplied_for_family_6(self):
        with self.assertRaises(ConfigurationError) as ctx:
            self._build(subnet_interfaces=None)
        self.assertIn('subnet_interfaces', str(ctx.exception))

    def test_control_socket_lease_database_and_hook(self):
        config = self._build()
        self.assertEqual(
            config['Dhcp6']['control-socket']['socket-name'],
            '/var/run/kea/kea6-ctrl-socket',
        )
        self.assertEqual(
            config['Dhcp6']['lease-database']['name'], '/var/lib/kea/kea-leases6.csv'
        )
        libraries = [h['library'] for h in config['Dhcp6']['hooks-libraries']]
        self.assertIn(
            '/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so', libraries
        )

    def test_renders_v6_dns_server_as_dns_servers_option(self):
        pool = Pool(**V6_POOL, dns_server='2001:db8::53')
        config = self._build(pools=[pool])
        option_data = config['Dhcp6']['subnet6'][0]['option-data']
        self.assertIn({'name': 'dns-servers', 'data': '2001:db8::53'}, option_data)

    def test_renders_no_router_option_for_v6(self):
        """DHCPv6 has no router option: the default route comes from the
        Router Advertisement. Ticket 03 turns a declared router= into a
        validation error; the builder must never render one."""
        mock_pool = Mock(
            cidr='2001:db8:1::/64', start='2001:db8:1::100', end='2001:db8:1::200',
            lease_time=None, preferred_lifetime=None, router='2001:db8:1::1',
            dns_server='', reservations=(),
        )
        config = self._build(pools=[mock_pool])
        self.assertNotIn('option-data', config['Dhcp6']['subnet6'][0])

    def test_renders_preferred_lifetime(self):
        pool = Pool(**V6_POOL, lease_time=120, preferred_lifetime=60)
        config = self._build(pools=[pool])
        self.assertEqual(config['Dhcp6']['subnet6'][0]['preferred-lifetime'], 60)
        self.assertEqual(config['Dhcp6']['subnet6'][0]['valid-lifetime'], 120)

    def test_renders_lease_time_as_valid_lifetime(self):
        pool = Pool(**V6_POOL, lease_time=120)
        config = self._build(pools=[pool])
        self.assertEqual(config['Dhcp6']['subnet6'][0]['valid-lifetime'], 120)

    def test_renders_reservations_with_ip_addresses_list(self):
        pool = Pool(
            **V6_POOL,
            reservations=(Reservation(mac='aa:bb:cc:dd:ee:ff', ip='2001:db8:1::50'),),
        )
        config = self._build(pools=[pool])
        reservations = config['Dhcp6']['subnet6'][0]['reservations']
        self.assertEqual(
            reservations,
            [{'hw-address': 'aa:bb:cc:dd:ee:ff', 'ip-addresses': ['2001:db8:1::50']}],
        )

    def test_renders_duid_identified_reservation_without_hw_address(self):
        """The DUID is the baseline identity: rendered as Kea's `duid` key,
        with no hw-address key alongside it."""
        pool = Pool(
            **V6_POOL,
            reservations=(Reservation(
                duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
                ip='2001:db8:1::50', hostname='h1',
            ),),
        )
        config = self._build(pools=[pool])
        reservations = config['Dhcp6']['subnet6'][0]['reservations']
        self.assertEqual(
            reservations,
            [{
                'duid': '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
                'ip-addresses': ['2001:db8:1::50'],
                'hostname': 'h1',
            }],
        )

    def test_mixed_identities_render_each_reservation_by_its_own_identifier(self):
        pool = Pool(
            **V6_POOL,
            reservations=(
                Reservation(duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
                            ip='2001:db8:1::50'),
                Reservation(mac='aa:bb:cc:dd:ee:ff', ip='2001:db8:1::51'),
            ),
        )
        config = self._build(pools=[pool])
        reservations = config['Dhcp6']['subnet6'][0]['reservations']
        self.assertEqual(reservations[0]['duid'], '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9')
        self.assertNotIn('hw-address', reservations[0])
        self.assertEqual(reservations[1]['hw-address'], 'aa:bb:cc:dd:ee:ff')
        self.assertNotIn('duid', reservations[1])

    def test_is_valid_json(self):
        json.dumps(self._build())  # must not raise


class KeaConfigBuilderV6AgainstTheRealBinaryTests(unittest.TestCase):
    """kea-dhcp6 -t against a config carrying every Pool field.

    Deliberately does *not* patch ``resolve_lease_cmds_hook`` — Kea must
    accept a config that names the real, installed hook path on this
    machine, mirroring the kea-dhcp4 variant in test_dhcp.py.
    """

    def test_kea_dhcp6_accepts_a_full_v6_config(self):
        pool = Pool(
            **V6_POOL, lease_time=90, preferred_lifetime=45, dns_server='2001:db8::53',
            reservations=(
                Reservation(mac='aa:bb:cc:dd:ee:01', ip='2001:db8:1::50'),
                # Ticket 08: the hardware-address branch is built and validated
                # here even though only a relayed client is guaranteed to match
                # it; the DUID branch below is the baseline a directly
                # attached client matches.
                Reservation(
                    duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
                    ip='2001:db8:1::51', hostname='h1',
                ),
            ),
        )
        config = KeaConfigBuilder.build(
            pools=[pool], interface_names=[],
            control_socket_path='/var/run/kea/kea6-ctrl-socket',
            lease_database_path='/tmp/test-kea-leases6.csv',
            family=6, subnet_interfaces={'2001:db8:1::/64': 'lo'},
        )
        with tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False) as f:
            json.dump(config, f)
            path = f.name
        try:
            result = subprocess.run(
                ['kea-dhcp6', '-t', path], capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            os.unlink(path)


class ResolveInterfaceV6Tests(unittest.TestCase):
    """CIDR resolution reads the IPv6 address recorded on the Interface.

    These tests use bare Mocks — an Interface double has no kernel behind
    it at all, which is exactly the point: resolution must
    succeed from the recorded address alone.
    """

    def test_resolves_a_v6_pool_against_the_recorded_ip6(self):
        node = FakeNode.with_addresses('r1', (None, '2001:db8:1::1'))
        pool = Pool(**V6_POOL)
        net = FakeLab(node)

        self.assertEqual(DHCPProvisioner.resolve_interface(node, pool, net), 'r1-eth0')

    def test_prefers_the_record_over_any_kernel_state(self):
        """FRR has wiped the kernel's IPv6 address at DHCP start
        time; the recorded address is the only thing that can match."""
        node = FakeNode.with_addresses('r1', (None, '2001:db8:1::1'))
        # Even if a kernel read reported nothing, the record must win:
        # _interface_on_network never shells out, so there is nothing to fake.
        pool = Pool(**V6_POOL)
        net = FakeLab(node)
        self.assertEqual(DHCPProvisioner.resolve_interface(node, pool, net), 'r1-eth0')
        self.assertEqual(node.recorder.commands, [])

    def test_skips_interfaces_whose_recorded_ip6_is_elsewhere(self):
        node = FakeNode.with_addresses(
            'r1', ('10.50.0.1', None), (None, '2001:db8:1::1')
        )
        pool = Pool(**V6_POOL)
        net = FakeLab(node)

        self.assertEqual(DHCPProvisioner.resolve_interface(node, pool, net), 'r1-eth1')

    def test_v6_pool_behind_another_router_names_relay(self):
        serving_node = FakeNode.with_addresses('r1', ('192.168.1.1', None))
        other_router = FakeNode.with_addresses('r2', (None, '2001:db8:1::1'))
        pool = Pool(**V6_POOL)
        net = FakeLab(serving_node, other_router)

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interface(serving_node, pool, net)

        message = str(ctx.exception)
        self.assertIn('relay', message)
        self.assertIn('r1', message)
        self.assertIn('r2', message)
        self.assertIn('2001:db8:1::/64', message)

    def test_v6_pool_matching_nothing_is_a_typo_error(self):
        node = FakeNode.with_addresses('r1', ('192.168.1.1', None))
        pool = Pool(**V6_POOL)
        net = FakeLab(node)

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interface(node, pool, net)

        self.assertNotIn('relay', str(ctx.exception))

    def test_dual_stack_pools_resolve_through_one_code_path(self):
        node = FakeNode.with_addresses(
            'r1', ('10.50.0.1', None), (None, '2001:db8:1::1')
        )
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
            Pool(**V6_POOL),
        )
        net = FakeLab(node)

        self.assertEqual(
            DHCPProvisioner.resolve_interfaces(node, service, net),
            {'10.50.0.0/24': 'r1-eth0', '2001:db8:1::/64': 'r1-eth1'},
        )


class ControlSocketHostPathV6Tests(unittest.TestCase):
    """IPv4 and IPv6 servers use separate control sockets."""

    def setUp(self):
        self.net = FakeLab()

    def test_family_6_names_the_kea6_socket(self):
        self.assertTrue(
            DHCPProvisioner.control_socket_host_path('r1', family=6, net=self.net).endswith(
                'kea6-ctrl-socket'
            )
        )

    def test_default_remains_the_kea4_socket(self):
        self.assertTrue(
            DHCPProvisioner.control_socket_host_path('r1', net=self.net).endswith(
                'kea4-ctrl-socket'
            )
        )

    def test_family_sockets_differ(self):
        self.assertNotEqual(
            DHCPProvisioner.control_socket_host_path('r1', family=4, net=self.net),
            DHCPProvisioner.control_socket_host_path('r1', family=6, net=self.net),
        )


class ProvisionerStartStopFamilyTests(ServicesReadyMixin, unittest.TestCase):
    """The provisioner starts one Kea server per family the Service holds."""

    @staticmethod
    def _node(
        name: str = 'r1',
        recorded: Tuple[Tuple[Optional[str], Optional[str]], ...] = (('10.50.0.1', None),),
    ) -> Mock:
        node = Mock()
        node.name = name
        node.services = []
        intfs = []
        for i, (ip, ip6) in enumerate(recorded):
            intf = Mock()
            intf.name = f'{name}-eth{i}'
            intf.ip = ip
            intf.ip6 = ip6
            intfs.append(intf)
        node.intfList.return_value = intfs
        return node

    @staticmethod
    def _net(*hosts: Any) -> FakeLab:
        return FakeLab(*hosts)

    def _started_commands(self, node: Any, service: DHCPService) -> List[str]:
        DHCPProvisioner.start_dhcp_service(node, service, self._net(node))
        return [call.args[0] for call in node.cmd.call_args_list]

    def test_v6_only_service_starts_only_kea_dhcp6(self):
        node = self._node(recorded=((None, '2001:db8:1::1'),))
        service = DHCPService(Pool(**V6_POOL))
        node.services = [service]

        commands = self._started_commands(node, service)

        starts = [c for c in commands if 'nohup kea-dhcp' in c]
        self.assertEqual(len(starts), 1)
        self.assertIn('kea-dhcp6 -c /etc/kea/kea-dhcp6.conf', starts[0])
        self.assertNotIn('kea-dhcp4', ' '.join(starts))

    def test_v4_only_service_starts_only_kea_dhcp4(self):
        node = self._node()
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        )
        node.services = [service]

        commands = self._started_commands(node, service)

        starts = [c for c in commands if 'nohup kea-dhcp' in c]
        self.assertEqual(len(starts), 1)
        self.assertIn('kea-dhcp4 -c /etc/kea/kea-dhcp4.conf', starts[0])

    def test_dual_stack_service_starts_both_with_separate_files(self):
        node = self._node(recorded=(('10.50.0.1', None), (None, '2001:db8:1::1')))
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
            Pool(**V6_POOL),
        )
        node.services = [service]

        commands = self._started_commands(node, service)
        starts = [c for c in commands if 'nohup kea-dhcp' in c]

        self.assertEqual(len(starts), 2)
        joined = ' '.join(starts)
        self.assertIn('kea-dhcp4 -c /etc/kea/kea-dhcp4.conf', joined)
        self.assertIn('kea-dhcp6 -c /etc/kea/kea-dhcp6.conf', joined)
        self.assertIn('kea-dhcp4.log', joined)
        self.assertIn('kea-dhcp6.log', joined)

    def test_start_writes_one_config_file_per_family(self):
        node = self._node(recorded=(('10.50.0.1', None), (None, '2001:db8:1::1')))
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
            Pool(**V6_POOL),
        )
        node.services = [service]

        net = self._net(node)
        DHCPProvisioner.start_dhcp_service(node, service, net)
        etc = net.dhcp_dir('r1', 'etc')
        with open(os.path.join(etc, 'kea-dhcp4.conf')) as f:
            self.assertIn('"Dhcp4"', f.read())
        with open(os.path.join(etc, 'kea-dhcp6.conf')) as f:
            rendered = f.read()
        self.assertIn('"Dhcp6"', rendered)
        self.assertIn('"interface": "r1-eth1"', rendered)
        self.assertNotIn('10.50.0.0/24', rendered)

    def test_stop_stops_both_servers_and_restores_declared_pools(self):
        declared_v6 = Pool(**V6_POOL)
        service = DHCPService(declared_v6)
        service.pools['2001:db8:1::/64'] = Pool(
            cidr='2001:db8:1::/64', start='2001:db8:1::150', end='2001:db8:1::160'
        )
        node = self._node()
        node.services = [service]

        DHCPProvisioner.stop_dhcp_service(node)

        commands = [call.args[0] for call in node.cmd.call_args_list]
        self.assertTrue(any("pkill -f 'kea-dhcp4 -c'" in c for c in commands))
        self.assertTrue(any("pkill -f 'kea-dhcp6 -c'" in c for c in commands))
        self.assertEqual(service.pools['2001:db8:1::/64'], declared_v6)


class RefuseDhcp6StaticAddressTests(unittest.TestCase):
    """The dhcp6 marker refuses a static IPv6 address and leaves IPv4 alone."""

    def test_dhcp6_true_refuses_ip6(self):
        params = {'dhcp6': True, 'ip6': '2001:db8::5/64'}
        with self.assertRaises(LinkError) as ctx:
            mnIPLab._refuse_static_address_beside_dhcp(FakeNode('h1'), params, 'r1–h1')
        self.assertIn("h1's ip6='2001:db8::5/64' is beside dhcp6", str(ctx.exception))

    def test_dhcp6_true_accepts_ipv4_ip(self):
        params = {'dhcp6': True, 'ip': '10.0.0.5/24'}
        mnIPLab._refuse_static_address_beside_dhcp(FakeNode('h1'), params, 'r1–h1')
        self.assertEqual(params['ip'], '10.0.0.5/24')

    def test_dhcp4_and_dhcp6_on_one_interface_each_refuse_their_own_family(self):
        """A dual-stack client Interface takes neither address."""
        for key, address in (('ip', '10.0.0.5/24'), ('ip6', '2001:db8::5/64')):
            with self.subTest(key):
                params = {'dhcp4': True, 'dhcp6': True, key: address}
                with self.assertRaises(LinkError) as ctx:
                    mnIPLab._refuse_static_address_beside_dhcp(FakeNode('h1'), params, 'r1–h1')
                self.assertIn(f"{key}='{address}'", str(ctx.exception))


class PoolServiceV6Tests(KeaClientStubMixin, unittest.TestCase):
    """PoolService validates and updates IPv6 Pools against kea-dhcp6."""

    @staticmethod
    def _net_with_v6_service():
        service = DHCPService(Pool(**V6_POOL))
        service.interface_names = {'2001:db8:1::/64': 'r1-eth0'}
        return FakeLab(FakeNode('r1', services=[service])), service

    def test_set_pool_rejects_router_option_on_v6_pool_before_calling_kea(self):
        net, service = self._net_with_v6_service()
        expected_msg = (
            "A DHCPv6 Pool has no router option: the default gateway comes "
            "from the Router Advertisement, not from DHCPv6."
        )

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '2001:db8:1::/64',
                '2001:db8:1::100', '2001:db8:1::200',
                router='2001:db8:1::1',
            )
        self.client6.test_config.assert_not_called()

        self.assertEqual(str(ctx.exception), expected_msg)
        self.assertEqual(service.pools['2001:db8:1::/64'], Pool(**V6_POOL))

    def test_set_pool_rejects_start_outside_subnet_before_calling_kea(self):
        net, service = self._net_with_v6_service()

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '2001:db8:1::/64',
                '2001:db8:2::100', '2001:db8:1::200',
            )
        self.client6.test_config.assert_not_called()

        self.assertIn('not in subnet 2001:db8:1::/64', str(ctx.exception))
        self.assertEqual(service.pools['2001:db8:1::/64'], Pool(**V6_POOL))

    def test_set_pool_rejects_start_after_end_before_calling_kea(self):
        net, service = self._net_with_v6_service()

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '2001:db8:1::/64',
                '2001:db8:1::200', '2001:db8:1::100',
            )
        self.client6.test_config.assert_not_called()

        self.assertIn('cannot be after', str(ctx.exception))
        self.assertEqual(service.pools['2001:db8:1::/64'], Pool(**V6_POOL))

    def test_set_pool_rejects_wrong_family_dns_server_before_calling_kea(self):
        net, service = self._net_with_v6_service()

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '2001:db8:1::/64',
                '2001:db8:1::100', '2001:db8:1::200',
                dns_server='8.8.8.8',
            )
        self.client6.test_config.assert_not_called()

        self.assertIn('IPv6', str(ctx.exception))
        self.assertEqual(service.pools['2001:db8:1::/64'], Pool(**V6_POOL))

    def test_set_pool_rejects_invalid_preferred_lifetime_before_calling_kea(self):
        net, service = self._net_with_v6_service()

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '2001:db8:1::/64',
                '2001:db8:1::100', '2001:db8:1::200',
                lease_time=90, preferred_lifetime=150,
            )
        self.client6.test_config.assert_not_called()

        self.assertIn('cannot be greater than valid lifetime', str(ctx.exception))
        self.assertEqual(service.pools['2001:db8:1::/64'], Pool(**V6_POOL))

    def test_set_pool_applies_valid_v6_pool_to_kea6(self):
        net, service = self._net_with_v6_service()

        result = PoolService.set_pool(
            net, 'r1', '2001:db8:1::/64',
            '2001:db8:1::150', '2001:db8:1::220',
            lease_time=90, preferred_lifetime=45,
            dns_server='2001:db8::53',
        )

        self.client6.test_config.assert_called_once()
        self.client6.set_config.assert_called_once()
        self.client.test_config.assert_not_called()

        updated = service.pools['2001:db8:1::/64']
        self.assertEqual(updated.start, '2001:db8:1::150')
        self.assertEqual(updated.end, '2001:db8:1::220')
        self.assertEqual(updated.lease_time, 90)
        self.assertEqual(updated.preferred_lifetime, 45)
        self.assertEqual(updated.dns_server, '2001:db8::53')
        self.assertEqual(result['success'], True)
        self.assertEqual(result['pool']['preferred_lifetime'], 45)

    def test_set_pool_applies_duid_reservation_to_kea6(self):
        net, service = self._net_with_v6_service()
        duid = '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9'

        result = PoolService.set_pool(
            net, 'r1', '2001:db8:1::/64',
            '2001:db8:1::100', '2001:db8:1::200',
            reservations=[{'mac': '', 'duid': duid, 'ip': '2001:db8:1::50',
                           'hostname': 'h1'}],
        )

        sent_config = self.client6.test_config.call_args.args[0]
        rendered = sent_config['Dhcp6']['subnet6'][0]['reservations']
        self.assertEqual(rendered, [{
            'duid': duid,
            'ip-addresses': ['2001:db8:1::50'],
            'hostname': 'h1',
        }])

        updated = service.pools['2001:db8:1::/64']
        self.assertEqual(
            updated.reservations,
            (Reservation(duid=duid, ip='2001:db8:1::50', hostname='h1'),),
        )
        self.assertEqual(
            result['pool']['reservations'],
            [{'mac': '', 'duid': duid, 'ip': '2001:db8:1::50', 'hostname': 'h1'}],
        )

    def test_set_pool_rejects_ambiguous_reservation_before_calling_kea(self):
        net, service = self._net_with_v6_service()

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '2001:db8:1::/64',
                '2001:db8:1::100', '2001:db8:1::200',
                reservations=[{
                    'mac': 'aa:bb:cc:dd:ee:ff',
                    'duid': '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
                    'ip': '2001:db8:1::50',
                }],
            )
        self.client6.test_config.assert_not_called()

        self.assertIn('not both and not neither', str(ctx.exception))
        self.assertEqual(service.pools['2001:db8:1::/64'], Pool(**V6_POOL))

    def test_set_pool_on_a_v4_pool_ignores_sibling_v6_pools(self):
        """The Dhcp4 document must contain only v4 Pools even when the
        Service also holds v6 Pools."""
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
            Pool(**V6_POOL),
        )
        service.interface_names = {
            '10.50.0.0/24': 'r1-eth0', '2001:db8:1::/64': 'r1-eth1',
        }
        net = FakeLab(FakeNode('r1', services=[service]))

        PoolService.set_pool(net, 'r1', '10.50.0.0/24', '10.50.0.150', '10.50.0.220')

        sent_config = self.client.test_config.call_args.args[0]
        self.client6.test_config.assert_not_called()
        self.assertEqual(list(sent_config), ['Dhcp4'])
        self.assertNotIn('2001:db8:1::/64', json.dumps(sent_config))


class RouterAdvertisementAdapterTests(unittest.TestCase):
    """RouterAdvertisementAdapter queries and parses Router Advertisement state."""

    def _make_adapter(self, config_output: str) -> Tuple[RouterAdvertisementAdapter, FakeNode]:
        node = FakeNode()
        node.recorder.respond_to('show running-config', config_output)
        return RouterAdvertisementAdapter(node), node

    def test_adapter_shells_out_to_vtysh(self):
        adapter, node = self._make_adapter("interface r1-eth0\nexit\n")
        adapter.query_interface('r1-eth0')
        self.assertTrue(node.recorder.issued_any('vtysh -c "show running-config"'))

    def test_adapter_parses_m_set(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd managed-config-flag\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        adapter, _ = self._make_adapter(config)
        report = adapter.query_interface('r1-eth0', prefix='2001:db8:1::/64')
        self.assertEqual(report.interface_name, 'r1-eth0')
        self.assertTrue(report.ra_sent)
        self.assertTrue(report.managed_flag)
        self.assertFalse(report.other_config_flag)
        self.assertFalse(report.advertises_autoconfig)

    def test_adapter_parses_m_clear(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        adapter, _ = self._make_adapter(config)
        report = adapter.query_interface('r1-eth0', prefix='2001:db8:1::/64')
        self.assertTrue(report.ra_sent)
        self.assertFalse(report.managed_flag)
        self.assertFalse(report.other_config_flag)
        self.assertFalse(report.advertises_autoconfig)

    def test_adapter_parses_o_without_m(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd other-config-flag\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        adapter, _ = self._make_adapter(config)
        report = adapter.query_interface('r1-eth0', prefix='2001:db8:1::/64')
        self.assertTrue(report.ra_sent)
        self.assertFalse(report.managed_flag)
        self.assertTrue(report.other_config_flag)
        self.assertFalse(report.advertises_autoconfig)

    def test_adapter_parses_autoconfiguration_on(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd managed-config-flag\n"
            "exit\n"
        )
        adapter, _ = self._make_adapter(config)
        report = adapter.query_interface('r1-eth0', prefix='2001:db8:1::/64')
        self.assertTrue(report.ra_sent)
        self.assertTrue(report.managed_flag)
        self.assertTrue(report.advertises_autoconfig)

    def test_adapter_parses_autoconfiguration_off(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd managed-config-flag\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        adapter, _ = self._make_adapter(config)
        report = adapter.query_interface('r1-eth0', prefix='2001:db8:1::/64')
        self.assertTrue(report.ra_sent)
        self.assertFalse(report.advertises_autoconfig)

    def test_adapter_parses_ra_suppressed_entirely(self):
        config = (
            "interface r1-eth0\n"
            " ipv6 nd managed-config-flag\n"
            "exit\n"
        )
        adapter, _ = self._make_adapter(config)
        report = adapter.query_interface('r1-eth0', prefix='2001:db8:1::/64')
        self.assertFalse(report.ra_sent)
        self.assertTrue(report.managed_flag)
        self.assertFalse(report.advertises_autoconfig)

    def test_adapter_parses_interface_not_in_running_config(self):
        adapter, _ = self._make_adapter("")
        report = adapter.query_interface('r1-eth0', prefix='2001:db8:1::/64')
        self.assertEqual(report.interface_name, 'r1-eth0')
        self.assertFalse(report.ra_sent)
        self.assertFalse(report.managed_flag)
        self.assertFalse(report.other_config_flag)
        self.assertFalse(report.advertises_autoconfig)


class RouterAdvertisementDiagnosticsTests(ServicesReadyMixin, unittest.TestCase):
    """RouterAdvertisementDiagnostics checks alignment between Pools and RAs."""

    @staticmethod
    def _make_router_mock(
        name: str = 'r1',
        ip6: Optional[str] = '2001:db8:1::1',
        config: str = '',
    ) -> Mock:
        router = Mock()
        router.name = name
        router.frr_daemons = ['zebra']
        intf = Mock()
        intf.name = f'{name}-eth0'
        intf.ip = None
        intf.ip6 = ip6
        router.intfList.return_value = [intf]
        router.cmd.return_value = config
        return router

    @staticmethod
    def _net_mock(*nodes: Any) -> Mock:
        net = Mock()
        net.hosts = list(nodes)
        return net

    def test_diagnose_warns_when_no_attached_router_sets_m(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        r1 = self._make_router_mock('r1', '2001:db8:1::1/64', config)
        service = DHCPService(Pool(**V6_POOL))
        net = self._net_mock(r1)

        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)

        self.assertEqual(len(warnings), 1)
        warning = warnings[0]
        self.assertIn('2001:db8:1::/64', warning)
        self.assertIn('r1', warning)
        self.assertIn('ipv6 nd managed-config-flag', warning)

    def test_diagnose_warns_when_autoconfiguration_is_left_on(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd managed-config-flag\n"
            "exit\n"
        )
        r1 = self._make_router_mock('r1', '2001:db8:1::1/64', config)
        service = DHCPService(Pool(**V6_POOL))
        net = self._net_mock(r1)

        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)

        self.assertEqual(len(warnings), 1)
        warning = warnings[0]
        self.assertIn('2001:db8:1::/64', warning)
        self.assertIn('ipv6 nd prefix 2001:db8:1::/64 no-autoconfig', warning)
        self.assertIn('Hosts will hold an address before the client runs', warning)

    def test_diagnose_produces_no_warnings_when_m_set_and_autoconfig_off(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd managed-config-flag\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        r1 = self._make_router_mock('r1', '2001:db8:1::1/64', config)
        service = DHCPService(Pool(**V6_POOL))
        net = self._net_mock(r1)

        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)
        self.assertEqual(warnings, [])

    def test_diagnose_warns_both_when_m_clear_and_autoconfig_on(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd other-config-flag\n"
            "exit\n"
        )
        r1 = self._make_router_mock('r1', '2001:db8:1::1/64', config)
        service = DHCPService(Pool(**V6_POOL))
        net = self._net_mock(r1)

        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)

        self.assertEqual(len(warnings), 2)
        m_warnings = [w for w in warnings if 'ipv6 nd managed-config-flag' in w]
        a_warnings = [w for w in warnings if 'Hosts will hold an address before the client runs' in w]
        self.assertEqual(len(m_warnings), 1)
        self.assertEqual(len(a_warnings), 1)
        self.assertIn('ipv6 nd prefix 2001:db8:1::/64 no-autoconfig', a_warnings[0])

    def test_diagnose_warns_m_when_ra_suppressed_entirely(self):
        config = (
            "interface r1-eth0\n"
            " ipv6 nd managed-config-flag\n"
            "exit\n"
        )
        r1 = self._make_router_mock('r1', '2001:db8:1::1/64', config)
        service = DHCPService(Pool(**V6_POOL))
        net = self._net_mock(r1)

        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)

        self.assertEqual(len(warnings), 1)
        self.assertIn('ipv6 nd managed-config-flag', warnings[0])
        self.assertIn('no ipv6 nd suppress-ra', warnings[0])
        self.assertNotIn('Hosts will hold an address before the client runs', warnings[0])

    def test_diagnose_handles_multihomed_router_on_same_subnet(self):
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
            "interface r1-eth1\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd managed-config-flag\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        r1 = Mock()
        r1.name = 'r1'
        r1.frr_daemons = ['zebra']
        intf0 = Mock()
        intf0.name = 'r1-eth0'
        intf0.ip = None
        intf0.ip6 = '2001:db8:1::1/64'
        intf1 = Mock()
        intf1.name = 'r1-eth1'
        intf1.ip = None
        intf1.ip6 = '2001:db8:1::2/64'
        r1.intfList.return_value = [intf0, intf1]
        r1.cmd.return_value = config

        service = DHCPService(Pool(**V6_POOL))
        net = self._net_mock(r1)

        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)
        self.assertEqual(warnings, [])

    def test_diagnose_finds_router_for_host_offered_service(self):
        """Ticket 04: The scan finds a Router that is not the Service's own
        Node, so a Host-offered Service is diagnosed correctly."""
        h1 = Mock()
        h1.name = 'h1'
        h1_intf = Mock()
        h1_intf.name = 'h1-eth0'
        h1_intf.ip = None
        h1_intf.ip6 = '2001:db8:1::2'
        h1.intfList.return_value = [h1_intf]

        # r1 is an attached router that lacks M flag
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
            "exit\n"
        )
        r1 = self._make_router_mock('r1', '2001:db8:1::1', config)
        net = self._net_mock(h1, r1)

        service = DHCPService(Pool(**V6_POOL))
        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)

        self.assertEqual(len(warnings), 1)
        self.assertIn('r1', warnings[0])
        self.assertIn('ipv6 nd managed-config-flag', warnings[0])

    def test_diagnose_ignores_ipv4_pools(self):
        v4_pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(v4_pool)
        r1 = self._make_router_mock('r1', '2001:db8:1::1/64', '')
        net = self._net_mock(r1)

        warnings = RouterAdvertisementDiagnostics.diagnose_service(service, net)
        self.assertEqual(warnings, [])

    def test_start_dhcp_service_emits_warnings_and_does_not_raise(self):
        """Ticket 04: Warnings do not prevent the service or Lab from starting."""
        config = (
            "interface r1-eth0\n"
            " no ipv6 nd suppress-ra\n"
            " ipv6 nd other-config-flag\n"
            "exit\n"
        )
        r1 = self._make_router_mock('r1', '2001:db8:1::1', config)
        service = DHCPService(Pool(**V6_POOL))
        r1.services = [service]
        net = FakeLab(r1)

        with patch('mniplab.provisioners.warn') as mock_warn:
            DHCPProvisioner.start_dhcp_service(r1, service, net)

        self.assertTrue(mock_warn.called)
        warned_text = ' '.join(call.args[0] for call in mock_warn.call_args_list)
        self.assertIn('ipv6 nd managed-config-flag', warned_text)
        self.assertIn('Hosts will hold an address before the client runs', warned_text)


class LeaseQueryServiceV6Tests(KeaClientStubMixin, unittest.TestCase):
    """LeaseQueryService with IPv6 and dual-stack Pools."""

    def test_returns_v6_leases_and_omits_v4_when_only_v6_pool_served(self):
        pool = Pool(**V6_POOL)
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))
        leases_v6 = [{
            "ip-address": "2001:db8:1::101",
            "duid": "00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9",
            "preferred-lft": 4500,
            "valid-lft": 7200,
        }]

        self.client6.get_all_leases.return_value = leases_v6

        result = LeaseQueryService.get_leases(net, 'r1')

        self.assertEqual(result, {'success': True, 'v6': leases_v6})
        self.assertNotIn('v4', result)
        self.client.get_all_leases.assert_not_called()

    def test_returns_both_families_when_dual_stack_served(self):
        pool_v4 = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        pool_v6 = Pool(**V6_POOL)
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool_v4, pool_v6)]))
        leases_v4 = [{'ip-address': '10.50.0.100', 'hw-address': 'aa:bb:cc:dd:ee:ff'}]
        leases_v6 = [{
            'ip-address': '2001:db8:1::101',
            'duid': '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
            'preferred-lft': 4500,
            'valid-lft': 7200,
        }]

        self.client.get_all_leases.return_value = leases_v4
        self.client6.get_all_leases.return_value = leases_v6

        result = LeaseQueryService.get_leases(net, 'r1')

        self.assertEqual(result, {'success': True, 'v4': leases_v4, 'v6': leases_v6})

    def test_empty_v6_leases_is_not_an_error(self):
        pool = Pool(**V6_POOL)
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))

        self.client6.get_all_leases.return_value = []

        result = LeaseQueryService.get_leases(net, 'r1')

        self.assertEqual(result, {'success': True, 'v6': []})
        self.assertNotIn('v4', result)

    def test_kea_dhcp6_not_running_fails_cleanly_with_an_explanation(self):
        pool = Pool(**V6_POOL)
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))

        self.client6.get_all_leases.side_effect = ConfigurationError(
            "Could not reach Kea's control socket at /tmp/x: [Errno 2] No such file. "
            "Is kea-dhcp6 running for this Service?"
        )

        with self.assertRaises(ValueError) as ctx:
            LeaseQueryService.get_leases(net, 'r1')

        self.assertIn('kea-dhcp6', str(ctx.exception))


class ShowLeasesCommandV6Tests(unittest.TestCase):
    """mnIPLabCLI.do_show_leases formatting for IPv6 and dual stack."""

    def test_prints_v6_leases_with_duid_and_both_lifetimes(self):
        leases_v6 = [{
            'ip-address': '2001:db8:1::101',
            'duid': '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
            'preferred-lft': 4500,
            'valid-lft': 7200,
        }]
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            return_value={'success': True, 'v6': leases_v6},
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('2001:db8:1::101', printed)
        self.assertIn('duid=00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9', printed)
        self.assertIn('preferred-lft=4500s', printed)
        self.assertIn('valid-lft=7200s', printed)
        self.assertNotIn('IPv4', printed)

    def test_prints_v6_leases_with_hw_address_when_present(self):
        leases_v6 = [{
            'ip-address': '2001:db8:1::101',
            'duid': '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
            'hw-address': '00:00:00:00:00:02',
            'preferred-lft': 4500,
            'valid-lft': 7200,
        }]
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            return_value={'success': True, 'v6': leases_v6},
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('2001:db8:1::101', printed)
        self.assertIn('duid=00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9', printed)
        self.assertIn('hw=00:00:00:00:00:02', printed)
        self.assertIn('preferred-lft=4500s', printed)
        self.assertIn('valid-lft=7200s', printed)

    def test_prints_both_families_split_when_dual_stack(self):
        leases_v4 = [{'ip-address': '10.50.0.100', 'hw-address': 'aa:bb:cc:dd:ee:ff', 'valid-lft': 3600}]
        leases_v6 = [{
            'ip-address': '2001:db8:1::101',
            'duid': '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
            'preferred-lft': 4500,
            'valid-lft': 7200,
        }]
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            return_value={'success': True, 'v4': leases_v4, 'v6': leases_v6},
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('*** IPv4 Leases held by r1:', printed)
        self.assertIn('10.50.0.100', printed)
        self.assertIn('*** IPv6 Leases held by r1:', printed)
        self.assertIn('2001:db8:1::101', printed)

    def test_family_filter_restricts_output(self):
        leases_v4 = [{'ip-address': '10.50.0.100', 'hw-address': 'aa:bb:cc:dd:ee:ff', 'valid-lft': 3600}]
        leases_v6 = [{
            'ip-address': '2001:db8:1::101',
            'duid': '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
            'preferred-lft': 4500,
            'valid-lft': 7200,
        }]
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            return_value={'success': True, 'v4': leases_v4, 'v6': leases_v6},
        ):
            printed = cli_output('show_leases', 'r1 6')

        self.assertIn('2001:db8:1::101', printed)
        self.assertNotIn('10.50.0.100', printed)

    def test_reports_no_v6_leases_when_empty(self):
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            return_value={'success': True, 'v6': []},
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('No Leases', printed)
        self.assertIn('IPv6', printed)


if __name__ == '__main__':
    unittest.main()

