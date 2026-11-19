"""Multiplexes a Lab Prompt's pseudo-terminal to every attached viewer."""

from __future__ import annotations

import asyncio
import fcntl
import os
import struct
import termios
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect, status

from mniplab.logging import get_logger
from .pseudo_terminal import PendingInput, read_master

logger = get_logger(__name__)

# Maximum bytes to keep in the rolling scrollback buffer so that late-attaching
# viewers (e.g. a browser opened after commands were typed) see the current
# screen and prompt immediately.
BUFFER_LIMIT = 65536

_prompt_reader_executor: Optional[ThreadPoolExecutor] = None


def get_prompt_reader_executor() -> ThreadPoolExecutor:
    """Return the dedicated ThreadPoolExecutor for Lab Prompt readers.

    Isolating prompt readers onto a dedicated thread pool prevents starvation
    of the default asyncio executor.

    Returns:
        The process-wide ThreadPoolExecutor dedicated to Lab Prompt reading.
    """
    global _prompt_reader_executor
    if _prompt_reader_executor is None:
        _prompt_reader_executor = ThreadPoolExecutor(
            max_workers=16,
            thread_name_prefix="prompt-reader",
        )
    return _prompt_reader_executor


class PromptService:
    """Multiplexes one pseudo-terminal to multiple viewers (browsers + local terminal).

    A Lab has exactly one Lab Prompt.  This service owns the master
    file descriptor of the pseudo-terminal running the Lab's CLI.  Output from
    the pseudo-terminal fans out to every attached viewer; input from any viewer is
    written through to the prompt.
    """

    def __init__(self, executor: Optional[ThreadPoolExecutor] = None) -> None:
        self.prompt_fd: Optional[int] = None
        self.input: Optional[PendingInput] = None
        self.local_fd: Optional[int] = None
        self.interrupt: Optional[Callable[[], None]] = None
        self.viewers: Set[WebSocket] = set()
        self.output_buffer: bytearray = bytearray()
        self.read_task: Optional[asyncio.Task] = None
        self.executor = executor
        self._lock = asyncio.Lock()

    @property
    def has_prompt(self) -> bool:
        """Whether a pseudo-terminal is currently attached."""
        return self.prompt_fd is not None

    def attach(
        self,
        fd: int,
        local_fd: Optional[int] = None,
        interrupt: Optional[Callable[[], None]] = None,
    ) -> None:
        """Attach a pseudo-terminal master file descriptor and start reading output.

        Args:
            fd: The master fd of the pseudo-terminal.
            local_fd: Optional fd of the launching terminal (for --enable-web).
            interrupt: Optional callable that interrupts the prompt of a Lab
                attached in-process; called when input holds Ctrl+C.
        """
        self.detach()
        self.prompt_fd = fd
        self.input = PendingInput(fd)
        self.local_fd = local_fd
        self.interrupt = interrupt
        self.output_buffer.clear()
        self._ensure_reading()

    def _ensure_reading(self) -> None:
        """Start the prompt reading task if a running event loop is available."""
        if self.prompt_fd is not None and (self.read_task is None or self.read_task.done()):
            try:
                loop = asyncio.get_running_loop()
                self.read_task = loop.create_task(self._read_loop())
            except RuntimeError:
                pass

    async def _read_loop(self) -> None:
        """Read output from prompt_fd and fan it out to all viewers."""
        loop = asyncio.get_running_loop()
        assert self.prompt_fd is not None
        fd = self.prompt_fd
        exec_pool = self.executor or get_prompt_reader_executor()

        try:
            while True:
                data = await loop.run_in_executor(exec_pool, read_master, fd)
                if not data:
                    break
                await self._broadcast_data(data)
        except (OSError, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            await self.close_all("Lab stopped")

    async def _broadcast_data(self, data: bytes) -> None:
        """Record output into rolling buffer and deliver to all viewers."""
        self.output_buffer.extend(data)
        if len(self.output_buffer) > BUFFER_LIMIT:
            del self.output_buffer[:-BUFFER_LIMIT]

        # Write to local terminal if registered
        if self.local_fd is not None:
            try:
                os.write(self.local_fd, data)
            except OSError:
                self.local_fd = None

        text = data.decode("utf-8", errors="replace")
        message = {"type": "output", "data": text}

        # Fan out to all connected WebSockets
        for ws in list(self.viewers):
            try:
                await ws.send_json(message)
            except (RuntimeError, OSError):
                self.viewers.discard(ws)

    async def add_viewer(self, websocket: WebSocket) -> None:
        """Register a browser WebSocket viewer and replay current screen buffer."""
        self._ensure_reading()
        self.viewers.add(websocket)
        if self.output_buffer:
            text = self.output_buffer.decode("utf-8", errors="replace")
            try:
                await websocket.send_json({"type": "output", "data": text})
            except (RuntimeError, OSError):
                self.viewers.discard(websocket)

    def remove_viewer(self, websocket: WebSocket) -> None:
        """Unregister a browser WebSocket viewer."""
        self.viewers.discard(websocket)

    def write_input(self, data: str) -> None:
        """Write input from a viewer to the pseudo-terminal.

        Args:
            data: Raw input string (keystrokes or command lines).
        """
        if self.input is not None:
            if "\x03" in data and self.interrupt is not None:
                self.interrupt()
            self.input.write(data.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        """Set window size on the pseudo-terminal.

        Args:
            cols: Terminal width in columns.
            rows: Terminal height in rows.
        """
        if self.prompt_fd is not None:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.prompt_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    async def close_all(self, reason: str = "Lab stopped") -> None:
        """Notify all viewers that the Lab has stopped and close their connections.

        Args:
            reason: Explanation sent to the viewers.
        """
        if self.input is not None:
            self.input.write(b"exit\n")
        viewers = list(self.viewers)
        self.viewers.clear()
        for ws in viewers:
            try:
                await ws.send_json({"type": "error", "message": reason})
                await ws.close(code=status.WS_1000_NORMAL_CLOSURE, reason=reason[:120])
            except (RuntimeError, OSError, WebSocketDisconnect):
                pass
        self.detach()

    def detach(self) -> None:
        """Close prompt_fd and cancel the reader task without touching viewers."""
        if self.read_task is not None and not self.read_task.done():
            self.read_task.cancel()
            self.read_task = None

        if self.input is not None:
            self.input.close()
            self.input = None
        if self.prompt_fd is not None:
            try:
                os.close(self.prompt_fd)
            except OSError:
                pass
            self.prompt_fd = None

        self.local_fd = None
        self.interrupt = None
