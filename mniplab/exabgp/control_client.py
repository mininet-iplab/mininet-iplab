"""SpeakerControlClient: the host side of a Speaker's FIFO pair.

ExaBGP's API is a line protocol over two named pipes: a command line goes
into ``exabgp.in``, the answer comes back on ``exabgp.out`` and ends with a
line that is exactly ``done``, ``error`` or ``shutdown``. ``error`` carries
no reason (the daemon's log has it), and the daemon opens ``exabgp.out``
for writing once per answer line, dropping the line if nobody holds the
reading end — so the response pipe is opened before the command is written,
and one command holds the pair until its terminator arrives. Two readers on
one pipe interleave at byte level, which is why one lock serialises every
command on a Speaker.
"""

from __future__ import annotations

import errno
import os
import select
import threading
import time
from typing import List

from mniplab.errors import ExaBGPControlError

#: The lines that end an answer, as ExaBGP's own CLI recognises them.
DONE = "done"
ERROR = "error"
SHUTDOWN = "shutdown"

#: Upstream's own ``COMMAND_RESPONSE_TIMEOUT``.
DEFAULT_TIMEOUT = 5.0
READ_CHUNK_BYTES = 4096


class SpeakerControlClient:
    """Adapter for one Speaker's ``exabgp.in`` / ``exabgp.out`` pair.

    Attributes:
        in_path: Host-side path of the pipe commands are written to.
        out_path: Host-side path of the pipe answers are read from.
        speaker: The Speaker's name, for messages.
        timeout: Seconds of silence after which an answer is given up on.
    """

    def __init__(self, run_dir: str, speaker: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Initialise the client against one Speaker's run directory.

        Args:
            run_dir: Host-side path of the directory holding the FIFO pair
                (the Node's ``/var/run/exabgp`` mapping).
            speaker: The Speaker's name, for messages.
            timeout: Seconds of silence tolerated while an answer is read.
        """
        self.in_path = os.path.join(run_dir, "exabgp.in")
        self.out_path = os.path.join(run_dir, "exabgp.out")
        self.speaker = speaker
        self.timeout = timeout
        self._lock = threading.Lock()

    def execute(self, command: str) -> List[str]:
        """Send one command and return the answer's lines, terminator excluded.

        Args:
            command: One ExaBGP API command line, e.g. ``show neighbor json``.

        Returns:
            Every line the daemon answered before ``done``.

        Raises:
            ExaBGPControlError: The command holds a line break, the FIFO pair
                is missing or unread (daemon not running), the daemon
                answered ``error`` or ``shutdown``, or it fell silent for
                longer than the timeout.
        """
        if "\n" in command or "\r" in command:
            raise ExaBGPControlError(
                f"Command {command!r} contains a line break, which would inject a "
                f"second command into {self.speaker}'s control pipe",
                command=command,
            )
        with self._lock:
            reader = self._open_reader(command)
            try:
                self._write(command)
                return self._read_answer(reader, command)
            finally:
                os.close(reader)

    def is_running(self) -> bool:
        """Report whether the Speaker's daemon answers on its pipe.

        Returns:
            True if ``version`` was answered, False otherwise.
        """
        try:
            self.execute("version")
        except ExaBGPControlError:
            return False
        return True

    def _open_reader(self, command: str) -> int:
        """Open the response pipe before anything is written.

        Opened for reading and writing: holding a writing end of our own
        means the pipe never reports end-of-file between the daemon's
        per-line opens, so ``select`` sleeps until data arrives instead of
        spinning on an empty pipe.

        Args:
            command: The command about to be sent, for the message.

        Returns:
            The file descriptor.

        Raises:
            ExaBGPControlError: The pipe does not exist.
        """
        try:
            return os.open(self.out_path, os.O_RDWR | os.O_NONBLOCK)
        except FileNotFoundError as exc:
            raise ExaBGPControlError(
                f"{self.speaker}'s control pipe {self.out_path} does not exist; "
                f"the Speaker has not prepared its FIFO pair",
                command=command,
            ) from exc

    def _write(self, command: str) -> None:
        """Write one whole command line into the command pipe.

        Args:
            command: The command line, newline excluded.

        Raises:
            ExaBGPControlError: The pipe is missing, or nobody reads it,
                which means the daemon is not running.
        """
        try:
            writer = os.open(self.in_path, os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError as exc:
            raise ExaBGPControlError(
                f"{self.speaker}'s control pipe {self.in_path} does not exist; "
                f"the Speaker has not prepared its FIFO pair",
                command=command,
            ) from exc
        except OSError as exc:
            if exc.errno == errno.ENXIO:
                raise ExaBGPControlError(
                    f"nothing is reading {self.in_path}: is the ExaBGP daemon on "
                    f"{self.speaker} running?",
                    command=command,
                ) from exc
            raise ExaBGPControlError(
                f"could not open {self.in_path} on {self.speaker}: {exc}", command=command
            ) from exc
        try:
            os.write(writer, f"{command}\n".encode())
        finally:
            os.close(writer)

    def _read_answer(self, reader: int, command: str) -> List[str]:
        """Read lines until a terminator, giving up after ``timeout`` of silence.

        Args:
            reader: The response pipe's descriptor.
            command: The command awaiting its answer, for messages.

        Returns:
            The answer's lines, terminator excluded.

        Raises:
            ExaBGPControlError: ``error``, ``shutdown``, or silence.
        """
        lines: List[str] = []
        buf = b""
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExaBGPControlError(
                    f"no answer from {self.speaker} to '{command}' within "
                    f"{self.timeout:g} s: is the ExaBGP daemon running?",
                    command=command,
                )
            ready, _, _ = select.select([reader], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(reader, READ_CHUNK_BYTES)
            if not chunk:
                continue
            deadline = time.monotonic() + self.timeout
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace")
                if line == DONE:
                    return lines
                if line == ERROR:
                    raise ExaBGPControlError(
                        f"ExaBGP on {self.speaker} rejected '{command}' "
                        f"(the reason is in the daemon's log)",
                        command=command,
                    )
                if line == SHUTDOWN:
                    raise ExaBGPControlError(
                        f"ExaBGP on {self.speaker} is shutting down; '{command}' was aborted",
                        command=command,
                    )
                lines.append(line)
