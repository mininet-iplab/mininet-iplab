"""Unit tests for the Open vSwitch host preflight check.

Open vSwitch depends on a kernel module the host must load. Without it,
Mininet's switch setup blocks rather than failing, so a lab appears to hang
with no clue why. These tests pin the check that turns that into a message.
"""

import subprocess
import unittest
from unittest.mock import Mock, patch

from mniplab.errors import NetworkError
from mniplab.kernel import ensure_ovs_available
from mniplab.tests.labs import unstarted_lab


def _completed(returncode=0, stderr=b''):
    """Build a CompletedProcess as subprocess.run would return."""
    return subprocess.CompletedProcess(
        args=['ovs-vsctl', '--timeout=5', 'show'],
        returncode=returncode,
        stdout=b'',
        stderr=stderr,
    )


class OVSAvailableTests(unittest.TestCase):
    """A working Open vSwitch passes silently."""

    def test_working_ovs_does_not_raise(self):
        with patch('mniplab.kernel.shutil.which', return_value='/usr/bin/ovs-vsctl'), \
                patch('mniplab.kernel.subprocess.run', return_value=_completed()):
            ensure_ovs_available()

    def test_probe_is_bounded_by_a_timeout(self):
        """Without a timeout, ovs-vsctl blocks forever — the very hang we prevent."""
        with patch('mniplab.kernel.shutil.which', return_value='/usr/bin/ovs-vsctl'), \
                patch('mniplab.kernel.subprocess.run', return_value=_completed()) as run:
            ensure_ovs_available(timeout=3)

        argv = run.call_args[0][0]
        self.assertIn('--timeout=3', argv)
        self.assertIsNotNone(run.call_args.kwargs.get('timeout'))


class OVSMissingTests(unittest.TestCase):
    """Each failure mode names its own fix."""

    def test_binary_missing_explains_installation(self):
        with patch('mniplab.kernel.shutil.which', return_value=None):
            with self.assertRaises(NetworkError) as ctx:
                ensure_ovs_available()

        message = str(ctx.exception)
        self.assertIn('not installed', message)
        self.assertIn('openvswitch-switch', message)

    def test_timeout_is_reported_as_the_hang_it_prevents(self):
        with patch('mniplab.kernel.shutil.which', return_value='/usr/bin/ovs-vsctl'), \
                patch('mniplab.kernel.subprocess.run',
                      side_effect=subprocess.TimeoutExpired('ovs-vsctl', 5)):
            with self.assertRaises(NetworkError) as ctx:
                ensure_ovs_available()

        self.assertIn('did not respond', str(ctx.exception))

    def test_daemon_down_suggests_starting_it(self):
        with patch('mniplab.kernel.shutil.which', return_value='/usr/bin/ovs-vsctl'), \
                patch('mniplab.kernel.subprocess.run',
                      return_value=_completed(1, b'database connection failed')):
            with self.assertRaises(NetworkError) as ctx:
                ensure_ovs_available()

        message = str(ctx.exception)
        self.assertIn('service openvswitch-switch start', message)
        self.assertIn('database connection failed', message)

    def test_exec_failure_is_reported(self):
        with patch('mniplab.kernel.shutil.which', return_value='/usr/bin/ovs-vsctl'), \
                patch('mniplab.kernel.subprocess.run', side_effect=OSError('permission denied')):
            with self.assertRaises(NetworkError) as ctx:
                ensure_ovs_available()

        self.assertIn('permission denied', str(ctx.exception))


class OVSGuidanceTests(unittest.TestCase):
    """The message names the host, not the container, as the place to fix it."""

    def _message(self):
        with patch('mniplab.kernel.shutil.which', return_value=None):
            try:
                ensure_ovs_available()
            except NetworkError as e:
                return str(e)
        self.fail('expected NetworkError')

    def test_says_the_module_is_a_host_concern(self):
        message = self._message()
        self.assertIn('HOST kernel', message)
        self.assertIn('modprobe openvswitch', message)

    def test_mentions_the_lib_modules_mount(self):
        """docker-compose mounts /lib/modules:ro precisely for this."""
        self.assertIn('/lib/modules', self._message())

    def test_says_router_only_labs_are_unaffected(self):
        self.assertIn('Router-only labs', self._message())


class AddSwitchPreflightTests(unittest.TestCase):
    """The check fires when a switch is declared, not at Lab construction."""

    def test_add_switch_checks_ovs_first(self):
        with unstarted_lab() as lab, \
                patch('mniplab.net.ensure_ovs_available') as check, \
                patch('mininet.net.Mininet.addSwitch', return_value=Mock()) as parent:
            lab.addSwitch('s1')

        check.assert_called_once()
        parent.assert_called_once()

    def test_add_switch_does_not_create_the_switch_when_ovs_is_missing(self):
        with unstarted_lab() as lab, \
                patch('mniplab.net.ensure_ovs_available',
                      side_effect=NetworkError('no ovs')), \
                patch('mininet.net.Mininet.addSwitch') as parent:
            with self.assertRaises(NetworkError):
                lab.addSwitch('s1')

        parent.assert_not_called()

    def test_router_only_labs_never_check_ovs(self):
        """addHost must not require Open vSwitch."""
        host = Mock()
        host.params = {'homeDir': '/tmp/x'}
        with unstarted_lab() as lab, \
                patch('mniplab.net.ensure_ovs_available') as check, \
                patch('mininet.net.Mininet.addHost', return_value=host):
            lab.addHost('h1')

        check.assert_not_called()
