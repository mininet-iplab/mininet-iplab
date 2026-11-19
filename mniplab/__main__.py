#!/usr/bin/env python3
"""Entry point for the ``python -m mniplab`` command-line interface."""

import sys
import argparse
from mininet.log import setLogLevel, info
from mniplab import __version__
from mniplab.errors import WebServerError
from mniplab.runner import load_lab_environment
from mniplab.cleanup import (
    full_cleanup,
    kill_frr_processes,
    cleanup_frr_configs,
    cleanup_python_cache,
    cleanup_mniplab_files,
    cleanup_mininet_leftovers,
)


class _Formatter(argparse.HelpFormatter):
    """Help formatter output display."""

    def add_argument(self, action: argparse.Action) -> None:
        super().add_argument(action)
        self._action_max_length = max(self._action_max_length, 24)


def main() -> int:
    """Main entry point for mnIPLab CLI."""
    load_lab_environment()
    parser = argparse.ArgumentParser(
        prog='python -m mniplab',
        description='Mininet-IPLab utilities.',
        formatter_class=_Formatter,
    )
    
    parser.add_argument(
        '-v', '--version',
        action='version',
        version=f'Mininet-IPLab {__version__}'
    )
    
    parser.add_argument(
        '-c', '--cleanup',
        action='store_true',
        help='Alias for the cleanup command'
    )
    
    subparsers = parser.add_subparsers(dest='command', metavar='<command>')
    
    # cleanup command
    cleanup_parser = subparsers.add_parser(
        'cleanup',
        help='removes the runtime root and stops every Lab on the host, kills FRR processes, and removes temp files',
        description='removes the runtime root and stops every Lab on the host, kills FRR processes, and removes temp files.',
    )
    cleanup_parser.add_argument(
        '--keep-committed',
        action='store_true',
        default=True,
        help='Keep ./frr-config/ directory (default: True)'
    )
    cleanup_parser.add_argument(
        '--no-keep-committed',
        action='store_false',
        dest='keep_committed',
        help='Remove ./frr-config/ directory'
    )
    
    # kill-frr command
    subparsers.add_parser(
        'kill-frr',
        help='Kill FRR daemon processes'
    )
    
    # cleanup-configs command
    subparsers.add_parser(
        'cleanup-configs',
        help='Remove temporary FRR config files'
    )
    
    # cleanup-python command
    subparsers.add_parser(
        'cleanup-python',
        help='Remove Python cache files'
    )
    
    # cleanup-mniplab command
    subparsers.add_parser(
        'cleanup-mniplab',
        help='Remove mnIPLab temp files'
    )
    
    # cleanup-mininet command
    subparsers.add_parser(
        'cleanup-mininet',
        help='Clean up Mininet leftovers'
    )

    # serve command
    serve_parser = subparsers.add_parser(
        'serve',
        help='Start the classroom server: no Lab up front, Labs started from the browser'
    )
    serve_parser.add_argument(
        '--web-host',
        help='Bind address (default: MNIPLAB_HOST, else 0.0.0.0)'
    )
    serve_parser.add_argument(
        '--web-port',
        type=int,
        help='Port (default: MNIPLAB_PORT, else 8050)'
    )
    serve_parser.add_argument(
        '--log-level',
        default='info',
        choices=['debug', 'info', 'warning', 'error'],
        help='Log level (default: info)'
    )
    
    args = parser.parse_args()
    
    # Enable verbose Mininet logging for cleanup output
    setLogLevel('info')
    
    # Handle -c / --cleanup shorthand (no subcommand needed)
    if args.cleanup:
        info('\n=== mnIPLab Cleanup ===\n')
        full_cleanup(keep_committed=True, verbose=True)
        info('=== Cleanup Complete ===\n')
        return 0
    
    if not args.command:
        parser.print_help()
        return 0
    
    # Execute the appropriate command
    if args.command == 'cleanup':
        info('\n=== mnIPLab Cleanup ===\n')
        full_cleanup(keep_committed=args.keep_committed, verbose=True)
        info('=== Cleanup Complete ===\n')
        return 0
    
    elif args.command == 'kill-frr':
        info('\n=== Killing FRR Processes ===\n')
        killed = kill_frr_processes(verbose=True)
        info(f'\n✓ Killed {killed} FRR daemon processes\n')
        return 0
    
    elif args.command == 'cleanup-configs':
        info('\n=== Cleaning FRR Configs ===\n')
        cleanup_frr_configs(keep_committed=True, verbose=True)
        info('=== Cleanup Complete ===\n')
        return 0
    
    elif args.command == 'cleanup-python':
        info('\n=== Cleaning Python Cache ===\n')
        cleanup_python_cache(verbose=True)
        info('=== Cleanup Complete ===\n')
        return 0
    
    elif args.command == 'cleanup-mniplab':
        info('\n=== Cleaning mnIPLab Files ===\n')
        cleanup_mniplab_files(verbose=True)
        info('=== Cleanup Complete ===\n')
        return 0
    
    elif args.command == 'cleanup-mininet':
        info('\n=== Cleaning Mininet Leftovers ===\n')
        cleanup_mininet_leftovers(verbose=True)
        info('=== Cleanup Complete ===\n')
        return 0

    elif args.command == 'serve':
        from mniplab.webserver import start_server
        from mniplab.webserver.config import settings
        from mniplab.logging import setup_logging

        settings.enable_web = True
        setup_logging(
            level=args.log_level.upper(),
            log_file="/tmp/mniplab-web.log",
            log_to_stdout=False,
            force=True,
            root_logger=False  # Web server logs only
        )
        try:
            # net=None → no pre-loaded lab; users select a lab from the web UI
            start_server(net=None, host=args.web_host, port=args.web_port, block=True)
        except KeyboardInterrupt:
            print('\n*** Web server stopped\n')
            return 0
        except WebServerError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
