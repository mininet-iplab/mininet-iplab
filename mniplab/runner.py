"""Lab runtime orchestration: argument parsing, logging setup, and network lifecycle."""

import argparse
import importlib.util
import os
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Dict, Optional

from dotenv import load_dotenv
from mininet.log import info, setLogLevel
from mniplab.constants import MNIPLAB_WEB_LOG_FILE, env_file_path
from mniplab.logging import setup_logging as setup_mniplab_logging

if TYPE_CHECKING:
    from mininet.net import Mininet

# Default paths, taken from the constants a Lab actually binds so an
# environment override reaches the CLI and cleanup alike.
DEFAULT_WEB_LOG_FILE = MNIPLAB_WEB_LOG_FILE
DEFAULT_WEB_HOST = '0.0.0.0'
DEFAULT_WEB_PORT = 8050
DEFAULT_IPERF_DURATION = 10

# Valid values
VALID_LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

# What load_lab_environment copied into os.environ from the env file, so a
# reader can tell a value from the file apart from a shell export.
_FROM_ENV_FILE: Dict[str, str] = {}


def load_lab_environment() -> None:
    """Load the env file into the process environment for a lab run.

    ``.env.example`` tells a user to copy it to ``.env``, edit the values and
    restart -- but only the web server's pydantic ``Settings`` ever read that
    file. Everything reached through a bare ``os.getenv`` call, including
    the ``MNIPLAB_PRIVATE_NAMESPACES`` escape hatch, saw nothing:
    ``0`` in ``.env`` did not disable anything, and the hatch an instructor
    is told to reach for mid-class answered only to a shell export.

    Called from the lab entry points rather than at package import, so that
    importing ``mniplab`` never mutates ``os.environ`` as a side effect of
    the working directory -- which would silently feed a developer's local
    ``.env`` into the unit suite. ``override=False`` (the default) keeps a
    shell export winning, so ``MNIPLAB_PRIVATE_NAMESPACES=0 python3
    examples/static-lab.py`` still overrides the file. The path is explicit
    rather than ``find_dotenv()``'s upward walk, so ``.env`` resolves against
    the working directory, matching the web server's ``Settings``; both take
    the path from :func:`mniplab.constants.env_file_path`, which is how
    ``MNIPLAB_ENV_FILE`` names another file for both at once.

    The trade this makes: settings read *eagerly*, at module scope, are
    already fixed by the time a lab reaches an entry point -- everything in
    ``mniplab.constants`` (``FRR_INIT_SCRIPT``, ``MNIPLAB_TMP_DIR``
    and the rest) is evaluated during ``import
    mniplab`` and so still answers only to a shell export. None of them
    appears in ``.env.example``, so none is advertised as ``.env``-settable;
    a setting that needs to be, needs to be read lazily like the hatch is.

    What the file supplied is remembered, for :func:`set_by_env_file`.
    """
    exported = set(os.environ)
    load_dotenv(env_file_path())
    _FROM_ENV_FILE.update(
        {name: value for name, value in os.environ.items() if name not in exported}
    )


def set_by_env_file(name: str) -> bool:
    """Whether a variable holds the value :func:`load_lab_environment` read from the env file.

    The env file sits below a shell export, and once loaded both are in
    ``os.environ``; this tells them apart.

    Args:
        name: The variable's name.

    Returns:
        True when the env file, not a shell export, set the variable's value.
    """
    value = _FROM_ENV_FILE.get(name)
    return value is not None and os.environ.get(name) == value


def load_lab_example(path: str) -> ModuleType:
    """Import a Lab Example script and return it as a module.

    The one way a Lab Example file becomes a ``build_network`` to call: the
    Web UI's lab start and the test suite both go through here, so what
    counts as a valid Lab Example is decided once.

    Args:
        path: Filesystem path of the Lab Example script.

    Returns:
        The imported module, exposing ``build_network()``.

    Raises:
        ValueError: If the file cannot be imported as a module or defines no
            ``build_network`` function.
    """
    name = f"lab_{os.path.splitext(os.path.basename(path))[0].replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Not a Lab Example script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'build_network', None)):
        raise ValueError(f"Lab Example {path} has no build_network() function")
    return module


def setup_logging(log_level: str, enable_web: bool, log_file: str = DEFAULT_WEB_LOG_FILE) -> None:
    """Configure logging for mnIPLab and Mininet.

    Args:
        log_level: Log level string (e.g., 'INFO', 'DEBUG')
        enable_web: Whether web UI is enabled
        log_file: Path to log file (used when enable_web is True)

    Note:
        When web UI is enabled, only webserver-related logs are written to the
        log file. Lab operations, FRR daemons, and Mininet events remain on stdout.
    """
    if enable_web:
        setup_mniplab_logging(
            level=log_level.upper(),
            log_file=log_file,
            log_to_stdout=False,
            force=True,
            root_logger=False,  # Web server logs only
        )
    else:
        setup_mniplab_logging(level=log_level.upper(), force=True)


def parse_lab_args(description: str = 'mnIPLab Network') -> argparse.Namespace:
    """Parse standard lab CLI arguments (--enable-web, --web-host, --web-port, --log-level)."""
    load_lab_environment()
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        '--enable-web',
        action='store_true',
        help='Enable web UI server'
    )
    parser.add_argument(
        '--web-host',
        help=f'Web server bind address (default: MNIPLAB_HOST, else {DEFAULT_WEB_HOST})'
    )
    parser.add_argument(
        '--web-port',
        type=int,
        help=f'Web server port (default: MNIPLAB_PORT, else {DEFAULT_WEB_PORT})'
    )
    parser.add_argument(
        '--log-level',
        default='info',
        choices=['debug', 'info', 'output', 'warning', 'error', 'critical'],
        help='Log level (default: info)'
    )

    args = parser.parse_args()

    # Apply immediately so debug() calls inside build_network() are visible
    setLogLevel(args.log_level)
    setup_logging(args.log_level, args.enable_web)

    return args


def run_lab(net: 'Mininet', enable_web: bool = False, web_host: Optional[str] = None,
            web_port: Optional[int] = None, log_level: str = 'info',
            start_network: bool = True) -> None:
    """Start the network, optionally the web UI, launch CLI, then clean up on exit.

    Pass ``start_network=False`` to perform custom pre-start configuration
    before the network is brought up. A ``web_host`` or ``web_port`` left
    unset is taken from ``MNIPLAB_HOST`` / ``MNIPLAB_PORT``, else the
    defaults.
    """
    # Local import to avoid circular dependency with cli.py
    from mniplab.cli import mnIPLabCLI

    # Also here, not only in parse_lab_args: a lab may call run_lab directly.
    # load_dotenv is idempotent and never overrides what is already set.
    load_lab_environment()

    setLogLevel(log_level)
    setup_logging(log_level, enable_web)

    if start_network:
        info('\n*** Starting network\n')
        net.start()

    if enable_web:
        import fcntl
        import pty
        import select
        import signal
        import termios
        import threading
        import tty
        from mniplab.webserver import start_server, stop_server
        from mniplab.webserver.pseudo_terminal import write_master

        master_fd, slave_fd = pty.openpty()

        saved_stdin = os.dup(0)
        saved_stdout = os.dup(1)
        saved_stderr = os.dup(2)

        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(slave_fd)

        prompt_open = threading.Event()
        main_ident = threading.get_ident()

        def interrupt_prompt() -> None:
            """Interrupt the Lab Prompt, which runs on the main thread."""
            if prompt_open.is_set():
                signal.pthread_kill(main_ident, signal.SIGINT)

        def relay_keys() -> None:
            """Copy the launching terminal's keys to the Lab Prompt."""
            while prompt_open.is_set():
                r, _, _ = select.select([saved_stdin], [], [], 0.2)
                if not r:
                    continue
                chunk = os.read(saved_stdin, 1024)
                if not chunk:
                    break
                try:
                    # The web server makes the master non-blocking.
                    write_master(master_fd, chunk)
                except OSError:
                    break
                if b"\x03" in chunk:
                    interrupt_prompt()

        is_tty = os.isatty(saved_stdin)

        start_server(
            net,
            host=web_host,
            port=web_port,
            block=False,
            prompt_fd=master_fd,
            local_fd=saved_stdout,
            # If raw mode fails below, the prompt runs on a thread and prompt_open
            # stays clear, so this callable does nothing.
            prompt_interrupt=interrupt_prompt if is_tty else None,
        )

        old_attr = None
        if is_tty:
            try:
                old_attr = termios.tcgetattr(saved_stdin)
                tty.setraw(saved_stdin)
                try:
                    winsize = fcntl.ioctl(saved_stdin, termios.TIOCGWINSZ, b"\x00" * 8)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                except OSError:
                    pass
            except termios.error:
                is_tty = False

        relay = None
        try:
            if is_tty:
                # The prompt runs here so Ctrl+C reaches it as on any terminal.
                prompt_open.set()
                relay = threading.Thread(target=relay_keys, daemon=True)
                relay.start()
                mnIPLabCLI(net)
            else:
                cli_thread = threading.Thread(target=mnIPLabCLI, args=(net,), daemon=True)
                cli_thread.start()
                try:
                    while cli_thread.is_alive():
                        cli_thread.join(timeout=0.2)
                except KeyboardInterrupt:
                    pass
        finally:
            prompt_open.clear()
            if relay is not None:
                relay.join(timeout=1)
            if old_attr is not None:
                try:
                    termios.tcsetattr(saved_stdin, termios.TCSADRAIN, old_attr)
                except termios.error:
                    pass
            os.dup2(saved_stdin, 0)
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdin)
            os.close(saved_stdout)
            os.close(saved_stderr)
            try:
                stop_server()
            finally:
                net.stop()
    else:
        try:
            mnIPLabCLI(net)
        finally:
            net.stop()


def isolate_package_imports(package: str) -> None:
    """Resolve authored imports from their package and installed dependencies.

    The library is already imported before this runs in a dedicated Lab or
    drawing process. Its package path continues to resolve library submodules,
    while loose modules in a source checkout cannot mask missing inputs. An
    installed distribution retains its site-packages dependency search path.

    Args:
        package: The authored script's working directory.
    """
    checkout = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source_checkout = os.path.isfile(os.path.join(checkout, "pyproject.toml"))
    package = os.path.realpath(package)
    search_paths = [package]
    for entry in sys.path:
        resolved = os.path.realpath(entry)
        if resolved == package:
            continue
        installed = os.path.basename(resolved) in {"site-packages", "dist-packages"}
        if (source_checkout and not installed
                and (resolved == checkout or resolved.startswith(checkout + os.sep))):
            continue
        search_paths.append(entry)
    sys.path[:] = search_paths
