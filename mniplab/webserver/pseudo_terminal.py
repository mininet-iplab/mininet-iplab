"""Reading and writing a pseudo-terminal master without stalling the server.

A write to a pty(7) master blocks once the program on the other side stops
reading: a full-screen program in raw mode, or a vtysh waiting on a daemon,
takes a few kilobytes and then nothing.  A blocked
write on the event loop stops every route and every Terminal on the server,
and it does not return even when the program's side of the pty(7) closes.  So
the master is made non-blocking: input the program has not taken yet waits
in :class:`PendingInput` and is written when there is room again.  Readers, which
run in threads, wait for output with :func:`read_master`.
"""

import asyncio
import os
import select
from typing import Optional

from mniplab.logging import get_logger

logger = get_logger(__name__)

# Input held for one pty(7) at most.  Far beyond any paste a Learner makes;
# input past it is dropped rather than held without limit.
MOST_PENDING_INPUT = 1024 * 1024


def read_master(fd: int, size: int = 4096) -> bytes:
    """Wait for output on a non-blocking pty(7) master and read it.

    Meant for a reader thread: it blocks the calling thread, never the loop.

    Args:
        fd: The pty(7) master, opened non-blocking.
        size: Most bytes to read.

    Returns:
        The bytes read; empty at end of file.

    Raises:
        OSError: The read failed, as it does with EIO once the shell exits.
    """
    while True:
        try:
            return os.read(fd, size)
        except BlockingIOError:
            select.select([fd], [], [])


def write_master(fd: int, data: bytes) -> None:
    """Write all of ``data`` to a non-blocking pty(7) master, waiting for room.

    Meant for a thread that is not the event loop, such as the one relaying
    the launching terminal's keystrokes under ``--enable-web``.

    Args:
        fd: The pty(7) master, possibly non-blocking.
        data: Bytes to write.

    Raises:
        OSError: The write failed for a reason other than a full pseudo-terminal.
    """
    view = memoryview(data)
    while view:
        try:
            view = view[os.write(fd, view):]
        except BlockingIOError:
            select.select([], [fd], [])


class PendingInput:
    """Input bound for one pty(7) master, written whenever it has room.

    The master is made non-blocking when the first input arrives.  Call
    :meth:`write` on the event loop, and :meth:`close` before the fd is
    closed.
    """

    def __init__(self, fd: int) -> None:
        """Start with nothing pending.

        Args:
            fd: The pty(7) master.
        """
        self.fd = fd
        self.pending = bytearray()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def write(self, data: bytes) -> None:
        """Write what the pseudo-terminal takes now and keep the rest for later.

        Args:
            data: Input to deliver after anything already pending.
        """
        if len(self.pending) + len(data) > MOST_PENDING_INPUT:
            logger.warning(
                f"Dropped {len(data)} bytes of input: the program has not read "
                f"the {len(self.pending)} bytes before them"
            )
            return
        self.pending += data
        if self._loop is None:
            self._flush()

    def close(self) -> None:
        """Stop writing and drop whatever is still pending."""
        self.pending.clear()
        self._stop_waiting()

    def _flush(self) -> None:
        """Write pending input until the pseudo-terminal is full or nothing is left."""
        try:
            os.set_blocking(self.fd, False)
            written = os.write(self.fd, self.pending) if self.pending else 0
        except BlockingIOError:
            written = 0
        except OSError:
            # The shell is gone; the reader ends the Terminal.
            self.close()
            return
        del self.pending[:written]
        if not self.pending:
            self._stop_waiting()
        elif self._loop is None:
            self._loop = asyncio.get_running_loop()
            self._loop.add_writer(self.fd, self._flush)

    def _stop_waiting(self) -> None:
        """Stop waiting for the pseudo-terminal to have room."""
        if self._loop is not None:
            self._loop.remove_writer(self.fd)
            self._loop = None
