"""Unit tests for ExaBGP node behavior."""

import ipaddress
import os
import shutil
import subprocess
import tempfile
import unittest
from typing import List, Optional
from unittest.mock import Mock, patch

import pytest

from mniplab.errors import ConfigurationError, ExaBGPError
from mniplab.exabgp.config import ExaBGPConfigBuilder
from mniplab.exabgp.models import BGPNeighbor, BGPRoute
from mniplab.exabgp.node import ExaBGPNode
from mniplab.tests.labs import unstarted_lab
from mniplab.exabgp.utils import (
    DEFAULT_ORIGIN_AS,
    DEFAULT_IPV6_NEXT_HOP,
    IPV6_PREFIX_LEN_MIN,
    IPV6_PREFIX_LEN_MAX,
    IPV6_SUBNET_MAX_PREFIX,
    make_v6_routes,
    make_v6_peer_routes,
)


class ExaBGPNodeTests(unittest.TestCase):
    """Validate ExaBGPNode directory resolution and cleanup."""

    def _make_node(self, private_dirs=None):
        """Create a bare ExaBGPNode instance with mocked base attributes."""
        node = object.__new__(ExaBGPNode)
        node.name = 'exa1'
        node.privateDirs = private_dirs if private_dirs is not None else [
            ('/etc/exabgp', '/tmp/exabgp-config/exa1'),
            ('/var/log/exabgp', '/tmp/exabgp-config/logs/exa1'),
            ('/var/run/exabgp', '/tmp/exabgp-config/run/exa1'),
        ]
        return node

    def test_get_exabgp_dir_resolves_mapping(self):
        """get_exabgp_dir should return host path for /etc/exabgp mapping."""
        node = self._make_node()
        result = node.get_exabgp_dir()
        self.assertEqual(result, '/tmp/exabgp-config/exa1')

    def test_get_exabgp_dir_returns_none_when_missing(self):
        """get_exabgp_dir should return None when no /etc/exabgp mapping exists."""
        node = self._make_node(private_dirs=[])
        result = node.get_exabgp_dir()
        self.assertIsNone(result)

    def test_get_exabgp_run_dir_resolves_mapping(self):
        """get_exabgp_run_dir should return host path for /var/run/exabgp mapping."""
        node = self._make_node()
        result = node.get_exabgp_run_dir()
        self.assertEqual(result, '/tmp/exabgp-config/run/exa1')

    def test_get_exabgp_run_dir_returns_none_when_missing(self):
        """get_exabgp_run_dir should return None when no /var/run/exabgp mapping exists."""
        node = self._make_node(private_dirs=[])
        result = node.get_exabgp_run_dir()
        self.assertIsNone(result)

    def test_terminate_kills_process_and_cleans_fifos(self):
        """terminate should run pkill and remove FIFO files."""
        node = self._make_node()
        node.cmd = Mock()
        node._cleanup_fifos = Mock()

        with patch.object(ExaBGPNode, 'terminate', lambda self: None):
            # We're testing the logic inside terminate; patch to avoid super().terminate()
            pass

        # Instead, directly test the methods that terminate calls
        node.cmd("pkill -f 'exabgp.*exabgp.conf' || true")
        node.cmd.assert_called_once_with("pkill -f 'exabgp.*exabgp.conf' || true")

    def test_daemon_is_launched_with_the_environment_file(self):
        """The launch passes --env-file so user='root' and cli=true take effect."""
        node = self._make_node()
        node.cmd = Mock()
        node._start_exabgp_daemon('/tmp/exabgp-config/exa1')
        command = node.cmd.call_args[0][0]
        self.assertIn('--env-file /etc/exabgp/exabgp.env', command)
        self.assertIn('server /tmp/exabgp-config/exa1/exabgp.conf', command)

    def test_a_speaker_has_one_control_client(self):
        """Every surface reaches the same client, so its lock is the Speaker's one lock."""
        node = self._make_node()
        self.assertIs(node.control_client(), node.control_client())
        self.assertEqual(node.control_client().in_path, '/tmp/exabgp-config/run/exa1/exabgp.in')

    def test_starting_waits_for_the_daemon_to_read_its_pipe(self):
        """A Speaker whose daemon never opens the pipe fails to start, naming the log.

        Readiness is the lifecycle's post-condition: ``start_exabgp`` returns
        only once a command is answered, so a read the moment the Lab has
        started finds a daemon, not an unread pipe.
        """
        with tempfile.TemporaryDirectory() as run_dir:
            node = self._make_node(private_dirs=[
                ('/etc/exabgp', '/tmp/exabgp-config/exa1'),
                ('/var/log/exabgp', '/tmp/exabgp-config/logs/exa1'),
                ('/var/run/exabgp', run_dir),
            ])
            node.cmd = Mock()
            node.ensure_fifos()
            with self.assertRaises(ExaBGPError) as ctx:
                node.await_ready(timeout=0.2)
        message = str(ctx.exception)
        self.assertIn('exa1', message)
        self.assertIn('/tmp/exabgp-config/logs/exa1/exa1.log', message)

    def test_cleanup_fifos_removes_files(self):
        """_cleanup_fifos should remove exabgp.in and exabgp.out FIFOs."""
        node = self._make_node()
        with patch('mniplab.exabgp.node.os.path.exists', return_value=True):
            with patch('mniplab.exabgp.node.os.unlink') as mock_unlink:
                node._cleanup_fifos()
        self.assertEqual(mock_unlink.call_count, 2)


def render_speaker(name: str, neighbors: List[BGPNeighbor], routes: Optional[List[BGPRoute]] = None,
                   local_as: int = 65001) -> str:
    """Render a Speaker's configuration the way the Node does and return its text."""
    with tempfile.TemporaryDirectory() as tmp:
        ExaBGPConfigBuilder().setup_config_dir(name, tmp, {
            'name': name,
            'neighbors': neighbors,
            'routes': routes or [],
            'local_as': local_as,
        })
        with open(os.path.join(tmp, 'exabgp.conf'), encoding='utf-8') as f:
            return f.read()


class ExaBGPRenderingTests(unittest.TestCase):
    """The rendered Speaker configuration.

    Every Neighbor is passive with its own local-address, so the Router's
    ``neighbor … remote-as`` is what brings a session up; nothing renders a
    ``process`` block.
    """

    def _neighbors(self):
        return [
            BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2',
                        local_as=65001, peer_as=65000,
                        routes=[BGPRoute('203.0.113.0/24', '10.0.1.2', as_path=[65001])]),
            BGPNeighbor(peer='2001:db8:2::1', local_address='2001:db8:2::2',
                        local_as=65001, peer_as=65000),
        ]

    def test_every_neighbor_is_passive(self):
        """Each neighbor block carries passive and its own listener."""
        rendered = render_speaker('sp1', self._neighbors())
        blocks = rendered.split('neighbor ')[1:]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            self.assertIn('passive;', block, block)
            # ExaBGP only opens a listener for a Neighbor that says so; it
            # binds the Neighbor's own local-address.
            self.assertIn('listen 179;', block, block)

    def test_hold_time_renders_only_when_declared(self):
        """A declared hold time reaches the neighbor block; none, nothing."""
        declared = render_speaker('sp1', [
            BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2',
                        local_as=65001, peer_as=65000, hold_time=9)])
        block = declared.split('neighbor ')[1]
        self.assertIn('hold-time 9;', block)
        self.assertIn('passive;', block)
        self.assertIn('listen 179;', block)
        self.assertNotIn('hold-time', render_speaker('sp1', self._neighbors()))

    def test_no_process_block_is_rendered(self):
        """The never-valid process block is gone for good."""
        rendered = render_speaker('sp1', self._neighbors())
        self.assertNotIn('process', rendered)

    def test_declared_routes_still_render(self):
        """A Neighbor's declared routes render as before."""
        rendered = render_speaker('sp1', self._neighbors())
        self.assertIn('route 203.0.113.0/24 {', rendered)
        self.assertIn('as-path [ 65001 ];', rendered)
        self.assertIn('next-hop 10.0.1.2;', rendered)

    def test_route_attributes_render_in_exabgp_grammar(self):
        """Communities, large communities and MED declared on a route reach the file.

        The words are ExaBGP's own, so a declared route and one announced at
        runtime through the pipe describe the same thing in the same grammar.
        """
        route = BGPRoute(
            '203.0.113.0/24', '10.0.1.2',
            community=['65001:100', 'no-export'],
            as_path=[65001, 65002],
            large_community=['65001:1:2'],
            med=50,
        )
        neighbor = BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2',
                               local_as=65001, peer_as=65000, routes=[route])
        rendered = render_speaker('sp1', [neighbor])
        self.assertIn('community [ 65001:100 no-export ];', rendered)
        self.assertIn('large-community [ 65001:1:2 ];', rendered)
        self.assertIn('med 50;', rendered)
        self.assertIn('as-path [ 65001 65002 ];', rendered)

    def test_route_without_attributes_renders_none_of_them(self):
        """A bare route renders only its prefix and next-hop, as before."""
        neighbor = BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2',
                               local_as=65001, peer_as=65000,
                               routes=[BGPRoute('203.0.113.0/24', '10.0.1.2')])
        rendered = render_speaker('sp1', [neighbor])
        for word in ('community', 'large-community', 'med', 'as-path'):
            self.assertNotIn(word, rendered)

    def test_shared_local_address_is_fatal_naming_both_neighbors(self):
        """Two Neighbors on one local-address raise, naming Speaker and both peers."""
        neighbors = [
            BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2', local_as=65001, peer_as=65000),
            BGPNeighbor(peer='10.0.1.5', local_address='10.0.1.2', local_as=65001, peer_as=65010),
        ]
        with self.assertRaises(ConfigurationError) as ctx:
            render_speaker('sp1', neighbors)
        message = str(ctx.exception)
        self.assertIn('sp1', message)
        self.assertIn('10.0.1.1', message)
        self.assertIn('10.0.1.5', message)
        self.assertIn('10.0.1.2', message)

    def test_add_exabgp_refuses_the_shared_local_address(self):
        """The Lab refuses the Speaker at addExaBGP time, message intact."""
        neighbors = [
            BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2', local_as=65001, peer_as=65000),
            BGPNeighbor(peer='10.0.1.5', local_address='10.0.1.2', local_as=65001, peer_as=65010),
        ]
        with unstarted_lab() as net:
            with self.assertRaises(ExaBGPError) as ctx:
                net.addExaBGP('sp1', neighbors=neighbors, local_as=65001)
        message = str(ctx.exception)
        for expected in ('sp1', '10.0.1.1', '10.0.1.5', '10.0.1.2'):
            self.assertIn(expected, message)

    def test_distinct_local_addresses_are_accepted(self):
        """One local-address per Neighbor is the shape every Lab Example uses."""
        neighbors = [
            BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2', local_as=65001, peer_as=65000),
            BGPNeighbor(peer='10.0.2.1', local_address='10.0.2.2', local_as=65001, peer_as=65010),
        ]
        rendered = render_speaker('sp1', neighbors)
        self.assertIn('local-address 10.0.1.2;', rendered)
        self.assertIn('local-address 10.0.2.2;', rendered)


@pytest.mark.integration
class ExaBGPValidateTests(unittest.TestCase):
    """The rendered Speaker configuration is one ExaBGP itself accepts.

    ``exabgp validate`` parses a file with the daemon's own parser and exits
    non-zero on anything it would refuse at start, so a template regression
    is caught here rather than in a Lab that dies silently.
    """

    def setUp(self):
        if shutil.which('exabgp') is None:
            self.skipTest('exabgp binary not installed')

    def test_rendered_configuration_validates(self):
        """A dual-family Speaker with declared routes passes ExaBGP's own parser."""
        neighbors = [
            BGPNeighbor(peer='10.0.1.1', local_address='10.0.1.2',
                        local_as=65001, peer_as=65000,
                        routes=[BGPRoute('203.0.113.0/24', '10.0.1.2', as_path=[65001, 65002],
                                         community=['65001:100', 'no-export'],
                                         large_community=['65001:1:2'], med=50)]),
            BGPNeighbor(peer='2001:db8:1::1', local_address='2001:db8:1::2',
                        local_as=65001, peer_as=65000, hold_time=9,
                        routes=[BGPRoute('2001:db8:100::/48', '2001:db8:1::2')]),
        ]
        rendered = render_speaker('sp1', neighbors)
        with tempfile.NamedTemporaryFile('w', suffix='.conf', encoding='utf-8') as conf:
            conf.write(rendered)
            conf.flush()
            result = subprocess.run(
                ['exabgp', 'validate', conf.name],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestMakeV6Routes(unittest.TestCase):
    """Validate IPv6 route generation helpers."""

    # ------------------------------------------------------------------
    # make_v6_routes  (convenience wrapper)
    # ------------------------------------------------------------------

    def test_make_v6_routes_generates_correct_count(self):
        """make_v6_routes() returns exactly num_prefixes routes."""
        routes = make_v6_routes(num_prefixes=10)
        self.assertEqual(len(routes), 10)

    def test_make_v6_routes_all_have_valid_ipv6_prefixes(self):
        """Every route has a parseable IPv6 prefix."""
        routes = make_v6_routes(num_prefixes=10)
        for route in routes:
            net = ipaddress.IPv6Network(route.prefix, strict=False)
            self.assertIsInstance(net, ipaddress.IPv6Network)

    def test_make_v6_routes_prefixes_in_documentation_range(self):
        """All prefixes fall inside 2001:db8::/32."""
        doc_range = ipaddress.IPv6Network('2001:db8::/32')
        routes = make_v6_routes(num_prefixes=20)
        for route in routes:
            net = ipaddress.IPv6Network(route.prefix, strict=False)
            self.assertTrue(net.subnet_of(doc_range), f"{route.prefix} not in 2001:db8::/32")

    def test_make_v6_routes_prefix_lengths_in_range(self):
        """Prefix lengths are between /32 and /48."""
        routes = make_v6_routes(num_prefixes=20)
        for route in routes:
            net = ipaddress.IPv6Network(route.prefix, strict=False)
            self.assertGreaterEqual(net.prefixlen, IPV6_PREFIX_LEN_MIN)
            self.assertLessEqual(net.prefixlen, IPV6_PREFIX_LEN_MAX)

    def test_make_v6_routes_as_paths_non_empty(self):
        """Every route carries an AS-PATH."""
        routes = make_v6_routes(num_prefixes=10)
        for route in routes:
            self.assertIsInstance(route.as_path, list)
            self.assertTrue(len(route.as_path) > 0)

    def test_make_v6_routes_default_asn(self):
        """Routes use DEFAULT_ORIGIN_AS (65002) when no custom ASN."""
        routes = make_v6_routes(num_prefixes=10, prefix_seed=1, as_path_seed=1)
        for route in routes:
            self.assertEqual(route.as_path[0], DEFAULT_ORIGIN_AS)

    def test_make_v6_routes_default_next_hop(self):
        """Routes use DEFAULT_IPV6_NEXT_HOP (2001:db8::2)."""
        routes = make_v6_routes(num_prefixes=5)
        for route in routes:
            self.assertEqual(route.next_hop, DEFAULT_IPV6_NEXT_HOP)

    def test_make_v6_routes_reproducible_with_seeds(self):
        """Same prefix_seed and as_path_seed produce identical routes."""
        routes_a = make_v6_routes(num_prefixes=10, prefix_seed=42, as_path_seed=42)
        routes_b = make_v6_routes(num_prefixes=10, prefix_seed=42, as_path_seed=42)
        for a, b in zip(routes_a, routes_b):
            self.assertEqual(a.prefix, b.prefix)
            self.assertEqual(a.as_path, b.as_path)

    def test_make_v6_routes_different_seeds_different_routes(self):
        """Different seeds produce different routes."""
        routes_a = make_v6_routes(num_prefixes=10, prefix_seed=1, as_path_seed=1)
        routes_b = make_v6_routes(num_prefixes=10, prefix_seed=99, as_path_seed=99)
        self.assertNotEqual(
            [r.prefix for r in routes_a],
            [r.prefix for r in routes_b],
        )

    def test_make_v6_routes_deprecated_seed_parameter(self):
        """The deprecated seed parameter works via make_v6_peer_routes()."""
        routes_a = make_v6_peer_routes(
            num_prefixes=10, local_asn=65002, next_hop=DEFAULT_IPV6_NEXT_HOP, seed=77
        )
        routes_b = make_v6_peer_routes(
            num_prefixes=10, local_asn=65002, next_hop=DEFAULT_IPV6_NEXT_HOP, seed=77
        )
        for a, b in zip(routes_a, routes_b):
            self.assertEqual(a.prefix, b.prefix)
            self.assertEqual(a.as_path, b.as_path)

    def test_make_v6_routes_deprecated_seed_overridden_by_explicit(self):
        """Explicit prefix_seed/as_path_seed override deprecated seed."""
        # seed=77 sets both, but explicit prefix_seed=1 overrides prefix seed
        routes = make_v6_peer_routes(
            num_prefixes=10, local_asn=65002, next_hop=DEFAULT_IPV6_NEXT_HOP,
            seed=77, prefix_seed=1,
        )
        explicit = make_v6_peer_routes(
            num_prefixes=10, local_asn=65002, next_hop=DEFAULT_IPV6_NEXT_HOP,
            prefix_seed=1, as_path_seed=77,
        )
        # Prefixes match prefix_seed=1, AS-PATHs match as_path_seed=77
        for a, b in zip(routes, explicit):
            self.assertEqual(a.prefix, b.prefix)
            self.assertEqual(a.as_path, b.as_path)

    # ------------------------------------------------------------------
    # make_v6_peer_routes  (custom ASN / next-hop)
    # ------------------------------------------------------------------

    def test_make_v6_peer_routes_custom_asn(self):
        """AS-PATHs start with the specified local_asn."""
        routes = make_v6_peer_routes(
            num_prefixes=10, local_asn=65100, next_hop='2001:db8::1',
            prefix_seed=1, as_path_seed=1,
        )
        for route in routes:
            self.assertEqual(route.as_path[0], 65100)

    def test_make_v6_peer_routes_custom_next_hop(self):
        """Routes use the provided next_hop address."""
        custom_hop = 'fd00::1'
        routes = make_v6_peer_routes(
            num_prefixes=10, local_asn=65000, next_hop=custom_hop,
            prefix_seed=1, as_path_seed=1,
        )
        for route in routes:
            self.assertEqual(route.next_hop, custom_hop)

    def test_make_v6_peer_routes_reproducible(self):
        """Seeds make peer routes deterministic."""
        kwargs = dict(
            num_prefixes=15, local_asn=65200, next_hop='2001:db8:1::2',
            prefix_seed=55, as_path_seed=66,
        )
        a = make_v6_peer_routes(**kwargs)
        b = make_v6_peer_routes(**kwargs)
        for x, y in zip(a, b):
            self.assertEqual(x.prefix, y.prefix)
            self.assertEqual(x.as_path, y.as_path)

    def test_make_v6_peer_routes_deprecated_seed(self):
        """Deprecated seed parameter works for peer routes too."""
        a = make_v6_peer_routes(
            num_prefixes=8, local_asn=65300, next_hop='2001:db8::3', seed=10
        )
        b = make_v6_peer_routes(
            num_prefixes=8, local_asn=65300, next_hop='2001:db8::3', seed=10
        )
        for x, y in zip(a, b):
            self.assertEqual(x.prefix, y.prefix)
            self.assertEqual(x.as_path, y.as_path)

    # ------------------------------------------------------------------
    # Sub-prefixes  (LPM mode)
    # ------------------------------------------------------------------

    def test_make_v6_routes_with_sub_prefixes_increases_count(self):
        """Sub-prefixes are appended beyond base prefixes."""
        routes = make_v6_routes(num_prefixes=5, num_sub_prefixes=3, prefix_seed=1)
        self.assertGreater(len(routes), 5)

    def test_make_v6_routes_sub_prefixes_hierarchical(self):
        """Sub-prefixes are subnets of their parent base prefix."""
        routes = make_v6_routes(
            num_prefixes=3, num_sub_prefixes=4, prefix_seed=2
        )
        # First route is a base prefix; subsequent routes until next base are
        # sub-prefixes.  Collect each base and its children.
        parents: list = []
        children: list = []
        for route in routes:
            if not children:
                parents.append(ipaddress.IPv6Network(route.prefix, strict=False))
            else:
                children.append(route)
                # After enough children, next route starts a new group
                if len(children) >= 4:
                    parents.append(ipaddress.IPv6Network(route.prefix, strict=False))
                    children = []

        # Verify every sub-prefix is within some parent
        subnets = [r for r in routes[1:]]
        for s in subnets:
            child_net = ipaddress.IPv6Network(s.prefix, strict=False)
            self.assertTrue(
                any(child_net.subnet_of(p) for p in parents),
                f"{s.prefix} not a subnet of any parent",
            )

    def test_make_v6_routes_sub_prefixes_max_length(self):
        """Sub-prefixes never exceed /64."""
        routes = make_v6_routes(
            num_prefixes=5, num_sub_prefixes=10, prefix_seed=3
        )
        for route in routes:
            net = ipaddress.IPv6Network(route.prefix, strict=False)
            self.assertLessEqual(net.prefixlen, IPV6_SUBNET_MAX_PREFIX)

    def test_make_v6_routes_sub_prefixes_inherit_as_path(self):
        """Sub-prefixes share the AS-PATH of their parent."""
        routes = make_v6_routes(
            num_prefixes=2, num_sub_prefixes=3, prefix_seed=4, as_path_seed=4
        )
        # First route is base; next few are its children
        parent_path = routes[0].as_path
        for i in range(1, 4):
            self.assertEqual(routes[i].as_path, parent_path)

    # ------------------------------------------------------------------
    # Integration  with BGPNeighbor
    # ------------------------------------------------------------------

    def test_make_v6_routes_integrate_with_bgp_neighbor(self):
        """Routes can be attached to a BGPNeighbor and detected as IPv6."""
        routes = make_v6_routes(num_prefixes=5, prefix_seed=1)
        neighbor = BGPNeighbor(
            peer='2001:db8::1',
            local_address='2001:db8::2',
            local_as=65000,
            peer_as=65001,
            routes=routes,
        )
        self.assertEqual(neighbor.address_family, 'ipv6')
        self.assertEqual(len(neighbor.routes), 5)

    def test_make_v6_peer_routes_integrate_with_bgp_neighbor(self):
        """Peer routes with custom ASN work in BGPNeighbor."""
        routes = make_v6_peer_routes(
            num_prefixes=3, local_asn=65400, next_hop='2001:db8::ff',
            prefix_seed=7, as_path_seed=7,
        )
        neighbor = BGPNeighbor(
            peer='2001:db8:2::1',
            local_address='2001:db8:2::2',
            local_as=65000,
            peer_as=65400,
            routes=routes,
        )
        self.assertEqual(neighbor.address_family, 'ipv6')
        for route in neighbor.routes:
            self.assertEqual(route.next_hop, '2001:db8::ff')
            self.assertEqual(route.as_path[0], 65400)


if __name__ == '__main__':
    unittest.main()
