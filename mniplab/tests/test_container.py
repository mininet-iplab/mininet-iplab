"""Unit tests for mnIPLab.addContainer API behavior."""

import os
import select
import unittest
from typing import Any
from unittest.mock import Mock, patch

from mniplab.errors import ConfigurationError
from mniplab.container import ContainerHost
from mniplab.tests.labs import unstarted_lab
from mniplab.tests.fakes import FakeLab


class AddContainerApiTests(unittest.TestCase):
    """Validate addContainer wrapper behavior without Mininet runtime."""

    def test_add_container_requires_dimage(self) -> None:
        """addContainer should fail fast when image is missing."""
        with unstarted_lab() as net:
            with self.assertRaises(ConfigurationError):
                net.addContainer('c1')

    def test_add_container_sets_default_private_dirs_with_hosts(self) -> None:
        """addContainer should include shared /etc/hosts by default."""
        with unstarted_lab() as net:
            net.addHost = Mock(return_value='node')  # type: ignore[method-assign]
            result = net.addContainer('c1', dimage='alpine:3.20')

        self.assertEqual(result, 'node')
        net.addHost.assert_called_once()
        call_args = net.addHost.call_args
        self.assertEqual(call_args.args[0], 'c1')
        self.assertIn('privateDirs', call_args.kwargs)
        private_dirs = call_args.kwargs['privateDirs']
        self.assertTrue(
            any('/etc/hosts' in str(entry) for entry in private_dirs),
            f"Expected /etc/hosts in privateDirs: {private_dirs}"
        )

    def test_add_container_preserves_explicit_private_dirs(self) -> None:
        """addContainer should keep user-provided privateDirs unchanged."""
        private_dirs = [('/etc/hosts', '/tmp/etc/hosts')]

        with unstarted_lab() as net:
            net.addHost = Mock(return_value='node')  # type: ignore[method-assign]
            net.addContainer('c1', dimage='alpine:3.20', privateDirs=private_dirs)

        call_args = net.addHost.call_args
        self.assertEqual(call_args.kwargs['privateDirs'], private_dirs)


class ContainerHostVolumeTests(unittest.TestCase):
    """Validate ContainerHost translates privateDirs to Docker volumes."""

    def _make_container_host(self, name='c1', **kwargs):
        """Create a ContainerHost with shell/container operations mocked."""
        with patch.object(ContainerHost, '_create_container'):
            with patch.object(ContainerHost, 'startShell'):
                return ContainerHost(name, dimage='alpine:latest', **kwargs)

    def test_private_dirs_converted_to_volumes(self) -> None:
        """Tuple privateDirs should become Docker -v mounts."""
        host = self._make_container_host(
            privateDirs=[
                ('/etc/hosts', '/tmp/etc/hosts'),
                ('/root', '/tmp/mniplab/c1'),
            ],
        )

        self.assertIn('/tmp/etc/hosts:/etc/hosts', host.volumes)
        self.assertIn('/tmp/mniplab/c1:/root', host.volumes)

    def test_private_dirs_name_placeholder_expanded(self) -> None:
        """%(name)s in privateDirs host path should be expanded."""
        host = self._make_container_host(
            name='router1',
            privateDirs=[
                ('/etc/frr', '/tmp/frr-config/%(name)s'),
            ],
        )

        self.assertIn('/tmp/frr-config/router1:/etc/frr', host.volumes)

    def test_string_private_dirs_are_backed_from_the_lab_directory(self) -> None:
        """Bare-string privateDirs are backed by the Node's tree in the Lab directory."""
        net = FakeLab()
        host = self._make_container_host(
            privateDirs=['/var/log'], net=net,
        )

        self.assertIn(f"{net.container_dir('c1', '/var/log')}:/var/log", host.volumes)

    def test_string_private_dirs_need_a_lab(self) -> None:
        """Without a Lab there is no Lab directory to back a bare entry from."""
        with self.assertRaises(ConfigurationError):
            self._make_container_host(privateDirs=['/var/log'])

    def test_no_private_dirs_leaves_volumes_empty(self) -> None:
        """Default empty privateDirs should not add any volumes."""
        host = self._make_container_host()

        self.assertEqual(host.volumes, [])


class ContainerHostNamingTests(unittest.TestCase):
    """The container and its namespace link carry the Lab key; the Node name does not."""

    def _make_container_host(self, name='c1', **kwargs):
        with patch.object(ContainerHost, '_create_container'):
            with patch.object(ContainerHost, 'startShell'):
                return ContainerHost(name, dimage='alpine:latest', **kwargs)

    def test_names_come_from_the_lab(self) -> None:
        lab = Mock()
        lab.container_name.return_value = 'mn.k1.c1'
        lab.netns_link_path.return_value = '/var/run/netns/mn.k1.c1'
        host = self._make_container_host(net=lab)

        self.assertEqual(host.name, 'c1')
        self.assertEqual(host.container_name, 'mn.k1.c1')
        self.assertEqual(host.netns_link_path, '/var/run/netns/mn.k1.c1')
        lab.container_name.assert_called_with('c1')

    def test_without_a_lab_the_names_keep_the_sweepable_prefix(self) -> None:
        host = self._make_container_host()

        self.assertEqual(host.container_name, 'mn.c1')
        self.assertEqual(host.netns_link_path, '/var/run/netns/mn.c1')


class ContainerHostShellTests(unittest.TestCase):
    """Validate ContainerHost.startShell session isolation and readiness timeout."""

    def _make_container_host(self, name: str = 'c1', **kwargs: Any) -> ContainerHost:
        with patch.object(ContainerHost, '_create_container'):
            with patch.object(ContainerHost, 'startShell'):
                return ContainerHost(name, dimage='alpine:latest', **kwargs)

    @patch('mniplab.container.pty.openpty')
    @patch('mniplab.container.os.fdopen')
    @patch.object(ContainerHost, '_popen')
    def test_start_shell_passes_start_new_session_to_popen(
        self, mock_popen: Mock, mock_fdopen: Mock, mock_openpty: Mock
    ) -> None:
        """startShell should isolate process session with start_new_session=True."""
        mock_openpty.return_value = (10, 11)
        mock_stdin = Mock()
        mock_stdin.fileno.return_value = 10
        mock_fdopen.return_value = mock_stdin
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = None
        mock_proc.pid = 5678
        mock_popen.return_value = mock_proc

        host = self._make_container_host('c1')
        host._is_container_running = Mock(return_value=True)  # type: ignore[method-assign]
        host._docker = Mock()
        host._docker.exec_interactive.return_value = ['docker', 'exec']
        host._get_container_pid = Mock(return_value=1234)  # type: ignore[method-assign]
        host.read = Mock(return_value=chr(127))
        host.cmd = Mock()  # type: ignore[method-assign]

        with patch('select.poll') as mock_poll_cls:
            mock_poll = Mock()
            mock_poll.poll.return_value = [(10, select.POLLIN)]
            mock_poll_cls.return_value = mock_poll
            host.startShell()

        mock_popen.assert_called_once()
        _, kwargs = mock_popen.call_args
        self.assertIs(kwargs.get('start_new_session'), True)
        self.assertNotIn('preexec_fn', kwargs)

    @patch('mniplab.container.pty.openpty')
    @patch('mniplab.container.os.fdopen')
    @patch.object(ContainerHost, '_popen')
    def test_start_shell_times_out_cleans_up_and_raises(
        self, mock_popen: Mock, mock_fdopen: Mock, mock_openpty: Mock
    ) -> None:
        """startShell should clean up descriptors and raise on readiness timeout."""
        # A real pty, so the test reads what startShell closed off the
        # descriptor itself: counting os.close calls also counts every other
        # close in the process, such as a collected TemporaryDirectory's.
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        mock_openpty.return_value = (master, slave)
        mock_stdin = Mock()
        mock_stdin.fileno.return_value = master
        mock_fdopen.return_value = mock_stdin
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = None
        mock_proc.pid = 5678
        mock_popen.return_value = mock_proc

        host = self._make_container_host('c1')
        host._is_container_running = Mock(return_value=True)  # type: ignore[method-assign]
        host._docker = Mock()
        host._docker.exec_interactive.return_value = ['docker', 'exec']
        host._get_container_pid = Mock(return_value=1234)  # type: ignore[method-assign]

        with patch('select.poll') as mock_poll_cls, \
             patch.object(host, 'read') as mock_read, \
             patch('mniplab.container.SHELL_READY_TIMEOUT', 0.05):
            mock_poll = Mock()
            mock_poll.poll.return_value = []
            mock_poll_cls.return_value = mock_poll

            with self.assertRaises(RuntimeError) as cm:
                host.startShell()

            self.assertIn('c1', str(cm.exception))
            self.assertIn('timed out waiting for container shell prompt', str(cm.exception))

            mock_poll.unregister.assert_called_once_with(mock_stdin)
            self.assertNotIn(master, host.outToNode)
            self.assertNotIn(master, host.inToNode)
            mock_stdin.close.assert_called_once()
            with self.assertRaises(OSError):
                os.fstat(slave)
            mock_proc.kill.assert_called_once()
            mock_proc.wait.assert_called_once()
            self.assertIsNone(host.shell)
            self.assertIsNone(host.stdin)
            self.assertIsNone(host.stdout)
            self.assertIsNone(host.master)
            self.assertIsNone(host.slave)
            self.assertFalse(host.waiting)
            mock_read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
