"""Web-tier tests for the Lab Prompt in the browser.

Tests the PromptService and PromptWebSocketHandler:
- Multiplexing a pseudo-terminal across multiple viewers (output fans out).
- Input from any viewer is written through to the pty.
- Two WebSockets attached to one prompt see the exact same screen.
- A stopped Lab is announced to every viewer.
- Session activity, auth, rate limiting, and key resolution.
"""

from __future__ import annotations

import asyncio
import json
import os
import pty
import select
import tty
import unittest
from typing import Any, List, Optional
from unittest.mock import patch

import pytest
from fastapi import WebSocketDisconnect

from mniplab.tests.browser import free_port
from mniplab.tests.fakes import (
    FAKE_LAB_KEY,
    LARGE_PASTE,
    PROGRAM_BUSY_FOR,
    FakeLab,
    FrozenSessionClockMixin,
    RecordingWebSocket,
    drain_after,
    longest_stall,
)
from mniplab.webserver import registry, start_server, stop_server
from mniplab.webserver.auth import create_session
from mniplab.webserver.prompt import PromptService
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.websocket import PromptWebSocketHandler


class FakePromptWebSocket:
    """A stand-in for Starlette's WebSocket for testing the Lab Prompt."""

    def __init__(
        self,
        cookies: Optional[dict[str, str]] = None,
        inbox: Optional[List[Any]] = None,
        disconnect_on_empty: bool = True,
    ) -> None:
        self.cookies = cookies or {}
        self.sent: List[Any] = []
        self.closes: List[tuple[int, str]] = []
        self.closed = False
        self._inbox = list(inbox or [])
        self.disconnect_on_empty = disconnect_on_empty
        self._waiter: Optional[asyncio.Future] = None

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        if self._inbox:
            return json.dumps(self._inbox.pop(0))
        if self.disconnect_on_empty:
            raise WebSocketDisconnect(code=1000)
        loop = asyncio.get_running_loop()
        self._waiter = loop.create_future()
        try:
            await self._waiter
        except asyncio.CancelledError:
            pass
        raise WebSocketDisconnect(code=1000)

    async def send_json(self, payload: Any) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closes.append((code, reason))
        self.closed = True
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)


class LabPromptWebTierTests(FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase):
    """Web-tier tests for Lab Prompt multiplexing."""

    async def asyncSetUp(self) -> None:
        self.registry = SessionRegistry()
        # A fake Lab under FAKE_LAB_KEY, whose prompt the pty below is.
        self.registry.lab_service.lab = FakeLab()
        self.master_fd, self.slave_fd = pty.openpty()
        self.addCleanup(self._cleanup_pty)
        self.registry.prompt(FAKE_LAB_KEY).attach(self.master_fd)

        patcher = patch("mniplab.webserver.websocket.registry", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cleanup_pty(self) -> None:
        self.registry.prompt(FAKE_LAB_KEY).detach()
        for fd in (self.slave_fd, self.master_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    async def test_two_websockets_share_one_lab_prompt_and_see_same_screen(self) -> None:
        """Two WebSockets attach to one Lab Prompt, type from each, and see the same screen."""
        session1 = create_session("instructor1", "instructor")
        session2 = create_session("instructor2", "instructor")

        ws1 = FakePromptWebSocket(
            cookies={"session": session1},
            inbox=[{"type": "input", "data": "pingall\n"}],
            disconnect_on_empty=False,
        )
        ws2 = FakePromptWebSocket(
            cookies={"session": session2},
            inbox=[{"type": "input", "data": "show_ips\n"}],
            disconnect_on_empty=False,
        )

        handler1 = PromptWebSocketHandler(ws1, FAKE_LAB_KEY)  # type: ignore[arg-type]
        handler2 = PromptWebSocketHandler(ws2, FAKE_LAB_KEY)  # type: ignore[arg-type]

        with patch.object(self.registry, "refuse_key", return_value=None):
            # Run both handlers concurrently
            task1 = asyncio.create_task(handler1.run())
            task2 = asyncio.create_task(handler2.run())

            # Give handlers time to attach viewers and process initial messages
            await asyncio.sleep(0.05)

            # Check that input from both ws1 and ws2 was written to the pty slave
            slave_read = os.read(self.slave_fd, 1024)
            if b"show_ips\n" not in slave_read:
                slave_read += os.read(self.slave_fd, 1024)
            self.assertIn(b"pingall\n", slave_read)
            self.assertIn(b"show_ips\n", slave_read)

            # Simulate terminal output generated on the pty slave
            os.write(self.slave_fd, b"mininet-iplab> pingall\r\n*** Ping: 0% dropped\r\nmininet-iplab> ")
            await asyncio.sleep(0.05)

            # Both WebSockets must have received the output!
            ws1_outputs = [m["data"] for m in ws1.sent if m.get("type") == "output"]
            ws2_outputs = [m["data"] for m in ws2.sent if m.get("type") == "output"]

            screen1 = "".join(ws1_outputs)
            screen2 = "".join(ws2_outputs)

            self.assertEqual(screen1, screen2)
            self.assertIn("0% dropped", screen1)

            # Now stop the Lab and verify every viewer is told
            await self.registry.prompt(FAKE_LAB_KEY).close_all("Lab stopped")
            await asyncio.sleep(0.05)

            self.assertTrue(ws1.closed)
            self.assertTrue(ws2.closed)
            self.assertTrue(any("Lab stopped" in str(c) for c in ws1.closes))
            self.assertTrue(any("Lab stopped" in str(c) for c in ws2.closes))

            # Handlers finish cleanly
            await asyncio.gather(task1, task2)

    async def test_late_attaching_websocket_receives_screen_buffer(self) -> None:
        """A viewer attaching after output was produced receives the buffered screen."""
        os.write(self.slave_fd, b"mininet-iplab> Initial prompt ready\r\n")
        await asyncio.sleep(0.05)

        session = create_session("instructor2", "instructor")
        ws = FakePromptWebSocket(cookies={"session": session}, inbox=[])
        handler = PromptWebSocketHandler(ws, FAKE_LAB_KEY)  # type: ignore[arg-type]

        with patch.object(self.registry, "refuse_key", return_value=None):
            await handler.run()

        outputs = [m["data"] for m in ws.sent if m.get("type") == "output"]
        self.assertIn("Initial prompt ready", "".join(outputs))

    async def test_prompt_refuses_when_no_prompt_available(self) -> None:
        """A prompt connection is refused if no pseudo-terminal is attached."""
        self.registry.prompt(FAKE_LAB_KEY).detach()

        session = create_session("instructor3", "instructor")
        ws = FakePromptWebSocket(cookies={"session": session}, inbox=[])
        handler = PromptWebSocketHandler(ws, FAKE_LAB_KEY)  # type: ignore[arg-type]

        with patch.object(self.registry, "refuse_key", return_value=None):
            await handler.run()

        self.assertTrue(ws.closed)
        self.assertTrue(any("No Lab Prompt available" in m.get("message", "") for m in ws.sent))

    async def test_prompt_refuses_invalid_lab_key(self) -> None:
        """Connecting with a key that is not running is refused."""
        session = create_session("instructor4", "instructor")
        ws = FakePromptWebSocket(cookies={"session": session}, inbox=[])
        handler = PromptWebSocketHandler(ws, "wrong-key")  # type: ignore[arg-type]

        with patch.object(self.registry, "refuse_key", return_value="No Lab with key 'wrong-key' is running"):
            await handler.run()

        self.assertTrue(ws.closed)
        self.assertTrue(any("wrong-key" in m.get("message", "") for m in ws.sent))

    async def test_prompt_refuses_a_learner(self) -> None:
        """The Lab Prompt is the Instructor's; a Learner is refused.

        It addresses the Lab as a whole rather than any Node, and one
        screen is shared by everyone attached, so a Learner typing into it
        reaches the whole class.  A Learner drives their Lab through Node
        Terminals and the Topology.
        """
        session = create_session("learner6", "learner")
        ws = FakePromptWebSocket(cookies={"session": session}, inbox=[])
        handler = PromptWebSocketHandler(ws, FAKE_LAB_KEY)  # type: ignore[arg-type]

        with patch.object(self.registry, "refuse_key", return_value=None):
            await handler.run()

        self.assertTrue(ws.closed)
        self.assertTrue(
            any("Instructor" in m.get("message", "") for m in ws.sent), ws.sent
        )

    async def test_prompt_requires_authentication(self) -> None:
        """Connecting without a valid session cookie is rejected."""
        ws = FakePromptWebSocket(cookies={}, inbox=[])
        handler = PromptWebSocketHandler(ws, FAKE_LAB_KEY)  # type: ignore[arg-type]

        await handler.run()

        self.assertTrue(ws.closed)
        self.assertEqual(ws.closes, [(1008, "Not authenticated")])

    async def test_prompt_traffic_refreshes_session(self) -> None:
        """Typing into the prompt refreshes the owning Session."""
        session = create_session("instructor5", "instructor")
        self.clock.advance(300.0)

        ws = FakePromptWebSocket(
            cookies={"session": session},
            inbox=[{"type": "input", "data": "nodes\n"}],
        )
        handler = PromptWebSocketHandler(ws, FAKE_LAB_KEY)  # type: ignore[arg-type]

        with patch.object(self.registry, "refuse_key", return_value=None):
            await handler.run()

        self.assertEqual(self.last_seen(session), 1300.0)

    async def test_a_large_paste_into_a_busy_prompt_leaves_the_server_free(self) -> None:
        """A paste the Lab Prompt is not reading yet waits without blocking the loop.

        The program at the prompt holds its tty in raw mode and does not read
        for a second.  The server must keep serving meanwhile, and the program
        must then get every byte of the paste, in order.
        """
        tty.setraw(self.slave_fd)
        session = create_session("instructor6", "instructor")
        paste = bytes(ord("a") + i % 26 for i in range(LARGE_PASTE))
        received = bytearray()
        drain_after(self.slave_fd, PROGRAM_BUSY_FOR, len(paste), received)
        ws = FakePromptWebSocket(
            cookies={"session": session},
            inbox=[{"type": "input", "data": paste.decode()}],
        )
        handler = PromptWebSocketHandler(ws, FAKE_LAB_KEY)  # type: ignore[arg-type]

        stall = asyncio.create_task(
            longest_stall(lambda: len(received) >= len(paste), limit=5.0)
        )
        await asyncio.sleep(0)  # the watch starts before the paste arrives
        with patch.object(self.registry, "refuse_key", return_value=None):
            await handler.run()

        self.assertLess(await stall, PROGRAM_BUSY_FOR / 2)
        self.assertEqual(bytes(received), paste)

    @pytest.mark.unit
    async def test_prompt_reader_does_not_starve_default_executor(self) -> None:
        """Prompt reader uses a dedicated executor and does not block default executor."""
        loop = asyncio.get_running_loop()
        prompt = PromptService()
        r_fd, w_fd = os.pipe()
        self.addCleanup(self._safe_close, r_fd)
        self.addCleanup(self._safe_close, w_fd)

        prompt.attach(r_fd)

        await asyncio.sleep(0.05)

        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: "default-executor-ok"),
            timeout=1.0,
        )
        self.assertEqual(result, "default-executor-ok")

        prompt.detach()

    @staticmethod
    def _safe_close(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass


class PromptLocalTerminalTests(unittest.TestCase):
    """``start_server`` must run the prompt reader on the loop that serves.

    ``python3 examples/x.py --enable-web`` hands the prompt's pty to
    ``start_server`` before any viewer exists; what the Lab prints must reach
    the launching terminal from then on, not only once a browser attaches.
    """

    def setUp(self) -> None:
        self.master_fd, self.slave_fd = pty.openpty()
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(self._close_fds)

    def _close_fds(self) -> None:
        for fd in (self.slave_fd, self.master_fd, self.read_fd, self.write_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    def _stop_fake_lab(self) -> None:
        # The server's shutdown leaves an in-process Lab to its script; the
        # fake one here must not outlive the test on the global registry.
        # Stopped while the server still serves, so the prompt reader is
        # cancelled on the loop it runs on.
        asyncio.run(registry.stop_lab(FAKE_LAB_KEY))

    def test_output_reaches_the_launching_terminal_before_any_viewer(self) -> None:
        """Bytes the Lab writes to its pty land on ``local_fd`` with no viewer attached."""
        start_server(
            FakeLab(), host="127.0.0.1", port=free_port(), block=False,
            prompt_fd=self.master_fd, local_fd=self.write_fd,
        )
        self.addCleanup(stop_server)
        self.addCleanup(self._stop_fake_lab)
        self.assertEqual(registry.lab_key, FAKE_LAB_KEY)

        os.write(self.slave_fd, b"mininet-iplab> ")

        ready, _, _ = select.select([self.read_fd], [], [], 5.0)
        self.assertTrue(ready, "nothing reached the launching terminal")
        self.assertEqual(os.read(self.read_fd, 64), b"mininet-iplab> ")


class PromptInterruptTests(unittest.TestCase):
    """``write_input`` tells an in-process Lab when the input holds Ctrl+C."""

    def setUp(self) -> None:
        self.master_fd, self.slave_fd = pty.openpty()
        tty.setraw(self.slave_fd)
        self.addCleanup(os.close, self.slave_fd)
        self.service = PromptService()
        self.addCleanup(self.service.detach)

    def _read_slave(self) -> bytes:
        ready, _, _ = select.select([self.slave_fd], [], [], 5.0)
        self.assertTrue(ready, "nothing reached the pseudo-terminal")
        return os.read(self.slave_fd, 64)

    def test_ctrl_c_calls_the_interrupt_and_is_still_written(self) -> None:
        calls: List[int] = []
        self.service.attach(self.master_fd, interrupt=lambda: calls.append(1))

        self.service.write_input("\x03")

        self.assertEqual(calls, [1])
        self.assertEqual(self._read_slave(), b"\x03")

    def test_other_input_does_not_call_the_interrupt(self) -> None:
        calls: List[int] = []
        self.service.attach(self.master_fd, interrupt=lambda: calls.append(1))

        self.service.write_input("ls\n")

        self.assertEqual(calls, [])
        self.assertEqual(self._read_slave(), b"ls\n")

    def test_ctrl_c_without_an_interrupt_is_only_written(self) -> None:
        self.service.attach(self.master_fd)

        self.service.write_input("\x03")

        self.assertEqual(self._read_slave(), b"\x03")


if __name__ == "__main__":
    unittest.main()
