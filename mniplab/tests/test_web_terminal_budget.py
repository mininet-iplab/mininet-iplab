"""Tests for how the Terminal budget is spent and released.

A Learner who reloads the page drops every Terminal at once and immediately
asks for them back.  The budget tests drive the real WebSocket handler against
the real Terminal registry — only the spawner and the browser socket are faked
— because the defects they lock down live in the seam between the two: what a
refused Terminal costs, and how long a closing one keeps the budget.

The ceiling and cleanup tests drive the registry directly through the same
spawner seam: every Terminal they count was opened by ``spawn_terminal``,
never written into the registry's books by hand.
"""

import asyncio
import os
import pty
import subprocess
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from fastapi import WebSocketDisconnect

from mniplab.tests.fakes import RecordingWebSocket
from mniplab.webserver.auth import create_session, ws_attempt_limiter, ws_limiter
from mniplab.webserver.config import RELOADS_PER_MINUTE, settings
from mniplab.labhandle import LocalLabHandle, NodeAttachment
from mniplab.webserver.services import TerminalService
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.terminal_spawner import TerminalSpawner
from mniplab.webserver.websocket import TerminalWebSocketHandler


class FakeNode:
    """A node with just the attributes the spawner reads."""

    def __init__(self, name: str) -> None:
        """Initialize a fake node.

        Args:
            name: The node's name.
        """
        self.name = name
        self.pid = os.getpid()
        self.params: Dict[str, Any] = {}


class FakeLab:
    """A Lab holding one node, enough for a Terminal to attach to."""

    def __init__(self) -> None:
        """Initialize a fake Lab with a single host."""
        self.hosts = [FakeNode("h1")]
        self.switches: List[FakeNode] = []

    def node_home_dir(self, node_name: str) -> str:
        """Return a home for the Node, as the attachment names one."""
        return f"/tmp/fake-home/{node_name}"


class FakeSpawner(TerminalSpawner):
    """Hands back a real pty(7) master fd and a real, killable child.

    Real on both counts deliberately: the reader must block in ``os.read`` the
    way it does against a quiet shell, and the close path must have a process
    to signal, since the cost of reaping one is what these tests measure.
    """

    def __init__(self) -> None:
        """Initialize a spawner tracking what it must clean up."""
        self.slaves: List[int] = []
        self.children: List[subprocess.Popen] = []

    def spawn(self, attachment: NodeAttachment) -> tuple[int, int]:
        """Open a pty(7) pair and start a child that sits still.

        Args:
            attachment: The Node the Terminal attaches to.

        Returns:
            Tuple of (child_pid, terminal_fd).
        """
        del attachment
        master, slave = pty.openpty()
        # The slave stays open so that a read of the master blocks instead of
        # reaching end-of-file, which is the state a live, quiet shell leaves
        # the reader in.
        self.slaves.append(slave)
        child = subprocess.Popen(["sleep", "60"])
        self.children.append(child)
        return child.pid, master

    def cleanup(self) -> None:
        """Close every descriptor and kill every child this spawner created."""
        for fd in self.slaves:
            try:
                os.close(fd)
            except OSError:
                pass
        for child in self.children:
            child.kill()
            child.wait()


class BrowserSocket:
    """A browser tab: connects, sits idle, then goes away or closes on purpose.

    Attributes:
        cookies: Cookies the tab presents, notably the session cookie.
        sent: Every payload the handler sent, in order.
        closed: True once the handler closed the connection.
    """

    def __init__(self, token: Optional[str]) -> None:
        """Initialize a tab presenting one Session.

        Args:
            token: The Session token in the tab's cookie, if any.
        """
        self.cookies = {"session": token} if token else {}
        self.sent: List[Any] = []
        self.closed = False
        self._gone = asyncio.Event()
        self._parting: Optional[str] = None

    async def accept(self) -> None:
        """Accept the connection."""

    async def receive_text(self) -> str:
        """Block until the tab goes away, saying so first if it closes on purpose.

        Returns:
            The close message of a tab closing its Terminal on purpose.

        Raises:
            WebSocketDisconnect: Once the tab has gone.
        """
        await self._gone.wait()
        if self._parting is not None:
            parting, self._parting = self._parting, None
            return parting
        raise WebSocketDisconnect(code=1001)

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
        self._gone.set()

    def reload(self) -> None:
        """Drop the connection the way a reloading tab does."""
        self._gone.set()

    def close_terminal(self) -> None:
        """Close the Terminal on purpose, as its close control does."""
        self._parting = '{"type": "close"}'
        self._gone.set()

    def terminal_id(self) -> str:
        """Return the id the server gave the Terminal this tab holds."""
        return next(p["id"] for p in self.sent if p.get("type") == "attached")

    def error_message(self) -> str:
        """Return the last error the handler sent.

        Returns:
            The error message, or an empty string if none was sent.
        """
        for payload in reversed(self.sent):
            if payload.get("type") == "error":
                return payload.get("message", "")
        return ""


class TerminalBudgetTests(unittest.IsolatedAsyncioTestCase):
    """The Terminal budget is spent on Terminals, and released with them."""

    async def asyncSetUp(self) -> None:
        """Install a registry whose Terminals are cheap to spawn and reap."""
        self.registry = SessionRegistry()
        self.spawner = FakeSpawner()
        self.registry.terminal_service = TerminalService(spawner=self.spawner)
        self.registry.lab_service.lab = FakeLab()
        patcher = patch("mniplab.webserver.websocket.registry", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.spawner.cleanup)
        ws_limiter.attempts.clear()
        ws_attempt_limiter.attempts.clear()
        self.token = create_session("bob", "learner")
        self.cap = settings.terminal_per_session_cap

    async def _open_terminal(
        self, terminal_id: Optional[str] = None
    ) -> tuple[BrowserSocket, asyncio.Task]:
        """Open one Terminal, or reattach the one named, and leave it connected.

        Args:
            terminal_id: The Terminal a reloaded tab names, if any.

        Returns:
            The tab and the handler task serving it.
        """
        socket = BrowserSocket(self.token)
        task = asyncio.create_task(TerminalWebSocketHandler(socket, str(self.registry.lab_key), "h1", terminal_id).run())  # type: ignore[arg-type]
        return socket, task

    async def _fill_the_cap(self) -> List[tuple[BrowserSocket, asyncio.Task]]:
        """Open Terminals until the Session is at its cap.

        Returns:
            Every tab opened, with the handler task serving it.
        """
        opened = [await self._open_terminal() for _ in range(self.cap)]
        await self._settle()
        self.assertEqual(self.registry.terminal_count(self.token), self.cap)
        return opened

    async def _settle(self) -> None:
        """Let every pending handler step run."""
        for _ in range(50):
            await asyncio.sleep(0)

    async def _close_all(self, opened: List[tuple[BrowserSocket, asyncio.Task]]) -> None:
        """Close every tab's Terminal on purpose and wait for its handler to finish.

        Args:
            opened: The tabs to close.
        """
        for socket, _ in opened:
            socket.close_terminal()
        await self._wait_for(opened)

    async def _reload(self, opened: List[tuple[BrowserSocket, asyncio.Task]]) -> None:
        """Drop every tab as a reload does and wait for its handler to finish.

        Args:
            opened: The tabs to drop.
        """
        for socket, _ in opened:
            socket.reload()
        await self._wait_for(opened)

    @staticmethod
    async def _wait_for(opened: List[tuple[BrowserSocket, asyncio.Task]]) -> None:
        """Wait for the handlers serving some tabs to finish.

        Args:
            opened: The tabs whose handlers to wait for.
        """
        await asyncio.wait_for(
            asyncio.gather(*(task for _, task in opened)), timeout=30
        )

    async def _refuse_one(self) -> str:
        """Ask for one Terminal beyond the cap.

        Returns:
            The error message the Learner was shown.
        """
        refused = BrowserSocket(self.token)
        await TerminalWebSocketHandler(refused, str(self.registry.lab_key), "h1").run()  # type: ignore[arg-type]
        return refused.error_message()

    async def test_a_refused_terminal_does_not_cost_a_reconnect_slot(self) -> None:
        """Being told 'you already have five' must not spend the budget.

        The browser offers to reconnect a Terminal it could not open, and every
        keystroke takes that offer.  Charging each attempt made the Learner's
        own allowed Terminals exhaust the reconnect budget, so the next refusal
        blamed the rate limit for a cap they had hit minutes earlier.
        """
        opened = await self._fill_the_cap()
        spent_at_the_cap = len(ws_limiter.attempts[self.token])

        for _ in range(settings.ws_rate_limit):
            self.assertIn(
                "cap exceeded", await self._refuse_one(),
                "a refused Terminal was blamed on the rate limit",
            )

        self.assertEqual(
            len(ws_limiter.attempts[self.token]), spent_at_the_cap,
            "refused Terminals spent the reconnect budget",
        )
        await self._close_all(opened)

    async def test_closing_every_terminal_releases_the_budget_at_once(self) -> None:
        """Closing Terminals must free every one before the shells are reaped.

        Reaping a shell costs half a second, and holding the registry lock
        across it serialised every close behind every other one while blocking
        new Terminals on the same lock.  A Learner closing a full cap
        therefore waited seconds for Terminals the server had already given up,
        and the Instructor's list went on showing them.
        """
        opened = await self._fill_the_cap()
        service = self.registry.terminal_service

        seen_during_reap: List[tuple[bool, int]] = []
        original_reap = service._reap_child

        async def observing_reap(child_pid: int) -> None:
            seen_during_reap.append(
                (service.terminals_lock.locked(),
                 self.registry.terminal_count(self.token))
            )
            await original_reap(child_pid)

        with patch.object(service, "_reap_child", observing_reap):
            await self._close_all(opened)

        self.assertEqual(len(seen_during_reap), self.cap)
        for lock_held, remaining in seen_during_reap:
            self.assertFalse(lock_held, "the registry lock was held while reaping")
            self.assertLess(
                remaining, self.cap,
                "the Terminal still counted against the budget while reaping",
            )
        self.assertEqual(self.registry.terminal_count(self.token), 0)
        self.assertEqual(await self.registry.get_terminals(), [])

    async def test_refused_terminals_are_bounded_by_the_attempt_ceiling(self) -> None:
        """Connections that are refused must still be counted somewhere.

        Charging only served Terminals is what keeps a Learner's own cap from
        eating their reconnect budget, but it leaves every refusal free: a
        client asking repeatedly for a node that does not exist was never cut
        off, and each attempt still took the registry lock.  The ceiling counts
        every attempt whatever its outcome, so a storm ends even when nothing
        it asks for is ever granted.
        """
        for _ in range(settings.ws_attempt_limit):
            refused = BrowserSocket(self.token)
            await TerminalWebSocketHandler(refused, str(self.registry.lab_key), "does-not-exist").run()  # type: ignore[arg-type]
            self.assertNotIn("Rate limit", refused.error_message())

        beyond = BrowserSocket(self.token)
        await TerminalWebSocketHandler(beyond, str(self.registry.lab_key), "does-not-exist").run()  # type: ignore[arg-type]
        self.assertIn(
            "Rate limit", beyond.error_message(),
            "refused connections were never counted, so the storm never ended",
        )

    async def test_reloading_three_times_in_a_minute_still_returns_every_terminal(self) -> None:
        """Three reloads in one minute must all give the Terminals back.

        This is the Learner's whole experience of a reload: the tabs come back,
        and none is refused for a cap or a budget the reload itself consumed.
        A full set of Terminals per page load means the reconnect budget has
        to outrun the cap by several reloads, or a later one locks the Learner
        out for the rest of the minute; three is ``RELOADS_PER_MINUTE``.  A
        reloaded page reattaches the Terminals it had, each one a connection
        charged to the budget.
        """
        opened = await self._fill_the_cap()

        for attempt in range(1, RELOADS_PER_MINUTE + 1):
            await self._reload(opened)
            ids = [socket.terminal_id() for socket, _ in opened]
            opened = [await self._open_terminal(terminal_id) for terminal_id in ids]
            await self._settle()

            for index, (socket, _) in enumerate(opened, start=1):
                self.assertEqual(
                    socket.error_message(), "",
                    f"Terminal {index} was refused after reload {attempt}",
                )
            self.assertEqual(self.registry.terminal_count(self.token), self.cap)

        await self._close_all(opened)


class TerminalServiceCase(unittest.IsolatedAsyncioTestCase):
    """A registry whose Terminals are opened through the spawner seam.

    Reaping is made instant: these tests are about the books, and the fake
    shells die on SIGTERM anyway, so the grace period would only be waited.
    """

    async def asyncSetUp(self) -> None:
        """Start an empty registry over a fake spawner and a one-node Lab."""
        self.spawner = FakeSpawner()
        self.addCleanup(self.spawner.cleanup)
        self.service = TerminalService(spawner=self.spawner)
        self.lab = LocalLabHandle(FakeLab())
        grace = patch("mniplab.webserver.services.REAP_GRACE_PERIOD", 0)
        grace.start()
        self.addCleanup(grace.stop)

    async def _spawn(self, token: str):
        """Open one Terminal on the Lab's node for a Session.

        Args:
            token: The owning Session's token.

        Returns:
            The Terminal the registry opened.
        """
        return await self.service.spawn_terminal(self.lab, token, "h1", "learner1")

    async def _fill_host(self, count: int) -> None:
        """Open Terminals until the host holds ``count``, one Session each.

        A Session token per Terminal keeps the per-Session cap out of a test
        that is about the host-wide ceiling.

        Args:
            count: How many Terminals to open.
        """
        for i in range(count):
            await self._spawn(f"filler-token-{i}")


class HostTerminalCeilingTests(TerminalServiceCase):
    """spawn_terminal bounds the aggregate, independent of any one Session's cap.

    Before this, `services.py` capped only per client and nothing else, so on
    a host also running the emulation the total Terminal count was unbounded.
    """

    async def test_a_spawn_below_the_ceiling_succeeds(self) -> None:
        """Room to spare must cost nobody their spawn."""
        with patch.object(settings, "terminal_global_cap", 3):
            await self._fill_host(2)
            terminal = await self._spawn("token-x")

        self.assertIn(terminal.id, self.service.terminals)

    async def test_a_spawn_at_the_ceiling_is_refused(self) -> None:
        """The host limit, not a per-Session one, is what refuses this spawn."""
        with patch.object(settings, "terminal_global_cap", 3):
            await self._fill_host(3)
            with self.assertRaises(ValueError) as ctx:
                await self._spawn("token-x")

        self.assertIn("host terminal limit", str(ctx.exception))

    async def test_a_spawn_above_the_ceiling_is_refused(self) -> None:
        """A host already over a ceiling lowered mid-run still refuses.

        The comparison is ``>=``, not ``==``, so a host that somehow already
        exceeds its own cap does not let one more slip through.
        """
        await self._fill_host(5)
        with patch.object(settings, "terminal_global_cap", 3):
            with self.assertRaises(ValueError) as ctx:
                await self._spawn("token-x")

        self.assertIn("host terminal limit", str(ctx.exception))
        self.assertEqual(len(self.service.terminals), 5)

    async def test_existing_terminals_are_unaffected_by_a_refused_spawn(self) -> None:
        """A refusal must not disturb the Terminals already counted against it."""
        with patch.object(settings, "terminal_global_cap", 1):
            existing = await self._spawn("filler-token-0")
            with self.assertRaises(ValueError):
                await self._spawn("token-x")

        self.assertEqual(set(self.service.terminals), {existing.id})

    async def test_closing_a_terminal_frees_a_slot_against_the_ceiling(self) -> None:
        """The ceiling counts live Terminals, not Terminals ever spawned."""
        with patch.object(settings, "terminal_global_cap", 1):
            existing = await self._spawn("filler-token-0")
            with self.assertRaises(ValueError):
                await self._spawn("token-x")

            await self.service.close_terminal(existing.id)
            terminal = await self._spawn("token-x")

        self.assertEqual(set(self.service.terminals), {terminal.id})

    async def test_lab_terminal_shells_reap_concurrently_without_touching_another_lab(
        self,
    ) -> None:
        """Stopping a Lab pays one shell grace period for all its Terminals."""
        first = await self._spawn("token-a")
        second = await self._spawn("token-b")
        other = await self.service.spawn_terminal(
            LocalLabHandle(FakeLab(), key="other"), "token-c", "h1", "learner2"
        )
        entered = set()
        all_entered = asyncio.Event()
        release = asyncio.Event()
        real_reap = self.service._reap_child

        async def reap(child_pid: int) -> None:
            entered.add(child_pid)
            if len(entered) == 2:
                all_entered.set()
            await release.wait()
            await real_reap(child_pid)

        with patch.object(self.service, "_reap_child", side_effect=reap):
            closing = asyncio.create_task(self.service.close_lab_terminals(first.lab_key))
            try:
                await asyncio.wait_for(all_entered.wait(), timeout=1)
                self.assertEqual(entered, {first.child_pid, second.child_pid})
            finally:
                release.set()
                await asyncio.wait_for(closing, timeout=2)
        self.assertEqual(set(self.service.terminals), {other.id})
        self.assertEqual(self.service.terminal_count("token-c"), 1)

    async def test_the_per_session_cap_is_checked_before_the_host_ceiling(self) -> None:
        """A Learner already at their own cap gets the more actionable message.

        Even when the host also happens to be full, someone over their own
        budget is told that, rather than being pointed at a host-wide problem
        their own usage does not explain.
        """
        with patch.object(settings, "terminal_global_cap", 2):
            with patch.object(settings, "terminal_per_session_cap", 1):
                await self._spawn("token-x")
                await self._spawn("filler-token-0")
                with self.assertRaises(ValueError) as ctx:
                    await self._spawn("token-x")

        self.assertIn("Terminal cap exceeded", str(ctx.exception))


class SessionTerminalCleanupTests(TerminalServiceCase):
    """The registry closes one Session's Terminals and leaves others alone."""

    async def test_closes_every_terminal_the_session_owns(self) -> None:
        """Every Terminal of that Session goes, and no other Session's does."""
        await self._spawn("token-a")
        await self._spawn("token-a")
        other = await self._spawn("token-b")

        closed = await self.service.close_session_terminals("token-a")

        self.assertEqual(closed, 2)
        self.assertEqual(set(self.service.terminals), {other.id})
        self.assertEqual(self.service.session_terminals["token-b"], {other.id})

    async def test_terminal_websocket_closes_with_a_reason(self) -> None:
        """The browser is told why its Terminal ended, as idle timeout does."""
        websocket = RecordingWebSocket()
        terminal = await self._spawn("token-a")
        terminal.websocket = websocket

        await self.service.close_session_terminals("token-a")

        self.assertEqual(websocket.closes, [(1000, "Session ended")])

    async def test_client_sessions_retains_no_entry_for_the_destroyed_token(self) -> None:
        """A destroyed Session must not keep counting Terminals against itself."""
        await self._spawn("token-a")

        await self.service.close_session_terminals("token-a")

        self.assertNotIn("token-a", self.service.session_terminals)

    async def test_one_stuck_terminal_does_not_strand_the_rest(self) -> None:
        """A Terminal that refuses to close must not save the others.

        Without a per-Terminal guard the loop aborts on the first failure and
        leaks exactly the shells it exists to reclaim.  A browser that has
        already vanished is handled further in and is not this case; this is a
        Terminal that will not go down.
        """
        stuck = await self._spawn("token-a")
        other = await self._spawn("token-a")

        real_close_terminal = self.service.close_terminal

        async def refuse_first(terminal_id: str) -> None:
            """Fail to close the stuck Terminal, close anything else for real.

            Args:
                terminal_id: The Terminal being closed.

            Raises:
                OSError: When asked to close the stuck Terminal.
            """
            if terminal_id == stuck.id:
                raise OSError("terminal will not close")
            await real_close_terminal(terminal_id)

        with patch.object(self.service, "close_terminal", refuse_first):
            with self.assertLogs("mniplab.webserver.services", level="ERROR"):
                closed = await self.service.close_session_terminals("token-a")

        self.assertEqual(closed, 1)
        self.assertNotIn(other.id, self.service.terminals)
        self.assertIn(stuck.id, self.service.terminals)

    async def test_a_session_with_no_terminals_leaves_no_trace(self) -> None:
        """Closing nothing must not create the Session an entry of its own."""
        self.assertEqual(await self.service.close_session_terminals("token-a"), 0)

        self.assertNotIn("token-a", self.service.session_terminals)


class NoTerminalOutsideANodeTests(TerminalServiceCase):
    """A Terminal opens only inside a Node's own namespace.

    A Switch and an Internet Gateway live in the host's namespace, so a
    shell on either would be a root shell on the machine every Lab shares.
    The Topology never offers one; the server must refuse a hand-made ask.
    """

    async def asyncSetUp(self) -> None:
        """Add a Switch and two Internet Gateways, one hidden, beside the Lab's host."""
        await super().asyncSetUp()
        switch = FakeNode("s1")
        switch.inNamespace = False  # type: ignore[attr-defined]
        self.lab.net.switches.append(switch)
        for name, hidden in (("nat0", True), ("nat1", False)):
            gateway = FakeNode(name)
            gateway.hidden = hidden  # type: ignore[attr-defined]
            gateway.inNamespace = False  # type: ignore[attr-defined]
            self.lab.net.hosts.append(gateway)

    async def test_a_switch_is_refused_and_no_shell_starts(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            await self.service.spawn_terminal(self.lab, "token-a", "s1", "learner1")

        self.assertEqual(str(ctx.exception), "s1 is a Switch; open its Switch panel instead")
        self.assertEqual((self.spawner.children, self.service.terminals), ([], {}))

    async def test_a_hidden_internet_gateway_is_refused_and_no_shell_starts(self) -> None:
        await self._refused("nat0")

    async def test_a_drawn_internet_gateway_is_refused_the_same_way(self) -> None:
        await self._refused("nat1")

    async def _refused(self, name: str) -> None:
        """Assert a Terminal on ``name`` is refused as outside the Lab, with no shell.

        Args:
            name: The Internet Gateway asked for.
        """
        with self.assertRaises(ValueError) as ctx:
            await self.service.spawn_terminal(self.lab, "token-a", name, "learner1")

        self.assertEqual(str(ctx.exception), f"{name} is the Internet, which is outside the Lab")
        self.assertEqual((self.spawner.children, self.service.terminals), ([], {}))


if __name__ == "__main__":
    unittest.main()
