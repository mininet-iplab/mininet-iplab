"""WebSocket handlers for Terminals and topology updates."""

import os
import asyncio
import json
import signal
import struct
import fcntl
import termios
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket, WebSocketDisconnect, status

from .auth import (
    check_ws_attempt_limit,
    check_ws_rate_limit,
    get_session_data,
    record_ws_connection,
    record_terminal_input,
    touch_session,
)
from .capture import CaptureSubscriber
from .network_tool import (
    BANDWIDTH_MESSAGE,
    BANDWIDTH_TOOLS,
    SERVER_START_DELAY,
    STOP_GRACE_PERIOD,
    TOOLS,
    signal_tool,
    start_tool,
)
from .prompt import PromptService
from .pseudo_terminal import read_master
from .services import NODE_EXITED_REASON, Terminal, TerminalEnded
from .session import registry
from .config import settings
from .server_settings import MOST_TERMINALS_ON_THE_SERVER
from mniplab.logging import get_logger
from mniplab.netutils import Iperf3ResultParser

logger = get_logger(__name__)

_terminal_reader_executor: Optional[ThreadPoolExecutor] = None


def get_terminal_reader_executor() -> ThreadPoolExecutor:
    """Return the dedicated ThreadPoolExecutor for Terminal readers.

    Terminal readers hold threads in blocking os.read calls while shells are
    idle. Isolating them onto a dedicated thread pool prevents starvation of the
    default asyncio executor.  The pool is sized for the largest server-wide
    Terminal cap Server settings allows, not the cap at start, since the
    Instructor may raise it while the server runs; threads are only made as
    Terminals need them.

    Returns:
        The process-wide ThreadPoolExecutor dedicated to Terminal reading.
    """
    global _terminal_reader_executor
    if _terminal_reader_executor is None:
        _terminal_reader_executor = ThreadPoolExecutor(
            max_workers=MOST_TERMINALS_ON_THE_SERVER + 16,
            thread_name_prefix="terminal-reader",
        )
    return _terminal_reader_executor


# ---------------------------------------------------------------------------
# Background task helpers (kept as free functions because they run as
# asyncio Tasks and do not need encapsulation beyond the handler scope)
# ---------------------------------------------------------------------------

async def read_terminal(
    terminal: Terminal,
    executor: Optional[ThreadPoolExecutor] = None,
) -> None:
    """Read a Terminal's output for as long as its shell lives.

    The reader belongs to the Terminal, not to a connection: it keeps every
    chunk in the Terminal's recent output and forwards it to whichever page
    is attached, if any.  Reading on while detached keeps a chatty program
    from blocking on a full pseudo-terminal and lets a reattaching page see what it
    missed.  A send to a connection that has just gone is dropped; the
    handler serving it detaches the Terminal.

    Each chunk forwarded refreshes the owning Session; see
    :func:`auth.touch_session`.

    When the shell exits — the Node has stopped, and the kernel has torn down
    the namespaces the shell lived in — the master fd reads EOF or fails with
    EIO.  The Terminal is then closed from this side: a Learner typing into a
    shell with nothing behind it is the failure this closes (under the old
    ``mnexec -a`` attach the shell outlived its Node and the browser was
    never told).  The close is idempotent, so racing the handler's own
    cleanup is safe.

    Args:
        terminal: The Terminal being read.
        executor: Optional dedicated executor for blocking reads.
    """
    exec_pool = executor or get_terminal_reader_executor()
    try:
        loop = asyncio.get_running_loop()
        while True:
            try:
                data = await loop.run_in_executor(
                    exec_pool, read_master, terminal.terminal_fd
                )
                if not data:
                    break
            except OSError:
                break

            async with terminal.send_lock:
                terminal.keep_output(data)
                text = terminal.decoder.decode(data)
                websocket = terminal.websocket
                if websocket is None or not text:
                    continue
                try:
                    await websocket.send_json({"type": "output", "data": text})
                except (OSError, RuntimeError, WebSocketDisconnect):
                    continue
            await registry.update_activity(terminal.id)
            touch_session(terminal.client_id)
    except (OSError, RuntimeError, ValueError) as e:
        logger.error(f"Terminal read error for {terminal.id}: {e}")

    await registry.close_terminal_with_reason(terminal.id, NODE_EXITED_REASON)


async def ping_loop(websocket: WebSocket) -> None:
    """Keep a Terminal's WebSocket alive until the connection fails.

    Args:
        websocket: The Terminal's WebSocket connection.
    """
    try:
        while True:
            await asyncio.sleep(settings.terminal_ping_interval)
            await websocket.send_json({"type": "ping"})
    except (OSError, RuntimeError, ValueError):
        pass


async def idle_timeout_check(terminal: Terminal, websocket: WebSocket) -> None:
    """Close a Terminal that has gone unused for the idle timeout.

    Args:
        terminal: The Terminal whose last activity is being watched.
        websocket: The Terminal's WebSocket connection.
    """
    try:
        while True:
            await asyncio.sleep(5)
            idle_time = time.time() - terminal.last_activity
            if idle_time >= settings.terminal_idle_timeout:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE, reason="Idle timeout")
                break
    except (OSError, RuntimeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Handler classes — encapsulate auth, spawning, and message dispatch so
# that the free functions become thin factory wrappers.
# ---------------------------------------------------------------------------

async def _resolve_lab(
    websocket: WebSocket, key: str, viewer: Optional[str], role: Optional[str]
) -> bool:
    """Refuse a connection whose key names no Lab the viewer may reach.

    Mirrors the HTTP routes' ``require_lab``: the URL is the binding.  A
    browser that reaches a Lab under the wrong key — another Group's, or
    one that ended — is told so and closed rather than shown another Lab.

    Args:
        websocket: The connection, already accepted.
        key: The Lab key from the path.
        viewer: The login behind the connection.
        role: Its role.

    Returns:
        True when the key names a running Lab the viewer may see.
    """
    reason = registry.refuse_key(key, viewer, role)
    if reason is None:
        return True
    await websocket.send_json({"type": "error", "message": reason})
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=reason[:120])
    return False


class TerminalWebSocketHandler:
    """Handles a single Terminal's WebSocket connection."""

    def __init__(
        self, websocket: WebSocket, key: str, node: str, terminal_id: Optional[str] = None
    ) -> None:
        """Serve one connection: a new Terminal, or one named to reattach.

        Args:
            websocket: The page's connection.
            key: The Lab key from the path.
            node: The Node the Terminal is on.
            terminal_id: The Terminal the page had before a reload, if any.
        """
        self.websocket = websocket
        self.key = key
        self.node = node
        self._wanted = terminal_id
        self.terminal_id: Optional[str] = None
        self._terminal: Optional[Any] = None
        self._session_token: Optional[str] = None
        self._username: Optional[str] = None
        self._role: Optional[str] = None
        self._closed_on_purpose = False

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            if not await self._authenticate():
                return
            if not await self._check_rate_limit():
                return
            if not await _resolve_lab(self.websocket, self.key, self._username, self._role):
                return
            if self._wanted is not None:
                if not await self._reattach_terminal(self._wanted):
                    return
            elif not await self._spawn_terminal():
                return
            await self._message_loop()
        finally:
            await self._cleanup()

    async def _authenticate(self) -> bool:
        session_token = self.websocket.cookies.get("session")
        session = await get_session_data(session_token)
        if not session:
            await self.websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated"
            )
            return False
        self._session_token = session_token
        self._username = session["username"]
        self._role = session["role"]
        logger.info(
            f"New Terminal request for node {self.node} from user {self._username} "
            f"(session: {session_token})"
        )
        return True

    async def _check_rate_limit(self) -> bool:
        """Refuse the connection if either connection limit is spent.

        Two limits, because one number cannot do both jobs.  The reconnect
        budget is charged in :meth:`_spawn_terminal`, once a Terminal has
        actually been handed over — a Terminal refused for the cap must not
        spend what the Learner needs to reopen the ones they are allowed, since
        the browser offers to reconnect a refused Terminal.  That leaves every
        refusal uncharged, so the attempt ceiling bounds them instead: it is
        charged on the way in and counts attempts whatever their outcome.

        Returns:
            True if the connection may proceed.
        """
        client_id = self._session_token
        # The ceiling is charged first and asked of every attempt, so that the
        # refusals below — and the ones _spawn_terminal raises — cannot go on
        # forever just because they are not charged to the reconnect budget.
        if client_id is None or not check_ws_attempt_limit(client_id):
            await self._refuse_for_rate_limit()
            return False

        if not check_ws_rate_limit(client_id):
            await self._refuse_for_rate_limit()
            return False
        return True

    async def _refuse_for_rate_limit(self) -> None:
        """Tell the browser it is connecting too fast, and close."""
        await self.websocket.send_json({
            "type": "error",
            "message": "Rate limit exceeded. Please wait a moment before reconnecting."
        })
        await asyncio.sleep(0.5)
        await self.websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Rate limit exceeded"
        )

    async def _spawn_terminal(self) -> bool:
        try:
            assert self._session_token is not None
            terminal = await registry.spawn_terminal(
                self.key, self._session_token, self.node, self._username or ""
            )
            self.terminal_id = terminal.id
            self._terminal = terminal
            record_ws_connection(self._session_token)
            await registry.broadcast_status_update()
        except ValueError as e:
            await self.websocket.send_json({
                "type": "error",
                "message": str(e)
            })
            await self.websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False

        # Hold the connection on the Terminal so that its output reaches this
        # page, and so that a close driven from outside this handler — its
        # Session being destroyed — can tell the browser why the Terminal ended.
        terminal.websocket = self.websocket
        self._start_connection_tasks(terminal)

        # The page keeps the id so that, after a reload, it can name this
        # Terminal and get it back.
        await self.websocket.send_json({
            "type": "attached", "id": terminal.id, "reattached": False,
        })
        await self.websocket.send_json({
            "type": "output",
            "data": f"\r\nConnected to {self.node}\r\n"
        })
        # Started last, so nothing the shell prints overtakes the greeting.
        terminal.read_task = asyncio.create_task(read_terminal(terminal))
        return True

    def _start_connection_tasks(self, terminal: Terminal) -> None:
        """Start the keepalive and idle check that last as long as this connection.

        Args:
            terminal: The Terminal this connection holds.
        """
        terminal.ping_task = asyncio.create_task(ping_loop(self.websocket))
        terminal.timeout_task = asyncio.create_task(
            idle_timeout_check(terminal, self.websocket)
        )

    async def _reattach_terminal(self, terminal_id: str) -> bool:
        """Give the page back the Terminal it names, or say it has ended.

        A Terminal that ended — or is not this page's — opens nothing: the
        page is told why, and the close is a normal one, so its usual prompt
        opens a new Terminal on the Node.  A reattach forks no shell but is a
        connection like any other, so it is charged to the reconnect budget.

        Args:
            terminal_id: The Terminal the page had before it went away.

        Returns:
            True when the Terminal is attached to this connection.
        """
        if self._session_token is None:
            raise RuntimeError("a Terminal is reattached only after authentication")
        # Set first so that a page gone before the replay is sent leaves the
        # Terminal detached again rather than held by a dead connection.
        self.terminal_id = terminal_id

        async def greet(replay: str) -> None:
            await self.websocket.send_json({
                "type": "attached", "id": terminal_id, "reattached": True, "replay": replay,
            })

        try:
            terminal = await registry.reattach_terminal(
                terminal_id, self._session_token, self.key, self.node, self.websocket, greet
            )
        except TerminalEnded as ended:
            reason = str(ended)
            await self.websocket.send_json({"type": "ended", "reason": reason})
            await self.websocket.close(code=status.WS_1000_NORMAL_CLOSURE, reason=reason[:120])
            return False
        self._terminal = terminal
        record_ws_connection(self._session_token)
        self._start_connection_tasks(terminal)
        await registry.broadcast_status_update()
        return True

    async def _message_loop(self) -> None:
        assert self._terminal is not None
        assert self.terminal_id is not None
        terminal = self._terminal

        while True:
            try:
                message = await self.websocket.receive_text()
                # Typing, a resize and a keepalive pong are equally proof the
                # browser is attached, so all three refresh the owning Session.
                # See auth.touch_session for why nothing else does.
                touch_session(self._session_token)
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "input":
                    input_data = data.get("data", "")
                    terminal.input.write(input_data.encode('utf-8'))
                    if input_data:
                        record_terminal_input(self._session_token)
                    await registry.update_activity(self.terminal_id)

                elif msg_type == "resize":
                    cols = data.get("cols", 80)
                    rows = data.get("rows", 24)
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(terminal.terminal_fd, termios.TIOCSWINSZ, winsize)

                elif msg_type == "pong":
                    await registry.update_activity(self.terminal_id)

                elif msg_type == "close":
                    # The page closed this Terminal on purpose: end it now
                    # rather than wait for a reload that will not come.
                    self._closed_on_purpose = True
                    break

            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                logger.error("Invalid JSON received from WebSocket")
            except (OSError, RuntimeError, ValueError, TypeError) as e:
                logger.error(f"WebSocket message error: {e}")
                break

    async def _cleanup(self) -> None:
        """End the Terminal if the page closed it on purpose, else detach it.

        A connection closed any other way — a reload, a closed tab, a dropped
        network — leaves the Terminal running for the grace window.
        """
        if not self.terminal_id:
            return
        if self._closed_on_purpose:
            await registry.close_terminal(self.terminal_id)
        else:
            await registry.detach_terminal(self.terminal_id, self.websocket)
        await registry.broadcast_status_update()


class TopologyWebSocketHandler:
    """Handles one browser's subscription to a Lab's feed.

    The feed carries the Lab's Topology and its Service updates; the
    connection is closed when the Lab ends.
    """

    def __init__(self, websocket: WebSocket, key: str):
        self.websocket = websocket
        self.key = key
        self._session_token: Optional[str] = None
        self._username: Optional[str] = None
        self._role: Optional[str] = None

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            if not await self._authenticate():
                return
            if not await _resolve_lab(self.websocket, self.key, self._username, self._role):
                return
            await registry.add_topology_subscriber(self.key, self.websocket)
            await self._send_initial_state()
            await self._message_loop()
        finally:
            await registry.remove_topology_subscriber(self.key, self.websocket)

    async def _authenticate(self) -> bool:
        session_token = self.websocket.cookies.get("session")
        session = await get_session_data(session_token)
        if not session:
            await self.websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated"
            )
            return False
        self._session_token = session_token
        self._username = session["username"]
        self._role = session["role"]
        logger.info(f"New topology WebSocket connection from user {self._username}")
        return True

    async def _send_initial_state(self) -> None:
        topology_data = await registry.get_topology(self.key)
        await self.websocket.send_json({"type": "topology_update", "data": topology_data})

    async def _message_loop(self) -> None:
        while True:
            try:
                message = await self.websocket.receive_json()
                # Refreshes the owning Session for the same reason the Terminal
                # loop does.  No client sends on this socket today — the page
                # answers a ping the server only ever sends on a Terminal
                # socket — so this path is latent, correct for the traffic it
                # would carry rather than load-bearing now.
                touch_session(self._session_token)
                msg_type = message.get("type")

                if msg_type == "pong":
                    pass
                elif msg_type == "request_update":
                    await registry.broadcast_topology_update(self.key)

            except WebSocketDisconnect:
                break
            except (OSError, RuntimeError, ValueError, TypeError) as e:
                logger.error(f"Topology WebSocket message error: {e}")
                break


class LabsWebSocketHandler:
    """Handles one browser's subscription to the Labs feed.

    The feed says which Lab is alive for the login behind the browser:
    every signed-in browser holds it, whether or not a Lab is running, so a
    Lab started or stopped from another browser reaches this one.
    """

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self._session_token: Optional[str] = None
        self._username: Optional[str] = None
        self._role: Optional[str] = None

    async def run(self) -> None:
        """Send the Labs status, then every update, until the viewer leaves."""
        await self.websocket.accept()
        try:
            if not await self._authenticate():
                return
            await registry.add_status_subscriber(
                self.websocket, self._username, self._role, send_initial=True
            )
            await self._message_loop()
        finally:
            await registry.remove_status_subscriber(self.websocket)

    async def _authenticate(self) -> bool:
        session_token = self.websocket.cookies.get("session")
        session = await get_session_data(session_token)
        if not session:
            await self.websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated"
            )
            return False
        self._session_token = session_token
        self._username = session["username"]
        self._role = session["role"]
        logger.info(f"New Labs WebSocket connection from user {self._username}")
        return True

    async def _message_loop(self) -> None:
        while True:
            try:
                message = await self.websocket.receive_json()
                touch_session(self._session_token)
                msg_type = message.get("type")

                if msg_type == "pong":
                    pass
                elif msg_type == "request_update":
                    await registry.broadcast_status_update()

            except WebSocketDisconnect:
                break
            except (OSError, RuntimeError, ValueError, TypeError) as e:
                logger.error(f"Labs WebSocket message error: {e}")
                break


class PromptWebSocketHandler:
    """Handles a WebSocket connection delivering the Lab Prompt."""

    def __init__(self, websocket: WebSocket, key: str):
        self.websocket = websocket
        self.key = key
        self._session_token: Optional[str] = None
        self._username: Optional[str] = None
        self._role: Optional[str] = None
        self._prompt: Optional[PromptService] = None

    async def _require_instructor(self) -> bool:
        """Refuse a Learner: the Lab Prompt is the Instructor's.

        One screen is shared by everyone attached to a Lab and every
        attacher may type, and its commands address the Lab as a whole
        rather than any Node.  A Learner drives their Lab through Node
        Terminals and the Topology instead.

        Returns:
            True when the caller may open the Lab Prompt.
        """
        if self._role == "instructor":
            return True
        message = "The Lab Prompt is the Instructor's; use a Node Terminal"
        await self.websocket.send_json({"type": "error", "message": message})
        await self.websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason=message[:120]
        )
        return False

    async def run(self) -> None:
        """Join the Instructor to the Lab's Prompt until the viewer leaves."""
        await self.websocket.accept()
        try:
            if not await self._authenticate():
                return
            if not await self._check_rate_limit():
                return
            if not await self._require_instructor():
                return
            if not await _resolve_lab(self.websocket, self.key, self._username, self._role):
                return
            lab = registry.lab_service.get(self.key)
            if lab is None or not lab.prompt.has_prompt:
                await self.websocket.send_json({
                    "type": "error",
                    "message": "No Lab Prompt available"
                })
                await self.websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="No Lab Prompt available",
                )
                return

            self._prompt = lab.prompt
            await self._prompt.add_viewer(self.websocket)
            assert self._session_token is not None
            record_ws_connection(self._session_token)
            await self._message_loop()
        finally:
            if self._prompt is not None:
                self._prompt.remove_viewer(self.websocket)

    async def _authenticate(self) -> bool:
        session_token = self.websocket.cookies.get("session")
        session = await get_session_data(session_token)
        if not session:
            await self.websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated"
            )
            return False
        self._session_token = session_token
        self._username = session["username"]
        self._role = session["role"]
        logger.info(
            f"New Lab Prompt request from user {self._username} (session: {session_token})"
        )
        return True

    async def _check_rate_limit(self) -> bool:
        client_id = self._session_token
        if client_id is None or not check_ws_attempt_limit(client_id):
            await self._refuse_for_rate_limit()
            return False

        if not check_ws_rate_limit(client_id):
            await self._refuse_for_rate_limit()
            return False
        return True

    async def _refuse_for_rate_limit(self) -> None:
        await self.websocket.send_json({
            "type": "error",
            "message": "Rate limit exceeded. Please wait a moment before reconnecting."
        })
        await asyncio.sleep(0.5)
        await self.websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Rate limit exceeded"
        )

    async def _message_loop(self) -> None:
        assert self._prompt is not None
        prompt = self._prompt
        while True:
            try:
                message = await self.websocket.receive_text()
                touch_session(self._session_token)
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "input":
                    input_data = data.get("data", "")
                    prompt.write_input(input_data)

                elif msg_type == "resize":
                    cols = data.get("cols", 80)
                    rows = data.get("rows", 24)
                    prompt.resize(cols, rows)

                elif msg_type == "pong":
                    pass

            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                logger.error("Invalid JSON received from Prompt WebSocket")
            except (OSError, RuntimeError, ValueError, TypeError) as e:
                logger.error(f"Prompt WebSocket message error: {e}")
                break


class CaptureWebSocketHandler:
    """Delivers one Packet Capture to one browser pane.

    The Capture is shared: this connection is one viewer of it, attached
    for as long as the pane is open.  A pane closed on purpose says so
    first, and the Capture stops at once if nobody else is watching; a
    connection that simply drops — a reload — leaves it running for the
    grace window, so the reloaded page picks up where it left off.

    A pane that changes its filter does so on this same connection: it
    leaves its Capture and joins the one under the new filter, and is told
    it restarted.  Doing it here rather than by reconnecting means the
    old Capture is left before the new one counts against the viewer's
    two.  A Capture that ends — tshark refusing the filter above all —
    leaves the pane connected, so the Learner can correct it.
    """

    def __init__(
        self, websocket: WebSocket, key: str, node: str, interface: str, capture_filter: str = ""
    ) -> None:
        self.websocket = websocket
        self.key = key
        self.node = node
        self.interface = interface
        self.capture_filter = capture_filter
        self._session_token: Optional[str] = None
        self._username: Optional[str] = None
        self._role: Optional[str] = None
        self._subscriber: Optional[CaptureSubscriber] = None
        self._linger = True

    async def run(self) -> None:
        """Serve the pane from the handshake until it closes or the Capture ends."""
        await self.websocket.accept()
        try:
            if not await self._authenticate():
                return
            if not await self._check_rate_limit():
                return
            if not await _resolve_lab(self.websocket, self.key, self._username, self._role):
                return
            if not await self._open_capture():
                return
            await self._pump()
        finally:
            if self._subscriber is not None:
                await registry.close_capture(self._subscriber, linger=self._linger)

    async def _authenticate(self) -> bool:
        session_token = self.websocket.cookies.get("session")
        session = await get_session_data(session_token)
        if not session:
            await self.websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated"
            )
            return False
        self._session_token = session_token
        self._username = session["username"]
        self._role = session["role"]
        logger.info(
            f"Packet Capture request for {self.node}:{self.interface} "
            f"from user {self._username}"
        )
        return True

    async def _check_rate_limit(self) -> bool:
        """Refuse the connection if either connection limit is spent.

        The same two limits a Terminal answers to, for the same reason: a
        pane that reconnects on its own must not be able to hammer the
        server.

        Returns:
            True if the connection may proceed.
        """
        client_id = self._session_token
        if client_id is None or not check_ws_attempt_limit(client_id) \
                or not check_ws_rate_limit(client_id):
            await self.websocket.send_json({
                "type": "error",
                "message": "Rate limit exceeded. Please wait a moment before reconnecting."
            })
            await asyncio.sleep(0.5)
            await self.websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Rate limit exceeded"
            )
            return False
        return True

    async def _join(self, capture_filter: str) -> None:
        """Watch this pane's Interface through ``capture_filter``, as this viewer."""
        self._subscriber = await registry.open_capture(
            self.key, self.node, self.interface,
            capture_filter=capture_filter, viewer=self._session_token,
        )

    async def _open_capture(self) -> bool:
        try:
            await self._join(self.capture_filter)
        except ValueError as e:
            await self.websocket.send_json({"type": "error", "message": str(e)})
            await self.websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason=str(e)[:120]
            )
            return False
        assert self._session_token is not None
        record_ws_connection(self._session_token)
        return True

    async def _pump(self) -> None:
        """Send the feed and answer the pane, until the pane goes."""
        receiving: "asyncio.Future[Any]" = asyncio.ensure_future(self.websocket.receive_json())
        feeding = self._next_feed()
        try:
            while True:
                waiting = {receiving} if feeding is None else {receiving, feeding}
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if feeding is not None and feeding in done:
                    feeding = await self._forward(feeding.result())
                if receiving in done:
                    message = receiving.result()
                    touch_session(self._session_token)
                    if message.get("type") == "close":
                        self._linger = False
                        return
                    if message.get("type") == "filter":
                        if feeding is not None:
                            feeding.cancel()
                        await self._refilter(str(message.get("filter") or ""))
                        feeding = self._next_feed()
                    receiving = asyncio.ensure_future(self.websocket.receive_json())
        except (OSError, RuntimeError, ValueError, WebSocketDisconnect) as e:
            logger.debug(f"Packet Capture connection ended: {e}")
        finally:
            for task in (receiving, feeding):
                if task is not None:
                    task.cancel()

    def _next_feed(self) -> "Optional[asyncio.Future[List[Dict[str, Any]]]]":
        """Wait for the Capture's next messages, or for nothing if none is open."""
        if self._subscriber is None:
            return None
        return asyncio.ensure_future(self._subscriber.get())

    async def _forward(
        self, messages: List[Dict[str, Any]]
    ) -> "Optional[asyncio.Future[List[Dict[str, Any]]]]":
        """Send the Capture's messages; stop feeding once it has ended."""
        for message in messages:
            await self.websocket.send_json(message)
            if message["type"] == "ended" and self._subscriber is not None:
                await registry.close_capture(self._subscriber, linger=False)
                self._subscriber = None
        # Packets arriving are proof the pane is open, as output is for a
        # Terminal; see auth.touch_session.
        touch_session(self._session_token)
        return self._next_feed()

    async def _refilter(self, capture_filter: str) -> None:
        """Leave the current Capture and join the one under ``capture_filter``.

        The filter is applied by the kernel as packets are captured, so
        nothing already captured can be filtered: the pane is told the
        Capture restarted, and why, before the new one's first message.
        """
        if self._subscriber is not None:
            previous, self._subscriber = self._subscriber, None
            await registry.close_capture(previous, linger=False)
        try:
            await self._join(capture_filter)
        except ValueError as e:
            await self.websocket.send_json({"type": "error", "message": str(e)})
            return
        self.capture_filter = capture_filter.strip()
        await self.websocket.send_json({"type": "restarted", "filter": self.capture_filter})


# Network Tools runs one login may have going at once.  Each is a process on
# the server, so a page that opened connections in a loop must not be able
# to start them without bound.
MAX_TOOL_RUNS_PER_SESSION = 2
_tool_runs: Dict[str, int] = {}

# The bandwidth tests running now, as (Lab key, tool, server Node).  iperf3
# refuses a second client on a busy server, so a second test on one is
# refused here before anything starts.
_busy_servers: Set[Tuple[str, str, str]] = set()


def _parse_tool_request(message: Any) -> Dict[str, Any]:
    """Read the test a page asked for from its first message.

    Args:
        message: The decoded first message.

    Returns:
        The keyword arguments of ``registry.network_tool_argv`` after the key,
        or of ``registry.bandwidth_test_argv`` for a bandwidth test.

    Raises:
        ValueError: The message is not a ``start`` naming a known tool, the
            Nodes it names are missing, its count or duration is not a whole
            number, or its bandwidth is neither absent nor text.
    """
    if not isinstance(message, dict) or message.get("type") != "start":
        raise ValueError("Expected a start message")
    tool = message.get("tool")
    if tool not in TOOLS and tool not in BANDWIDTH_TOOLS:
        raise ValueError(f"Unknown Network Tools test: {tool!r}")
    if tool in BANDWIDTH_TOOLS:
        server = message.get("server")
        client = message.get("client")
        if not isinstance(server, str) or not server or not isinstance(client, str) or not client:
            raise ValueError("Select server and client")
        seconds = message.get("seconds", 10)
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise ValueError("Duration must be a whole number")
        protocol = message.get("protocol", "tcp")
        json_output = bool(message.get("json_output"))
        # The builder checks a bandwidth it is given; here only its type is read.
        bandwidth = message.get("bandwidth")
        if bandwidth is None or bandwidth == "":
            bandwidth = None
        elif not isinstance(bandwidth, str):
            raise ValueError(BANDWIDTH_MESSAGE)
        return {
            "tool": tool,
            "server": server,
            "client": client,
            "seconds": seconds,
            "protocol": protocol,
            "json_output": json_output,
            "bandwidth": bandwidth,
        }
    source = message.get("source")
    destination = message.get("destination")
    if not isinstance(source, str) or not source or not isinstance(destination, str) \
            or not destination.strip():
        raise ValueError("Select a source and enter a destination")
    count = message.get("count", 4)
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("Packet Count must be a whole number")
    return {
        "tool": tool,
        "source": source,
        "destination": destination.strip(),
        "count": count,
        "ipv6": bool(message.get("ipv6")),
        "resolve": bool(message.get("resolve")),
    }


def _summarise_iperf3(request: Dict[str, Any], raw: str) -> str:
    """Format a JSON iperf3 run as the short summary the page shows.

    Args:
        request: The bandwidth test as :func:`_parse_tool_request` read it.
        raw: Everything the client printed.

    Returns:
        The summary, with the raw output only where it could not be read.
    """
    return Iperf3ResultParser.format_summary(
        request["server"], request["client"], request["seconds"], request["protocol"],
        Iperf3ResultParser.parse(raw, request["protocol"]), raw,
    )


class NetworkToolWebSocketHandler:
    """Streams one Network Tools test to a page, and stops it on request.

    The page opens one connection per run and sends the test as its first
    message.  The server answers ``started``, then each line of the tool's
    output as it is printed, then ``exit`` with whether it was stopped, and
    closes.  A ``stop`` message interrupts the tool as Ctrl+C would, so a
    ping still prints its summary; a connection that drops kills it.

    A bandwidth test starts its server quietly in the other Node first, and
    ends that server when the client is done, however the run ends.  With
    JSON output its one summary comes at the end instead of the lines.

    A run counts against the connection attempt ceiling but not against the
    Terminal reconnect budget, which a Learner pinging often would
    otherwise spend.
    """

    def __init__(self, websocket: WebSocket, key: str) -> None:
        self.websocket = websocket
        self.key = key
        self._session_token: Optional[str] = None
        self._username: Optional[str] = None
        self._role: Optional[str] = None

    async def run(self) -> None:
        """Serve one run from the handshake until the tool exits or the page goes."""
        await self.websocket.accept()
        if not await self._authenticate():
            return
        token = self._session_token
        assert token is not None
        if not check_ws_attempt_limit(token):
            await self._refuse("Rate limit exceeded. Please wait a moment before trying again.")
            return
        if not await _resolve_lab(self.websocket, self.key, self._username, self._role):
            return
        if _tool_runs.get(token, 0) >= MAX_TOOL_RUNS_PER_SESSION:
            await self._refuse(
                f"At most {MAX_TOOL_RUNS_PER_SESSION} Network Tools tests may run at once; "
                "stop one first"
            )
            return
        # Counted before anything is awaited, so connections opened together
        # cannot all pass the check above.
        _tool_runs[token] = _tool_runs.get(token, 0) + 1
        try:
            await self._serve()
        finally:
            _tool_runs[token] -= 1
            if not _tool_runs[token]:
                del _tool_runs[token]

    async def _serve(self) -> None:
        """Read the page's test, start it, and stream it until it exits."""
        server_argv: Optional[List[str]] = None
        try:
            request = _parse_tool_request(await self.websocket.receive_json())
            if request["tool"] in BANDWIDTH_TOOLS:
                server_argv, argv = await registry.bandwidth_test_argv(self.key, **request)
            else:
                argv = await registry.network_tool_argv(self.key, **request)
        except WebSocketDisconnect:
            return
        except json.JSONDecodeError:
            await self._refuse("Expected a start message")
            return
        except ValueError as e:
            await self._refuse(str(e))
            return
        if server_argv is not None:
            await self._serve_bandwidth(request, server_argv, argv)
            return
        try:
            process = await start_tool(argv)
        except OSError as e:
            logger.error(f"Network Tools could not start {argv}: {e}")
            await self._refuse(f"Could not start {request['tool']}: {e.strerror or e}")
            return
        await self._pump(process)

    async def _serve_bandwidth(
        self, request: Dict[str, Any], server_argv: List[str], client_argv: List[str]
    ) -> None:
        """Run one bandwidth test: its server quietly first, then its client, streamed.

        The server is ended here whatever happens, since it outlives its
        client only while nothing has connected to it.

        Args:
            request: The test as :func:`_parse_tool_request` read it.
            server_argv: The server's command.
            client_argv: The client's command.
        """
        tool, server = request["tool"], request["server"]
        busy = (self.key, tool, server)
        if busy in _busy_servers:
            await self._refuse(
                f"An {tool} test is already running on {server}; try again when it finishes"
            )
            return
        _busy_servers.add(busy)
        server_process: Optional["asyncio.subprocess.Process"] = None
        summarise = partial(_summarise_iperf3, request) if request["json_output"] else None
        try:
            try:
                server_process = await start_tool(server_argv, quiet=True)
                await asyncio.sleep(SERVER_START_DELAY)
                client_process = await start_tool(client_argv)
            except OSError as e:
                logger.error(f"Network Tools could not start {client_argv}: {e}")
                await self._refuse(f"Could not start {tool}: {e.strerror or e}")
                return
            await self._pump(client_process, summarise)
        finally:
            _busy_servers.discard(busy)
            if server_process is not None:
                if server_process.returncode is None:
                    signal_tool(server_process, signal.SIGKILL)
                await server_process.wait()

    async def _authenticate(self) -> bool:
        session_token = self.websocket.cookies.get("session")
        session = await get_session_data(session_token)
        if not session:
            await self._refuse("Not signed in; sign in again")
            return False
        self._session_token = session_token
        self._username = session["username"]
        self._role = session["role"]
        return True

    async def _refuse(self, message: str) -> None:
        await self.websocket.send_json({"type": "error", "message": message})
        await self.websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=message[:120])

    async def _pump(
        self,
        process: "asyncio.subprocess.Process",
        summarise: Optional[Callable[[str], str]] = None,
    ) -> None:
        """Send the tool's output and answer ``stop``, until the tool exits.

        Args:
            process: The running test.
            summarise: When given, the output is held back and sent once, at
                the end, as ``summarise`` of everything printed.
        """
        assert process.stdout is not None
        stdout = process.stdout
        loop = asyncio.get_running_loop()
        reading: "asyncio.Future[bytes]" = asyncio.ensure_future(stdout.readline())
        receiving: "asyncio.Future[Any]" = asyncio.ensure_future(self.websocket.receive_json())
        kill: Optional[asyncio.TimerHandle] = None
        stopped = False
        collected: List[str] = []
        try:
            await self.websocket.send_json({"type": "started"})
            while True:
                done, _ = await asyncio.wait(
                    {reading, receiving}, return_when=asyncio.FIRST_COMPLETED
                )
                if reading in done:
                    line = reading.result()
                    if not line:
                        break
                    text = line.decode(errors="replace")
                    if summarise is None:
                        await self.websocket.send_json({"type": "output", "data": text})
                    else:
                        collected.append(text)
                    # Output arriving is proof the page is open, as for a Terminal.
                    touch_session(self._session_token)
                    reading = asyncio.ensure_future(stdout.readline())
                if receiving in done:
                    message = receiving.result()
                    if isinstance(message, dict) and message.get("type") == "stop" and not stopped:
                        stopped = True
                        signal_tool(process, signal.SIGINT)
                        kill = loop.call_later(
                            STOP_GRACE_PERIOD, signal_tool, process, signal.SIGKILL
                        )
                    receiving = asyncio.ensure_future(self.websocket.receive_json())
            code = await process.wait()
            if kill is not None:
                kill.cancel()
            if summarise is not None:
                await self.websocket.send_json(
                    {"type": "output", "data": summarise("".join(collected))}
                )
            await self.websocket.send_json({"type": "exit", "code": code, "stopped": stopped})
            await self.websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        except (OSError, RuntimeError, ValueError, WebSocketDisconnect) as e:
            logger.debug(f"Network Tools run ended early: {e}")
        finally:
            for task in (reading, receiving):
                task.cancel()
            if kill is not None:
                kill.cancel()
            if process.returncode is None:
                signal_tool(process, signal.SIGKILL)
                await process.wait()


# ---------------------------------------------------------------------------
# Free-function entry points — thin wrappers for FastAPI routing
# ---------------------------------------------------------------------------

async def handle_terminal_websocket(
    websocket: WebSocket, key: str, node: str, terminal: Optional[str] = None
) -> None:
    """Handle the WebSocket connection delivering a Terminal on a node of a Lab.

    Args:
        websocket: The page's connection.
        key: The Lab key.
        node: The Node the Terminal is on.
        terminal: The Terminal to reattach, when the page had one before a reload.
    """
    handler = TerminalWebSocketHandler(websocket, key, node, terminal)
    await handler.run()


async def handle_capture_websocket(
    websocket: WebSocket, key: str, node: str, interface: str, capture_filter: str = ""
) -> None:
    """Handle the WebSocket connection delivering a Packet Capture on an Interface."""
    handler = CaptureWebSocketHandler(websocket, key, node, interface, capture_filter)
    await handler.run()


async def handle_network_tool_websocket(websocket: WebSocket, key: str) -> None:
    """Handle the WebSocket connection streaming one Network Tools test."""
    handler = NetworkToolWebSocketHandler(websocket, key)
    await handler.run()


async def handle_prompt_websocket(websocket: WebSocket, key: str) -> None:
    """Handle the WebSocket connection delivering the Lab Prompt."""
    handler = PromptWebSocketHandler(websocket, key)
    await handler.run()


async def handle_topology_websocket(websocket: WebSocket, key: str) -> None:
    """Handle a WebSocket subscription to a Lab's Topology feed."""
    handler = TopologyWebSocketHandler(websocket, key)
    await handler.run()


async def handle_labs_websocket(websocket: WebSocket) -> None:
    """Handle a WebSocket subscription to the Labs feed."""
    handler = LabsWebSocketHandler(websocket)
    await handler.run()
