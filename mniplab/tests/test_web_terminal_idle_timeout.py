"""An attached Terminal nobody types in is closed at the idle timeout."""

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any, List, Tuple
from unittest.mock import patch

from mniplab.webserver import websocket as ws_module
from mniplab.webserver.config import settings


class RecordingSocket:
    """A WebSocket stand-in that records how it was closed."""

    def __init__(self) -> None:
        self.closed: List[Tuple[int, str]] = []

    async def close(self, code: int, reason: str) -> None:
        self.closed.append((code, reason))


class IdleTimeoutCheckTests(unittest.IsolatedAsyncioTestCase):
    """`idle_timeout_check` must read the same clock that stamps activity."""

    async def _run_check(self, idle_for: float) -> RecordingSocket:
        """Run the check once against a Terminal idle for `idle_for` seconds."""
        socket = RecordingSocket()
        # `update_activity` stamps `time.time()`, so the Terminal does too.
        terminal: Any = SimpleNamespace(last_activity=time.time() - idle_for)

        real_sleep = asyncio.sleep

        async def no_wait(_: float) -> None:
            await real_sleep(0)

        with patch.object(settings, "terminal_idle_timeout", 60), patch.object(
            ws_module.asyncio, "sleep", no_wait
        ):
            # A check that never fires loops forever, so bound it.
            try:
                await asyncio.wait_for(
                    ws_module.idle_timeout_check(terminal, socket),  # type: ignore[arg-type]
                    timeout=1,
                )
            except asyncio.TimeoutError:
                pass
        return socket

    async def test_a_terminal_idle_past_the_timeout_is_closed(self) -> None:
        socket = await self._run_check(idle_for=61)

        self.assertEqual([reason for _, reason in socket.closed], ["Idle timeout"])

    async def test_a_terminal_used_within_the_timeout_is_left_open(self) -> None:
        socket = await self._run_check(idle_for=5)

        self.assertEqual(socket.closed, [])
