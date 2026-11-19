"""Tests for the DHCP Service: Pool, ServiceMixin, config, and lifecycle.

Unit coverage (unprivileged): Pool
declaration, CIDR-to-Interface resolution including the no-match case, Kea
configuration generation from a declared Pool, and the client-marker flag
suppressing a static address. The privileged end-to-end test at the bottom
starts a real Lab, runs the real client, and asserts a Lease — the only test
that proves the feature actually works.
"""

import ipaddress
import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import pytest

from mniplab.dhcp.config import KeaConfigBuilder, resolve_lease_cmds_hook
from mniplab.dhcp.leases import LeaseQueryService
from mniplab.dhcp.pool import (
    Pool,
    Reservation,
    validate_lease_time,
    validate_option_address,
    validate_pool_range,
    validate_reservation,
)
from mniplab.dhcp.pool_service import PoolService
from mniplab.dhcp.query import DHCPQueryService
from mniplab.dhcp.service import DHCPService
from mniplab.errors import ConfigurationError, LinkError
from mniplab.net import mnIPLab
from mniplab.provisioners import DHCPProvisioner
from mniplab.service import Service, ServiceMixin
from mniplab.tests.fakes import FakeLab, FakeNode, KeaClientStubMixin, cli_output


class PoolTests(unittest.TestCase):
    """Pool is a plain, frozen declaration of the addresses one Subnet serves."""

    def test_carries_cidr_start_and_end(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        self.assertEqual(pool.cidr, '10.50.0.0/24')
        self.assertEqual(pool.start, '10.50.0.100')
        self.assertEqual(pool.end, '10.50.0.200')

    def test_is_immutable(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        with self.assertRaises(Exception):
            pool.cidr = '10.60.0.0/24'

    def test_lease_time_router_dns_server_and_reservations_default_empty(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        self.assertIsNone(pool.lease_time)
        self.assertEqual(pool.router, '')
        self.assertEqual(pool.dns_server, '')
        self.assertEqual(pool.reservations, ())

    def test_carries_lease_time_options_and_reservations(self):
        reservation = Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50', hostname='h1')
        pool = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            lease_time=120, router='10.50.0.1', dns_server='8.8.8.8',
            reservations=(reservation,),
        )
        self.assertEqual(pool.lease_time, 120)
        self.assertEqual(pool.router, '10.50.0.1')
        self.assertEqual(pool.dns_server, '8.8.8.8')
        self.assertEqual(pool.reservations, (reservation,))

class ReservationTests(unittest.TestCase):
    """Reservation is a plain, frozen declaration of one fixed-address client."""

    def test_carries_mac_ip_and_hostname(self):
        reservation = Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50', hostname='h1')
        self.assertEqual(reservation.mac, 'aa:bb:cc:dd:ee:ff')
        self.assertEqual(reservation.duid, '')
        self.assertEqual(reservation.ip, '10.50.0.50')
        self.assertEqual(reservation.hostname, 'h1')

    def test_carries_duid_instead_of_mac(self):
        reservation = Reservation(
            duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9', ip='2001:db8:1::50',
        )
        self.assertEqual(reservation.mac, '')
        self.assertEqual(reservation.duid, '00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9')
        self.assertEqual(reservation.ip, '2001:db8:1::50')

    def test_rejects_both_identifiers(self):
        with self.assertRaises(ValueError) as ctx:
            Reservation(
                mac='aa:bb:cc:dd:ee:ff',
                duid='00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9',
                ip='2001:db8:1::50',
            )
        self.assertIn('not both and not neither', str(ctx.exception))

    def test_rejects_neither_identifier(self):
        with self.assertRaises(ValueError) as ctx:
            Reservation(ip='10.50.0.50')
        self.assertIn('not both and not neither', str(ctx.exception))

    def test_hostname_defaults_empty(self):
        reservation = Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50')
        self.assertEqual(reservation.hostname, '')

    def test_is_immutable(self):
        reservation = Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50')
        with self.assertRaises(Exception):
            reservation.ip = '10.50.0.99'


class ValidateLeaseTimeTests(unittest.TestCase):
    """validate_lease_time rejects anything but a positive whole number of seconds."""

    def test_accepts_positive_int(self):
        validate_lease_time(120)
        validate_lease_time(1)

    def test_rejects_zero_and_negative(self):
        with self.assertRaises(ValueError) as ctx:
            validate_lease_time(0)
        self.assertIn('positive', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_lease_time(-30)
        self.assertIn('positive', str(ctx.exception))

    def test_rejects_non_integer(self):
        with self.assertRaises(ValueError) as ctx:
            validate_lease_time('120')
        self.assertIn('whole number', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_lease_time(90.5)
        self.assertIn('whole number', str(ctx.exception))

    def test_rejects_bool(self):
        """bool is a subclass of int in Python; True/False must still be rejected."""
        with self.assertRaises(ValueError):
            validate_lease_time(True)


class ValidateOptionAddressTests(unittest.TestCase):
    """validate_option_address covers the router and DNS-server options."""

    def test_empty_value_is_always_valid(self):
        validate_option_address('router', '')

    def test_accepts_valid_ipv4(self):
        validate_option_address('router', '10.50.0.1')
        validate_option_address('DNS server', '8.8.8.8')

    def test_rejects_invalid_format(self):
        with self.assertRaises(ValueError) as ctx:
            validate_option_address('router', 'not-an-ip')
        self.assertIn('Invalid router address', str(ctx.exception))

    def test_rejects_ipv6(self):
        with self.assertRaises(ValueError) as ctx:
            validate_option_address('DNS server', '2001:db8::1')
        self.assertIn('IPv4', str(ctx.exception))


class ValidateReservationTests(unittest.TestCase):
    """validate_reservation checks the MAC format and containment within the Subnet."""

    def test_accepts_valid_mac_and_ip_in_subnet(self):
        validate_reservation('10.50.0.0/24', 'aa:bb:cc:dd:ee:ff', '10.50.0.50')

    def test_accepts_address_outside_the_pool_range_but_inside_the_subnet(self):
        """A Reservation is the exception to the Pool, not bound to its range."""
        validate_reservation('10.50.0.0/24', 'aa:bb:cc:dd:ee:ff', '10.50.0.5')

    def test_rejects_malformed_mac(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('10.50.0.0/24', 'not-a-mac', '10.50.0.50')
        self.assertIn('Invalid MAC address', str(ctx.exception))

    def test_rejects_ip_outside_the_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('10.50.0.0/24', 'aa:bb:cc:dd:ee:ff', '192.168.1.5')
        self.assertIn('not in subnet', str(ctx.exception))

    def test_rejects_invalid_ip_format(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('10.50.0.0/24', 'aa:bb:cc:dd:ee:ff', 'not-an-ip')
        self.assertIn('Invalid Reservation address', str(ctx.exception))

    def test_rejects_duid_on_an_ipv4_subnet(self):
        """DHCPv4's chaddr means an IPv4 Reservation names a hardware address."""
        with self.assertRaises(ValueError) as ctx:
            validate_reservation(
                '10.50.0.0/24', '', '10.50.0.50',
                duid='00:01:00:01:aa:bb:cc:dd',
            )
        self.assertIn('DUID is an IPv6 client identifier', str(ctx.exception))

    def test_rejects_missing_identifier(self):
        with self.assertRaises(ValueError) as ctx:
            validate_reservation('10.50.0.0/24', '', '10.50.0.50')
        self.assertIn('exactly one client identifier', str(ctx.exception))

    def test_rejects_both_identifiers(self):
        """On an IPv4 Subnet the family objection is the one worth making."""
        with self.assertRaises(ValueError) as ctx:
            validate_reservation(
                '10.50.0.0/24', 'aa:bb:cc:dd:ee:ff', '10.50.0.50',
                duid='00:01:00:01:aa:bb:cc:dd',
            )
        self.assertIn('DUID is an IPv6 client identifier', str(ctx.exception))


class ValidatePoolRangeTests(unittest.TestCase):
    """validate_pool_range checks format, containment, order and boundary addresses."""

    def test_accepts_valid_range_within_subnet(self):
        validate_pool_range('10.50.0.0/24', '10.50.0.100', '10.50.0.200')
        validate_pool_range('10.50.0.0/24', '10.50.0.100', '10.50.0.100')

    def test_rejects_invalid_ip_format(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', 'invalid-ip', '10.50.0.200')
        self.assertIn('Invalid start address', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', '10.50.0.100', 'not-an-ip')
        self.assertIn('Invalid end address', str(ctx.exception))

    def test_rejects_ipv6_addresses_in_v4_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', '2001:db8::1', '10.50.0.200')
        self.assertIn('IPv4', str(ctx.exception))

    def test_rejects_start_address_outside_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', '192.168.1.50', '10.50.0.200')
        self.assertIn('not in subnet', str(ctx.exception))
        self.assertIn('10.50.0.0/24', str(ctx.exception))

    def test_rejects_end_address_outside_subnet(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', '10.50.0.50', '10.51.0.100')
        self.assertIn('not in subnet', str(ctx.exception))
        self.assertIn('10.51.0.100', str(ctx.exception))

    def test_rejects_start_address_after_end_address(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', '10.50.0.200', '10.50.0.100')
        self.assertIn('cannot be after', str(ctx.exception))

    def test_rejects_network_or_broadcast_address(self):
        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', '10.50.0.0', '10.50.0.100')
        self.assertIn('network address', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_pool_range('10.50.0.0/24', '10.50.0.100', '10.50.0.255')
        self.assertIn('broadcast address', str(ctx.exception))


class ServiceMixinTests(unittest.TestCase):
    """The Service attachment point is a plain mixin, usable on any Node."""

    def test_add_service_returns_the_service(self):
        class DummyNode(ServiceMixin):
            pass

        node = DummyNode()
        service = Service()
        self.assertIs(node.addService(service), service)

    def test_add_service_sets_the_services_node(self):
        class DummyNode(ServiceMixin):
            name = 'n1'

        node = DummyNode()
        service = Service()
        node.addService(service)
        self.assertIs(service.node, node)

    def test_services_lists_every_attached_service_in_order(self):
        class DummyNode(ServiceMixin):
            pass

        node = DummyNode()
        first, second = Service(), Service()
        node.addService(first)
        node.addService(second)
        self.assertEqual(node.services, [first, second])

    def test_a_fresh_node_has_no_services(self):
        class DummyNode(ServiceMixin):
            pass

        self.assertEqual(DummyNode().services, [])

    def test_host_and_router_both_carry_the_attachment_point(self):
        """The attachment point must reach both Host and Router."""
        from mniplab.host import mnIPLabHost
        from mniplab.router import FRRouter

        self.assertTrue(issubclass(mnIPLabHost, ServiceMixin))
        self.assertTrue(issubclass(FRRouter, ServiceMixin))

    def test_add_service_works_on_an_unconstructed_host(self):
        """addService needs no Node __init__ state — it lazy-inits its own."""
        from mniplab.host import mnIPLabHost

        host = object.__new__(mnIPLabHost)
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)

        host.addService(service)

        self.assertEqual(host.services, [service])
        self.assertIs(service.node, host)

    def test_base_service_badge_text_and_families(self) -> None:
        s = Service()
        s.name = "dns"
        self.assertEqual(s.badge_text, "DNS")
        self.assertEqual(s.families, [])


class DHCPServiceTests(unittest.TestCase):
    """A DHCP Service holds one Pool per Subnet it serves, keyed by CIDR."""

    def test_accepts_a_single_pool(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        self.assertEqual(service.pools, {'10.50.0.0/24': pool})

    def test_accepts_several_pools_each_keyed_by_its_subnets_cidr(self):
        pool_a = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        pool_b = Pool(cidr='10.51.0.0/24', start='10.51.0.100', end='10.51.0.200')

        service = DHCPService(pool_a, pool_b)

        self.assertEqual(
            service.pools,
            {'10.50.0.0/24': pool_a, '10.51.0.0/24': pool_b},
        )

    def test_no_interface_is_resolved_before_lab_start(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        self.assertEqual(DHCPService(pool).interface_names, {})

    def test_reset_pools_restores_lease_time_options_and_reservations(self):
        """A Learner's edit must not survive a reset — including the lease
        time options and Reservations, not just start/end (the field set the
        reconstruction this replaced would have silently dropped)."""
        reservation = Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50', hostname='h1')
        declared = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            lease_time=7200, router='10.50.0.1', dns_server='8.8.8.8',
            reservations=(reservation,),
        )
        service = DHCPService(declared)

        service.pools['10.50.0.0/24'] = Pool(
            cidr='10.50.0.0/24', start='10.50.0.150', end='10.50.0.160', lease_time=60,
        )
        service.reset_pools()

        self.assertEqual(service.pools['10.50.0.0/24'], declared)

    def test_two_services_on_the_same_subnet_are_permitted(self):
        """Two Services on one Subnet are not validated against — the
        rogue-server scenario is a lesson, not an error."""
        service_one = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        )
        service_two = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.201', end='10.50.0.254')
        )
        node_one = FakeNode.with_addresses('r1', '10.50.0.1')
        node_two = FakeNode.with_addresses('r1', '10.50.0.2')
        net = FakeLab(node_one, node_two)

        self.assertEqual(
            DHCPProvisioner.resolve_interfaces(node_one, service_one, net),
            {'10.50.0.0/24': 'r1-eth0'},
        )
        self.assertEqual(
            DHCPProvisioner.resolve_interfaces(node_two, service_two, net),
            {'10.50.0.0/24': 'r1-eth0'},
        )


class KeaConfigBuilderTests(unittest.TestCase):
    """KeaConfigBuilder renders a Dhcp4 document Kea can accept.

    These assert behaviourally relevant
    content (the subnet, the served addresses, the interface, the required
    hook) rather than the overall document shape, which is a Kea
    implementation detail expected to change.
    """

    def setUp(self):
        self.pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        patcher = patch(
            'mniplab.dhcp.config.resolve_lease_cmds_hook',
            return_value='/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so',
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_subnet_and_pool_range(self):
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        subnet = config['Dhcp4']['subnet4'][0]
        self.assertEqual(subnet['id'], 1)
        self.assertEqual(subnet['subnet'], '10.50.0.0/24')
        self.assertEqual(subnet['pools'], [{'pool': '10.50.0.100 - 10.50.0.200'}])

    def test_listens_on_the_resolved_interface(self):
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertEqual(config['Dhcp4']['interfaces-config']['interfaces'], ['r1-eth0'])

    def test_control_socket_and_lease_database_paths(self):
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertEqual(
            config['Dhcp4']['control-socket']['socket-name'], '/var/run/kea/kea4-ctrl-socket'
        )
        self.assertEqual(
            config['Dhcp4']['lease-database']['name'], '/var/lib/kea/kea-leases4.csv'
        )

    def test_lease_cmds_hook_is_always_loaded(self):
        """Ticket 01's spike found lease4-* commands silently unsupported without this hook."""
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        libraries = [h['library'] for h in config['Dhcp4']['hooks-libraries']]
        self.assertIn(
            '/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so', libraries
        )

    def test_is_valid_json(self):
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        json.dumps(config)  # must not raise

    def test_renders_one_subnet_entry_per_pool(self):
        pool_b = Pool(cidr='10.51.0.0/24', start='10.51.0.100', end='10.51.0.200')
        config = KeaConfigBuilder.build(
            pools=[self.pool, pool_b], interface_names=['r1-eth0', 'r1-eth1'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        subnets = config['Dhcp4']['subnet4']
        self.assertEqual(
            [s['subnet'] for s in subnets], ['10.50.0.0/24', '10.51.0.0/24']
        )
        self.assertEqual([s['id'] for s in subnets], [1, 2])
        self.assertEqual(
            subnets[1]['pools'], [{'pool': '10.51.0.100 - 10.51.0.200'}]
        )

    def test_listens_only_on_the_interfaces_pools_resolved_to(self):
        """A third attached Interface with no declared Pool must not appear."""
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertEqual(
            config['Dhcp4']['interfaces-config']['interfaces'], ['r1-eth0']
        )

    def test_renders_empty_pools_list_when_start_or_end_empty(self):
        pool_empty = Pool(cidr='10.50.0.0/24', start='', end='')
        config = KeaConfigBuilder.build(
            pools=[pool_empty], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertEqual(config['Dhcp4']['subnet4'][0]['pools'], [])

    def test_renders_lease_time_as_valid_lifetime(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200', lease_time=120)
        config = KeaConfigBuilder.build(
            pools=[pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertEqual(config['Dhcp4']['subnet4'][0]['valid-lifetime'], 120)

    def test_omits_valid_lifetime_when_lease_time_not_declared(self):
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertNotIn('valid-lifetime', config['Dhcp4']['subnet4'][0])

    def test_renders_router_and_dns_server_as_option_data(self):
        pool = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            router='10.50.0.1', dns_server='8.8.8.8',
        )
        config = KeaConfigBuilder.build(
            pools=[pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        option_data = config['Dhcp4']['subnet4'][0]['option-data']
        self.assertIn({'name': 'routers', 'data': '10.50.0.1'}, option_data)
        self.assertIn({'name': 'domain-name-servers', 'data': '8.8.8.8'}, option_data)

    def test_omits_option_data_when_no_options_declared(self):
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertNotIn('option-data', config['Dhcp4']['subnet4'][0])

    def test_renders_reservations_by_hw_address(self):
        from mniplab.dhcp.pool import Reservation

        pool = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            reservations=(Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50', hostname='h1'),),
        )
        config = KeaConfigBuilder.build(
            pools=[pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        reservations = config['Dhcp4']['subnet4'][0]['reservations']
        self.assertEqual(reservations, [
            {'hw-address': 'aa:bb:cc:dd:ee:ff', 'ip-address': '10.50.0.50', 'hostname': 'h1'},
        ])

    def test_reservation_without_hostname_omits_the_key(self):
        from mniplab.dhcp.pool import Reservation

        pool = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            reservations=(Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50'),),
        )
        config = KeaConfigBuilder.build(
            pools=[pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        reservation = config['Dhcp4']['subnet4'][0]['reservations'][0]
        self.assertNotIn('hostname', reservation)

    def test_omits_reservations_when_none_declared(self):
        config = KeaConfigBuilder.build(
            pools=[self.pool], interface_names=['r1-eth0'],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/var/lib/kea/kea-leases4.csv',
        )
        self.assertNotIn('reservations', config['Dhcp4']['subnet4'][0])


class KeaConfigBuilderAgainstTheRealBinaryTests(unittest.TestCase):
    """kea-dhcp4 -t against a config carrying every new Pool field.

    Deliberately does *not* patch ``resolve_lease_cmds_hook`` — Kea must
    accept a config that names the real, installed hook path on this
    machine, not a stand-in for one architecture (see
    ``KeaConfigBuilderTests.setUp``, which mocks it for the tests that only
    inspect the built dict).
    """

    def test_kea_accepts_a_config_with_lease_time_options_and_reservations(self):
        import subprocess
        import tempfile
        from mniplab.dhcp.pool import Reservation

        pool = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            lease_time=90, router='10.50.0.1', dns_server='8.8.8.8',
            reservations=(Reservation(mac='aa:bb:cc:dd:ee:01', ip='10.50.0.5', hostname='h1'),),
        )
        config = KeaConfigBuilder.build(
            pools=[pool], interface_names=[],
            control_socket_path='/var/run/kea/kea4-ctrl-socket',
            lease_database_path='/tmp/test-kea-leases.csv',
        )
        with tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False) as f:
            json.dump(config, f)
            path = f.name
        try:
            result = subprocess.run(
                ['kea-dhcp4', '-t', path], capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            os.unlink(path)


class ResolveLeaseCmdsHookTests(unittest.TestCase):
    """The hook's path is architecture-dependent and resolved at runtime."""

    @patch('mniplab.dhcp.config.glob.glob', return_value=[])
    def test_missing_hook_raises_configuration_error(self, _mock_glob):
        with self.assertRaises(ConfigurationError) as ctx:
            resolve_lease_cmds_hook()
        self.assertIn('libdhcp_lease_cmds.so', str(ctx.exception))

    @patch(
        'mniplab.dhcp.config.glob.glob',
        return_value=['/usr/lib/aarch64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so'],
    )
    def test_found_hook_is_returned(self, _mock_glob):
        self.assertEqual(
            resolve_lease_cmds_hook(),
            '/usr/lib/aarch64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so',
        )


class ResolveInterfaceTests(unittest.TestCase):
    """CIDR-to-Interface resolution matches a Node's own attached Interface."""

    def test_matches_the_interface_on_the_pools_subnet(self):
        node = FakeNode.with_addresses('r1', '10.50.0.1', '192.168.1.1')
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(node)

        self.assertEqual(DHCPProvisioner.resolve_interface(node, pool, net), 'r1-eth0')

    def test_matches_the_second_interface_when_that_is_the_one(self):
        node = FakeNode.with_addresses('r1', '192.168.1.1', '10.50.0.1')
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(node)

        self.assertEqual(DHCPProvisioner.resolve_interface(node, pool, net), 'r1-eth1')

    def test_no_match_anywhere_in_the_lab_raises_configuration_error(self):
        """Naming the Pool and the Node: no Interface anywhere carries this
        CIDR, which is usually a typo in the Lab Example."""
        node = FakeNode.with_addresses('r1', '192.168.1.1')
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(node)

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interface(node, pool, net)
        self.assertIn('r1', str(ctx.exception))
        self.assertIn('10.50.0.0/24', str(ctx.exception))
        self.assertNotIn('relay', str(ctx.exception))

    def test_cidr_behind_another_router_names_relay_as_the_reason(self):
        """This is the ticket's sharpest edge: a Pool for a Subnet that is
        real, but reachable only through another Router, must say relay and
        name the Relay declaration — not say relay is out of scope."""
        serving_node = FakeNode.with_addresses('r1', '192.168.1.1')
        other_router = FakeNode.with_addresses('r2', '10.50.0.1')
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(serving_node, other_router)

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interface(serving_node, pool, net)
        message = str(ctx.exception)
        self.assertIn('relay', message.lower())
        self.assertNotIn('out of scope', message)
        self.assertIn('r1', message)
        self.assertIn('r2', message)
        self.assertIn('10.50.0.0/24', message)
        self.assertIn('r2.addRelay', message)

    def test_interfaces_without_an_address_are_skipped(self):
        node = FakeNode.with_addresses('r1', None, '10.50.0.1')
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(node)

        self.assertEqual(DHCPProvisioner.resolve_interface(node, pool, net), 'r1-eth1')

    def test_resolve_interfaces_maps_each_pool_to_its_own_interface(self):
        """Each Pool's CIDR resolves independently — the third attached
        Subnet, with no declared Pool, never appears in the mapping."""
        node = FakeNode.with_addresses('r1', '10.50.0.1', '10.51.0.1', '10.52.0.1')
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
            Pool(cidr='10.51.0.0/24', start='10.51.0.100', end='10.51.0.200'),
        )
        net = FakeLab(node)

        self.assertEqual(
            DHCPProvisioner.resolve_interfaces(node, service, net),
            {'10.50.0.0/24': 'r1-eth0', '10.51.0.0/24': 'r1-eth1'},
        )

    def test_resolve_interfaces_raises_when_any_pool_matches_nothing(self):
        node = FakeNode.with_addresses('r1', '10.50.0.1')
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
            Pool(cidr='10.51.0.0/24', start='10.51.0.100', end='10.51.0.200'),
        )
        net = FakeLab(node)

        with self.assertRaises(ConfigurationError) as ctx:
            DHCPProvisioner.resolve_interfaces(node, service, net)
        self.assertIn('10.51.0.0/24', str(ctx.exception))


class RefuseDhcp4StaticAddressTests(unittest.TestCase):
    """The dhcp4 marker refuses a static IPv4 address; nothing else."""

    def test_dhcp4_true_refuses_ip(self):
        params = {'dhcp4': True, 'ip': '10.0.0.5/24'}
        with self.assertRaises(LinkError) as ctx:
            mnIPLab._refuse_static_address_beside_dhcp(FakeNode('h1'), params, 'r1–h1')
        self.assertIn("h1's ip='10.0.0.5/24' is beside dhcp4", str(ctx.exception))

    def test_dhcp4_absent_accepts_ip(self):
        params = {'ip': '10.0.0.5/24'}
        mnIPLab._refuse_static_address_beside_dhcp(FakeNode('h1'), params, 'r1–h1')
        self.assertEqual(params, {'ip': '10.0.0.5/24'})

    def test_dhcp4_false_accepts_ip(self):
        params = {'dhcp4': False, 'ip': '10.0.0.5/24'}
        mnIPLab._refuse_static_address_beside_dhcp(FakeNode('h1'), params, 'r1–h1')
        self.assertEqual(params, {'dhcp4': False, 'ip': '10.0.0.5/24'})

    def test_dhcp4_true_with_no_ip_present_is_accepted(self):
        params = {'dhcp4': True}
        mnIPLab._refuse_static_address_beside_dhcp(FakeNode('h1'), params, 'r1–h1')
        self.assertEqual(params, {'dhcp4': True})


class ServiceReadinessTests(unittest.TestCase):
    """start_dhcp_service returns only once Kea answers, or fails the start."""

    def _serving_node(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        return FakeNode.with_addresses('r1', '10.50.0.1', services=[DHCPService(pool)])

    def test_start_waits_for_the_control_socket_of_each_launched_family(self):
        r1 = self._serving_node()
        probed = []

        def is_running(client):
            probed.append((client.socket_path, client.family))
            return True

        fake_net = FakeLab(r1)
        with patch('mniplab.provisioners.KeaControlClient.is_running', is_running):
            DHCPProvisioner.start_dhcp_service(r1, r1.services[0], fake_net)
            expected = DHCPProvisioner.control_socket_host_path('r1', family=4, net=fake_net)

        self.assertEqual(probed, [(expected, 4)])

    def test_a_server_that_never_answers_fails_the_start_naming_its_log(self):
        r1 = self._serving_node()

        with patch.object(DHCPProvisioner, 'READY_TIMEOUT', 0), \
                patch('mniplab.provisioners.KeaControlClient.is_running', return_value=False):
            with self.assertRaises(ConfigurationError) as ctx:
                DHCPProvisioner.start_dhcp_service(r1, r1.services[0], FakeLab(r1))

        message = str(ctx.exception)
        self.assertIn("r1", message)
        self.assertIn("kea-dhcp4", message)
        self.assertIn("kea-dhcp4.log", message)


class StartAllStopAllFilteringTests(unittest.TestCase):
    """start_all/stop_all are silent unless a Node actually has a DHCP Service."""

    @staticmethod
    def _node(name, services=()):
        node = Mock()
        node.name = name
        node.services = list(services)
        return node

    def test_start_all_does_nothing_when_no_node_has_a_service(self):
        net = Mock()
        nodes = [self._node('h1'), self._node('r1')]

        with patch.object(DHCPProvisioner, 'start_node_services') as mock_start:
            DHCPProvisioner.start_all(net, nodes)

        mock_start.assert_not_called()

    def test_start_all_starts_only_nodes_with_a_dhcp_service(self):
        net = Mock()
        net._ephemeral_frr_dirs = set()
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        served = self._node('r1', services=[DHCPService(pool)])
        unserved = self._node('h1')

        with patch.object(DHCPProvisioner, 'start_node_services') as mock_start:
            DHCPProvisioner.start_all(net, [unserved, served])

        mock_start.assert_called_once_with(served, net)

    def test_stop_all_stops_only_nodes_with_a_dhcp_service(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        served = self._node('r1', services=[DHCPService(pool)])
        unserved = self._node('h1')

        with patch.object(DHCPProvisioner, 'stop_node_services') as mock_stop:
            DHCPProvisioner.stop_all([unserved, served])

        mock_stop.assert_called_once_with(served)


class ControlSocketHostPathTests(unittest.TestCase):
    """DHCPProvisioner.control_socket_host_path names the same socket render_config does."""

    def setUp(self):
        self.net = FakeLab()

    def test_matches_the_run_subdir_and_socket_filename(self):
        expected = os.path.join(
            DHCPProvisioner.host_dir('r1', 'run', net=self.net),
            DHCPProvisioner.CONTROL_SOCKET_FILENAME,
        )
        self.assertEqual(DHCPProvisioner.control_socket_host_path('r1', net=self.net), expected)

    def test_is_scoped_per_node(self):
        self.assertNotEqual(
            DHCPProvisioner.control_socket_host_path('r1', net=self.net),
            DHCPProvisioner.control_socket_host_path('r2', net=self.net),
        )


class DHCPProvisionerDirectoryTests(unittest.TestCase):
    """Kea's runtime directory is prepared with its required permissions."""

    def test_run_directory_is_created_with_0750_permissions(self):
        node = FakeNode('r1')
        net = FakeLab(node)

        with patch('mniplab.provisioners.ensure_directory') as mock_ensure:
            DHCPProvisioner.bind_service_directories(node)

        mock_ensure.assert_any_call(
            DHCPProvisioner.host_dir('r1', 'run', net=net), 0o750, silent=True
        )
        mock_ensure.assert_any_call(
            DHCPProvisioner.host_dir('r1', 'etc', net=net),
            DHCPProvisioner.PERM_USER_RWX_GROUP_RX,
            silent=True,
        )
        mock_ensure.assert_any_call(
            DHCPProvisioner.host_dir('r1', 'lib', net=net),
            DHCPProvisioner.PERM_USER_RWX_GROUP_RX,
            silent=True,
        )


class DHCPQueryServiceTests(unittest.TestCase):
    """DHCPQueryService: the lookup every Lease and Pool query starts from."""

    def test_node_not_found_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            DHCPQueryService.find_service(FakeLab(), 'ghost')
        self.assertIn('Node not found', str(ctx.exception))

    def test_node_with_no_dhcp_service_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            DHCPQueryService.find_service(FakeLab(FakeNode('h1')), 'h1')
        self.assertIn('no DHCP Service', str(ctx.exception))

    def test_client_reaches_the_familys_own_socket(self):
        service = DHCPService(
            Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200'),
            Pool(cidr='2001:db8:1::/64', start='2001:db8:1::100', end='2001:db8:1::200'),
        )
        net = FakeLab(FakeNode('r1', services=[service]))

        for family in (4, 6):
            _, found, client = DHCPQueryService.get_client(net, 'r1', family)
            self.assertIs(found, service)
            self.assertEqual(
                client.socket_path,
                DHCPProvisioner.control_socket_host_path('r1', family=family, net=net),
            )
            self.assertEqual(client.family, family)

    def test_refuses_a_family_the_service_does_not_serve(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))

        with self.assertRaises(ValueError) as ctx:
            DHCPQueryService.get_client(net, 'r1', 6)
        self.assertIn('no DHCPv6 Pool', str(ctx.exception))


class LeaseQueryServiceTests(KeaClientStubMixin, unittest.TestCase):
    """LeaseQueryService: reading Leases through a scripted Kea client."""

    def test_node_not_found_raises_value_error(self):
        net = FakeLab()

        with self.assertRaises(ValueError) as ctx:
            LeaseQueryService.get_leases(net, 'ghost')

        self.assertIn('Node not found', str(ctx.exception))

    def test_node_with_no_dhcp_service_raises_value_error(self):
        net = FakeLab(FakeNode('h1'))

        with self.assertRaises(ValueError) as ctx:
            LeaseQueryService.get_leases(net, 'h1')

        self.assertIn('h1', str(ctx.exception))
        self.assertIn('no DHCP Service', str(ctx.exception))

    def test_returns_leases_from_the_control_client(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))
        leases = [{'ip-address': '10.50.0.100'}]
        self.client.get_all_leases.return_value = leases

        result = LeaseQueryService.get_leases(net, 'r1')

        self.assertEqual(result, {'success': True, 'v4': leases})
        self.assertNotIn('v6', result)
        self.client6.get_all_leases.assert_not_called()

    def test_a_lease_not_yet_applied_by_any_interface_still_appears(self):
        """The server's own record, not Interface state — the whole point of the ticket."""
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        node = FakeNode('r1', services=[DHCPService(pool)])
        node.intfList = Mock(return_value=[])  # no Interface reflects this Lease
        net = FakeLab(node)
        leases = [{'ip-address': '10.50.0.150', 'hw-address': 'aa:bb:cc:dd:ee:ff'}]
        self.client.get_all_leases.return_value = leases

        result = LeaseQueryService.get_leases(net, 'r1')

        self.assertEqual(result['v4'], leases)
        self.assertNotIn('v6', result)

    def test_empty_leases_is_not_an_error(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))

        self.client.get_all_leases.return_value = []

        result = LeaseQueryService.get_leases(net, 'r1')

        self.assertEqual(result, {'success': True, 'v4': []})
        self.assertNotIn('v6', result)

    def test_kea_not_running_fails_cleanly_with_an_explanation(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))

        self.client.get_all_leases.side_effect = ConfigurationError(
            "Could not reach Kea's control socket at /tmp/x: [Errno 2] No such file. "
            "Is kea-dhcp4 running for this Service?"
        )

        with self.assertRaises(ValueError) as ctx:
            LeaseQueryService.get_leases(net, 'r1')

        self.assertIn('kea-dhcp4', str(ctx.exception))


class PoolServiceTests(KeaClientStubMixin, unittest.TestCase):
    """PoolService: reading and updating Pools through a scripted Kea client."""

    def test_node_not_found_raises_value_error(self):
        net = FakeLab()

        with self.assertRaises(ValueError) as ctx:
            PoolService.get_pools(net, 'ghost')
        self.assertIn('Node not found', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(net, 'ghost', '10.50.0.0/24', '10.50.0.10', '10.50.0.20')
        self.assertIn('Node not found', str(ctx.exception))

    def test_node_with_no_dhcp_service_raises_value_error(self):
        net = FakeLab(FakeNode('h1'))

        with self.assertRaises(ValueError) as ctx:
            PoolService.get_pools(net, 'h1')
        self.assertIn('no DHCP Service', str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(net, 'h1', '10.50.0.0/24', '10.50.0.10', '10.50.0.20')
        self.assertIn('no DHCP Service', str(ctx.exception))

    def test_get_pools_returns_serialized_pools(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        service.interface_names = {'10.50.0.0/24': 'r1-eth0'}
        net = FakeLab(FakeNode('r1', services=[service]))

        result = PoolService.get_pools(net, 'r1')
        self.assertEqual(result, {
            'success': True,
            'pools': [{
                'family': 4,
                'cidr': '10.50.0.0/24',
                'start': '10.50.0.100',
                'end': '10.50.0.200',
                'interface': 'r1-eth0',
                'lease_time': None,
                'router': '',
                'dns_server': '',
                'reservations': [],
            }],
        })

    def test_get_pools_serializes_lease_time_options_and_reservations(self):
        reservation = Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50', hostname='h1')
        pool = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            lease_time=120, router='10.50.0.1', dns_server='8.8.8.8',
            reservations=(reservation,),
        )
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        result = PoolService.get_pools(net, 'r1')
        served = result['pools'][0]
        self.assertEqual(served['lease_time'], 120)
        self.assertEqual(served['router'], '10.50.0.1')
        self.assertEqual(served['dns_server'], '8.8.8.8')
        self.assertEqual(
            served['reservations'],
            [{'mac': 'aa:bb:cc:dd:ee:ff', 'duid': '', 'ip': '10.50.0.50', 'hostname': 'h1'}],
        )

    def test_get_pools_serializes_defaults_when_nothing_declared(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        served = PoolService.get_pools(net, 'r1')['pools'][0]
        self.assertIsNone(served['lease_time'])
        self.assertEqual(served['router'], '')
        self.assertEqual(served['dns_server'], '')
        self.assertEqual(served['reservations'], [])

    def test_set_pool_updates_lease_time_options_and_reservations(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        result = PoolService.set_pool(
            net, 'r1', '10.50.0.0/24', '10.50.0.100', '10.50.0.200',
            lease_time=90, router='10.50.0.1', dns_server='8.8.8.8',
            reservations=[{'mac': 'aa:bb:cc:dd:ee:ff', 'ip': '10.50.0.50', 'hostname': 'h1'}],
        )

        updated = service.pools['10.50.0.0/24']
        self.assertEqual(updated.lease_time, 90)
        self.assertEqual(updated.router, '10.50.0.1')
        self.assertEqual(updated.dns_server, '8.8.8.8')
        self.assertEqual(
            updated.reservations,
            (Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50', hostname='h1'),),
        )
        self.assertEqual(result['pool']['lease_time'], 90)
        self.assertEqual(
            result['pool']['reservations'],
            [{'mac': 'aa:bb:cc:dd:ee:ff', 'duid': '', 'ip': '10.50.0.50', 'hostname': 'h1'}],
        )

    def test_set_pool_rejects_invalid_lease_time_before_calling_kea(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '10.50.0.0/24', '10.50.0.100', '10.50.0.200', lease_time=-5,
            )
        self.client.test_config.assert_not_called()

        self.assertIn('positive', str(ctx.exception))
        self.assertIsNone(service.pools['10.50.0.0/24'].lease_time)

    def test_set_pool_rejects_invalid_router_option_before_calling_kea(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '10.50.0.0/24', '10.50.0.100', '10.50.0.200', router='not-an-ip',
            )
        self.client.test_config.assert_not_called()

        self.assertIn('router', str(ctx.exception))

    def test_set_pool_rejects_invalid_reservation_before_calling_kea(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '10.50.0.0/24', '10.50.0.100', '10.50.0.200',
                reservations=[{'mac': 'not-a-mac', 'ip': '10.50.0.50'}],
            )
        self.client.test_config.assert_not_called()

        self.assertIn('Invalid MAC address', str(ctx.exception))
        self.assertEqual(service.pools['10.50.0.0/24'].reservations, ())

    def test_set_pool_reservation_outside_subnet_rejected_before_calling_kea(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(
                net, 'r1', '10.50.0.0/24', '10.50.0.100', '10.50.0.200',
                reservations=[{'mac': 'aa:bb:cc:dd:ee:ff', 'ip': '192.168.1.5'}],
            )
        self.client.test_config.assert_not_called()

        self.assertIn('not in subnet', str(ctx.exception))

    def test_set_pool_omitting_new_fields_clears_them(self):
        """A Learner submitting the form with a Reservation removed, or an
        option field blanked, must actually clear it — not silently keep the
        old value alive on the server."""
        pool = Pool(
            cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200',
            lease_time=120, router='10.50.0.1', dns_server='8.8.8.8',
            reservations=(Reservation(mac='aa:bb:cc:dd:ee:ff', ip='10.50.0.50'),),
        )
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        PoolService.set_pool(net, 'r1', '10.50.0.0/24', '10.50.0.100', '10.50.0.200')

        updated = service.pools['10.50.0.0/24']
        self.assertIsNone(updated.lease_time)
        self.assertEqual(updated.router, '')
        self.assertEqual(updated.dns_server, '')
        self.assertEqual(updated.reservations, ())

    def test_set_pool_rejects_unserved_subnet(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(net, 'r1', '192.168.1.0/24', '192.168.1.10', '192.168.1.20')
        self.assertIn('not served by r1', str(ctx.exception))

    def test_set_pool_rejects_invalid_pool_range_before_calling_kea(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(net, 'r1', '10.50.0.0/24', '10.50.0.200', '10.50.0.100')
        self.client.test_config.assert_not_called()

        self.assertIn('cannot be after', str(ctx.exception))
        self.assertEqual(service.pools['10.50.0.0/24'].start, '10.50.0.100')

    def test_set_pool_applies_via_kea_and_updates_service(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        service.interface_names = {'10.50.0.0/24': 'r1-eth0'}
        net = FakeLab(FakeNode('r1', services=[service]))

        result = PoolService.set_pool(net, 'r1', '10.50.0.0/24', '10.50.0.150', '10.50.0.220')

        self.client.test_config.assert_called_once()
        self.client.set_config.assert_called_once()
        self.client6.test_config.assert_not_called()

        self.assertEqual(service.pools['10.50.0.0/24'].start, '10.50.0.150')
        self.assertEqual(service.pools['10.50.0.0/24'].end, '10.50.0.220')
        self.assertEqual(result['success'], True)
        self.assertEqual(result['pool']['start'], '10.50.0.150')
        self.assertEqual(result['pool']['end'], '10.50.0.220')

    def test_set_pool_kea_test_rejection_leaves_service_pools_unchanged(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        net = FakeLab(FakeNode('r1', services=[service]))

        self.client.test_config.side_effect = ConfigurationError(
            "Kea rejected configuration: subnet configuration failed"
        )

        with self.assertRaises(ValueError) as ctx:
            PoolService.set_pool(net, 'r1', '10.50.0.0/24', '10.50.0.150', '10.50.0.220')

        self.client.set_config.assert_not_called()

        self.assertIn('subnet configuration failed', str(ctx.exception))
        # Crucial requirement: A rejected edit leaves the running server exactly as it was.
        self.assertEqual(service.pools['10.50.0.0/24'].start, '10.50.0.100')
        self.assertEqual(service.pools['10.50.0.0/24'].end, '10.50.0.200')

    def test_stopping_dhcp_service_discards_edits_and_restores_declared_pools(self):
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        service = DHCPService(pool)
        node = FakeNode('r1', services=[service])
        node.cmd = Mock()
        net = FakeLab(node)

        PoolService.set_pool(net, 'r1', '10.50.0.0/24', '10.50.0.150', '10.50.0.220')

        self.assertEqual(service.pools['10.50.0.0/24'].start, '10.50.0.150')

        # Stopping the service must discard Learner edits and restore declared pools
        DHCPProvisioner.stop_dhcp_service(node)
        self.assertEqual(service.pools['10.50.0.0/24'].start, '10.50.0.100')
        self.assertEqual(service.pools['10.50.0.0/24'].end, '10.50.0.200')

    def test_dhcp_service_find_on(self):
        service = DHCPService(Pool(cidr='10.50.0.0/24', start='10.50.0.10', end='10.50.0.20'))
        node = FakeNode('r1', services=[service])
        self.assertIs(DHCPService.find_on(node), service)

        node_empty = FakeNode('r2', services=[])
        with self.assertRaises(ValueError) as ctx:
            DHCPService.find_on(node_empty)
        self.assertIn('no DHCP Service', str(ctx.exception))



class ShowLeasesCommandTests(KeaClientStubMixin, unittest.TestCase):
    """mnIPLabCLI.do_show_leases: the CLI surface, against a fake LeaseQueryService."""

    def test_requires_a_node_argument(self):
        self.assertIn('Usage', cli_output('show_leases', ''))

    def test_prints_each_lease(self):
        leases = [
            {'ip-address': '10.50.0.100', 'hw-address': 'aa:bb:cc:dd:ee:ff', 'valid-lft': 3600},
        ]
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            return_value={'success': True, 'v4': leases},
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('10.50.0.100', printed)
        self.assertIn('aa:bb:cc:dd:ee:ff', printed)

    def test_reports_no_leases_without_erroring(self):
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            return_value={'success': True, 'v4': []},
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('No Leases', printed)

    def test_no_service_fails_cleanly_with_an_explanation(self):
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            side_effect=ValueError('r1 has no DHCP Service attached'),
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('no DHCP Service', printed)

    def test_kea_not_running_fails_cleanly_with_an_explanation(self):
        with patch(
            'mniplab.dhcp.leases.LeaseQueryService.get_leases',
            side_effect=ValueError("Is kea-dhcp4 running for this Service?"),
        ):
            printed = cli_output('show_leases', 'r1')

        self.assertIn('kea-dhcp4', printed)

    def test_end_to_end_through_the_real_service_against_a_fake_control_client(self):
        """The command, the shared service, and the resolution logic, faking
        only the socket-facing adapter — not LeaseQueryService itself."""
        pool = Pool(cidr='10.50.0.0/24', start='10.50.0.100', end='10.50.0.200')
        net = FakeLab(FakeNode('r1', services=[DHCPService(pool)]))
        leases = [{'ip-address': '10.50.0.100', 'hw-address': 'aa:bb:cc:dd:ee:ff'}]

        self.client.get_all_leases.return_value = leases

        printed = cli_output('show_leases', 'r1', net=net)
        self.assertIn('10.50.0.100', printed)


if __name__ == '__main__':
    unittest.main()

