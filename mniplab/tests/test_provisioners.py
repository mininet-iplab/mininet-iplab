"""Unit tests for mniplab.provisioners — node setup logic."""

import os
import shutil
import tempfile
import unittest
from typing import List, Optional, Tuple, Union
from unittest.mock import Mock, patch

from mniplab.errors import ConfigurationError
from mniplab.frrouter.config import rpki_module_installed
from mniplab.persistent_config import CONFIG_COPY_ENV
from mniplab.tests.fakes import FakeLab
from mniplab.tests.labs import unstarted_lab
from mniplab.provisioners import (
    FRRProvisioner,
    ExaBGPProvisioner,
    NATProvisioner,
    VRFProvisioner,
)


class FRRProvisionerNormalizeDaemonsTests(unittest.TestCase):
    """normalize_daemons should convert various inputs to list."""

    def test_none_returns_empty(self):
        self.assertEqual(FRRProvisioner.normalize_daemons(None), [])

    def test_string_returns_list(self):
        self.assertEqual(FRRProvisioner.normalize_daemons('zebra'), ['zebra'])

    def test_list_returns_copy(self):
        src = ['zebra', 'ospfd']
        result = FRRProvisioner.normalize_daemons(src)
        self.assertEqual(result, src)
        self.assertIsNot(result, src)

    def test_tuple_returns_list(self):
        self.assertEqual(FRRProvisioner.normalize_daemons(('zebra',)), ['zebra'])


class FRRProvisionerParseProtocolsTests(unittest.TestCase):
    """parse_protocols validates protocol strings and maps to daemons."""

    def test_single_protocol(self):
        result = FRRProvisioner.parse_protocols('OSPF', 'r1', [])
        self.assertEqual(result, ['ospfd'])

    def test_comma_separated_protocols(self):
        result = FRRProvisioner.parse_protocols('OSPF,BGP', 'r1', [])
        self.assertEqual(result, ['ospfd', 'bgpd'])

    def test_pipe_separated_protocols(self):
        result = FRRProvisioner.parse_protocols('OSPF|BGP', 'r1', [])
        self.assertEqual(result, ['ospfd', 'bgpd'])

    def test_list_input(self):
        result = FRRProvisioner.parse_protocols(['OSPF', 'BGP'], 'r1', [])
        self.assertEqual(result, ['ospfd', 'bgpd'])

    def test_deduplicates_existing_daemons(self):
        result = FRRProvisioner.parse_protocols('OSPF', 'r1', ['ospfd'])
        self.assertEqual(result, ['ospfd'])

    def test_unknown_protocol_raises(self):
        with self.assertRaises(ConfigurationError) as ctx:
            FRRProvisioner.parse_protocols('UNKNOWN', 'r1', [])
        self.assertIn("Unknown routing protocol 'UNKNOWN'", str(ctx.exception))


class FRRProvisionerCustomizePrivateDirsTests(unittest.TestCase):
    """customize_private_dirs replaces target path mappings."""

    def test_replaces_target_tuple(self):
        dirs = [('/etc/frr', '/tmp/old'), ('/var/log', '/tmp/logs')]
        result = FRRProvisioner.customize_private_dirs(dirs, '/etc/frr', '/tmp/new')
        self.assertEqual(result, [('/etc/frr', '/tmp/new'), ('/var/log', '/tmp/logs')])

    def test_leaves_non_target_untouched(self):
        dirs = [('/etc/frr', '/tmp/frr'), ('/var/log', '/tmp/logs')]
        result = FRRProvisioner.customize_private_dirs(dirs, '/etc/exabgp', '/tmp/exa')
        self.assertEqual(result, dirs)

    def test_leaves_string_entries(self):
        dirs = ['/tmp/foo', ('/etc/frr', '/tmp/frr')]
        result = FRRProvisioner.customize_private_dirs(dirs, '/etc/frr', '/tmp/new')
        self.assertEqual(result, ['/tmp/foo', ('/etc/frr', '/tmp/new')])


class ExaBGPProvisionerResolveConfigTests(unittest.TestCase):
    """resolve_config_directory finds ExaBGP config path from privateDirs."""

    def test_resolves_tuple_mapping(self):
        dirs = [('/etc/exabgp', '/tmp/exabgp/%(name)s')]
        result = ExaBGPProvisioner.resolve_config_directory(dirs, 'exa1')
        self.assertEqual(result, '/tmp/exabgp/exa1')

    def test_missing_mapping_raises(self):
        dirs = [('/var/log', '/tmp/logs')]
        with self.assertRaises(ConfigurationError) as ctx:
            ExaBGPProvisioner.resolve_config_directory(dirs, 'exa1')
        self.assertIn("Missing /etc/exabgp", str(ctx.exception))


class ExaBGPProvisionerSetupDirectoriesTests(unittest.TestCase):
    """setup_directories creates log and run directories."""

    @patch('mniplab.provisioners.ensure_directory')
    def test_with_explicit_exabgp_dir(self, mock_ensure):
        log, run = ExaBGPProvisioner.setup_directories([], '/tmp/exabgp/%(name)s', 'exa1')
        self.assertEqual(log, '/tmp/exabgp/logs/exa1')
        self.assertEqual(run, '/tmp/exabgp/run/exa1')
        self.assertEqual(mock_ensure.call_count, 2)

    @patch('mniplab.provisioners.ensure_directory')
    def test_with_private_dirs(self, mock_ensure):
        dirs = [
            ('/var/log/exabgp', '/tmp/logs/%(name)s'),
            ('/var/run/exabgp', '/tmp/run/%(name)s'),
        ]
        log, run = ExaBGPProvisioner.setup_directories(dirs, None, 'exa1')
        self.assertEqual(log, '/tmp/logs/exa1')
        self.assertEqual(run, '/tmp/run/exa1')

    @patch('mniplab.provisioners.ensure_directory')
    def test_none_when_no_mapping(self, mock_ensure):
        log, run = ExaBGPProvisioner.setup_directories([], None, 'exa1')
        self.assertIsNone(log)
        self.assertIsNone(run)
        mock_ensure.assert_not_called()


class ExaBGPProvisionerPrepareVariablesTests(unittest.TestCase):
    """prepare_variables builds template context dict."""

    def test_basic_variables(self):
        result = ExaBGPProvisioner.prepare_variables('exa1', neighbors=['n1'], routes=['r1'])
        self.assertEqual(result['name'], 'exa1')
        self.assertEqual(result['neighbors'], ['n1'])
        self.assertEqual(result['routes'], ['r1'])
        self.assertNotIn('process', result)
        self.assertIsNone(result['local_as'])


class NATProvisionerTests(unittest.TestCase):
    """NATProvisioner auto-configures default routes for MasqueradeNAT peers."""

    def test_node2_gets_default_route_from_nat(self):
        from mniplab.nodelib import MasqueradeNAT

        nat = Mock(spec=MasqueradeNAT)
        nat._peer_default_gw = True
        peer = Mock()
        peer.params = {}

        link = Mock()
        link.intf1.ip = '10.0.0.1/24'
        link.intf1.params = {}
        link.intf2 = Mock()

        NATProvisioner.configure_default_route(link, nat, peer)
        self.assertEqual(peer.params['defaultRoute'], 'via 10.0.0.1')

    def test_no_override_if_default_route_already_set(self):
        from mniplab.nodelib import MasqueradeNAT

        nat = Mock(spec=MasqueradeNAT)
        nat._peer_default_gw = True
        peer = Mock()
        peer.params = {'defaultRoute': 'via 192.168.1.1'}

        link = Mock()
        link.intf1.ip = '10.0.0.1/24'
        link.intf1.params = {}
        link.intf2 = Mock()

        NATProvisioner.configure_default_route(link, nat, peer)
        self.assertEqual(peer.params['defaultRoute'], 'via 192.168.1.1')

    def test_no_action_when_nat_peer_default_gw_false(self):
        from mniplab.nodelib import MasqueradeNAT

        nat = Mock(spec=MasqueradeNAT)
        nat._peer_default_gw = False
        peer = Mock()
        peer.params = {}

        link = Mock()
        link.intf1 = Mock()
        link.intf2 = Mock()

        NATProvisioner.configure_default_route(link, nat, peer)
        self.assertNotIn('defaultRoute', peer.params)


class VRFProvisionerTests(unittest.TestCase):
    """VRFProvisioner attaches interfaces to VRFs on FRRouters."""

    def test_attach_intf1_to_vrf(self):
        from mniplab.router import FRRouter

        router = Mock(spec=FRRouter)
        router.attach_interface = Mock()

        link = Mock()
        link.intf1.name = 'r1-eth0'

        VRFProvisioner.handle_attachments(link, {'vrf': 'vrf-red'}, {}, router, Mock())
        router.attach_interface.assert_called_once_with('r1-eth0', 'vrf-red')

    def test_attach_intf2_to_vrf(self):
        from mniplab.router import FRRouter

        router = Mock(spec=FRRouter)
        router.attach_interface = Mock()

        link = Mock()
        link.intf2.name = 'r2-eth0'

        VRFProvisioner.handle_attachments(link, {}, {'vrf': 'vrf-blue'}, Mock(), router)
        router.attach_interface.assert_called_once_with('r2-eth0', 'vrf-blue')

    def test_no_vrf_key_no_action(self):
        from mniplab.router import FRRouter

        router = Mock(spec=FRRouter)
        router.attach_interface = Mock()

        link = Mock()
        VRFProvisioner.handle_attachments(link, {}, {}, router, Mock())
        router.attach_interface.assert_not_called()

    def test_non_router_ignores_vrf(self):
        host = Mock()  # Not an FRRouter

        link = Mock()
        VRFProvisioner.handle_attachments(link, {'vrf': 'vrf-red'}, {}, host, Mock())
        # Should not raise; attach_interface doesn't exist on host


if __name__ == '__main__':
    unittest.main()


class TestFRRConfigDirFailures(unittest.TestCase):
    """A Router Config directory that cannot be set up is reported, not swallowed."""

    def setUp(self):
        self.net = FakeLab()

    def test_setup_failure_raises_naming_router_and_path(self):
        private_dirs = [(FRRProvisioner.ETCFRR_PATH, '/nonexistent-root/%(name)s')]

        with patch('mniplab.frrouter.config.FRRConfigBuilder.setup_config_dir',
                   side_effect=OSError('Permission denied')):
            with self.assertRaises(ConfigurationError) as ctx:
                FRRProvisioner.setup_config_dir('r1', private_dirs, net=self.net)

        message = str(ctx.exception)
        self.assertIn('r1', message)
        self.assertIn('/nonexistent-root/r1', message)
        self.assertIn('Permission denied', message)

    def test_placeholder_is_resolved_in_error_message(self):
        private_dirs = [(FRRProvisioner.ETCFRR_PATH, '/bad/%(name)s/frr')]

        with patch('mniplab.frrouter.config.FRRConfigBuilder.setup_config_dir',
                   side_effect=ConfigurationError('template missing')):
            with self.assertRaises(ConfigurationError) as ctx:
                FRRProvisioner.setup_config_dir('core1', private_dirs, net=self.net)

        self.assertIn('/bad/core1/frr', str(ctx.exception))
        self.assertNotIn('%(name)s', str(ctx.exception))

    def test_success_does_not_raise(self):
        private_dirs = [(FRRProvisioner.ETCFRR_PATH, '/tmp/mniplab-test/%(name)s')]

        with patch('mniplab.frrouter.config.FRRConfigBuilder.setup_config_dir'):
            FRRProvisioner.setup_config_dir('r1', private_dirs, net=self.net)

    def test_no_frr_mapping_is_not_an_error(self):
        FRRProvisioner.setup_config_dir('r1', ['/var/log'], net=self.net)


class EphemeralFRRConfigResetTests(unittest.TestCase):
    """A Lab's ephemeral FRR config is built from its own declaration alone.

    The ephemeral default (the Lab directory's ``nodes/<name>/etc/frr``) is
    documented as reset on each run, but a previous Lab of the same Node
    name could leave files there — an ``frr.conf`` for another topology, or
    an included snippet. Only the default is reset: a caller-supplied
    ``frr_dir`` keeps its ``frr.conf``, which an Instructor version-controls.
    """

    def setUp(self):
        # The reset applies to the Lab's default binding only, so these
        # exercise it at that exact path.
        self.net = FakeLab()
        self.template = self.net.frr_config_template()
        self.config_dir = self.net.frr_config_dir('r1')

    def _daemons_line(self, config_dir: str, daemon: str) -> Optional[str]:
        with open(os.path.join(config_dir, 'daemons')) as f:
            for line in f:
                if line.startswith(f'{daemon}='):
                    return line.strip()
        return None

    def test_ephemeral_config_is_regenerated_over_a_previous_labs_files(self):
        private_dirs = [(FRRProvisioner.ETCFRR_PATH, self.template)]
        config_dir = self.config_dir

        FRRProvisioner.setup_config_dir('r1', private_dirs, net=self.net, daemons=['zebra'])
        self.assertEqual(self._daemons_line(config_dir, 'ospfd'), 'ospfd=no')

        FRRProvisioner.setup_config_dir('r1', private_dirs, net=self.net, daemons=['zebra', 'ospfd'])
        self.assertEqual(self._daemons_line(config_dir, 'ospfd'), 'ospfd=yes')

    def test_ephemeral_reset_drops_files_the_previous_lab_left_behind(self):
        private_dirs = [(FRRProvisioner.ETCFRR_PATH, self.template)]
        config_dir = self.config_dir
        os.makedirs(config_dir, exist_ok=True)
        leftover = os.path.join(config_dir, 'stale-from-a-previous-lab.conf')
        with open(leftover, 'w') as f:
            f.write('router ospf\n')

        FRRProvisioner.setup_config_dir('r1', private_dirs, net=self.net, daemons=['zebra'])

        self.assertFalse(os.path.exists(leftover))
        self.assertTrue(os.path.isfile(os.path.join(config_dir, 'frr.conf')))

    def test_only_the_default_binding_is_ephemeral(self):
        self.assertTrue(
            FRRProvisioner.is_ephemeral_config_template(self.template, net=self.net)
        )
        self.assertTrue(
            FRRProvisioner.is_ephemeral_config_template(
                self.template.replace('/nodes/', '//nodes/'), net=self.net
            )
        )
        self.assertFalse(
            FRRProvisioner.is_ephemeral_config_template(
                './frr-config/ospf-lab/%(name)s', net=self.net
            )
        )

    def test_a_persistent_frr_dir_inside_the_lab_directory_is_not_reset(self):
        """An Instructor may point frr_dir anywhere; only the default resets."""
        instructor_template = os.path.join(self.net.lab_dir, 'mylab', '%(name)s')
        self.assertFalse(
            FRRProvisioner.is_ephemeral_config_template(instructor_template, net=self.net)
        )

        instructor_dir = instructor_template % {'name': 'r1'}
        os.makedirs(instructor_dir)
        with open(os.path.join(instructor_dir, 'frr.conf'), 'w') as f:
            f.write('instructor-authored\n')

        FRRProvisioner.setup_config_dir(
            'r1',
            [(FRRProvisioner.ETCFRR_PATH, instructor_template)],
            net=self.net,
            daemons=['zebra', 'ospfd'],
        )

        with open(os.path.join(instructor_dir, 'frr.conf')) as f:
            self.assertEqual(f.read(), 'instructor-authored\n')


class ToolOwnedFRRFilesTests(unittest.TestCase):
    """A Seed holds only ``frr.conf``; every other FRR file comes from the Protocols.

    ``daemons``, ``vtysh.conf`` and ``support_bundle_commands.conf`` are
    rendered on every start, over any copy present, so a hand edit can never
    drift from what the Router declares. ``frr.conf`` is the Instructor's
    and Learner's file: rendered only when missing, otherwise never touched.
    """

    OWNED = ('daemons', 'vtysh.conf', 'support_bundle_commands.conf')

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seed_template = os.path.join(self.tmp.name, '%(name)s')
        self.seed_dir = os.path.join(self.tmp.name, 'r1')
        os.makedirs(self.seed_dir)
        self._write('frr.conf', 'router ospf\n ospf router-id 1.1.1.1\n')

    def _write(self, filename: str, text: str) -> None:
        with open(os.path.join(self.seed_dir, filename), 'w') as f:
            f.write(text)

    def _read(self, filename: str) -> str:
        with open(os.path.join(self.seed_dir, filename)) as f:
            return f.read()

    def _add_r1(self) -> None:
        with unstarted_lab() as net:
            net.addRouter('r1', proto='OSPF', frr_dir=self.seed_template)

    def test_a_seed_holding_only_frr_conf_gets_the_files_its_protocols_need(self) -> None:
        self._add_r1()

        self.assertEqual(sorted(os.listdir(self.seed_dir)), sorted(('frr.conf', *self.OWNED)))
        self.assertIn('ospfd=yes', self._read('daemons').splitlines())
        self.assertIn('bfdd=no', self._read('daemons').splitlines())
        self.assertIn('hostname r1', self._read('vtysh.conf').splitlines())
        self.assertEqual(self._read('frr.conf'), 'router ospf\n ospf router-id 1.1.1.1\n')

    def test_hand_edited_owned_files_are_rendered_again_and_frr_conf_is_kept(self) -> None:
        self._write('daemons', 'ospfd=yes\nbfdd=yes\n')
        self._write('vtysh.conf', 'hostname edited\n')
        self._write('support_bundle_commands.conf', 'edited\n')

        self._add_r1()

        self.assertIn('bfdd=no', self._read('daemons').splitlines())
        self.assertIn('ospfd=yes', self._read('daemons').splitlines())
        self.assertIn('hostname r1', self._read('vtysh.conf').splitlines())
        self.assertNotEqual(self._read('support_bundle_commands.conf'), 'edited\n')
        self.assertEqual(self._read('frr.conf'), 'router ospf\n ospf router-id 1.1.1.1\n')


class RPKIModuleDaemonsTests(unittest.TestCase):
    """bgpd loads FRR's RPKI module only where the Host machine carries it.

    The module is an optional FRR package. ``-M rpki`` on a machine without
    it stops bgpd from starting, so a Lab without RPKI must render the
    ``bgpd_options`` line exactly as it was before the module was offered.
    """

    PLAIN_OPTIONS = 'bgpd_options="   --daemon -A 127.0.0.1"'
    RPKI_OPTIONS = 'bgpd_options="   --daemon -A 127.0.0.1 -M rpki"'

    def setUp(self):
        self.net = FakeLab()
        self.template = self.net.frr_config_template()
        self.config_dir = self.net.frr_config_dir('r1')

    def _bgpd_options(self, installed: bool) -> Optional[str]:
        private_dirs: List[Union[str, Tuple[str, str]]] = [
            (FRRProvisioner.ETCFRR_PATH, self.template)]
        with patch('mniplab.frrouter.config.rpki_module_installed',
                   return_value=installed):
            FRRProvisioner.setup_config_dir('r1', private_dirs, net=self.net,
                                            daemons=['bgpd'])
        with open(os.path.join(self.config_dir, 'daemons')) as f:
            for line in f:
                if line.startswith('bgpd_options='):
                    return line.strip()
        return None

    def test_bgpd_loads_the_rpki_module_when_it_is_installed(self):
        self.assertEqual(self._bgpd_options(installed=True), self.RPKI_OPTIONS)

    def test_bgpd_options_omit_the_rpki_module_when_it_is_absent(self):
        self.assertEqual(self._bgpd_options(installed=False), self.PLAIN_OPTIONS)

    def test_rpki_module_installed_finds_the_module_in_a_multiarch_dir(self):
        with tempfile.TemporaryDirectory() as lib_root:
            self.assertFalse(rpki_module_installed(lib_root))

            multiarch = os.path.join(lib_root, 'x86_64-linux-gnu', 'frr', 'modules')
            os.makedirs(multiarch)
            with open(os.path.join(multiarch, 'bgpd_bmp.so'), 'w'):
                pass
            self.assertFalse(rpki_module_installed(lib_root))

            with open(os.path.join(multiarch, 'bgpd_rpki.so'), 'w'):
                pass
            self.assertTrue(rpki_module_installed(lib_root))

    def test_rpki_module_installed_finds_the_module_in_the_plain_frr_dir(self):
        with tempfile.TemporaryDirectory() as lib_root:
            modules = os.path.join(lib_root, 'frr', 'modules')
            os.makedirs(modules)
            with open(os.path.join(modules, 'bgpd_rpki.so'), 'w'):
                pass
            self.assertTrue(rpki_module_installed(lib_root))


class GroupCopyOfPersistentConfigTests(unittest.TestCase):
    """Under the manager, a Router's Persistent Config is the Group's copy of the seed.

    The Lab Example's ``frr_dir`` is the seed.  A Lab process launched for a
    Group is handed the Group's copy directory in the environment; its first
    start copies each Router's seed into it, later starts resume the copy,
    and the seed is never written.  Without that directory — CLI Mode — the
    seed is used directly, as the Instructor authoring it expects.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seed_template = os.path.join(self.tmp.name, 'seed', '%(name)s')
        self.seed_dir = os.path.join(self.tmp.name, 'seed', 'r1')
        self.copy_root = os.path.join(self.tmp.name, 'copy')
        os.makedirs(self.seed_dir)
        for filename in ('daemons', 'frr.conf'):
            with open(os.path.join(self.seed_dir, filename), 'w') as f:
                f.write('instructor-authored\n')

    def _read(self, directory: str, filename: str) -> str:
        with open(os.path.join(directory, filename)) as f:
            return f.read()

    def _add_r1_for_the_group(self) -> str:
        with patch.dict(os.environ, {CONFIG_COPY_ENV: self.copy_root}):
            with unstarted_lab() as net:
                frr_dir = net.addRouter('r1', proto='OSPF', frr_dir=self.seed_template).get_frr_dir()
                assert frr_dir is not None
                return frr_dir

    def test_the_first_start_seeds_the_copy_and_leaves_the_seed_alone(self) -> None:
        frr_dir = self._add_r1_for_the_group()

        self.assertEqual(frr_dir, os.path.join(self.copy_root, 'r1'))
        self.assertEqual(self._read(frr_dir, 'frr.conf'), 'instructor-authored\n')
        self.assertIn('ospfd=yes', self._read(frr_dir, 'daemons'))
        self.assertEqual(sorted(os.listdir(self.seed_dir)), ['daemons', 'frr.conf'])
        self.assertEqual(self._read(self.seed_dir, 'daemons'), 'instructor-authored\n')
        self.assertEqual(self._read(self.seed_dir, 'frr.conf'), 'instructor-authored\n')

    def test_a_later_start_resumes_the_copy(self) -> None:
        frr_dir = self._add_r1_for_the_group()
        with open(os.path.join(frr_dir, 'frr.conf'), 'w') as f:
            f.write('written by the Group\n')  # what `write memory` leaves

        again = self._add_r1_for_the_group()

        self.assertEqual(again, frr_dir)
        self.assertEqual(self._read(again, 'frr.conf'), 'written by the Group\n')
        self.assertEqual(self._read(self.seed_dir, 'frr.conf'), 'instructor-authored\n')

    def test_a_missing_seed_renders_the_copy_from_templates(self) -> None:
        shutil.rmtree(self.seed_dir)

        frr_dir = self._add_r1_for_the_group()

        self.assertIn('ospfd=yes', self._read(frr_dir, 'daemons'))
        self.assertFalse(os.path.exists(self.seed_dir))

    def test_without_a_copy_directory_the_seed_is_used_directly(self) -> None:
        with patch.dict(os.environ, {}):
            os.environ.pop(CONFIG_COPY_ENV, None)
            with unstarted_lab() as net:
                frr_dir = net.addRouter('r1', frr_dir=self.seed_template).get_frr_dir()

        self.assertEqual(frr_dir, self.seed_dir)
        self.assertFalse(os.path.exists(self.copy_root))


class NodeNameReuseAcrossLabsTests(unittest.TestCase):
    """A Lab under a key a crashed Lab left files under resets a reused Router's config."""

    def test_second_labs_proto_wins_for_a_reused_router_name(self):
        with unstarted_lab(lab_key="reuse") as first:
            first.addRouter('r1')
            config_dir = first.frr_config_dir('r1')
            with open(os.path.join(config_dir, 'daemons')) as f:
                stale_daemons = f.read()
            self.assertIn('ospfd=no', stale_daemons)

        # A clean stop removed the directory; a crashed Lab would have left it.
        os.makedirs(config_dir)
        with open(os.path.join(config_dir, 'daemons'), 'w') as f:
            f.write(stale_daemons)

        with unstarted_lab(lab_key="reuse") as second:
            second.addRouter('r1', proto='OSPF')

            with open(os.path.join(config_dir, 'daemons')) as f:
                self.assertIn('ospfd=yes', f.read())
