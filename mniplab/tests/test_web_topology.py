"""Unit tests for the Topology payload the web UI draws from.

``TopologyBuilder`` walks a Lab into nodes, links, and relays; these tests
build it from ``FakeLab`` and pin both the Service and Relay serialisation
and the Link Condition field names the browser already reads.
"""

import unittest

from mniplab.tests.fakes import FakeIntf, FakeLab, FakeLink, FakeNode
from mniplab.topology import TopologyBuilder
from mniplab.utils import is_drawn


class TopologyBuilderTests(unittest.TestCase):
    """Test topology building from lab instances."""

    def test_an_empty_lab_builds_empty_nodes_links_and_relays(self):
        """build should emit every top-level key, each empty."""
        topology = TopologyBuilder.build(FakeLab())
        self.assertEqual(topology['nodes'], [])
        self.assertEqual(topology['links'], [])
        self.assertEqual(topology['relays'], [])

    def test_build_lists_hosts_and_switches_as_nodes(self):
        """build should include hosts and switches in the nodes list, typed."""
        host = FakeNode.with_addresses('h1', '10.0.0.1')

        topology = TopologyBuilder.build(FakeLab(host, switches=[FakeNode('s1')]))
        nodes = {n['id']: n['type'] for n in topology['nodes']}
        self.assertEqual(nodes, {'h1': 'host', 's1': 'switch'})

    def test_build_includes_services_when_present(self) -> None:
        """build should serialize node services and preserve display type."""
        from mniplab.dhcp.service import DHCPService
        from mniplab.dhcp.pool import Pool

        router = FakeNode.with_addresses(
            'r1', '10.0.0.1', display_type='router',
            services=[DHCPService(Pool('10.0.0.0/24', '10.0.0.10', '10.0.0.20'))],
        )
        host = FakeNode.with_addresses('h1', '10.0.0.2')

        topology = TopologyBuilder.build(FakeLab(router, host))
        r1_node = next(n for n in topology['nodes'] if n['id'] == 'r1')
        h1_node = next(n for n in topology['nodes'] if n['id'] == 'h1')

        self.assertEqual(r1_node['type'], 'router')
        self.assertEqual(r1_node['services'], ['dhcp'])
        self.assertEqual(
            r1_node['service_details'],
            [{'name': 'dhcp', 'badge': 'DHCP', 'families': [4]}],
        )
        self.assertEqual(h1_node['type'], 'host')
        self.assertEqual(h1_node['services'], [])
        self.assertEqual(h1_node['service_details'], [])

    def test_build_serializes_v6_and_dual_stack_service_badges(self) -> None:
        """build should serialize service_details reflecting IPv6 and dual-stack families."""
        from mniplab.dhcp.service import DHCPService
        from mniplab.dhcp.pool import Pool

        v4_pool = Pool('10.0.0.0/24', '10.0.0.10', '10.0.0.20')
        v6_pool = Pool('2001:db8:1::/64', '2001:db8:1::10', '2001:db8:1::20')

        r_v6 = FakeNode.with_addresses(
            'r_v6', '10.0.0.1', display_type='router', services=[DHCPService(v6_pool)],
        )
        r_dual = FakeNode.with_addresses(
            'r_dual', '10.0.0.2', display_type='router',
            services=[DHCPService(v4_pool, v6_pool)],
        )

        topology = TopologyBuilder.build(FakeLab(r_v6, r_dual))
        v6_node = next(n for n in topology['nodes'] if n['id'] == 'r_v6')
        dual_node = next(n for n in topology['nodes'] if n['id'] == 'r_dual')

        self.assertEqual(v6_node['services'], ['dhcp'])
        self.assertEqual(
            v6_node['service_details'],
            [{'name': 'dhcp', 'badge': 'DHCPv6', 'families': [6]}],
        )

        self.assertEqual(dual_node['services'], ['dhcp'])
        self.assertEqual(
            dual_node['service_details'],
            [{'name': 'dhcp', 'badge': 'DHCP · DHCPv6', 'families': [4, 6]}],
        )

    def test_build_serializes_dns_service_badge_and_metadata(self) -> None:
        """build should serialize DNS service with badge 'DNS' and hosted zone names."""
        from mniplab.dns import DNSService, Zone, ARecord

        zone1 = Zone("example.com.", [ARecord("web", "10.0.0.1")])
        zone2 = Zone("0.0.10.in-addr.arpa.", [])
        dns_host = FakeNode.with_addresses(
            "dns1", "10.0.0.1", services=[DNSService(zone1, zone2)],
        )

        topology = TopologyBuilder.build(FakeLab(dns_host))
        node = next(n for n in topology['nodes'] if n['id'] == 'dns1')

        self.assertIn('dns', node['services'])
        self.assertEqual(len(node['service_details']), 1)
        detail = node['service_details'][0]
        self.assertEqual(detail['name'], 'dns')
        self.assertEqual(detail['badge'], 'DNS')
        self.assertEqual(detail['families'], [4, 6])
        self.assertEqual(set(detail['zones']), {'example.com.', '0.0.10.in-addr.arpa.'})

    def test_build_composes_dhcp_and_dns_badges(self) -> None:
        """build should compose DHCP and DNS services on a single node."""
        from mniplab.dhcp import Pool, DHCPService
        from mniplab.dns import DNSService, Zone, ARecord

        dhcp_service = DHCPService(Pool('10.0.0.0/24', '10.0.0.10', '10.0.0.20'))
        dns_service = DNSService(Zone("example.com.", [ARecord("web", "10.0.0.1")]))
        router = FakeNode.with_addresses(
            "r1", "10.0.0.1", display_type="router",
            services=[dhcp_service, dns_service],
        )

        topology = TopologyBuilder.build(FakeLab(router))
        node = next(n for n in topology['nodes'] if n['id'] == 'r1')

        self.assertEqual(node['services'], ['dhcp', 'dns'])
        self.assertEqual(len(node['service_details']), 2)
        self.assertEqual(node['service_details'][0]['badge'], 'DHCP')
        self.assertEqual(node['service_details'][1]['badge'], 'DNS')

    def test_build_distinguishes_secondary_dns_badge(self) -> None:
        """build should serialize 'DNS · SECONDARY' badge for nodes with SecondaryZone."""
        from mniplab.dns import DNSService, SecondaryZone

        host = FakeNode.with_addresses(
            "ns2", "10.0.0.2",
            services=[DNSService(SecondaryZone("example.com.", primary="10.0.0.1"))],
        )

        topology = TopologyBuilder.build(FakeLab(host))
        node = next(n for n in topology['nodes'] if n['id'] == 'ns2')
        self.assertEqual(node['service_details'][0]['badge'], 'DNS · SECONDARY')

    def test_build_marks_a_resolving_node(self) -> None:
        """A Node hosting a Resolver Service carries a RESOLVER badge."""
        from mniplab.resolver import ResolverService

        host = FakeNode("res1", services=[ResolverService(root_hints=["10.0.1.2"])])

        topology = TopologyBuilder.build(FakeLab(host))
        node = topology["nodes"][0]

        self.assertIn("resolver", node["services"])
        detail = next(d for d in node["service_details"] if d["name"] == "resolver")
        self.assertEqual(detail["badge"], "RESOLVER")

    def test_build_serializes_relays_separately_from_services(self) -> None:
        """build should expose relay subnets without conflating them with Services."""
        from mniplab.dhcp.pool import Pool
        from mniplab.dhcp.relay import Relay
        from mniplab.dhcp.service import DHCPService

        relaying_router = FakeNode.with_addresses(
            'r1', '10.50.0.1', display_type='router',
            services=[DHCPService(Pool('10.50.0.0/24', '10.50.0.100', '10.50.0.200'))],
            relays=[
                Relay(
                    '10.60.0.0/24',
                    '2001:db8:60::/64',
                    server=['10.0.0.2', '2001:db8:0::2'],
                )
            ],
        )
        plain_host = FakeNode.with_addresses('h1', '10.50.0.2')

        topology = TopologyBuilder.build(FakeLab(relaying_router, plain_host))
        router_node = next(n for n in topology['nodes'] if n['id'] == 'r1')
        host_node = next(n for n in topology['nodes'] if n['id'] == 'h1')

        self.assertEqual(router_node['type'], 'router')
        self.assertEqual(router_node['services'], ['dhcp'])
        self.assertEqual(router_node['relays'], [{
            'subnets': ['10.60.0.0/24', '2001:db8:60::/64'],
        }])
        self.assertEqual(host_node['services'], [])
        self.assertEqual(host_node['relays'], [])

    def test_build_includes_every_interfaces_ip_not_just_the_default_one(self) -> None:
        """A multi-homed host's every Interface address is reported.

        The DHCP Leases panel tells an applied Lease from an unapplied one by
        matching a Lease's address against a Node's Interfaces — not just its
        default Interface, since a DHCP-client Interface is often not the
        default one (one Interface managed, another static).
        """
        host = FakeNode.with_addresses('h1', '10.0.0.5', '192.168.1.5')

        topology = TopologyBuilder.build(FakeLab(host))
        h1_node = topology['nodes'][0]
        self.assertEqual(sorted(h1_node['ips']), ['10.0.0.5', '192.168.1.5'])

    def test_build_resolves_single_family_relay(self) -> None:
        """build should resolve server_ip to target_node and client subnets to client_nodes."""
        from mniplab.dhcp.relay import Relay

        r1 = FakeNode.with_addresses('r1', '10.0.0.1', display_type='router')
        r2 = FakeNode.with_addresses(
            'r2', '10.0.0.2', '10.20.0.1', display_type='router',
            relays=[Relay('10.20.0.0/24', server='10.0.0.1')],
        )
        h2 = FakeNode.with_addresses('h2', '10.20.0.2')

        topology = TopologyBuilder.build(FakeLab(r1, r2, h2))
        self.assertIn('relays', topology)
        self.assertEqual(len(topology['relays']), 1)
        relay_entry = topology['relays'][0]
        self.assertEqual(relay_entry['id'], 'relay-r2')
        self.assertEqual(relay_entry['relay_node'], 'r2')
        self.assertEqual(relay_entry['server_ip'], '10.0.0.1')
        self.assertEqual(relay_entry['target_node'], 'r1')
        self.assertTrue(relay_entry['reachable'])
        self.assertEqual(relay_entry['subnets'], ['10.20.0.0/24'])
        self.assertEqual(relay_entry['client_nodes'], ['h2'])
        self.assertIsNone(relay_entry['intermediate_switch'])
        self.assertEqual(relay_entry['families'], [4])

    def test_build_resolves_dual_stack_relay(self) -> None:
        """build should resolve dual-stack relay with multiple subnets and families."""
        from mniplab.dhcp.relay import Relay

        r1 = FakeNode.with_addresses(
            'r1', ('10.0.0.1', '2001:db8:0::1/64'), display_type='router',
        )
        r2 = FakeNode.with_addresses(
            'r2', ('10.20.0.1', '2001:db8:20::1/64'), display_type='router',
            relays=[
                Relay(
                    '10.20.0.0/24',
                    '2001:db8:20::/64',
                    server=['10.0.0.1', '2001:db8:0::1'],
                )
            ],
        )
        h2 = FakeNode.with_addresses('h2', ('10.20.0.2', '2001:db8:20::2/64'))

        topology = TopologyBuilder.build(FakeLab(r1, r2, h2))
        relay_entry = topology['relays'][0]
        self.assertEqual(relay_entry['id'], 'relay-r2')
        self.assertEqual(relay_entry['relay_node'], 'r2')
        self.assertEqual(relay_entry['target_node'], 'r1')
        self.assertTrue(relay_entry['reachable'])
        self.assertEqual(relay_entry['subnets'], ['10.20.0.0/24', '2001:db8:20::/64'])
        self.assertEqual(relay_entry['client_nodes'], ['h2'])
        self.assertEqual(relay_entry['families'], [4, 6])

    def test_build_resolves_unreachable_server_ip(self) -> None:
        """build should mark unreachable server IP with target_node=None and reachable=False."""
        from mniplab.dhcp.relay import Relay

        r3 = FakeNode.with_addresses(
            'r3', '10.30.0.1', display_type='router',
            relays=[Relay('10.30.0.0/24', server='10.99.0.1')],
        )
        h3 = FakeNode.with_addresses('h3', '10.30.0.2')

        topology = TopologyBuilder.build(FakeLab(r3, h3))
        relay_entry = topology['relays'][0]
        self.assertEqual(relay_entry['relay_node'], 'r3')
        self.assertEqual(relay_entry['server_ip'], '10.99.0.1')
        self.assertIsNone(relay_entry['target_node'])
        self.assertFalse(relay_entry['reachable'])
        self.assertEqual(relay_entry['client_nodes'], ['h3'])

    def test_build_resolves_dhcp_client_nodes_without_assigned_ip(self) -> None:
        """build should resolve client nodes connected to the relay router's subnet interface even when unconfigured."""
        from mniplab.dhcp.relay import Relay

        r2 = FakeNode(
            'r2', display_type='router',
            intfs=[FakeIntf('r2-eth1', ip='10.20.0.1', ip6='')],
            relays=[Relay('10.20.0.0/24', server='10.0.0.1')],
        )
        # Client host has no IP configured yet (pure DHCP client)
        h2 = FakeNode('h2', intfs=[FakeIntf('h2-eth0', ip='', ip6='')])

        link = FakeLink(r2.intfList()[0], h2.intfList()[0])

        topology = TopologyBuilder.build(FakeLab(r2, h2, links=[link]))
        relay_entry = topology['relays'][0]
        self.assertEqual(relay_entry['client_nodes'], ['h2'])

    def test_a_switch_end_is_labelled_without_the_lab_key(self) -> None:
        """A Switch port's keyed name leaves the payload as the name a Learner writes."""
        from mniplab.tests.test_switch import FakeSwitch

        s1 = FakeSwitch('s1')
        s1.bridge_name = 'k7-s1'
        h1 = FakeNode('h1', intfs=[FakeIntf('h1-eth0', ip='', ip6='')])
        link = FakeLink(h1.intfList()[0], FakeIntf('k7-s1-eth1', s1))

        topology = TopologyBuilder.build(FakeLab(h1, switches=[s1], links=[link]))

        self.assertEqual(topology['links'][0]['source_intf'], 'h1-eth0')
        self.assertEqual(topology['links'][0]['target_intf'], 's1-eth1')

    def test_build_resolves_dhcp_client_nodes_behind_intermediate_switch(self) -> None:
        """build should resolve unconfigured client nodes and intermediate switch."""
        from mniplab.dhcp.relay import Relay

        r2 = FakeNode(
            'r2', display_type='router',
            intfs=[FakeIntf('r2-eth1', ip='10.20.0.1', ip6='')],
            relays=[Relay('10.20.0.0/24', server='10.0.0.1')],
        )
        s1 = FakeNode('s1')
        h2a = FakeNode('h2a', intfs=[FakeIntf('h2a-eth0', ip='', ip6='')])
        h2b = FakeNode('h2b', intfs=[FakeIntf('h2b-eth0', ip='', ip6='')])

        links = [
            FakeLink(r2.intfList()[0], FakeIntf('s1-eth1', s1)),
            FakeLink(h2a.intfList()[0], FakeIntf('s1-eth2', s1)),
            FakeLink(h2b.intfList()[0], FakeIntf('s1-eth3', s1)),
        ]

        topology = TopologyBuilder.build(
            FakeLab(r2, h2a, h2b, switches=[s1], links=links)
        )
        relay_entry = topology['relays'][0]
        self.assertEqual(relay_entry['intermediate_switch'], 's1')
        self.assertEqual(relay_entry['client_nodes'], ['h2a', 'h2b'])

    def test_build_unifies_multiple_relays_between_identical_endpoints(self) -> None:
        """build should unify multiple separate Relay declarations sharing router and target."""
        from mniplab.dhcp.relay import Relay

        r1 = FakeNode.with_addresses(
            'r1', ('10.0.0.1', '2001:db8:0::1/64'), display_type='router',
        )
        # Two separate Relay instances between r2 and r1: one IPv4, one IPv6
        r2 = FakeNode.with_addresses(
            'r2', ('10.20.0.1', '2001:db8:20::1/64'), display_type='router',
            relays=[
                Relay('10.20.0.0/24', server='10.0.0.1'),
                Relay('2001:db8:20::/64', server='2001:db8:0::1'),
            ],
        )
        h2 = FakeNode.with_addresses('h2', ('10.20.0.2', '2001:db8:20::2/64'))

        topology = TopologyBuilder.build(FakeLab(r1, r2, h2))
        # Should be unified into a single relay entry
        self.assertEqual(len(topology['relays']), 1)
        relay_entry = topology['relays'][0]
        self.assertEqual(relay_entry['relay_node'], 'r2')
        self.assertEqual(relay_entry['target_node'], 'r1')
        self.assertEqual(relay_entry['families'], [4, 6])
        self.assertEqual(relay_entry['subnets'], ['10.20.0.0/24', '2001:db8:20::/64'])
        self.assertEqual(relay_entry['client_nodes'], ['h2'])


class TopologyPayloadContractTests(unittest.TestCase):
    """The payload keeps the field names the web UI already reads.

    network-tools.js reads link.params.bw for bandwidth and link.status for
    administrative state. The Link Condition rename was internal; these names
    must not change or the Link Config modal silently shows empty fields.
    """

    def test_bandwidth_is_reported_as_bw_and_the_scheduler_is_not_leaked(self):
        params = TopologyBuilder._link_params(
            {'status': 'up', 'bandwidth': 100, 'scheduler': 'hfsc'}
        )

        self.assertEqual(params, {'bw': 100})

    def test_delay_jitter_and_loss_keep_their_names(self):
        params = TopologyBuilder._link_params(
            {'status': 'up', 'delay': '10ms', 'jitter': '2ms', 'loss': '5%'}
        )

        self.assertEqual(params['delay'], '10ms')
        self.assertEqual(params['jitter'], '2ms')
        self.assertEqual(params['loss'], '5%')

    def test_unimpaired_link_reports_no_params(self):
        self.assertEqual(TopologyBuilder._link_params({'status': 'up'}), {})


class InternetGatewayTopologyTests(unittest.TestCase):
    """An Internet Gateway is drawn as the cloud a Lab's traffic leaves by."""

    def setUp(self):
        r1 = FakeNode('r1', display_type='router')
        nat1 = FakeNode('nat1', display_type='internet', hidden=True)
        r1_eth1 = FakeIntf('r1-eth1', r1, ip='203.0.113.2')
        nat1_eth0 = FakeIntf('nat1-eth0', nat1, ip='203.0.113.1')
        r1.addIntf(r1_eth1)
        nat1.addIntf(nat1_eth0)
        self.lab = FakeLab(r1, nat1, links=[FakeLink(r1_eth1, nat1_eth0)])

    def test_an_internet_gateway_is_listed_as_a_node_of_its_own_kind(self):
        nodes = {n['id']: n['type'] for n in TopologyBuilder.build(self.lab)['nodes']}

        self.assertEqual(nodes, {'r1': 'router', 'nat1': 'internet'})

    def test_its_link_names_both_ends_and_both_addresses(self):
        [link] = TopologyBuilder.build(self.lab)['links']

        self.assertEqual((link['source'], link['target']), ('r1', 'nat1'))
        self.assertEqual(link['source_addresses'], ['203.0.113.2'])
        self.assertEqual(link['target_addresses'], ['203.0.113.1'])

    def test_a_hidden_host_stays_out_of_the_topology(self):
        lab = FakeLab(FakeNode('r1', display_type='router'), FakeNode('h9', hidden=True))

        self.assertEqual([n['id'] for n in TopologyBuilder.build(lab)['nodes']], ['r1'])

    def test_is_drawn_keeps_hidden_nodes_out_except_internet_gateways(self):
        self.assertTrue(is_drawn(FakeNode('r1')))
        self.assertFalse(is_drawn(FakeNode('h9', hidden=True)))
        self.assertTrue(is_drawn(FakeNode('nat1', display_type='internet', hidden=True)))
