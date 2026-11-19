"""Unit tests for mniplab.runner — lab entry point orchestration."""

import os
import unittest
from unittest.mock import ANY, Mock, patch

from mniplab.runner import (
    setup_logging,
    parse_lab_args,
    run_lab,
)


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
        # run_lab points fds 0-2 at a pseudo-terminal and, when the server
        # fails to start, does not give them back.
        saved = [os.dup(fd) for fd in (0, 1, 2)]
        for fd, copy in enumerate(saved):
            self.addCleanup(os.close, copy)
            self.addCleanup(os.dup2, copy, fd)
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


if __name__ == '__main__':
    unittest.main()
