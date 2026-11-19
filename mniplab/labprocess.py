"""A Lab in a child process of the manager.

Run as ``python -m mniplab.labprocess``, this module is the Lab's process:
it builds the Lab Example, starts the network, serves the Lab's handle on
the unix socket named by its key, and stays until told to stop.  The
manager side is :class:`LabChild`, which launches that process, learns
when the Lab is ready through a pipe, and notices when the process ends.

The key travels in the environment (``MNIPLAB_LAB_KEY``), so a Lab
Example's ``build_network()`` is run exactly as it is from a terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import sys
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple

from mniplab.constants import LAB_KEY_ENV
from mniplab.errors import LabUnavailableError, mnIPLabError
from mniplab.lab_key import DEFAULT_ALLOCATOR
from mniplab.labrpc import RemoteLabHandle, socket_path_for
from mniplab.persistent_config import CONFIG_COPY_ENV
from mniplab.logging import get_logger

logger = get_logger(__name__)

#: Seconds the manager gives a Lab process to exit after telling it to stop
#: before killing it and sweeping its key.
STOP_TIMEOUT = 90.0

#: Seconds the manager gives a Lab process to build, start, and report ready
#: before killing it and sweeping its key.
START_TIMEOUT = 60.0


class LabStartError(mnIPLabError):
    """Raised when a Lab process ends, or fails, before its Lab is ready."""
    pass


# ---------------------------------------------------------------------------
# The child
# ---------------------------------------------------------------------------

def _report(ready_fd: int, payload: Dict[str, Any], prompt_fd: Optional[int] = None) -> None:
    """Write the readiness line and optional prompt fd to the manager and close the socket."""
    import socket
    sock = socket.socket(fileno=ready_fd)
    data = (json.dumps(payload) + "\n").encode("utf-8")
    try:
        if prompt_fd is not None:
            socket.send_fds(sock, [data], [prompt_fd])
        else:
            sock.sendall(data)
    finally:
        sock.close()


def _script_location(exc: BaseException, lab_file: str) -> str:
    """Where in the Lab Example's own script a start failed, if it was there.

    Args:
        exc: What building or starting the Lab raised.
        lab_file: The script's path.

    Returns:
        `` (line N of <file>: <code>)`` for the script's last frame in the
        traceback, or ``""`` when the failure was elsewhere.
    """
    script = os.path.realpath(lab_file)
    frames = [frame for frame in traceback.extract_tb(exc.__traceback__)
              if os.path.realpath(frame.filename) == script]
    if not frames:
        return ""
    frame = frames[-1]
    code = f": {frame.line.strip()}" if frame.line else ""
    return f" (line {frame.lineno} of {os.path.basename(lab_file)}{code})"


def run_lab_process(example_id: str, lab_file: str, ready_fd: int) -> int:
    """Build, start and serve one Lab until it is stopped.

    Args:
        example_id: The Lab Example's catalog id, for diagnostics.
        lab_file: The Lab Example script to build from.
        ready_fd: A socket to write the readiness line on.

    Returns:
        The process exit code: 0 after a clean stop, 1 when the Lab never
        started.
    """
    import fcntl
    import pty
    import termios
    from mniplab.cli import mnIPLabCLI
    from mniplab.labhandle import LocalLabHandle
    from mniplab.labrpc import LabRPCServer
    from mniplab.runner import load_lab_example

    try:
        module = load_lab_example(lab_file)
        net = module.build_network()
        net.start()
    except Exception as exc:  # whatever the Example raised is the diagnostic
        message = f"{exc}{_script_location(exc, lab_file)}"
        logger.error(f"Lab '{example_id}' failed to start: {message}")
        key = os.environ.get(LAB_KEY_ENV)
        if key:
            from mniplab.cleanup import emergency_cleanup
            emergency_cleanup(lab_key=key)
        _report(ready_fd, {"error": message})
        return 1

    master_fd, slave_fd = pty.openpty()

    handle = LocalLabHandle(net, example=example_id)
    stopped = threading.Event()
    signalled = threading.Event()

    def _unblock_cli() -> None:
        try:
            for b in b"exit\n":
                fcntl.ioctl(0, termios.TIOCSTI, bytes([b]))
        except OSError:
            pass

    def on_signal(signum: int, _frame: Any) -> None:
        signalled.set()
        stopped.set()
        _unblock_cli()

    signal.signal(signal.SIGTERM, on_signal)

    def on_rpc_stop() -> None:
        stopped.set()
        _unblock_cli()

    server = LabRPCServer(handle, socket_path_for(handle.key), on_stopped=on_rpc_stop)
    server.start()
    _report(ready_fd, {
        "ready": True,
        "key": handle.key,
        "nodes": handle.status()["nodes"],
        "socket": server.socket_path,
        "warnings": net.startup_warnings,
    }, prompt_fd=master_fd)
    os.close(master_fd)

    os.login_tty(slave_fd)
    try:
        mnIPLabCLI(net, node_lock=handle.node_lock)
    except Exception as exc:
        logger.debug(f"CLI loop exited: {exc}")

    handle.stop()
    server.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point of ``python -m mniplab.labprocess``."""
    from mininet.log import setLogLevel
    from mniplab.runner import load_lab_environment

    parser = argparse.ArgumentParser(prog="python -m mniplab.labprocess")
    parser.add_argument("--example-id", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--ready-fd", type=int, required=True)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    load_lab_environment()
    setLogLevel(args.log_level)
    return run_lab_process(args.example_id, args.file, args.ready_fd)


# ---------------------------------------------------------------------------
# The manager's side
# ---------------------------------------------------------------------------

def _read_ready_sock(sock: socket.socket) -> Tuple[str, Optional[int]]:
    """Read one readiness line and an optional prompt fd from a socket.

    Args:
        sock: The UNIX domain stream socket connected to the child.

    Returns:
        A tuple of (line, prompt_fd).
    """
    chunks = []
    received_fds: List[int] = []
    try:
        while True:
            try:
                data, fds, _, _ = socket.recv_fds(sock, 4096, 1)
            except OSError:
                break
            if fds:
                received_fds.extend(fds)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
    finally:
        sock.close()
    line = b"".join(chunks).decode("utf-8", errors="replace").split("\n", 1)[0]
    prompt_fd = received_fds[0] if received_fds else None
    return line, prompt_fd


class LabChild:
    """One Lab running in a child process, seen from the manager.

    Attributes:
        key: The Lab's key.
        example: The Lab Example id it runs.
        handle: The RPC client to the Lab's process.
        process: The child process.
        prompt_fd: The Lab's prompt pty master file descriptor, if any.
        exit_reason: Why the process ended, once it has; None while alive.
        startup_warnings: Failures of Startup commands allowed to fail.
    """

    def __init__(
        self,
        key: str,
        example: str,
        process: "asyncio.subprocess.Process",
        handle: RemoteLabHandle,
        prompt_fd: Optional[int] = None,
    ) -> None:
        """Record a launched Lab process.

        Args:
            key: The Lab's key.
            example: The Lab Example id it runs.
            process: The child process.
            handle: The RPC client to it.
            prompt_fd: The Lab's prompt pty master file descriptor, if any.
        """
        self.key = key
        self.example = example
        self.process = process
        self.handle = handle
        self.prompt_fd = prompt_fd
        self.exit_reason: Optional[str] = None
        self.startup_warnings: List[str] = []

    @classmethod
    async def launch(
        cls,
        example_id: str,
        lab_file: str,
        key: Optional[str] = None,
        config_copy: Optional[str] = None,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
    ) -> "LabChild":
        """Start a Lab Example in a child process and wait until its Lab is ready.

        Args:
            example_id: The Lab Example's catalog id.
            lab_file: Its script's path.
            key: The Lab key to hand the process; allocated here when omitted.
            config_copy: The Group's copy directory for Persistent Config,
                handed to the process in the environment; omitted, every
                seed is used directly.
            timeout: Seconds to wait for the Lab to build and report ready
                before timing out; defaults to configured lab_builder_timeout.
            cwd: The process's working directory, so a Python-based Lab
                Example's relative Seed paths name its own package; this
                package stays importable through ``PYTHONPATH``. Omitted,
                the process shares the manager's working directory.

        Returns:
            The running child, its handle answering.

        Raises:
            LabStartError: The process ended, reported an error, or timed out
                before its Lab was ready.  The Lab's keyed objects are swept.
        """
        allocated = key is None
        if key is None:
            key = DEFAULT_ALLOCATOR.allocate(script=lab_file)
        env = dict(os.environ)
        env[LAB_KEY_ENV] = key
        env.pop(CONFIG_COPY_ENV, None)
        if config_copy is not None:
            env[CONFIG_COPY_ENV] = config_copy
        if cwd is not None:
            checkout = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env["PYTHONPATH"] = os.pathsep.join(
                [checkout] + [path for path in env.get("PYTHONPATH", "").split(os.pathsep) if path])

        if timeout is None:
            timeout = START_TIMEOUT

        parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        child_fd = child_sock.fileno()
        try:
            # Package initialization already imports this module through the
            # web services; call main normally rather than re-executing it.
            entry = "from mniplab.labprocess import main; "
            if cwd is not None:
                entry += ("from mniplab.runner import isolate_package_imports; "
                          f"isolate_package_imports({os.path.abspath(cwd)!r}); ")
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", entry + "raise SystemExit(main())",
                "--example-id", example_id,
                "--file", lab_file,
                "--ready-fd", str(child_fd),
                env=env,
                cwd=cwd,
                pass_fds=(child_fd,),
                stdin=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            parent_sock.close()
            child_sock.close()
            if allocated:
                DEFAULT_ALLOCATOR.release(key)
            raise
        finally:
            child_sock.close()

        loop = asyncio.get_running_loop()
        try:
            line, prompt_fd = await asyncio.wait_for(
                loop.run_in_executor(None, _read_ready_sock, parent_sock),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                parent_sock.close()
            except OSError:
                pass
            try:
                process.kill()
            except OSError:
                pass
            await process.wait()
            await sweep_key(key)
            if allocated:
                DEFAULT_ALLOCATOR.release(key)
            raise LabStartError(
                f"Lab '{example_id}' failed to start: build timed out after {timeout:g} s"
            )

        report: Dict[str, Any] = {}
        if line:
            try:
                report = json.loads(line)
            except ValueError:
                report = {}

        if not report.get("ready"):
            if prompt_fd is not None:
                try:
                    os.close(prompt_fd)
                except OSError:
                    pass
            code = await process.wait()
            reason = report.get("error") or f"Lab process exited with code {code} before the Lab was ready"
            await sweep_key(key)
            if allocated:
                DEFAULT_ALLOCATOR.release(key)
            raise LabStartError(f"Lab '{example_id}' failed to start: {reason}")

        # The Lab's own key is the truth: a Lab Example that names its key
        # overrides the one handed down.
        actual_key = report.get("key") or key
        if allocated and actual_key != key:
            DEFAULT_ALLOCATOR.release(key)
        handle = RemoteLabHandle(actual_key, example_id, socket_path=report.get("socket"))
        child = cls(actual_key, example_id, process, handle, prompt_fd=prompt_fd)
        child.startup_warnings = [str(item) for item in report.get("warnings") or []]
        return child

    @property
    def alive(self) -> bool:
        """Whether the process has not ended."""
        return self.process.returncode is None

    async def wait(self) -> str:
        """Wait for the process to end and return why it did.

        Returns:
            The exit reason, also stored on :attr:`exit_reason`.
        """
        code = await self.process.wait()
        if code < 0:
            try:
                name = signal.Signals(-code).name
            except ValueError:
                name = str(-code)
            self.exit_reason = f"killed by {name}"
        elif code == 0:
            self.exit_reason = "stopped"
        else:
            self.exit_reason = f"exited with code {code}"
        return self.exit_reason

    async def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """Stop the Lab and wait for its process to end.

        The Lab is asked to stop over its handle, which tears the network
        down inside its own process.  A Lab that does not answer in
        ``timeout`` seconds, whose process does not then end in ``timeout``
        seconds, or that was already gone is killed and its key swept from
        the host.

        Args:
            timeout: Seconds to wait for the answer, and then for the process.
        """
        loop = asyncio.get_running_loop()
        if self.alive:
            try:
                # A call still running holds the Lab; its stop would queue behind it.
                await asyncio.wait_for(loop.run_in_executor(None, self.handle.stop), timeout)
            except (LabUnavailableError, asyncio.TimeoutError) as exc:
                reason = exc if isinstance(exc, LabUnavailableError) else (
                    f"Lab '{self.example}' ({self.key}) did not answer 'stop' within {timeout:g} s")
                logger.warning(f"{reason}; killing the Lab process and sweeping its key")
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass  # The Lab exited before the kill reached its process.
        try:
            await asyncio.wait_for(self.wait(), timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"Lab '{self.example}' ({self.key}) did not stop within {timeout:g} s; killing it"
            )
            try:
                self.process.kill()
            except ProcessLookupError:
                pass  # The Lab exited between the deadline and the kill.
            await self.wait()
        await self.sweep()

    async def sweep(self) -> None:
        """Remove whatever the Lab left on the host, by its key."""
        if self.prompt_fd is not None:
            try:
                os.close(self.prompt_fd)
            except OSError:
                pass
            self.prompt_fd = None
        await sweep_key(self.key)
        try:
            os.unlink(self.handle.socket_path)
        except OSError:
            pass
        DEFAULT_ALLOCATOR.release(self.key)


async def sweep_key(key: str) -> None:
    """Run the keyed emergency sweep off the event loop.

    Args:
        key: The Lab key to sweep the host by.
    """
    from mniplab.cleanup import emergency_cleanup
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: emergency_cleanup(lab_key=key))


if __name__ == "__main__":
    sys.exit(main())
