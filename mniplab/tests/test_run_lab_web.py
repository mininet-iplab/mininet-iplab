"""Integration tests for ``run_lab``: the web UI, and how a Lab script ends.

These tests require root privileges and start a real Lab.
Run with: python -m pytest -m integration mniplab/tests/test_run_lab_web.py -v
"""

import os
import pty
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

from mniplab import mnIPLab, run_lab
from mniplab.cleanup import emergency_cleanup
from mniplab.constants import LAB_KEY_ENV
from mniplab.errors import WebServerError
from mniplab.lab_path import lab_dir_for
from mniplab.tests.labs import LabCase

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A Lab script as an Instructor writes one: build, then hand it to run_lab.
LAB_SCRIPT = textwrap.dedent("""\
    import sys
    from mniplab import mnIPLab, run_lab

    net = mnIPLab(topo=None, controller=None)
    r1 = net.addRouter('r1')
    h1 = net.addHost('h1')
    net.addLink(r1, h1, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})
    run_lab(net, enable_web=True, web_host='127.0.0.1', web_port=int(sys.argv[1]))
""")


def _stream_identity(fd: int) -> tuple:
    """Return what a standard stream is attached to."""
    info = os.fstat(fd)
    return info.st_dev, info.st_ino, info.st_rdev


def _kill_if_running(proc: subprocess.Popen) -> None:
    """Kill and reap ``proc`` should it still be running."""
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def _stop_if_running(net: mnIPLab) -> None:
    """Stop ``net`` for the next test should run_lab have left it running."""
    if os.path.exists(net.lab_dir):
        net.stop()


def _pids() -> List[int]:
    """Return the IDs of every process now running."""
    return [int(entry) for entry in os.listdir('/proc') if entry.isdigit()]


def _net_namespace(pid: int) -> Optional[str]:
    """Return the network namespace of ``pid``, or None once it is gone."""
    try:
        return os.readlink(f'/proc/{pid}/ns/net')
    except OSError:
        return None


def _node_namespaces(lab_pid: int) -> Set[str]:
    """Return the network namespaces of the processes a Lab process started."""
    parents = {}
    for pid in _pids():
        try:
            stat = Path(f'/proc/{pid}/stat').read_text()
            parents[pid] = int(stat.rsplit(')', 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
    descendants = {lab_pid}
    grew = True
    while grew:
        found = {pid for pid, parent in parents.items() if parent in descendants}
        grew = not found <= descendants
        descendants |= found
    own = _net_namespace(os.getpid())
    namespaces = {_net_namespace(pid) for pid in descendants - {lab_pid}}
    return {namespace for namespace in namespaces if namespace not in (None, own)}


def _processes_in(namespaces: Set[str]) -> List[int]:
    """Return the processes still running in any of ``namespaces``."""
    return [pid for pid in _pids() if _net_namespace(pid) in namespaces]


def _links_named(prefix: str) -> List[str]:
    """Return the host's network links whose name starts with ``prefix``."""
    lines = subprocess.run(['ip', '-o', 'link'], capture_output=True, text=True,
                           check=True).stdout.splitlines()
    names = [line.split(':', 2)[1].strip().split('@', 1)[0] for line in lines]
    return [name for name in names if name.startswith(prefix)]


class RunLabWebStartFailureTests(LabCase):
    """A web server that cannot start stops the Lab and gives the terminal back."""

    def test_a_busy_port_stops_the_lab_and_raises_the_bind_error(self) -> None:
        """The bind error reaches the caller, after the streams and the Lab are put back."""
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.bind(('127.0.0.1', 0))
        holder.listen()
        port = holder.getsockname()[1]

        streams = [_stream_identity(fd) for fd in (0, 1, 2)]
        # Should run_lab keep the streams, put them back for the rest of the suite.
        for fd in (0, 1, 2):
            copy = os.dup(fd)
            self.addCleanup(os.close, copy)
            self.addCleanup(os.dup2, copy, fd)

        net = mnIPLab(topo=None, controller=None, lab_key='p5wb')
        r1 = net.addRouter('r1')
        h1 = net.addHost('h1')
        net.addLink(r1, h1, params1={'ip': '10.0.0.1/24'}, params2={'ip': '10.0.0.2/24'})
        lab_dir = net.lab_dir
        self.addCleanup(_stop_if_running, net)

        with self.assertRaises(WebServerError) as raised:
            run_lab(net, enable_web=True, web_host='127.0.0.1', web_port=port)

        self.assertIn(f'127.0.0.1:{port}', str(raised.exception))
        self.assertEqual([_stream_identity(fd) for fd in (0, 1, 2)], streams)
        self.assertFalse(os.path.exists(lab_dir), 'the Lab was not stopped')


class RunLabWebWithoutTerminalTests(LabCase):
    """A Lab launched with no terminal, as under a supervisor, ends on SIGINT."""

    def test_sigint_stops_the_lab_and_ends_the_process(self) -> None:
        """The process exits within 5 s of SIGINT, leaving nothing of its Lab."""
        key = 'p6nt'
        workdir = tempfile.mkdtemp(prefix='nt-lab-')
        self.addCleanup(shutil.rmtree, workdir, True)
        script = os.path.join(workdir, 'lab.py')
        with open(script, 'w', encoding='utf-8') as handle:
            handle.write(LAB_SCRIPT)
        output = os.path.join(workdir, 'output')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]

        env = dict(os.environ, PYTHONPATH=REPO)
        env[LAB_KEY_ENV] = key
        with open(output, 'wb') as out:
            proc = subprocess.Popen(
                [sys.executable, script, str(port)], env=env, cwd=workdir,
                stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                start_new_session=True,
            )
        self.addCleanup(lambda: emergency_cleanup(lab_key=key))
        self.addCleanup(_kill_if_running, proc)

        deadline = time.monotonic() + 60
        while b'mininet-iplab>' not in Path(output).read_bytes():
            self.assertIsNone(proc.poll(), Path(output).read_text(errors='replace'))
            self.assertLess(time.monotonic(), deadline, 'the Lab Prompt never opened')
            time.sleep(0.2)
        node_namespaces = _node_namespaces(proc.pid)
        self.assertTrue(node_namespaces, 'no Node namespace found under the Lab process')

        os.kill(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.fail('the process outlived its Lab by more than 5 s after SIGINT')
        self.assertIn('*** Done', Path(output).read_text(errors='replace'))
        self.assertFalse(os.path.exists(lab_dir_for(key)), 'the Lab directory was left behind')
        self.assertEqual(_links_named(f'{key}-'), [], 'veths of the Lab were left behind')
        self.assertEqual(_processes_in(node_namespaces), [],
                         'processes, FRR among them, were left in the Lab\'s Nodes')


def _bridges_named(prefix: str) -> List[str]:
    """Return the Open vSwitch bridges whose name starts with ``prefix``."""
    names = subprocess.run(['ovs-vsctl', 'list-br'], capture_output=True, text=True,
                           check=True).stdout.split()
    return [name for name in names if name.startswith(prefix)]


# This suite's own Lab keys and web port: the ``p7`` prefix and ports from
# 8120 keep it clear of Labs and a web server already running alongside.
SIGTERM_CLI_LAB_KEY = 'p7cl'
SIGTERM_WEB_LAB_KEY = 'p7wb'
SIGTERM_WEB_PORT = 8121


class RunLabSigtermTests(LabCase):
    """A switch-based Lab script stops its Lab on SIGTERM, as on ``exit``."""

    def _launch(self, key: str, args: List[str],
                on_terminal: bool) -> Tuple[subprocess.Popen, Callable[[], bytes]]:
        """Start ``ospf-lab.py`` under ``key``; return the process and its output reader."""
        env = dict(os.environ, PYTHONPATH=REPO)
        env[LAB_KEY_ENV] = key
        command = [sys.executable, 'examples/ospf-lab.py', *args]
        if on_terminal:
            master, slave = pty.openpty()
            self.addCleanup(os.close, master)
            proc = subprocess.Popen(command, env=env, cwd=REPO, stdin=slave, stdout=slave,
                                    stderr=slave, start_new_session=True)
            os.close(slave)
        else:
            master = None
            out_dir = tempfile.mkdtemp(prefix='sigterm-lab-')
            self.addCleanup(shutil.rmtree, out_dir, True)
            output = os.path.join(out_dir, 'output')
            with open(output, 'wb') as out:
                proc = subprocess.Popen(command, env=env, cwd=REPO, stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=out, start_new_session=True)
        self.addCleanup(lambda: emergency_cleanup(lab_key=key))
        self.addCleanup(_kill_if_running, proc)
        seen = bytearray()

        def read() -> bytes:
            """Return everything the script has written so far."""
            if master is None:
                return Path(output).read_bytes()
            while select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                seen.extend(chunk)
            return bytes(seen)

        return proc, read

    def _assert_sigterm_stops_the_lab(self, key: str, args: List[str],
                                      on_terminal: bool) -> None:
        """Send SIGTERM to an ``ospf-lab.py`` at its prompt; assert it stopped its Lab.

        Args:
            key: The Lab key to run the script under.
            args: The script's command-line arguments.
            on_terminal: Whether the script runs on a pseudo-terminal.
        """
        proc, read = self._launch(key, args, on_terminal)
        deadline = time.monotonic() + 90
        while b'mininet-iplab>' not in read():
            self.assertIsNone(proc.poll(), read().decode(errors='replace'))
            self.assertLess(time.monotonic(), deadline, 'the Lab Prompt never opened')
            time.sleep(0.2)
        self.assertTrue(_bridges_named(f'{key}-'), 'the Lab has no switch to leave behind')

        os.kill(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while proc.poll() is None and time.monotonic() < deadline:
            read()  # Keep a terminal drained, so the script never blocks on output.
            time.sleep(0.1)
        self.assertIsNotNone(proc.poll(), 'the script outlived SIGTERM by more than 10 s')

        self.assertEqual(proc.returncode, 128 + signal.SIGTERM, read().decode(errors='replace'))
        self.assertIn(b'*** Stopping', read())
        self.assertFalse(os.path.exists(lab_dir_for(key)), 'the Lab directory was left behind')
        self.assertEqual(_bridges_named(f'{key}-'), [], 'switches of the Lab were left behind')
        self.assertEqual(_links_named(f'{key}-'), [], 'veths of the Lab were left behind')

    def test_sigterm_stops_a_cli_mode_lab_on_a_terminal(self) -> None:
        """CLI Mode on a terminal: exit 143 within 10 s, nothing of the Lab left."""
        self._assert_sigterm_stops_the_lab(SIGTERM_CLI_LAB_KEY, [], on_terminal=True)

    def test_sigterm_stops_a_web_lab_without_terminal(self) -> None:
        """--enable-web with no terminal: exit 143 within 10 s, nothing of the Lab left."""
        self._assert_sigterm_stops_the_lab(
            SIGTERM_WEB_LAB_KEY,
            ['--enable-web', '--web-host', '127.0.0.1', '--web-port', str(SIGTERM_WEB_PORT)],
            on_terminal=False)
