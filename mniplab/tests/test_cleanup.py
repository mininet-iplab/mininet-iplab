"""Unit tests for cleanup error handling specificity."""

import os
import re
import subprocess
import tempfile
import unittest
from typing import List
from unittest.mock import Mock, patch

import pytest

from mniplab import cleanup


class CleanupExceptionHandlingTests(unittest.TestCase):
    """Validate cleanup functions catch specific exceptions, not bare Exception."""

    @patch('mniplab.cleanup._run_command_silent',
           side_effect=subprocess.TimeoutExpired(cmd=['pgrep'], timeout=5))
    def test_kill_lingering_labs_catches_timeout(self, _mock_run):
        """kill_lingering_labs should survive subprocess timeout."""
        result = cleanup.kill_lingering_labs(verbose=False)
        self.assertEqual(result, 0)

    @patch('mniplab.cleanup._run_command_silent',
           side_effect=subprocess.SubprocessError("broken pipe"))
    def test_kill_lingering_labs_catches_subprocess_error(self, _mock_run):
        """kill_lingering_labs should survive generic subprocess errors."""
        result = cleanup.kill_lingering_labs(verbose=False)
        self.assertEqual(result, 0)

    @patch('mniplab.cleanup.os.path.exists', return_value=True)
    @patch('mniplab.cleanup.os.remove', side_effect=PermissionError("denied"))
    def test_remove_path_safe_catches_oserror(self, _mock_remove, _mock_exists):
        """_remove_path_safe should catch OSError on file removal."""
        # Should not raise
        cleanup._remove_path_safe('/tmp/test', verbose=False)

    @patch('mniplab.cleanup.os.path.exists', return_value=True)
    @patch('mniplab.cleanup.shutil.rmtree', side_effect=OSError("busy"))
    def test_remove_path_safe_catches_rmtree_oserror(self, _mock_rmtree, _mock_exists):
        """_remove_path_safe should catch OSError on directory removal."""
        # Should not raise
        cleanup._remove_path_safe('/tmp/testdir', verbose=False)


if __name__ == '__main__':
    unittest.main()


@pytest.mark.unit
class AncestorGuardTests(unittest.TestCase):
    """Cleanup must never kill the shell that launched it.

    ``LAB_PROCESS_PATTERNS`` is matched against full command lines, so the
    documented ``sudo python -m mniplab cleanup`` makes ``sudo`` -- the
    parent -- match ``-m mniplab``. Excluding only ``os.getpid()`` left the
    launcher to be SIGKILLed, taking the Learner's session with it.
    """

    def test_ancestors_include_self_and_parent(self):
        pids = cleanup._ancestor_pids()

        self.assertIn(os.getpid(), pids)
        self.assertIn(os.getppid(), pids)

    def test_ancestor_walk_terminates(self):
        # A cycle or a runaway walk would hang cleanup before it does anything.
        pids = cleanup._ancestor_pids()

        self.assertLess(len(pids), 200)
        self.assertTrue(all(isinstance(pid, int) for pid in pids))

    @patch('mniplab.cleanup._kill_processes')
    @patch('mniplab.cleanup._run_command_silent')
    def test_an_ancestor_is_never_killed(self, mock_run, mock_kill):
        parent = os.getppid()
        stranger = 999999
        mock_run.return_value = subprocess.CompletedProcess(
            args=['pgrep'], returncode=0, stdout=f'{parent}\n{stranger}\n')

        killed = cleanup.kill_lingering_labs(verbose=False)

        mock_kill.assert_called_once_with([str(stranger)])
        self.assertEqual(killed, 1)

    @patch('mniplab.cleanup._ancestor_pids', return_value={100, 200})
    @patch('mniplab.cleanup._kill_processes')
    @patch('mniplab.cleanup._run_command_silent')
    @patch('mniplab.cleanup.subprocess.run')
    @patch('mniplab.cleanup.cleanup_nat_rules')
    def test_emergency_cleanup_unkeyed_excludes_ancestor_pids(
        self, _mock_nat, mock_subproc_run, mock_cmd_silent, mock_kill, _mock_ancestors
    ):
        """Unkeyed emergency_cleanup must use kill_lingering_labs and guard ancestors."""
        ancestor = 100
        stranger = 999999

        def silent_cmd_side_effect(cmd, **kwargs):
            if cmd and cmd[0] == 'pgrep':
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=f'{ancestor}\n{stranger}\n'
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='')

        mock_cmd_silent.side_effect = silent_cmd_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = os.path.join(tmpdir, 'mniplab')
            os.makedirs(os.path.join(runtime_root, 'labs', 'k9'))
            log_file = os.path.join(tmpdir, 'mniplab-web.log')
            open(log_file, 'w').close()

            with patch.object(cleanup, 'EPHEMERAL_TMP_PATHS', (log_file, runtime_root)):
                cleanup.emergency_cleanup()

            self.assertFalse(os.path.exists(runtime_root))
            self.assertFalse(os.path.exists(log_file))

        for call in mock_subproc_run.call_args_list:
            args, kwargs = call
            self.assertFalse(kwargs.get('shell', False), f"Found shell=True call: {call}")
            if args:
                self.assertNotIn('xargs -r kill -9', str(args[0]))

        mock_kill.assert_called_once_with([str(stranger)])


@pytest.mark.unit
class CaptureProcessSweepTests(unittest.TestCase):
    """A crashed web server's Packet Captures are found by the path they write."""

    @patch('mniplab.cleanup._run_command_silent')
    def test_matches_a_capture_writing_under_a_lab_directory_only(self, mock_run):
        cleanup.kill_capture_processes(verbose=False)

        pattern = mock_run.call_args.args[0][-1]
        capture = (f"tshark -i h1-eth0 -l -n -T pdml -w "
                   f"{cleanup.LABS_DIR}/k1/captures/h1/h1-eth0.pcapng -b files:2")
        self.assertRegex(capture, pattern)
        self.assertNotRegex("tshark -i eth0 -w /home/me/trace.pcapng", pattern)


@pytest.mark.unit
class EphemeralTmpTreeTests(unittest.TestCase):
    """`mniplab cleanup` must leave nothing the package wrote on the host behind.

    Every Lab directory lives under the runtime root, and only a clean
    stop removes one; what cleanup misses is inherited by the next Lab of
    that key -- the defect this suite once guarded for the FRR config
    directory and the shared hosts file.
    """

    def test_cleanup_removes_the_runtime_root_and_the_web_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = os.path.join(tmpdir, 'mniplab')
            os.makedirs(os.path.join(runtime_root, 'labs', 'k1', 'nodes', 'r1', 'etc', 'frr'))
            os.makedirs(os.path.join(runtime_root, 'ip-forward'))
            log_file = os.path.join(tmpdir, 'mniplab-web.log')
            open(log_file, 'w').close()

            with patch.object(cleanup, 'EPHEMERAL_TMP_PATHS', (log_file, runtime_root)):
                cleanup.cleanup_mniplab_files(verbose=False)

            self.assertFalse(os.path.exists(runtime_root))
            self.assertFalse(os.path.exists(log_file))

    def test_cleanup_paths_follow_the_constants_a_lab_binds(self):
        """Cleanup must remove the paths a Lab actually used, the root override included."""
        from mniplab import constants

        self.assertIn(constants.MNIPLAB_WEB_LOG_FILE, cleanup.EPHEMERAL_TMP_PATHS)
        self.assertIn(constants.MNIPLAB_TMP_DIR, cleanup.EPHEMERAL_TMP_PATHS)

    def test_every_ephemeral_tmp_constant_is_cleaned(self):
        """No ephemeral path may exist that cleanup does not know about.

        The bug class this guards is a new ephemeral tree landing in
        ``constants.py`` and never being added to cleanup — the next Lab then
        inherits it, exactly as Routers inherited a stale daemons file.
        """
        from mniplab import constants

        # The one /tmp path a Learner authors rather than a Lab generates.
        authored = {'/tmp'}
        roots = tuple(cleanup.EPHEMERAL_TMP_PATHS)

        uncovered = []
        for name in dir(constants):
            value = getattr(constants, name)
            if not isinstance(value, str) or not value.startswith('/tmp'):
                continue
            path = value.split('/%(name)s')[0]
            if path in authored:
                continue
            if not any(path == root or path.startswith(root + '/') for root in roots):
                uncovered.append(f'{name} = {value}')

        self.assertEqual(uncovered, [], f'ephemeral paths no cleanup removes: {uncovered}')


@pytest.mark.unit
class ContainerNameFilterTests(unittest.TestCase):
    """Cleanup removes only containers whose name starts with the prefix.

    Docker's ``name`` filter is an unanchored regular expression, so a bare
    ``mn.`` matched ``alumni-db`` and ``mnemonic`` and force-removed them.
    """

    NAMES = ['alumni-db', 'admnx', 'mnemonic', 'xmn.lab1.h1', 'mn.lab1.h1']

    def _matches(self, docker_filter: str) -> List[str]:
        self.assertTrue(docker_filter.startswith('name='))
        pattern = docker_filter[len('name='):]
        candidates = self.NAMES + ['/' + name for name in self.NAMES]
        return sorted(n for n in candidates if re.search(pattern, n))

    def test_all_labs_filter_matches_only_lab_containers(self):
        self.assertEqual(self._matches(cleanup.container_name_filter()),
                         ['/mn.lab1.h1', 'mn.lab1.h1'])

    def test_keyed_filter_matches_only_that_labs_containers(self):
        self.assertEqual(self._matches(cleanup.container_name_filter('lab1')),
                         ['/mn.lab1.h1', 'mn.lab1.h1'])
        self.assertEqual(self._matches(cleanup.container_name_filter('lab2')), [])

    def test_keyed_filter_matches_the_key_literally(self):
        pattern = cleanup.container_name_filter('lab1')[len('name='):]
        self.assertIsNone(re.search(pattern, 'mn.labXyh1'))
        self.assertIsNone(re.search(pattern, 'mnXlab1.h1'))


@pytest.mark.unit
@patch('mniplab.cleanup.os.path.isdir', return_value=False)
@patch('mniplab.cleanup._run_command_silent')
class ContainerCleanupFilterTests(unittest.TestCase):
    """Every path that removes containers asks Docker with the anchored filter."""

    def _docker_ps_filters(self, mock_run: Mock) -> List[str]:
        return [call.args[0][4] for call in mock_run.call_args_list
                if call.args[0][:4] == ['docker', 'ps', '-aq', '--filter']]

    def _idle(self, mock_run: Mock) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='', stderr='')

    def test_cleanup_command_filters_on_every_labs_prefix(self, mock_run, _isdir):
        self._idle(mock_run)
        cleanup.kill_container_processes(verbose=False)
        self.assertEqual(self._docker_ps_filters(mock_run), [r'name=^/?mn\.'])

    def test_unkeyed_emergency_cleanup_filters_on_every_labs_prefix(self, mock_run, _isdir):
        self._idle(mock_run)
        with patch('mniplab.cleanup.os.path.exists', return_value=False):
            cleanup.emergency_cleanup()
        self.assertEqual(self._docker_ps_filters(mock_run), [r'name=^/?mn\.'])

    def test_lab_sweep_filters_on_that_labs_prefix(self, mock_run, _isdir):
        self._idle(mock_run)
        with patch('mniplab.kernel.release_ip_forward_leases_of'):
            cleanup.sweep_root_namespace_by_key('lab1')
        self.assertEqual(self._docker_ps_filters(mock_run), [r'name=^/?mn\.lab1\.'])
