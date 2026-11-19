"""Unit tests for mniplab.docker_client.DockerClient."""

import subprocess
import unittest
from unittest.mock import patch

from mniplab.docker_client import DockerClient


class DockerClientRunHelperTests(unittest.TestCase):
    """Validate DockerClient._run() delegates correctly to subprocess."""

    def test_run_passes_cmd_and_defaults(self) -> None:
        """_run should call subprocess.run with check=False, text=True."""
        with patch('subprocess.run') as mock_run:
            DockerClient._run(["docker", "ps"])
            mock_run.assert_called_once_with(
                ["docker", "ps"], check=False, capture_output=True, text=True
            )

    def test_run_respects_capture_false(self) -> None:
        """_run should forward capture=False to subprocess.run."""
        with patch('subprocess.run') as mock_run:
            DockerClient._run(["docker", "ps"], capture=False)
            mock_run.assert_called_once_with(
                ["docker", "ps"], check=False, capture_output=False, text=True
            )

    def test_run_returns_completed_process(self) -> None:
        """_run should return the CompletedProcess from subprocess.run."""
        with patch('subprocess.run') as mock_run:
            result = DockerClient._run(["docker", "ps"])
            self.assertIs(result, mock_run.return_value)


class DockerClientImageExistsTests(unittest.TestCase):
    """Validate DockerClient.image_exists()."""

    def setUp(self) -> None:
        self.client = DockerClient('test_container')

    def test_image_exists_returns_true(self) -> None:
        """Should return True when docker inspect succeeds."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{}', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.assertTrue(self.client.image_exists('alpine:3.20'))

    def test_image_exists_returns_false(self) -> None:
        """Should return False when docker inspect fails."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='no such image'
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.assertFalse(self.client.image_exists('nonexistent:img'))


class DockerClientPullTests(unittest.TestCase):
    """Validate DockerClient.pull()."""

    def setUp(self) -> None:
        self.client = DockerClient('test_container')

    def test_pull_success(self) -> None:
        """Should not raise when pull succeeds."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='Pull complete', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.client.pull('alpine:3.20')

    def test_pull_failure_raises(self) -> None:
        """Should raise RuntimeError when pull fails."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='manifest unknown'
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            with self.assertRaises(RuntimeError) as cm:
                self.client.pull('bad:image')
            self.assertIn('Failed to pull image bad:image', str(cm.exception))
            self.assertIn('manifest unknown', str(cm.exception))


class DockerClientRmTests(unittest.TestCase):
    """Validate DockerClient.rm()."""

    def setUp(self) -> None:
        self.client = DockerClient('mycontainer')

    def test_rm_with_force(self) -> None:
        """Should include -f flag when force=True."""
        with patch.object(DockerClient, '_run') as mock_run:
            self.client.rm(force=True)
            mock_run.assert_called_once_with(
                ['docker', 'rm', '-f', 'mycontainer'], check=False
            )

    def test_rm_without_force(self) -> None:
        """Should omit -f flag when force=False."""
        with patch.object(DockerClient, '_run') as mock_run:
            self.client.rm(force=False)
            mock_run.assert_called_once_with(
                ['docker', 'rm', 'mycontainer'], check=False
            )


class DockerClientIsRunningTests(unittest.TestCase):
    """Validate DockerClient.is_running()."""

    def setUp(self) -> None:
        self.client = DockerClient('mycontainer')

    def test_is_running_true(self) -> None:
        """Should return True when container is running."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='true\n', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.assertTrue(self.client.is_running())

    def test_is_running_false(self) -> None:
        """Should return False when container is stopped."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='false\n', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.assertFalse(self.client.is_running())

    def test_is_running_not_found(self) -> None:
        """Should return False when container does not exist."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='No such container'
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.assertFalse(self.client.is_running())

    def test_is_running_uppercase_true(self) -> None:
        """Should handle uppercase 'True' output."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='True\n', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.assertTrue(self.client.is_running())


class DockerClientGetPidTests(unittest.TestCase):
    """Validate DockerClient.get_pid()."""

    def setUp(self) -> None:
        self.client = DockerClient('mycontainer')

    def test_get_pid_valid(self) -> None:
        """Should return integer PID for running container."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='12345\n', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            self.assertEqual(self.client.get_pid(), 12345)

    def test_get_pid_container_not_found(self) -> None:
        """Should raise RuntimeError when container does not exist."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='No such container'
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            with self.assertRaises(RuntimeError) as cm:
                self.client.get_pid()
            self.assertIn(
                'Failed to get PID for container mycontainer', str(cm.exception)
            )

    def test_get_pid_invalid_output(self) -> None:
        """Should raise RuntimeError when PID output is not a number."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='<invalid>\n', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            with self.assertRaises(RuntimeError) as cm:
                self.client.get_pid()
            self.assertIn('Invalid container pid', str(cm.exception))


class DockerClientRunTests(unittest.TestCase):
    """Validate DockerClient.run() builds correct docker run commands."""

    def setUp(self) -> None:
        self.client = DockerClient('mycontainer')

    def test_run_minimal_args(self) -> None:
        """Should build minimal command with required args."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run('alpine:3.20', 'myhost')
            called_cmd = mock_run.call_args[0][0]
            self.assertEqual(called_cmd[:3], ['docker', 'run', '-d'])
            self.assertIn('--name', called_cmd)
            self.assertIn('mycontainer', called_cmd)
            self.assertIn('--hostname', called_cmd)
            self.assertIn('myhost', called_cmd)
            self.assertIn('--network', called_cmd)
            self.assertIn('none', called_cmd)
            self.assertIn('alpine:3.20', called_cmd)

    def test_run_includes_default_caps(self) -> None:
        """Should always add NET_ADMIN and NET_RAW capabilities."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run('alpine:3.20', 'myhost')
            called_cmd = mock_run.call_args[0][0]
            # NET_ADMIN and NET_RAW should each appear after --cap-add
            cap_adds = [
                called_cmd[i + 1]
                for i, item in enumerate(called_cmd)
                if item == '--cap-add'
            ]
            self.assertIn('NET_ADMIN', cap_adds)
            self.assertIn('NET_RAW', cap_adds)

    def test_run_with_custom_network(self) -> None:
        """Should use the specified network mode."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run('alpine:3.20', 'myhost', network='host')
            called_cmd = mock_run.call_args[0][0]
            idx = called_cmd.index('--network')
            self.assertEqual(called_cmd[idx + 1], 'host')

    def test_run_with_command(self) -> None:
        """Should append cmd at the end after the image."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run('alpine:3.20', 'myhost', cmd=['sh', '-c', 'sleep 999'])
            called_cmd = mock_run.call_args[0][0]
            self.assertTrue(called_cmd[-3:] == ['sh', '-c', 'sleep 999'])

    def test_run_with_environment(self) -> None:
        """Should add -e flags for each environment variable."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run(
                'alpine:3.20', 'myhost',
                environment={'FOO': 'bar', 'BAZ': 'qux'}
            )
            called_cmd = mock_run.call_args[0][0]
            self.assertIn('-e', called_cmd)
            self.assertIn('FOO=bar', called_cmd)
            self.assertIn('BAZ=qux', called_cmd)

    def test_run_with_volumes(self) -> None:
        """Should add -v flags for each volume."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run(
                'alpine:3.20', 'myhost',
                volumes=['/host/data:/container/data']
            )
            called_cmd = mock_run.call_args[0][0]
            self.assertIn('/host/data:/container/data', called_cmd)

    def test_run_with_ports(self) -> None:
        """Should add -p flags for each port mapping string."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run(
                'alpine:3.20', 'myhost',
                ports=['8080:80']
            )
            called_cmd = mock_run.call_args[0][0]
            self.assertIn('8080:80', called_cmd)

    def test_run_with_port_bindings(self) -> None:
        """Should add -p flags for each port binding dict entry."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run(
                'alpine:3.20', 'myhost',
                port_bindings={'80/tcp': '8080'}
            )
            called_cmd = mock_run.call_args[0][0]
            self.assertIn('8080:80/tcp', called_cmd)

    def test_run_with_cap_add(self) -> None:
        """Should add extra capabilities beyond defaults."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr=''
        )
        with patch.object(DockerClient, '_run', return_value=proc) as mock_run:
            self.client.run(
                'alpine:3.20', 'myhost',
                cap_add=['SYS_ADMIN']
            )
            called_cmd = mock_run.call_args[0][0]
            cap_adds = [
                called_cmd[i + 1]
                for i, item in enumerate(called_cmd)
                if item == '--cap-add'
            ]
            self.assertIn('SYS_ADMIN', cap_adds)

    def test_run_failure_raises(self) -> None:
        """Should raise RuntimeError when docker run fails."""
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='Conflict'
        )
        with patch.object(DockerClient, '_run', return_value=proc):
            with self.assertRaises(RuntimeError) as cm:
                self.client.run('alpine:3.20', 'myhost')
            self.assertIn('Failed to create container mycontainer', str(cm.exception))


class DockerClientExecInteractiveTests(unittest.TestCase):
    """Validate DockerClient.exec_interactive() builds correct commands."""

    def setUp(self) -> None:
        self.client = DockerClient('mycontainer')

    def test_exec_interactive_with_tty(self) -> None:
        """Should include -it flag when tty=True."""
        cmd = self.client.exec_interactive('bash')
        self.assertEqual(cmd, ['docker', 'exec', '-it', 'mycontainer', 'bash'])

    def test_exec_interactive_without_tty(self) -> None:
        """Should include -i flag only when tty=False."""
        cmd = self.client.exec_interactive('bash', tty=False)
        self.assertEqual(cmd, ['docker', 'exec', '-i', 'mycontainer', 'bash'])

    def test_exec_interactive_with_multiple_args(self) -> None:
        """Should append all args after the container name."""
        cmd = self.client.exec_interactive('vtysh', '-c', 'show ip route')
        self.assertEqual(
            cmd,
            ['docker', 'exec', '-it', 'mycontainer', 'vtysh', '-c', 'show ip route']
        )


if __name__ == '__main__':
    unittest.main()
