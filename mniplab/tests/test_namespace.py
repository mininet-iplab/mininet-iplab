"""Unit tests for mniplab.namespace -- the Private Namespace Node capability seam."""

import os
import signal
import subprocess
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mininet.node import Node
from mniplab.container import ContainerHost
from mniplab.errors import mnIPLabError
from mniplab.exabgp.node import ExaBGPNode
from mniplab.host import mnIPLabHost
from mniplab.namespace import (
    PRIVATE_NAMESPACES_ENV_VAR,
    NamespaceError,
    NamespaceMixin,
    PopenGroup,
    _recover_namespace_anchor_pid,
    build_nsenter_argv,
    has_private_namespaces,
    install_popen_guard,
)
from mniplab.router import FRRouter


class HasPrivateNamespacesTests(unittest.TestCase):
    """has_private_namespaces answers per Node, honouring the escape hatch."""

    def _clean_env(self):
        return patch.dict(os.environ, {}, clear=False)

    def test_container_host_is_capable(self):
        node = object.__new__(ContainerHost)
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            self.assertTrue(has_private_namespaces(node))

    def test_ordinary_host_is_capable(self):
        node = object.__new__(mnIPLabHost)
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            self.assertTrue(has_private_namespaces(node))

    def test_router_is_capable(self):
        node = object.__new__(FRRouter)
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            self.assertTrue(has_private_namespaces(node))

    def test_plain_mininet_node_is_not_capable(self):
        node = object.__new__(Node)
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            self.assertFalse(has_private_namespaces(node))

    def test_exabgp_speaker_is_capable(self):
        node = object.__new__(ExaBGPNode)
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            self.assertTrue(has_private_namespaces(node))

    def test_hatch_off_disables_container_host(self):
        node = object.__new__(ContainerHost)
        with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '0'}):
            self.assertFalse(has_private_namespaces(node))

    def test_hatch_off_disables_host(self):
        node = object.__new__(mnIPLabHost)
        with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '0'}):
            self.assertFalse(has_private_namespaces(node))

    def test_hatch_off_disables_router(self):
        node = object.__new__(FRRouter)
        with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '0'}):
            self.assertFalse(has_private_namespaces(node))

    def test_hatch_on_is_the_default(self):
        node = object.__new__(mnIPLabHost)
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            self.assertTrue(has_private_namespaces(node))

    def test_hatch_accepts_any_nonzero_value_as_on(self):
        node = object.__new__(mnIPLabHost)
        with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '1'}):
            self.assertTrue(has_private_namespaces(node))


class NamespaceErrorTests(unittest.TestCase):
    """NamespaceError joins the project's exception hierarchy."""

    def test_is_an_mniplab_error(self):
        self.assertTrue(issubclass(NamespaceError, mnIPLabError))

    def test_can_be_raised_and_caught_as_mniplab_error(self):
        with self.assertRaises(mnIPLabError):
            raise NamespaceError("anchor recovery failed")


class BuildNsenterArgvTests(unittest.TestCase):
    """The shared attach recipe: one flag set, decided once."""

    def test_targets_the_given_pid(self):
        argv = build_nsenter_argv(4242, ['bash'])
        self.assertEqual(argv[:3], ['nsenter', '-t', '4242'])

    def test_enters_mount_uts_net_and_pid_namespaces(self):
        argv = build_nsenter_argv(1, ['bash'])
        for flag in ('-m', '-u', '-n', '-p'):
            self.assertIn(flag, argv)

    def test_never_enters_the_ipc_namespace(self):
        # No Node in a Lab has a private IPC namespace, so entering the
        # target's would change nothing and only narrow valid targets.
        argv = build_nsenter_argv(1, ['bash'])
        self.assertNotIn('-i', argv)

    def test_never_passes_no_fork(self):
        # -F would leave the shell outside the PID namespace and missing
        # from its own ps; the fork is mandatory.
        argv = build_nsenter_argv(1, ['bash'])
        self.assertNotIn('-F', argv)
        self.assertNotIn('--no-fork', argv)

    def test_double_dash_separates_the_command(self):
        argv = build_nsenter_argv(1, ['bash', '--norc'])
        separator = argv.index('--')
        self.assertEqual(argv[separator + 1:], ['bash', '--norc'])

    def test_command_words_that_look_like_options_pass_through(self):
        argv = build_nsenter_argv(1, ['vtysh', '-c', 'show ip route'])
        self.assertEqual(argv[argv.index('--') + 1:], ['vtysh', '-c', 'show ip route'])

    def test_pid_is_stringified(self):
        argv = build_nsenter_argv(999, ['true'])
        self.assertEqual(argv[2], '999')


class PopenGroupTests(unittest.TestCase):
    """The handle a capable Node's popen() returns signals its process group."""

    def _bare_popen_group(self, pid):
        """A PopenGroup standing in for a live handle, without spawning one.

        Bypasses Popen.__init__ (mirroring InstallPopenGuardTests'
        _bare_node) so these tests exercise send_signal's dispatch logic
        without launching and then abandoning a real child process.
        """
        proc = PopenGroup.__new__(PopenGroup)
        proc.pid = pid
        proc.returncode = None
        return proc

    def test_class_signals_the_group_not_the_wrapper_pid(self):
        """terminate/kill send their signal to the process group, so the
        process that runs inside the Node — not nsenter's forked wrapper —
        is what dies."""
        handled = []
        proc = self._bare_popen_group(123)
        with patch.object(proc, 'poll', return_value=None):
            with patch('os.killpg', side_effect=lambda pgid, sig: handled.append((pgid, sig))):
                proc.terminate()
                proc.kill()
        self.assertEqual(handled, [(123, signal.SIGTERM), (123, signal.SIGKILL)])

    def test_send_signal_overload_signals_the_group(self):
        handled = []
        proc = self._bare_popen_group(456)
        with patch.object(proc, 'poll', return_value=None):
            with patch('os.killpg', side_effect=lambda pgid, sig: handled.append((pgid, sig))):
                proc.send_signal(signal.SIGINT)
        self.assertEqual(handled, [(456, signal.SIGINT)])

    def test_killpg_error_is_swallowed_the_way_popen_does(self):
        """A process that exited between poll() and the group signal must
        not raise — mirroring Popen.send_signal's tolerance."""
        proc = self._bare_popen_group(789)
        with patch.object(proc, 'poll', return_value=None):
            with patch('os.killpg', side_effect=ProcessLookupError):
                with patch('os.kill', side_effect=ProcessLookupError):
                    proc.terminate()  # must not raise

    def test_killpg_failure_falls_back_to_the_pid(self):
        """A bare attach (a docker exec client) never makes its child a
        group leader, so killpg finds no group — the signal then lands on
        the pid, which is what stock Popen did."""
        handled = []
        proc = self._bare_popen_group(123)
        with patch.object(proc, 'poll', return_value=None):
            with patch('os.killpg', side_effect=ProcessLookupError):
                with patch('os.kill', side_effect=lambda pid, sig: handled.append((pid, sig))):
                    proc.terminate()
        self.assertEqual(handled, [(123, signal.SIGTERM)])

    def test_skips_signal_when_process_already_died(self):
        """The poll-then-skip guard Popen carries for the exit race is
        preserved by the override."""
        handled = []
        proc = self._bare_popen_group(123)
        proc.returncode = 0
        with patch.object(proc, 'poll', return_value=0):
            with patch('os.killpg', side_effect=lambda pgid, sig: handled.append((pgid, sig))):
                proc.terminate()
        self.assertEqual(handled, [])


class InstallPopenGuardTests(unittest.TestCase):
    """The popen override: capable Nodes attach with nsenter; the rest keep mnexec."""

    def setUp(self):
        install_popen_guard()

    def _bare_node(self, cls, name='n1'):
        node = object.__new__(cls)
        node.name = name
        node.pid = 4242
        node.inNamespace = True
        return node

    def test_install_is_idempotent(self):
        install_popen_guard()
        first = Node._popen
        second = Node.popen
        install_popen_guard()
        self.assertIs(Node._popen, first)
        self.assertIs(Node.popen, second)

    def test_capable_host_routes_through_nsenter(self):
        node = self._bare_node(mnIPLabHost, 'h1')
        with patch('mniplab.namespace.PopenGroup') as popen:
            handle = node.popen(['iperf3', '-s'])
        self.assertIs(handle, popen.return_value)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, build_nsenter_argv(4242, ['iperf3', '-s']))

    def test_capable_router_routes_through_nsenter_with_string_command(self):
        node = self._bare_node(FRRouter, 'r1')
        with patch('mniplab.namespace.PopenGroup') as popen:
            node.popen('ip link show')
        argv = popen.call_args.args[0]
        self.assertEqual(argv, build_nsenter_argv(4242, ['ip', 'link', 'show']))

    def test_non_capable_node_keeps_mnexec(self):
        """A Node that does not declare the property keeps stock behaviour:
        mnexec -da against its own pid."""
        node = self._bare_node(Node, 'h1')
        with patch('mniplab.namespace.PopenGroup') as popen:
            node.popen(['sleep', '1'])
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ['mnexec', '-da', '4242', 'sleep', '1'])
        self.assertNotIn('nsenter', argv)

    def test_hatch_off_keeps_mnexec(self):
        """The escape hatch is answered per call: with it off, a capable
        Node reverts to stock attach."""
        node = self._bare_node(mnIPLabHost, 'h1')
        with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '0'}):
            with patch('mniplab.namespace.PopenGroup') as popen:
                node.popen(['sleep', '1'])
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ['mnexec', '-da', '4242', 'sleep', '1'])

    def test_explicit_mncmd_wins_over_nsenter(self):
        """A caller-supplied mncmd — ContainerHost's `docker exec` — is
        honoured rather than overridden, so container attach is untouched."""
        node = self._bare_node(mnIPLabHost, 'h1')
        with patch('mniplab.namespace.PopenGroup') as popen:
            node.popen(['bash'], mncmd=['docker', 'exec', '-i', 'c1'])
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ['docker', 'exec', '-i', 'c1', 'bash'])

    def test_shell_flag_keeps_working(self):
        """Mininet's own shell=True handling reads $SHELL, which a
        non-interactive container does not always export — pin it here
        rather than depend on the ambient environment."""
        node = self._bare_node(mnIPLabHost, 'h1')
        with patch.dict(os.environ, {'SHELL': '/bin/bash'}):
            with patch('mniplab.namespace.PopenGroup') as popen:
                node.popen('ip addr show', shell=True)
        argv = popen.call_args.args[0]
        self.assertEqual(
            argv,
            build_nsenter_argv(4242, ['/bin/bash', '-c', 'ip addr show']),
        )

    def test_start_new_session_defaults_for_the_nsenter_path(self):
        """The nsenter attach must make its child a session leader so it
        has a process group to signal; a caller-provided preexec_fn must
        win over it."""
        node = self._bare_node(mnIPLabHost, 'h1')
        with patch('mniplab.namespace.PopenGroup') as popen:
            node.popen(['sleep', '1'])
        argv = popen.call_args.args[0]
        self.assertTrue(argv[0].endswith('nsenter'))
        self.assertIs(popen.call_args.kwargs.get('start_new_session'), True)

        preexec = lambda: None  # noqa: E731
        with patch('mniplab.namespace.PopenGroup') as popen:
            node.popen(['sleep', '1'], preexec_fn=preexec)
        self.assertNotIn('start_new_session', popen.call_args.kwargs)
        self.assertIs(popen.call_args.kwargs.get('preexec_fn'), preexec)

    def test_start_new_session_not_added_for_mnexec_path(self):
        """The stock mnexec -da path must not get a new session: mnexec's
        own setsid() forks when the child already leads a process group,
        orphaning the command. (Measured before the guard was written.)"""
        node = self._bare_node(Node, 'h1')
        with patch('mniplab.namespace.PopenGroup') as popen:
            node.popen(['sleep', '1'])
        argv = popen.call_args.args[0]
        self.assertTrue(argv[0].endswith('mnexec'))
        self.assertNotIn('start_new_session', popen.call_args.kwargs)


class RecoverNamespaceAnchorPidTests(unittest.TestCase):
    """_recover_namespace_anchor_pid: the launcher's sole child, or a loud failure.

    Exercised against real /proc, not a fake -- this is the one place an
    unproven mock would hide the exact failure mode that matters:
    falling back to the launcher's own pid instead of raising.
    """

    def _spawn_wrapper(self, child_shell: str):
        """Spawn a real process that forks exactly one real child.

        Mirrors ``unshare --fork``'s shape: the returned process is alive in
        the caller's pid namespace and has, at the kernel level, exactly one
        task in ``/proc/<pid>/task/<pid>/children``.
        """
        proc = subprocess.Popen(['sh', '-c', child_shell])
        deadline = time.monotonic() + 2.0
        children_path = f'/proc/{proc.pid}/task/{proc.pid}/children'
        while time.monotonic() < deadline:
            with open(children_path) as f:
                if f.read().split():
                    break
            time.sleep(0.01)
        self.addCleanup(self._reap, proc)
        return proc

    @staticmethod
    def _reap(proc):
        proc.kill()
        proc.wait()

    def test_recovers_the_sole_child_pid(self):
        wrapper = self._spawn_wrapper('sleep 5 & wait')
        with open(f'/proc/{wrapper.pid}/task/{wrapper.pid}/children') as f:
            expected = int(f.read().split()[0])

        anchor = _recover_namespace_anchor_pid(wrapper.pid, 'h1')

        self.assertEqual(anchor, expected)
        self.assertNotEqual(anchor, wrapper.pid)

    def test_raises_when_the_launcher_has_no_children(self):
        # A plain 'sleep' with nothing to fork never gains a child; this is
        # the same shape as a launcher whose unshare invocation failed
        # before it could --fork.
        proc = subprocess.Popen(['sleep', '5'])
        self.addCleanup(self._reap, proc)
        time.sleep(0.1)

        with self.assertRaises(NamespaceError):
            _recover_namespace_anchor_pid(proc.pid, 'h1')

    def test_raises_when_the_launcher_has_more_than_one_child(self):
        wrapper = self._spawn_wrapper('sleep 5 & sleep 5 & wait')

        with self.assertRaises(NamespaceError):
            _recover_namespace_anchor_pid(wrapper.pid, 'h1')

    def test_raises_when_the_launcher_pid_does_not_exist(self):
        # A pid that never existed, or that already exited -- the fatal
        # path a Lab hits if the unshare process itself dies before /proc
        # can be read.
        with self.assertRaises(NamespaceError):
            _recover_namespace_anchor_pid(2 ** 30, 'h1')

    def test_error_names_the_node(self):
        with self.assertRaisesRegex(NamespaceError, 'r1'):
            _recover_namespace_anchor_pid(2 ** 30, 'r1')


class _StockShellStub:
    """Stands in for Mininet's ``Node`` at the end of the MRO chain.

    Lets :class:`NamespaceMixin`'s ``super().startShell()`` call resolve to
    something observable instead of ``object``, without dragging in real
    Mininet Node construction.
    """

    def __init__(self):
        self.stock_started_with = 'not called'

    def startShell(self, mnopts=None):
        self.stock_started_with = mnopts


class _FakeNamespaceNode(NamespaceMixin, _StockShellStub):
    """A minimal Node double driving NamespaceMixin.startShell for real.

    Runs the mixin's actual control flow -- a real pty pair, a real
    ``select.poll`` -- while faking only the two things a real Node would
    need root and a live Lab for: the spawned shell process (``_popen``)
    and the in-namespace commands (``cmd``).
    """

    private_namespace_capable = True

    def __init__(self, name='h1'):
        _StockShellStub.__init__(self)
        self.name = name
        self.shell = None
        self.pid = None
        self.stdin = None
        self.slave = None
        self.outToNode = {}
        self.inToNode = {}
        self.popen_calls = []
        self.commands = []

    def _popen(self, cmd, **kwargs):
        self.popen_calls.append((cmd, kwargs))
        return SimpleNamespace(pid=555)

    def read(self, n):
        return chr(127)

    def cmd(self, *args, **kwargs):
        self.commands.append(args[0])
        return ''

    def close_pty(self):
        if self.stdin is not None:
            self.stdin.close()
        if getattr(self, 'slave', None) is not None:
            os.close(self.slave)


class NamespaceMixinStartShellTests(unittest.TestCase):
    """NamespaceMixin.startShell: unshare when capable, stock path otherwise."""

    def _clean_env(self):
        return patch.dict(os.environ, {}, clear=False)

    def test_hatch_off_defers_to_the_stock_path(self):
        node = _FakeNamespaceNode('h1')
        with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '0'}):
            node.startShell(mnopts='-cdn')
        self.assertEqual(node.stock_started_with, '-cdn')
        self.assertEqual(node.popen_calls, [])

    def test_incapable_node_defers_to_the_stock_path(self):
        node = _FakeNamespaceNode('h1')
        node.private_namespace_capable = False
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            node.startShell()
        self.assertIsNone(node.stock_started_with)
        self.assertEqual(node.popen_calls, [])

    def test_refuses_to_start_a_second_shell(self):
        node = _FakeNamespaceNode('h1')
        node.shell = SimpleNamespace(pid=1)
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            with patch('mininet.log.error') as error:
                node.startShell()
        error.assert_called_once()
        self.assertEqual(node.popen_calls, [])

    def test_launches_unshare_with_the_full_flag_set(self):
        node = _FakeNamespaceNode('h1')
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            with patch(
                'mniplab.namespace._recover_namespace_anchor_pid',
                return_value=777,
            ):
                node.startShell()
        self.addCleanup(node.close_pty)

        argv, kwargs = node.popen_calls[0]
        self.assertEqual(argv[0], 'unshare')
        for flag in ('--net', '--mount', '--uts', '--pid', '--fork', '--kill-child'):
            self.assertIn(flag, argv)
        self.assertIn('bash', argv)
        self.assertEqual(argv[-1], 'mininet:h1')
        self.assertTrue(kwargs['close_fds'])
        self.assertTrue(kwargs['start_new_session'])

    def test_argv0_is_bash_not_an_absolute_path(self):
        # connect_node.sh and the Web UI discover a Node's shell by matching
        # argv starting with the literal token 'bash'.
        node = _FakeNamespaceNode('h1')
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            with patch(
                'mniplab.namespace._recover_namespace_anchor_pid',
                return_value=777,
            ):
                node.startShell()
        self.addCleanup(node.close_pty)

        argv, _ = node.popen_calls[0]
        bash_index = argv.index('bash')
        self.assertEqual(argv[bash_index], 'bash')

    def test_pid_becomes_the_recovered_anchor_not_the_launcher(self):
        node = _FakeNamespaceNode('h1')
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            with patch(
                'mniplab.namespace._recover_namespace_anchor_pid',
                return_value=777,
            ) as recover:
                node.startShell()
        self.addCleanup(node.close_pty)

        recover.assert_called_once_with(555, 'h1')
        self.assertEqual(node.pid, 777)
        self.assertEqual(node.shell.pid, 555)
        self.assertNotEqual(node.pid, node.shell.pid)

    def test_mounts_proc_and_sysfs_and_sets_hostname_before_returning(self):
        node = _FakeNamespaceNode('r1')
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            with patch(
                'mniplab.namespace._recover_namespace_anchor_pid',
                return_value=777,
            ):
                node.startShell()
        self.addCleanup(node.close_pty)

        self.assertIn('mount -t proc proc /proc', node.commands)
        self.assertIn('mount -t sysfs sysfs /sys', node.commands)
        self.assertIn('hostname r1', node.commands)
        # Order matters: hostname must not race a not-yet-mounted /proc.
        self.assertLess(
            node.commands.index('mount -t proc proc /proc'),
            node.commands.index('hostname r1'),
        )

    def test_anchor_recovery_failure_propagates(self):
        node = _FakeNamespaceNode('h1')
        with self._clean_env():
            os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)
            with patch(
                'mniplab.namespace._recover_namespace_anchor_pid',
                side_effect=NamespaceError('anchor recovery failed'),
            ):
                with self.assertRaises(NamespaceError):
                    node.startShell()
        self.addCleanup(node.close_pty)
        # No in-namespace command ran against an unrecovered anchor.
        self.assertNotIn('hostname h1', node.commands)


if __name__ == '__main__':
    unittest.main()
