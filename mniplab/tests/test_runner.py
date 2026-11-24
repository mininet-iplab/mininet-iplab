"""Unit tests for mniplab.runner — lab entry point orchestration."""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from typing import Any, List, Tuple
from unittest.mock import ANY, Mock, patch

from mniplab.runner import (
    _LabSignals,
    setup_logging,
    parse_lab_args,
    run_lab,
)


def _close_quietly(fd: int) -> None:
    """Close ``fd`` unless it is closed already."""
    try:
        os.close(fd)
    except OSError:
        pass


class SetupLoggingTests(unittest.TestCase):
    """setup_logging delegates to mniplab.logging with correct flags."""

    @patch('mniplab.runner.setup_mniplab_logging')
    def test_cli_mode_logs_to_stdout(self, mock_setup):
        setup_logging('debug', enable_web=False)
        mock_setup.assert_called_once_with(level='DEBUG', force=True)

    @patch('mniplab.runner.setup_mniplab_logging')
    def test_web_mode_logs_to_file_only(self, mock_setup):
        setup_logging('info', enable_web=True, log_file='/tmp/test-web.log')
        mock_setup.assert_called_once_with(
            level='INFO',
            log_file='/tmp/test-web.log',
            log_to_stdout=False,
            force=True,
            root_logger=False,
        )


class ParseLabArgsTests(unittest.TestCase):
    """parse_lab_args builds argparse.Namespace from CLI flags."""

    @patch('mniplab.runner.setLogLevel')
    @patch('mniplab.runner.setup_logging')
    @patch('sys.argv', ['lab.py'])
    def test_defaults(self, mock_setup, mock_set_level):
        args = parse_lab_args()
        self.assertFalse(args.enable_web)
        # Unset, so MNIPLAB_HOST and MNIPLAB_PORT decide the address.
        self.assertIsNone(args.web_host)
        self.assertIsNone(args.web_port)
        self.assertEqual(args.log_level, 'info')
        mock_set_level.assert_called_once_with('info')
        mock_setup.assert_called_once_with('info', False)

    @patch('mniplab.runner.setLogLevel')
    @patch('mniplab.runner.setup_logging')
    @patch('sys.argv', ['lab.py', '--enable-web', '--web-port', '9000'])
    def test_enable_web(self, mock_setup, mock_set_level):
        args = parse_lab_args()
        self.assertTrue(args.enable_web)
        self.assertEqual(args.web_port, 9000)
        mock_setup.assert_called_once_with('info', True)

    @patch('mniplab.runner.setLogLevel')
    @patch('mniplab.runner.setup_logging')
    @patch('sys.argv', ['lab.py', '--log-level', 'debug'])
    def test_log_level_choice(self, mock_setup, mock_set_level):
        args = parse_lab_args()
        self.assertEqual(args.log_level, 'debug')
        mock_setup.assert_called_once_with('debug', False)

    @patch('mniplab.runner.setLogLevel')
    @patch('mniplab.runner.setup_logging')
    @patch('sys.argv', ['lab.py'])
    def test_custom_description(self, mock_setup, mock_set_level):
        # argparse stores description internally; just ensure no exception
        args = parse_lab_args(description='Custom Lab')
        self.assertEqual(args.log_level, 'info')


class RunLabTests(unittest.TestCase):
    """run_lab orchestrates network start, web UI, CLI, and cleanup."""

    def _keep_standard_streams(self) -> None:
        """Put fds 0-2 back after the test, should run_lab not do so.

        run_lab points them at a pseudo-terminal under --enable-web.
        """
        for fd in (0, 1, 2):
            copy = os.dup(fd)
            self.addCleanup(os.close, copy)
            self.addCleanup(os.dup2, copy, fd)

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_starts_network_and_cli(self, mock_set_level, mock_setup):
        net = Mock()
        with patch('mniplab.cli.mnIPLabCLI') as mock_cli_cls:
            run_lab(net, enable_web=False, start_network=True)

        net.start.assert_called_once()
        mock_cli_cls.assert_called_once_with(net)
        net.stop.assert_called_once()  # stop is called in finally block

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_skip_start_for_custom_config(self, mock_set_level, mock_setup):
        net = Mock()
        with patch('mniplab.cli.mnIPLabCLI') as mock_cli_cls:
            run_lab(net, enable_web=False, start_network=False)

        net.start.assert_not_called()
        mock_cli_cls.assert_called_once_with(net)

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_a_started_lab_publishes_no_hosts_snapshot(self, mock_set_level, mock_setup):
        net = Mock()
        with patch('mniplab.utils.update_hosts_file') as update:
            with patch('mniplab.cli.mnIPLabCLI'):
                run_lab(net, enable_web=False, start_network=True)
        update.assert_not_called()

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_web_server_start_and_stop(self, mock_set_level, mock_setup):
        net = Mock()
        with patch('mniplab.webserver.start_server') as mock_start:
            with patch('mniplab.webserver.stop_server') as mock_stop:
                with patch('mniplab.cli.mnIPLabCLI'):
                    run_lab(net, enable_web=True, web_host='127.0.0.1', web_port=8080)

        mock_start.assert_called_once_with(
            net, host='127.0.0.1', port=8080, block=False, prompt_fd=ANY, local_fd=ANY,
            prompt_interrupt=None,
        )
        mock_stop.assert_called_once()
        net.stop.assert_called_once()

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_web_mode_stops_the_server_before_the_lab(self, mock_set_level, mock_setup):
        net = Mock()
        order = Mock()
        order.attach_mock(net.stop, 'net_stop')
        with patch('mniplab.webserver.start_server'):
            with patch('mniplab.webserver.stop_server') as mock_stop:
                order.attach_mock(mock_stop, 'stop_server')
                with patch('mniplab.cli.mnIPLabCLI'):
                    run_lab(net, enable_web=True)

        self.assertEqual(
            [c[0] for c in order.mock_calls], ['stop_server', 'net_stop']
        )

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_web_mode_stops_the_lab_when_the_server_will_not_stop(
        self, mock_set_level, mock_setup
    ):
        net = Mock()
        with patch('mniplab.webserver.start_server'):
            with patch('mniplab.webserver.stop_server', side_effect=RuntimeError('boom')):
                with patch('mniplab.cli.mnIPLabCLI'):
                    with self.assertRaises(RuntimeError):
                        run_lab(net, enable_web=True)

        net.stop.assert_called_once()

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_web_server_starts_before_the_terminal_goes_raw(self, mock_set_level, mock_setup):
        net = Mock()
        order = Mock()
        order.start_server.side_effect = RuntimeError('port in use')
        self._keep_standard_streams()
        with patch('mniplab.webserver.start_server', order.start_server):
            with patch('mniplab.webserver.stop_server'):
                with patch('mniplab.cli.mnIPLabCLI'):
                    with patch('os.isatty', return_value=True):
                        with patch('termios.tcgetattr', return_value=[]):
                            with patch('termios.tcsetattr'):
                                with patch('tty.setraw', order.setraw):
                                    with patch('fcntl.ioctl'):
                                        with patch('select.select', return_value=([], [], [])):
                                            with self.assertRaises(RuntimeError):
                                                run_lab(net, enable_web=True)

        order.start_server.assert_called_once()
        order.setraw.assert_not_called()

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_a_web_server_that_will_not_start_stops_the_lab(self, mock_set_level, mock_setup):
        net = Mock()
        self._keep_standard_streams()
        with patch('mniplab.webserver.start_server', side_effect=RuntimeError('port in use')):
            with patch('mniplab.webserver.stop_server'):
                with patch('mniplab.cli.mnIPLabCLI') as mock_cli_cls:
                    with self.assertRaises(RuntimeError):
                        run_lab(net, enable_web=True)

        mock_cli_cls.assert_not_called()
        net.stop.assert_called_once()

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_web_server_address_left_to_settings_without_flags(self, mock_set_level, mock_setup):
        net = Mock()
        with patch('mniplab.webserver.start_server') as mock_start:
            with patch('mniplab.webserver.stop_server'):
                with patch('mniplab.cli.mnIPLabCLI'):
                    run_lab(net, enable_web=True)

        mock_start.assert_called_once_with(
            net, host=None, port=None, block=False, prompt_fd=ANY, local_fd=ANY,
            prompt_interrupt=None,
        )

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_non_web_mode_calls_net_stop(self, mock_set_level, mock_setup):
        net = Mock()
        with patch('mniplab.cli.mnIPLabCLI'):
            run_lab(net, enable_web=False)
        net.stop.assert_called_once()

    def _own_pseudo_terminal(self) -> tuple:
        """Open the pseudo-terminal run_lab will use, closed after the test."""
        master, slave = os.openpty()
        for fd in (master, slave):
            self.addCleanup(_close_quietly, fd)
        return master, slave

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_a_web_server_that_will_not_start_leaves_no_prompt_terminal_open(
        self, mock_set_level, mock_setup
    ):
        self._keep_standard_streams()
        master, slave = self._own_pseudo_terminal()
        with patch('pty.openpty', return_value=(master, slave)):
            with patch('mniplab.webserver.start_server', side_effect=RuntimeError('port in use')):
                with patch('mniplab.webserver.stop_server'):
                    with patch('mniplab.cli.mnIPLabCLI'):
                        with self.assertRaises(RuntimeError):
                            run_lab(Mock(), enable_web=True)

        with self.assertRaises(OSError, msg='the prompt master was left open'):
            os.fstat(master)

    @patch('mniplab.runner.setup_logging')
    @patch('mniplab.runner.setLogLevel')
    def test_a_refused_hangup_still_ends_a_prompt_without_terminal(
        self, mock_set_level, mock_setup
    ):
        """Without CAP_SYS_ADMIN the prompt still sees end of input, and it is logged."""
        self._keep_standard_streams()
        master, slave = self._own_pseudo_terminal()
        main_ident = threading.get_ident()
        prompt_ended = threading.Event()

        def prompt(net: Mock) -> None:
            """Read the pseudo-terminal until end of input, as the Lab Prompt does."""
            time.sleep(0.3)
            signal.pthread_kill(main_ident, signal.SIGINT)  # the supervisor's SIGINT
            while os.read(0, 1024):
                pass
            prompt_ended.set()

        with patch('pty.openpty', return_value=(master, slave)):
            with patch('os.isatty', return_value=False):
                with patch('mniplab.webserver.start_server'):
                    with patch('mniplab.webserver.stop_server'):
                        with patch('mniplab.cli.mnIPLabCLI', side_effect=prompt):
                            with patch('fcntl.ioctl', side_effect=PermissionError(1, 'EPERM')):
                                with self.assertLogs('mniplab.runner', 'WARNING'):
                                    run_lab(Mock(), enable_web=True)

        self.assertTrue(prompt_ended.wait(timeout=2), 'the prompt never saw end of input')


def _signal(signum: int) -> None:
    """Send ``signum`` to this process, as a supervisor or a Ctrl+C would."""
    os.kill(os.getpid(), signum)


def _streams() -> List[Tuple[int, int]]:
    """Return the (device, inode) each standard stream is attached to."""
    return [(os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in (0, 1, 2)]


_HELD = _LabSignals._HELD  # pylint: disable=protected-access


class RunLabSignalTests(unittest.TestCase):
    """A Lab stops whatever signal ends the prompt or arrives while it stops."""

    def setUp(self) -> None:
        self.handlers = {signum: signal.getsignal(signum) for signum in _HELD}
        for signum, handler in self.handlers.items():
            self.addCleanup(signal.signal, signum, handler)
        for target in ('mniplab.runner.setup_logging', 'mniplab.runner.setLogLevel'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        for fd in (0, 1, 2):
            copy = os.dup(fd)
            self.addCleanup(os.close, copy)
            self.addCleanup(os.dup2, copy, fd)
        self.streams = _streams()

    def assert_handlers_restored(self) -> None:
        """The handlers in place before run_lab are in place again."""
        self.assertEqual({signum: signal.getsignal(signum) for signum in _HELD},
                         self.handlers)

    def test_sigterm_or_sighup_at_the_prompt_stops_the_lab_and_exits_128_plus_it(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signal.Signals(signum).name):
                net = Mock()
                with patch('mniplab.cli.mnIPLabCLI', side_effect=lambda _, s=signum: _signal(s)):
                    with self.assertRaises(SystemExit) as raised:
                        run_lab(net, enable_web=False)

                self.assertEqual(raised.exception.code, 128 + signum)
                net.stop.assert_called_once()
                self.assert_handlers_restored()

    def test_signals_during_the_cli_mode_shutdown_are_ignored(self) -> None:
        net = Mock()
        stopped = []

        def stop() -> None:
            """Stop the Lab slowly, under repeated Ctrl+C and SIGTERM."""
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                _signal(signum)
                time.sleep(0.01)
            stopped.append(True)

        net.stop.side_effect = stop
        with patch('mniplab.cli.mnIPLabCLI'):
            run_lab(net, enable_web=False)

        self.assertEqual(stopped, [True])
        self.assert_handlers_restored()

    def test_a_command_the_lab_stop_runs_survives_ctrl_c(self) -> None:
        """The terminal sends Ctrl+C to the whole group, the Lab stop's commands too."""
        net = Mock()
        results = []

        def stop() -> None:
            """Run a command, as the Lab stop runs ovs-vsctl, and Ctrl+C it."""
            command = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.5)'])
            time.sleep(0.2)
            os.kill(command.pid, signal.SIGINT)
            results.append(command.wait(timeout=5))

        net.stop.side_effect = stop
        with patch('mniplab.cli.mnIPLabCLI'):
            run_lab(net, enable_web=False)

        self.assertEqual(results, [0], 'Ctrl+C killed a command of the Lab stop')

    def _run_on_a_terminal(self, net: Mock, stop_server: Mock, tcsetattr: Mock) -> None:
        """Run an --enable-web Lab whose prompt runs on a (patched) terminal."""
        with patch('mniplab.webserver.start_server'), \
                patch('mniplab.webserver.stop_server', stop_server), \
                patch('mniplab.cli.mnIPLabCLI'), \
                patch('os.isatty', return_value=True), \
                patch('termios.tcgetattr', return_value=[]), \
                patch('termios.tcsetattr', tcsetattr), \
                patch('tty.setraw'), patch('fcntl.ioctl'), \
                patch('select.select', return_value=([], [], [])):
            run_lab(net, enable_web=True)

    def test_a_signal_at_each_web_shutdown_step_skips_no_later_step(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            for step in ('terminal restore', 'server stop', 'lab stop'):
                with self.subTest(signal=signal.Signals(signum).name, step=step):
                    net, stop_server, tcsetattr = Mock(), Mock(), Mock()
                    sender = {'terminal restore': tcsetattr, 'server stop': stop_server,
                              'lab stop': net.stop}[step]
                    sender.side_effect = lambda *_: _signal(signum)

                    self._run_on_a_terminal(net, stop_server, tcsetattr)

                    tcsetattr.assert_called_once()
                    stop_server.assert_called_once()
                    net.stop.assert_called_once()
                    self.assertEqual(_streams(), self.streams, 'the streams were not restored')
                    self.assert_handlers_restored()

    def test_sigterm_ends_a_web_prompt_without_terminal_and_exits_128_plus_it(self) -> None:
        net, stop_server = Mock(), Mock()
        prompt_ended = threading.Event()

        def prompt(_: Mock) -> None:
            """Read the pseudo-terminal until end of input, as the Lab Prompt does."""
            while os.read(0, 1024):
                pass
            prompt_ended.set()

        timer = threading.Timer(0.3, _signal, (signal.SIGTERM,))
        self.addCleanup(timer.cancel)
        with patch('os.isatty', return_value=False), \
                patch('mniplab.webserver.start_server'), \
                patch('mniplab.webserver.stop_server', stop_server), \
                patch('mniplab.cli.mnIPLabCLI', side_effect=prompt), \
                patch('fcntl.ioctl', side_effect=PermissionError(1, 'EPERM')), \
                self.assertLogs('mniplab.runner', 'WARNING'):
            # With the hangup refused, the prompt's thread is seen to end.
            timer.start()
            with self.assertRaises(SystemExit) as raised:
                run_lab(net, enable_web=True)

        self.assertEqual(raised.exception.code, 128 + signal.SIGTERM)
        self.assertTrue(prompt_ended.wait(timeout=2), 'the prompt never saw end of input')
        stop_server.assert_called_once()
        net.stop.assert_called_once()
        self.assert_handlers_restored()

    def test_ctrl_c_during_the_hangup_keeps_a_sigterm_exit(self) -> None:
        """No terminal: SIGTERM ends the prompt, then Ctrl+C lands while it is hung up."""
        net, stop_server = Mock(), Mock()
        prompt_ended = threading.Event()

        def prompt(_: Mock) -> None:
            """Read the pseudo-terminal until end of input, as the Lab Prompt does."""
            while os.read(0, 1024):
                pass
            prompt_ended.set()

        def hang_up(*_: object) -> None:
            """Take a Ctrl+C mid-hangup, then refuse it so the prompt's input ends."""
            _signal(signal.SIGINT)
            time.sleep(0.01)
            raise PermissionError(1, 'EPERM')

        timer = threading.Timer(0.3, _signal, (signal.SIGTERM,))
        self.addCleanup(timer.cancel)
        with patch('os.isatty', return_value=False), \
                patch('mniplab.webserver.start_server'), \
                patch('mniplab.webserver.stop_server', stop_server), \
                patch('mniplab.cli.mnIPLabCLI', side_effect=prompt), \
                patch('fcntl.ioctl', side_effect=hang_up), \
                self.assertLogs('mniplab.runner', 'WARNING'):
            timer.start()
            with self.assertRaises(SystemExit) as raised:
                run_lab(net, enable_web=True)

        self.assertEqual(raised.exception.code, 128 + signal.SIGTERM)
        self.assertTrue(prompt_ended.wait(timeout=2), 'the prompt never saw end of input')
        stop_server.assert_called_once()
        net.stop.assert_called_once()
        self.assert_handlers_restored()

    def test_a_handler_not_set_from_python_is_left_at_the_default(self) -> None:
        """A previous handler Python cannot name (``None``) comes back as SIG_DFL."""
        real_signal = signal.signal
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

        def install(signum: int, handler: Any) -> Any:
            """Install for real, reporting SIGHUP's first previous handler as foreign."""
            previous = real_signal(signum, handler)
            if signum == signal.SIGHUP and not reported:
                reported.append(True)
                return None
            return previous

        reported: List[bool] = []
        with patch('signal.signal', side_effect=install), patch('mniplab.cli.mnIPLabCLI'):
            run_lab(Mock(), enable_web=False)

        self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_DFL)


if __name__ == '__main__':
    unittest.main()
