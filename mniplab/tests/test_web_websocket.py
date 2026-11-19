"""Unit tests for the WebSocket handlers' effect on the owning Session.

A Learner makes no HTTP request after the page loads, so Terminal and topology
WebSocket traffic is the only thing that can keep their Session current.  These
tests drive the handlers with a fake websocket and a fake registry, and a fake
clock stands in for elapsed time.
"""

import asyncio
import codecs
import json
import os
import pty
import tty
import unittest
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect

from mniplab.tests.fakes import (
    FAKE_LAB_KEY,
    FrozenSessionClockMixin,
    LARGE_PASTE,
    PROGRAM_BUSY_FOR,
    RecordingWebSocket,
    drain_after,
    longest_stall,
)
from mniplab.webserver.auth import create_session
from mniplab.webserver import auth
from mniplab.webserver.pseudo_terminal import PendingInput
from mniplab.webserver.services import NODE_EXITED_REASON, Terminal
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.websocket import (
    TerminalWebSocketHandler,
    TopologyWebSocketHandler,
    read_terminal,
)


# Seconds the fake clock jumps between the handshake and each client message,
# so a refresh can only come from the traffic and never from the handshake.
IDLE_GAP = 300.0


class FakeWebSocket:
    """A stand-in for Starlette's WebSocket, driven by a scripted inbox.

    Attributes:
        cookies: Cookies the client is presenting.
        sent: Every payload the handler sent, in order.
        closed: True once the handler closed the connection.
    """

    def __init__(
        self,
        cookies: Optional[Dict[str, str]] = None,
        inbox: Optional[List[Any]] = None,
        on_receive: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Initialize a fake websocket.

        Args:
            cookies: Cookies the client presents, notably the session cookie.
            inbox: Messages the client sends, delivered in order.  The
                connection then drops the way a closed browser tab does.
            on_receive: Called just before each message is handed over, so a
                test can let time pass between the handshake and the traffic.
        """
        self.cookies: Dict[str, str] = cookies or {}
        self.sent: List[Any] = []
        self.closed = False
        self._inbox: List[Any] = list(inbox or [])
        self._on_receive = on_receive

    async def accept(self) -> None:
        """Accept the connection."""

    async def receive_json(self) -> Any:
        """Return the next scripted client message.

        Returns:
            The next message from the inbox.

        Raises:
            WebSocketDisconnect: Once the inbox is empty.
        """
        if not self._inbox:
            raise WebSocketDisconnect(code=1000)
        if self._on_receive is not None:
            self._on_receive()
        return self._inbox.pop(0)

    async def receive_text(self) -> str:
        """Return the next scripted client message as JSON text.

        Returns:
            The next message from the inbox, serialised.

        Raises:
            WebSocketDisconnect: Once the inbox is empty.
        """
        return json.dumps(await self.receive_json())

    async def send_json(self, payload: Any) -> None:
        """Record a payload the handler sent.

        Args:
            payload: The JSON-serialisable payload.
        """
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Record that the handler closed the connection.

        Args:
            code: WebSocket close code.
            reason: Human-readable close reason.
        """
        del code, reason
        self.closed = True


class FakeTerminal:
    """A stand-in for a Terminal, carrying only what the handlers read.

    Attributes:
        id: Terminal identifier.
        client_id: The owning Session's identifier.
        terminal_fd: File descriptor the handler reads from and writes to.
        websocket: The browser connection delivering this Terminal.
        input: Input the shell has not read yet, as a Terminal holds it.
    """

    def __init__(
        self, session_id: str, terminal_fd: int, terminal_id: str = "terminal-1"
    ) -> None:
        """Initialize a fake Terminal.

        Args:
            session_id: The owning Session's identifier.
            terminal_fd: File descriptor standing in for the Terminal's shell.
            terminal_id: Terminal identifier.
        """
        self.id = terminal_id
        self.client_id = session_id
        self.terminal_fd = terminal_fd
        self.websocket: Optional[FakeWebSocket] = None
        self.read_task: Optional[asyncio.Task] = None
        self.ping_task: Optional[asyncio.Task] = None
        self.timeout_task: Optional[asyncio.Task] = None
        self.send_lock = asyncio.Lock()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self.recent_output = bytearray()
        self.input = PendingInput(terminal_fd)

    def keep_output(self, data: bytes) -> None:
        """Keep output the reader read, as a Terminal does for a replay.

        Args:
            data: Bytes the shell wrote.
        """
        self.recent_output += data


class FakeRegistry:
    """A stand-in for SessionRegistry recording only what the handlers call.

    Attributes:
        activity: Terminal ids passed to update_activity, in order.
    """

    def __init__(self, terminal: Optional[FakeTerminal] = None) -> None:
        """Initialize a fake registry.

        Args:
            terminal: The Terminal spawn_terminal should hand back.
        """
        self.activity: List[str] = []
        self.terminal = terminal
        self.closed_with_reason: Optional[tuple] = None
        # The one Lab the handlers may address.
        self.lab_key = FAKE_LAB_KEY

    def refuse_key(
        self, key: str, viewer: Optional[str] = None, role: Optional[str] = None
    ) -> Optional[str]:
        """Accept the fake Lab's key and refuse any other.

        Args:
            key: The Lab key from the path.
            viewer: The login asking; everyone may see the fake Lab.
            role: Its role.

        Returns:
            None for the fake Lab's key, a refusal otherwise.
        """
        del viewer, role
        return None if key == self.lab_key else f"No Lab with key '{key}' is running"

    async def update_activity(self, terminal_id: str) -> None:
        """Record Terminal activity.

        Args:
            terminal_id: Terminal identifier reporting activity.
        """
        self.activity.append(terminal_id)

    async def spawn_terminal(
        self, key: str, client_id: str, node_name: str, username: str
    ) -> FakeTerminal:
        """Return the pre-built Terminal for this connection.

        Args:
            key: The Lab's key.
            client_id: The owning Session's identifier.
            node_name: Node the Terminal attaches to.
            username: Account that opened the Terminal.

        Returns:
            The fake Terminal supplied at construction.
        """
        del key, client_id, node_name, username
        assert self.terminal is not None
        return self.terminal

    async def close_terminal(self, terminal_id: str) -> None:
        """Cancel the Terminal's background tasks the way the real registry does.

        Args:
            terminal_id: Terminal identifier being closed.
        """
        del terminal_id
        if self.terminal is None:
            return
        for task in (
            self.terminal.read_task,
            self.terminal.ping_task,
            self.terminal.timeout_task,
        ):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, RuntimeError):
                    pass

    async def detach_terminal(self, terminal_id: str, websocket: FakeWebSocket) -> None:
        """Stop the connection's tasks, as the real registry does on a detach.

        Args:
            terminal_id: Terminal identifier whose connection closed.
            websocket: The connection that closed.
        """
        del websocket
        await self.close_terminal(terminal_id)

    async def close_terminal_with_reason(
        self, terminal_id: str, reason: str
    ) -> None:
        """Close the Terminal the way the real registry does, recording the reason.

        Args:
            terminal_id: Terminal identifier whose shell exited.
            reason: The reason string the browser would be sent.
        """
        self.closed_with_reason = (terminal_id, reason)
        await self.close_terminal(terminal_id)

    async def broadcast_status_update(self) -> None:
        """Ignore a status broadcast."""

    async def broadcast_topology_update(self, key: str) -> None:
        """Ignore a topology broadcast."""
        del key

    async def add_topology_subscriber(self, key: str, websocket: FakeWebSocket) -> None:
        """Ignore a new topology subscriber.

        Args:
            key: The Lab's key.
            websocket: The subscribing connection.
        """
        del key, websocket

    async def remove_topology_subscriber(self, key: str, websocket: FakeWebSocket) -> None:
        """Ignore a departing topology subscriber.

        Args:
            key: The Lab's key.
            websocket: The departing connection.
        """
        del key, websocket

    async def get_status(self, group: Optional[str] = None) -> Dict[str, Any]:
        """Return an empty Lab status.

        Args:
            group: The login asking.

        Returns:
            A status payload with no Lab running.
        """
        del group
        return {"running": False}

    async def get_topology(self, key: str) -> Dict[str, Any]:
        """Return an empty Topology.

        Args:
            key: The Lab's key.

        Returns:
            A Topology payload with no nodes or links.
        """
        del key
        return {"nodes": [], "links": []}


class SessionRefreshFromWebSocketTests(
    FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase
):
    """WebSocket traffic keeps the owning Session's last_seen current."""

    def setUp(self) -> None:
        """Freeze the Session clock and install a fake registry."""
        super().setUp()
        self.registry = FakeRegistry()
        patcher = patch("mniplab.webserver.websocket.registry", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _terminal_reading(self, session_id: str, output: bytes) -> FakeTerminal:
        """Return a Terminal whose shell yields the given output, then EOF.

        Args:
            session_id: The owning Session's identifier.
            output: Bytes the Terminal produces before end-of-file.

        Returns:
            A FakeTerminal wired to a pipe holding the output.
        """
        read_fd, write_fd = os.pipe()
        os.write(write_fd, output)
        os.close(write_fd)
        self.addCleanup(os.close, read_fd)
        return FakeTerminal(session_id, read_fd)

    def _attached_terminal(self, session_id: str) -> FakeTerminal:
        """Return a Terminal on a real pty(7) whose far end is already gone.

        Writes and window-size changes behave as they do on a live Terminal,
        while reads fail at once so the streaming task finishes without waiting.

        Args:
            session_id: The owning Session's identifier.

        Returns:
            A FakeTerminal wired to a pty(7) master.
        """
        master_fd, slave_fd = pty.openpty()
        os.close(slave_fd)
        self.addCleanup(os.close, master_fd)
        return FakeTerminal(session_id, master_fd)

    async def _run_terminal(self, session_id: str, inbox: List[Any]) -> FakeWebSocket:
        """Drive one Terminal WebSocket connection to completion.

        Args:
            session_id: The Session whose cookie the browser presents.
            inbox: Messages the browser sends before the connection drops.

        Returns:
            The fake websocket the handler used.
        """
        terminal = self._attached_terminal(session_id)
        self.registry.terminal = terminal
        websocket = FakeWebSocket(
            cookies={"session": session_id},
            inbox=inbox,
            on_receive=lambda: self.clock.advance(IDLE_GAP),
        )
        await TerminalWebSocketHandler(websocket, FAKE_LAB_KEY, "r1").run()  # type: ignore[arg-type]
        return websocket

    async def test_terminal_output_refreshes_the_session(self) -> None:
        """Terminal read activity should refresh the owning Session."""
        session_id = create_session("learner1", "learner")
        self.clock.advance(IDLE_GAP)
        terminal = self._terminal_reading(session_id, b"B>\r\n")
        terminal.websocket = FakeWebSocket()

        await read_terminal(terminal)  # type: ignore[arg-type]

        self.assertEqual(self.last_seen(session_id), 1000.0 + IDLE_GAP)

    async def test_a_terminal_whose_shell_exited_is_closed_with_the_reason(self) -> None:
        """EOF from the pty — the Node is gone — must close the Terminal.

        A Learner left typing into a shell with nothing behind it is the
        failure this locks down: the read loop ends, the registry is told to
        close the Terminal, and the browser gets "Node exited" rather than a
        silent socket.
        """
        session_id = create_session("learner1", "learner")
        terminal = self._terminal_reading(session_id, b"")
        self.registry.terminal = terminal
        terminal.websocket = FakeWebSocket()

        await read_terminal(terminal)  # type: ignore[arg-type]

        self.assertEqual(
            self.registry.closed_with_reason, (terminal.id, "Node exited")
        )

    async def test_terminal_input_refreshes_the_session(self) -> None:
        """Typing into a Terminal should refresh the owning Session."""
        session_id = create_session("learner1", "learner")

        await self._run_terminal(session_id, [{"type": "input", "data": "ip route\n"}])

        self.assertEqual(self.last_seen(session_id), 1000.0 + IDLE_GAP)

    async def test_last_input_excludes_output_keepalives_and_resize(self) -> None:
        """A continuing ping or attached browser cannot impersonate typing."""
        session_id = create_session("learner1", "learner")
        self.assertIsNone(auth.list_sessions()[0]["last_input_at"])
        await self._run_terminal(session_id, [{"type": "input", "data": "ping h2\n"}])
        recorded = auth.list_sessions()[0]["last_input_at"]
        self.assertEqual(recorded, self.clock())
        self.clock.advance(IDLE_GAP)
        terminal = self._terminal_reading(session_id, b"64 bytes from h2\r\n")
        terminal.websocket = FakeWebSocket()
        await read_terminal(terminal)  # type: ignore[arg-type]
        await self._run_terminal(
            session_id,
            [
                {"type": "pong"},
                {"type": "resize", "cols": 120, "rows": 40},
                {"type": "input", "data": ""},
            ],
        )
        self.assertEqual(auth.list_sessions()[0]["last_input_at"], recorded)
        self.assertGreater(self.last_seen(session_id), recorded)

    async def test_last_input_advances_for_each_nonempty_terminal_input(self) -> None:
        """Typing and pasted commands update only their owning Session."""
        session_id = create_session("learner1", "learner")
        other = create_session("learner1", "learner")
        await self._run_terminal(session_id, [{"type": "input", "data": "ip route\n"}])
        await self._run_terminal(session_id, [{"type": "input", "data": "\x03"}])
        rows = {row["session_id"]: row for row in auth.list_sessions()}
        self.assertEqual(rows[session_id]["last_input_at"], self.clock())
        self.assertIsNone(rows[other]["last_input_at"])

    async def test_terminal_resize_refreshes_the_session(self) -> None:
        """Resizing a Terminal should refresh the owning Session."""
        session_id = create_session("learner1", "learner")

        await self._run_terminal(session_id, [{"type": "resize", "cols": 120, "rows": 40}])

        self.assertEqual(self.last_seen(session_id), 1000.0 + IDLE_GAP)

    async def test_topology_traffic_refreshes_the_session(self) -> None:
        """A topology-WebSocket message should refresh the owning Session."""
        session_id = create_session("learner1", "learner")
        websocket = FakeWebSocket(
            cookies={"session": session_id},
            inbox=[{"type": "pong"}],
            on_receive=lambda: self.clock.advance(IDLE_GAP),
        )

        await TopologyWebSocketHandler(websocket, FAKE_LAB_KEY).run()  # type: ignore[arg-type]

        self.assertEqual(self.last_seen(session_id), 1000.0 + IDLE_GAP)

    async def test_terminal_traffic_alone_keeps_a_session_advancing(self) -> None:
        """A Learner working only in a Terminal should keep their Session current.

        The browser issues no HTTP request after the page loads, so every
        refresh here comes from Terminal traffic, and each message must move
        last_seen again rather than only the first.
        """
        session_id = create_session("learner1", "learner")
        inbox: List[Any] = [
            {"type": "input", "data": "vtysh\n"},
            {"type": "pong"},
            {"type": "input", "data": "show ip route\n"},
        ]

        await self._run_terminal(session_id, inbox)

        self.assertEqual(self.last_seen(session_id), 1000.0 + 3 * IDLE_GAP)


class TerminalExitCleanupTests(unittest.IsolatedAsyncioTestCase):
    """A PTY reader can close the Terminal that owns it."""

    async def test_node_exit_does_not_self_cancel_the_reader(self) -> None:
        """EOF removes the Terminal without leaving its task cancelling."""
        registry = SessionRegistry()
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        browser = RecordingWebSocket()
        terminal = Terminal(
            id="terminal-1",
            node="h1",
            client_id="session-1",
            username="learner1",
            lab_key="fake",
            terminal_fd=read_fd,
            child_pid=4242,
            created_at=0.0,
            last_activity=0.0,
            websocket=browser,  # type: ignore[arg-type]
        )
        terminal.read_task = asyncio.current_task()
        service = registry.terminal_service
        service.terminals[terminal.id] = terminal
        service.session_terminals[terminal.client_id].add(terminal.id)

        with patch("mniplab.webserver.websocket.registry", registry), patch.object(
            service, "_reap_child", new_callable=AsyncMock
        ):
            await read_terminal(terminal)

        self.assertEqual(browser.closes, [(1000, NODE_EXITED_REASON)])
        self.assertNotIn(terminal.id, service.terminals)
        current_task = asyncio.current_task()
        assert current_task is not None
        self.assertEqual(current_task.cancelling(), 0)


class TerminalConnectionTests(
    FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase
):
    """A Terminal records the browser connection that delivers it.

    Destroying a Session closes its Terminals from outside their own handlers,
    and each closed Terminal has to tell its browser why.  That is only
    possible if the Terminal knows which connection it belongs to.
    """

    def setUp(self) -> None:
        """Freeze the Session clock and install a fake registry."""
        super().setUp()
        self.registry = FakeRegistry()
        patcher = patch("mniplab.webserver.websocket.registry", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_terminal_records_its_browser_connection(self) -> None:
        """The spawned Terminal should carry the connection it was opened on."""
        session_id = create_session("learner1", "learner")
        master_fd, slave_fd = pty.openpty()
        os.close(slave_fd)
        self.addCleanup(os.close, master_fd)
        self.registry.terminal = FakeTerminal(session_id, master_fd)
        websocket = FakeWebSocket(cookies={"session": session_id}, inbox=[])

        await TerminalWebSocketHandler(websocket, FAKE_LAB_KEY, "r1").run()  # type: ignore[arg-type]

        self.assertIs(self.registry.terminal.websocket, websocket)


class TerminalReaderExecutorTests(unittest.IsolatedAsyncioTestCase):
    """Terminal PTY readers execute on a dedicated thread pool executor."""

    async def test_terminal_readers_do_not_starve_default_executor(self) -> None:
        """Concurrent blocking terminal reads do not block the default executor."""
        loop = asyncio.get_running_loop()
        pipes = [os.pipe() for _ in range(5)]
        for r_fd, w_fd in pipes:
            self.addCleanup(self._safe_close, r_fd)
            self.addCleanup(self._safe_close, w_fd)

        readers = []
        for i, (r_fd, _) in enumerate(pipes):
            term = FakeTerminal(f"session-{i}", r_fd, f"term-{i}")
            term.websocket = FakeWebSocket()
            task = asyncio.create_task(read_terminal(term))  # type: ignore[arg-type]
            readers.append(task)

        # Give reader tasks a moment to enter blocking os.read in the worker threads
        await asyncio.sleep(0.05)

        # A task on the default executor must run immediately without waiting for terminals
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: "default-executor-ran"),
            timeout=1.0,
        )
        self.assertEqual(result, "default-executor-ran")

        # Clean up readers by closing write ends to signal EOF
        for _, w_fd in pipes:
            self._safe_close(w_fd)
        await asyncio.gather(*readers, return_exceptions=True)

    @staticmethod
    def _safe_close(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass


class TerminalInputTests(FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase):
    """Input to a busy Terminal never holds up the server."""

    def setUp(self) -> None:
        """Freeze the Session clock and install a fake registry."""
        super().setUp()
        self.registry = FakeRegistry()
        patcher = patch("mniplab.webserver.websocket.registry", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_large_paste_into_a_busy_program_leaves_the_server_free(self) -> None:
        """A paste the program is not reading yet waits without blocking the loop.

        The program in the Terminal holds its tty in raw mode and does not
        read for a second, as vim or a vtysh waiting on a daemon can.  The
        server must keep serving meanwhile, and the program must then get
        every byte of the paste, in order.
        """
        master_fd, slave_fd = pty.openpty()
        tty.setraw(slave_fd)
        terminal = FakeTerminal(create_session("learner1", "learner"), master_fd)
        self.addCleanup(os.close, slave_fd)
        self.addCleanup(os.close, master_fd)
        self.registry.terminal = terminal
        paste = bytes(ord("a") + i % 26 for i in range(LARGE_PASTE))
        received = bytearray()
        drain_after(slave_fd, PROGRAM_BUSY_FOR, len(paste), received)
        websocket = FakeWebSocket(
            cookies={"session": terminal.client_id},
            inbox=[{"type": "input", "data": paste.decode()}],
        )

        stall = asyncio.create_task(
            longest_stall(lambda: len(received) >= len(paste), limit=5.0)
        )
        await asyncio.sleep(0)  # the watch starts before the paste arrives
        await TerminalWebSocketHandler(websocket, FAKE_LAB_KEY, "r1").run()  # type: ignore[arg-type]

        self.assertLess(await stall, PROGRAM_BUSY_FOR / 2)
        self.assertEqual(bytes(received), paste)


if __name__ == "__main__":
    unittest.main()
