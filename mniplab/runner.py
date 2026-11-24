"""Lab runtime orchestration: argument parsing, logging setup, and network lifecycle."""

import argparse
import importlib.util
import os
import signal
import sys
import threading
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

from dotenv import load_dotenv
from mininet.log import info, setLogLevel
from mniplab.constants import MNIPLAB_WEB_LOG_FILE, env_file_path
from mniplab.logging import get_logger, setup_logging as setup_mniplab_logging

if TYPE_CHECKING:
    from mininet.net import Mininet

logger = get_logger(__name__)

# Default paths, taken from the constants a Lab actually binds so an
# environment override reaches the CLI and cleanup alike.
DEFAULT_WEB_LOG_FILE = MNIPLAB_WEB_LOG_FILE
DEFAULT_WEB_HOST = '0.0.0.0'
DEFAULT_WEB_PORT = 8050
DEFAULT_IPERF_DURATION = 10

# ioctl(2) that hangs up a terminal (Linux); needs CAP_SYS_ADMIN, as a Lab does.
TIOCVHANGUP = 0x5437

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


class StopSignal(BaseException):
    """SIGTERM or SIGHUP, raised to end the Lab Prompt as ``exit`` does.

    Not a ``KeyboardInterrupt``, which the Lab Prompt catches to stay open.

    Attributes:
        signum: The signal that arrived.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class _LabSignals:
    """What one ``run_lab`` does with SIGINT, SIGTERM and SIGHUP.

    While the Lab runs, SIGTERM and SIGHUP raise :class:`StopSignal`. From
    the moment the prompt has returned until the Lab has stopped, none of
    the three raises, and afterwards every handler is put back. Signal
    handlers belong to the main thread, so run elsewhere this changes
    nothing.
    """

    # The signals that stop the Lab as ``exit`` does.
    _STOPPING = (signal.SIGTERM, signal.SIGHUP)
    _HELD = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

    def __init__(self) -> None:
        self._previous: Dict[int, Any] = {}
        self._active = threading.current_thread() is threading.main_thread()

    def install(self) -> None:
        """Have SIGTERM and SIGHUP end the Lab Prompt."""
        if self._active:
            for signum in self._STOPPING:
                self._previous[signum] = signal.signal(signum, self._end_prompt)

    @staticmethod
    def _end_prompt(signum: int, frame: Any) -> None:
        """End the Lab Prompt, from the main thread it runs on."""
        raise StopSignal(signum)

    def hold(self) -> None:
        """Ignore all three signals, keeping the first handler seen for each.

        Called as soon as the prompt has returned, so nothing before the
        shutdown's first step can be interrupted either.

        Ignored rather than caught: the commands the Lab stop runs, in the
        terminal's process group, inherit the disposition, so a Ctrl+C at
        the terminal cannot kill one halfway through removing a switch.
        """
        if self._active:
            for signum in self._HELD:
                self._previous.setdefault(signum, signal.signal(signum, signal.SIG_IGN))

    def shut_down(self, *steps: Callable[[], None]) -> None:
        """Run every step in order, none skipped by a signal or a failing step.

        Signals are held before each step, so one that lands before the
        first hold took effect can still skip only the step it lands in.
        A step's error is raised once the later steps have run. A previous
        handler not set from Python, which reads as ``None``, comes back as
        ``SIG_DFL``.

        Args:
            steps: The shutdown, in order.
        """
        try:
            self._run(steps)
        finally:
            for signum, handler in self._previous.items():
                signal.signal(signum, signal.SIG_DFL if handler is None else handler)

    def _run(self, steps: Tuple[Callable[[], None], ...]) -> None:
        """Run the first step, holding signals, then the rest even if it raised."""
        if not steps:
            return
        # Recursion, not a loop: each later step runs in a finally, so every
        # step's error is chained and none stops the steps after it.
        try:
            self.hold()
            steps[0]()
        finally:
            self._run(steps[1:])


def run_lab(net: 'Mininet', enable_web: bool = False, web_host: Optional[str] = None,
            web_port: Optional[int] = None, log_level: str = 'info',
            start_network: bool = True) -> None:
    """Start the network, optionally the web UI, launch CLI, then clean up on exit.

    Pass ``start_network=False`` to perform custom pre-start configuration
    before the network is brought up. A ``web_host`` or ``web_port`` left
    unset is taken from ``MNIPLAB_HOST`` / ``MNIPLAB_PORT``, else the
    defaults.

    SIGTERM and SIGHUP stop the Lab as ``exit`` does. From the moment the
    prompt has returned until the Lab has stopped, SIGINT, SIGTERM and
    SIGHUP are ignored, so none interrupts the shutdown. In CLI Mode, and
    under ``--enable-web`` on a terminal, the prompt runs on the main
    thread itself, so a window of a few bytecodes remains between the
    prompt returning and signals being held: a signal landing there can
    still skip the shutdown.

    This handling is installed only when ``run_lab`` runs on the main
    thread; elsewhere no handler is installed and none is held. A caller's
    own SIGTERM and SIGHUP handlers are replaced for the run and restored
    after it, as is SIGINT's.

    Args:
        net: The Lab to run.
        enable_web: Whether to serve the web UI beside the Lab Prompt.
        web_host: Web server bind address, or None for the settings.
        web_port: Web server port, or None for the settings.
        log_level: Mininet and mnIPLab log level.
        start_network: Whether to start ``net`` before the prompt opens.

    Raises:
        SystemExit: With code ``128 + signum`` when SIGTERM or SIGHUP ended
            the run, once the Lab has stopped.
    """
    # Also here, not only in parse_lab_args: a lab may call run_lab directly.
    # load_dotenv is idempotent and never overrides what is already set.
    load_lab_environment()

    setLogLevel(log_level)
    setup_logging(log_level, enable_web)

    if start_network:
        info('\n*** Starting network\n')
        net.start()

    signals = _LabSignals()
    try:
        _run_prompt(net, signals, enable_web, web_host, web_port)
    except StopSignal as stop:
        raise SystemExit(128 + stop.signum) from None


def _run_prompt(net: 'Mininet', signals: _LabSignals, enable_web: bool,
                web_host: Optional[str], web_port: Optional[int]) -> None:
    """Run the Lab Prompt, then stop the Lab however the prompt ended.

    Args:
        net: The started Lab.
        signals: The run's signal handling, which runs the shutdown.
        enable_web: Whether to serve the web UI beside the prompt.
        web_host: Web server bind address, or None for the settings.
        web_port: Web server port, or None for the settings.
    """
    # Local import to avoid circular dependency with cli.py
    from mniplab.cli import mnIPLabCLI

    if enable_web:
        import fcntl
        import pty
        import select
        import termios
        import tty
        from mniplab.webserver import start_server, stop_server
        from mniplab.webserver.pseudo_terminal import write_master

        master_fd, slave_fd = pty.openpty()
        # run_lab's own handle on the master: the server closes master_fd
        # once the prompt ends, and its number may then be reused.
        prompt_master = os.dup(master_fd)

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

        old_attr = None
        relay = None
        server_started = False
        # A server that fails to start, on a busy port say, falls through the
        # same restore and stop as a normal exit: its error then reaches the
        # launching terminal, not the pseudo-terminal nobody reads.
        try:
            signals.install()
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
            server_started = True

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

            if is_tty:
                # The prompt runs here so Ctrl+C reaches it as on any terminal.
                prompt_open.set()
                relay = threading.Thread(target=relay_keys, daemon=True)
                relay.start()
                mnIPLabCLI(net)
            else:
                cli_thread = threading.Thread(target=mnIPLabCLI, args=(net,), daemon=True)
                cli_thread.start()
                # Kept apart from is_alive(), which a join that SIGINT
                # interrupts can leave reporting the running prompt as ended.
                interrupted = False
                stop: Optional[StopSignal] = None
                try:
                    while cli_thread.is_alive():
                        cli_thread.join(timeout=0.2)
                except KeyboardInterrupt:
                    interrupted = True
                except StopSignal as signalled:
                    interrupted, stop = True, signalled
                # Held before the hangup, so a Ctrl+C now cannot replace a
                # SIGTERM's exit status or skip the shutdown.
                signals.hold()
                # The prompt's thread is still reading its pseudo-terminal, so
                # the server's reader, which the interpreter waits for at exit,
                # never sees the prompt end. A hangup gives that reader end of
                # file, as a prompt that exits does; the thread is a daemon.
                try:
                    fcntl.ioctl(prompt_master, TIOCVHANGUP)
                except OSError as exc:
                    if interrupted:
                        # Without the capability, end the prompt's input
                        # instead: kill the line typed so far, then end of
                        # file, which the prompt reads as exit.
                        logger.warning('Could not hang up the Lab Prompt (%s); '
                                       'ending its input instead', exc)
                        write_master(prompt_master, b"\x15\x04")
                if stop is not None:
                    raise stop
        finally:
            signals.hold()

            def restore_terminal() -> None:
                """End the key relay and give the launching terminal its mode back."""
                prompt_open.clear()
                if relay is not None:
                    relay.join(timeout=1)
                if old_attr is not None:
                    try:
                        termios.tcsetattr(saved_stdin, termios.TCSADRAIN, old_attr)
                    except termios.error:
                        pass

            def restore_streams() -> None:
                """Point fds 0-2 back at the launching terminal."""
                os.dup2(saved_stdin, 0)
                os.dup2(saved_stdout, 1)
                os.dup2(saved_stderr, 2)
                os.close(saved_stdin)
                os.close(saved_stdout)
                os.close(saved_stderr)
                os.close(prompt_master)
                if not server_started:
                    # The server takes master_fd only once it has started.
                    os.close(master_fd)

            signals.shut_down(restore_terminal, restore_streams, stop_server, net.stop)
    else:
        try:
            signals.install()
            mnIPLabCLI(net)
        finally:
            signals.hold()
            signals.shut_down(net.stop)


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
