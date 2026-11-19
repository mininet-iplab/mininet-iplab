"""Tests for a Terminal outliving its page and reattaching to it.

A reload, a closed tab or a dropped network closes the browser connection
without closing the Terminal on purpose.  The shell must keep running for
the grace window, its output kept, so the reloaded page can name the
Terminal and get it back with what it printed in the meantime.

Driven at the seam the Terminal budget tests use: the real WebSocket handler
against the real Terminal service, with a real pty(7) behind each Terminal
and only the spawner and the browser socket faked.
"""

import asyncio
import json
import os
import types
import unittest
from typing import Any, List, Optional
from unittest.mock import patch

from fastapi import WebSocketDisconnect

from mniplab.labhandle import LocalLabHandle
from mniplab.tests.test_web_terminal_budget import FakeLab, FakeSpawner
from mniplab.webserver.auth import (
    create_session, destroy_session, ws_attempt_limiter, ws_limiter,
)
from mniplab.webserver.config import settings
from mniplab.webserver.services import TerminalService
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.websocket import TerminalWebSocketHandler


class BrowserTab:
    """A browser tab's Terminal connection: it can type, close on purpose, or drop.

    Attributes:
        cookies: Cookies the tab presents, notably the session cookie.
        sent: Every payload the server sent, in order.
        closes: Code and reason of each close the server made.
    """

    def __init__(self, token: Optional[str]) -> None:
        """Initialize a tab presenting one Session.

        Args:
            token: The Session token in the tab's cookie, if any.
        """
        self.cookies = {"session": token} if token else {}
        self.sent: List[Any] = []
        self.closes: List[tuple[int, str]] = []
        self._inbox: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> None:
        """Accept the connection."""

    async def receive_text(self) -> str:
        """Hand the server the tab's next message.

        Raises:
            WebSocketDisconnect: Once the tab has gone.
        """
        message = await self._inbox.get()
        if message is None:
            raise WebSocketDisconnect(code=1001)
        return message

    async def send_json(self, payload: Any) -> None:
        """Record a payload the server sent.

        Raises:
            RuntimeError: If the connection is already closed, as Starlette does.
        """
        if self.closes:
            raise RuntimeError("Cannot call send once a close message has been sent")
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Record that the server closed the connection."""
        self.closes.append((code, reason))
        self._inbox.put_nowait(None)

    def say(self, payload: dict) -> None:
        """Send the server one message from the page."""
        self._inbox.put_nowait(json.dumps(payload))

    def drop(self) -> None:
        """Go away without a word, as a reloading page does."""
        self._inbox.put_nowait(None)

    def screen(self) -> str:
        """Everything the tab was shown, replay included."""
        text = []
        for payload in self.sent:
            if payload.get("type") == "output":
                text.append(payload["data"])
            elif payload.get("type") == "attached":
                text.append(payload.get("replay", ""))
        return "".join(text)

    def attached(self) -> Optional[dict]:
        """The server's word that the Terminal is attached, if it was sent."""
        return next((p for p in self.sent if p.get("type") == "attached"), None)

    def ended(self) -> Optional[dict]:
        """The server's word that the named Terminal has ended, if it was sent."""
        return next((p for p in self.sent if p.get("type") == "ended"), None)


class TerminalReattachTests(unittest.IsolatedAsyncioTestCase):
    """A Terminal whose page goes away waits for it, and comes back to it."""

    async def asyncSetUp(self) -> None:
        """Install a registry over real ptys, with instant reaping."""
        self.registry = SessionRegistry()
        self.spawner = FakeSpawner()
        self.registry.terminal_service = TerminalService(spawner=self.spawner)
        self.registry.lab_service.lab = FakeLab()
        self.key = str(self.registry.lab_key)
        for target, value in (
            ("mniplab.webserver.websocket.registry", self.registry),
            ("mniplab.webserver.services.REAP_GRACE_PERIOD", 0),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.spawner.cleanup)
        ws_limiter.attempts.clear()
        ws_attempt_limiter.attempts.clear()
        self.token = create_session("bob", "learner")
        self.handlers: List[asyncio.Task] = []
        self.addAsyncCleanup(self._end_everything)

    async def _end_everything(self) -> None:
        """Close what a test left open, so no reader outlives it."""
        await self.registry.terminal_service.close_session_terminals(self.token)
        for task in self.handlers:
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)

    def _connect(
        self, token: Optional[str] = None, node: str = "h1",
        terminal: Optional[str] = None, key: Optional[str] = None,
    ) -> BrowserTab:
        """Open a Terminal connection, naming a Terminal to reattach if given."""
        tab = BrowserTab(token or self.token)
        handler = TerminalWebSocketHandler(
            tab, key or self.key, node, terminal_id=terminal  # type: ignore[arg-type]
        )
        self.handlers.append(asyncio.create_task(handler.run()))
        return tab

    async def _until(self, condition, timeout: float = 3.0) -> None:
        """Wait until a condition holds, failing the test if it never does."""
        async def poll() -> None:
            while not condition():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), timeout)

    async def _open(self) -> tuple[BrowserTab, str]:
        """Open one Terminal and return the tab with the Terminal's id."""
        tab = self._connect()
        await self._until(lambda: tab.attached() is not None)
        attached = tab.attached()
        assert attached is not None
        return tab, attached["id"]

    def _shell_prints(self, text: str, index: int = 0) -> None:
        """Make the shell behind the index-th Terminal print a line."""
        os.write(self.spawner.slaves[index], text.encode())

    def _shell_alive(self, index: int = 0) -> bool:
        """Whether the shell behind the index-th Terminal is still running."""
        return self.spawner.children[index].poll() is None

    async def _drop(self, tab: BrowserTab) -> None:
        """Drop a tab and let the server notice."""
        tab.drop()
        await asyncio.sleep(0.1)

    async def test_a_dropped_page_leaves_the_shell_running(self) -> None:
        """A reload is not a close: the shell and its place stay."""
        tab, _ = await self._open()

        await self._drop(tab)

        self.assertTrue(self._shell_alive(), "the shell was ended by a reload")
        self.assertEqual(self.registry.terminal_count(self.token), 1)

    async def test_reattaching_replays_what_the_shell_printed_while_away(self) -> None:
        """The reloaded page sees the earlier output and what came during the reload."""
        tab, terminal_id = await self._open()
        self._shell_prints("before the reload\n")
        await self._until(lambda: "before the reload" in tab.screen())
        await self._drop(tab)
        self._shell_prints("during the reload\n")
        await asyncio.sleep(0.1)

        again = self._connect(terminal=terminal_id)
        await self._until(lambda: again.attached() is not None)

        attached = again.attached()
        assert attached is not None
        self.assertEqual(attached["id"], terminal_id)
        self.assertTrue(attached["reattached"])
        self.assertIn("before the reload", again.screen())
        self.assertIn("during the reload", again.screen())
        self.assertEqual(len(self.spawner.children), 1, "a reattach forked a shell")

    async def test_live_output_follows_the_replay(self) -> None:
        """After the replay, what the shell prints reaches the reattached page."""
        tab, terminal_id = await self._open()
        await self._drop(tab)
        again = self._connect(terminal=terminal_id)
        await self._until(lambda: again.attached() is not None)

        self._shell_prints("after the reload\n")

        await self._until(lambda: "after the reload" in again.screen())

    async def test_typing_reaches_the_shell_after_a_reattach(self) -> None:
        """Input from the reattached page is written to the same pty."""
        tab, terminal_id = await self._open()
        await self._drop(tab)
        again = self._connect(terminal=terminal_id)
        await self._until(lambda: again.attached() is not None)

        again.say({"type": "input", "data": "typed again\n"})

        await self._until(
            lambda: b"typed again" in _read_available(self.spawner.slaves[0])
        )

    async def test_a_terminal_not_reattached_in_time_ends(self) -> None:
        """The grace window is the Terminal idle timeout; after it the shell goes."""
        with patch.object(settings, "terminal_idle_timeout", 0.2):
            tab, terminal_id = await self._open()
            await self._drop(tab)
            await self._until(lambda: not self._shell_alive())

            again = self._connect(terminal=terminal_id)
            await self._until(lambda: again.closes != [])

        self.assertEqual(self.registry.terminal_count(self.token), 0)
        self.assertIsNone(again.attached())
        ended = again.ended()
        assert ended is not None
        self.assertIn("away", ended["reason"])
        self.assertNotEqual(again.closes[0][0], 1008, "an ended Terminal was refused")
        self.assertEqual(len(self.spawner.children), 1, "an ended reattach forked a shell")

    async def test_the_labs_feed_hears_when_a_detached_terminal_expires(self) -> None:
        """The Instructor's view must not keep showing a Terminal that has ended."""
        watcher = BrowserTab(self.token)
        await self.registry.add_status_subscriber(watcher, "bob", "learner")  # type: ignore[arg-type]
        with patch.object(settings, "terminal_idle_timeout", 0.2):
            tab, _ = await self._open()
            await self._drop(tab)
            await self._until(lambda: not self._shell_alive())
            await self._until(
                lambda: watcher.sent
                and watcher.sent[-1]["data"].get("active_terminals") == 0
            )

    async def test_reattaching_in_time_keeps_the_terminal_past_the_grace_window(self) -> None:
        """A reattach stops the grace timer, so it never ends a Terminal in use."""
        with patch.object(settings, "terminal_idle_timeout", 0.3):
            tab, terminal_id = await self._open()
            await self._drop(tab)
            again = self._connect(terminal=terminal_id)
            await self._until(lambda: again.attached() is not None)
            await asyncio.sleep(0.5)

        self.assertTrue(self._shell_alive())
        self.assertEqual(again.closes, [])

    async def test_closing_on_purpose_ends_the_shell_at_once(self) -> None:
        """The close control says so, and the Terminal does not wait for a reload."""
        tab, terminal_id = await self._open()

        tab.say({"type": "close"})
        await self._until(lambda: not self._shell_alive())

        self.assertEqual(self.registry.terminal_count(self.token), 0)
        again = self._connect(terminal=terminal_id)
        await self._until(lambda: again.ended() is not None)
        self.assertIsNone(again.attached())

    async def test_only_the_same_session_lab_and_node_may_reattach(self) -> None:
        """Another Session, Lab or Node naming the Terminal is told it ended."""
        tab, terminal_id = await self._open()
        await self._drop(tab)
        other_lab = self.registry.lab_service._attach(
            LocalLabHandle(FakeLab(), key="other"), group=None
        )
        attempts = {
            "another Session": self._connect(
                token=create_session("eve", "learner"), terminal=terminal_id
            ),
            "another Lab": self._connect(key=other_lab.key, terminal=terminal_id),
            "another Node": self._connect(node="h2", terminal=terminal_id),
        }
        for what, attempt in attempts.items():
            await self._until(lambda: attempt.closes != [])
            self.assertIsNone(attempt.attached(), f"{what} took the Terminal")
            self.assertIsNotNone(attempt.ended(), f"{what} was not told it ended")
            self.assertNotEqual(attempt.closes[0][0], 1008)

        self.assertEqual(len(self.spawner.children), 1, "a refused reattach forked a shell")
        again = self._connect(terminal=terminal_id)
        await self._until(lambda: again.attached() is not None)

    async def test_a_reattach_is_charged_to_the_reconnect_budget(self) -> None:
        """A reattach costs one connection, as a reopen did."""
        tab, terminal_id = await self._open()
        await self._drop(tab)
        spent = len(ws_limiter.attempts[self.token])

        again = self._connect(terminal=terminal_id)
        await self._until(lambda: again.attached() is not None)

        self.assertEqual(len(ws_limiter.attempts[self.token]), spent + 1)

    async def test_logging_out_ends_detached_terminals(self) -> None:
        """A Session's end reaches the Terminals waiting for a reload too."""
        tab, _ = await self._open()
        await self._drop(tab)

        await self.registry.close_session_terminals(self.token)

        await self._until(lambda: not self._shell_alive())
        self.assertEqual(self.registry.terminal_count(self.token), 0)

    async def test_stopping_the_lab_ends_detached_terminals(self) -> None:
        """No shell outlives its Lab, attached or not."""
        tab, _ = await self._open()
        await self._drop(tab)

        await self.registry.terminal_service.close_lab_terminals(self.key)

        await self._until(lambda: not self._shell_alive())
        self.assertEqual(self.registry.terminal_count(self.token), 0)

    @staticmethod
    def _grace_timers_pending() -> int:
        """How many detached Terminals are still waiting out a grace window."""
        pending = 0
        for task in asyncio.all_tasks():
            coro = task.get_coro()
            if (not task.done() and isinstance(coro, types.CoroutineType)
                    and coro.__qualname__.endswith("_end_unless_reattached")):
                pending += 1
        return pending

    async def test_destroying_the_session_ends_detached_terminals_at_once(self) -> None:
        """Logout, and the Instructor ending a Session, share this path."""
        tab, _ = await self._open()
        await self._drop(tab)
        self.assertEqual(self._grace_timers_pending(), 1)

        with patch("mniplab.webserver.auth._terminal_closer",
                   self.registry.close_session_terminals):
            self.assertTrue(await destroy_session(self.token))

        await self._until(lambda: not self._shell_alive(), timeout=1.0)
        self.assertEqual(self.registry.terminal_count(self.token), 0)
        await asyncio.sleep(0)
        self.assertEqual(self._grace_timers_pending(), 0, "a grace timer outlived its Terminal")

    async def test_stopping_or_switching_the_lab_ends_detached_terminals_at_once(self) -> None:
        """A Lab switch stops the current Lab through the same stop."""
        self.registry.lab_service.lab = StoppableLab()
        self.key = str(self.registry.lab_key)
        tab, _ = await self._open()
        await self._drop(tab)
        self.assertEqual(self._grace_timers_pending(), 1)

        result = await self.registry.stop_lab(self.key)

        self.assertEqual(result.get("status"), "stopped")

        await self._until(lambda: not self._shell_alive(), timeout=1.0)
        self.assertEqual(self.registry.terminal_count(self.token), 0)
        await asyncio.sleep(0)
        self.assertEqual(self._grace_timers_pending(), 0, "a grace timer outlived its Terminal")

    async def _fill_the_cap(self) -> List[tuple[BrowserTab, str]]:
        """Open Terminals until the Session is at its cap."""
        return [await self._open() for _ in range(settings.terminal_per_session_cap)]

    async def test_at_the_cap_a_new_terminal_ends_the_oldest_detached_one(self) -> None:
        """A tab closed for good must not make a new tab wait for its shells."""
        opened = await self._fill_the_cap()
        await self._drop(opened[3][0])
        await self._drop(opened[1][0])

        newcomer = self._connect()
        await self._until(lambda: newcomer.attached() is not None or newcomer.closes != [])

        self.assertIsNotNone(newcomer.attached(), "a Session at its cap with a detached "
                             "Terminal was refused a new one")
        await self._until(lambda: not self._shell_alive(3))
        self.assertTrue(self._shell_alive(1), "a more recently detached Terminal was ended")
        for index in (0, 2, 4):
            self.assertTrue(self._shell_alive(index), "an attached Terminal was ended")
        self.assertEqual(
            self.registry.terminal_count(self.token), settings.terminal_per_session_cap
        )
        late = self._connect(terminal=opened[3][1])
        await self._until(lambda: late.ended() is not None)

    async def test_at_the_cap_with_only_attached_terminals_a_new_one_is_refused(self) -> None:
        """Attached Terminals are never ended to make room."""
        await self._fill_the_cap()

        refused = self._connect()
        await self._until(lambda: refused.closes != [])

        self.assertIsNone(refused.attached())
        self.assertEqual(refused.closes[0][0], 1008)
        for index in range(settings.terminal_per_session_cap):
            self.assertTrue(self._shell_alive(index), "an attached Terminal was ended")

    async def test_the_host_ceiling_counts_detached_terminals(self) -> None:
        """Reloads cannot pile shells on the host, and no Session gives up another's."""
        eve = create_session("eve", "learner")
        with patch.object(settings, "terminal_global_cap", 2):
            tab, _ = await self._open()
            await self._drop(tab)
            first = self._connect(token=eve)
            await self._until(lambda: first.attached() is not None)

            refused = self._connect(token=eve)
            await self._until(lambda: refused.closes != [])

        self.assertIsNone(refused.attached())
        self.assertIn("host terminal limit", refused.sent[0]["message"])
        self.assertTrue(self._shell_alive(0), "another Session's detached Terminal was ended")
        await self.registry.terminal_service.close_session_terminals(eve)

    async def test_a_duplicated_tab_takes_the_terminal_and_the_older_is_told(self) -> None:
        """Two tabs never type into one shell: the newer has it, the older knows why."""
        older, terminal_id = await self._open()
        self._shell_prints("before the copy\n")
        await self._until(lambda: "before the copy" in older.screen())

        newer = self._connect(terminal=terminal_id)
        await self._until(lambda: newer.attached() is not None)
        await self._until(lambda: older.closes != [])

        self.assertIn("before the copy", newer.screen())
        moved = next((p for p in older.sent if p.get("type") == "moved"), None)
        self.assertIsNotNone(moved, "the older tab was not told the Terminal moved")
        assert moved is not None
        self.assertIn("another tab", moved["reason"])
        code = older.closes[0][0]
        self.assertNotIn(code, (1000, 1008), "the takeover close looks like an ending or "
                         "a refusal, so the older tab would reconnect or misreport it")

        await asyncio.sleep(0.1)
        self._shell_prints("after the copy\n")
        await self._until(lambda: "after the copy" in newer.screen())
        self.assertNotIn("after the copy", older.screen())
        self.assertTrue(self._shell_alive())
        self.assertEqual(newer.closes, [])
        self.assertEqual(len(self.spawner.children), 1, "a takeover forked a shell")

    async def test_two_tabs_racing_to_reattach_leave_one_holding_it(self) -> None:
        """Whichever the lock lets in second has it; the other is told it moved."""
        tab, terminal_id = await self._open()
        await self._drop(tab)

        racers = [self._connect(terminal=terminal_id), self._connect(terminal=terminal_id)]
        await self._until(lambda: any(r.closes for r in racers))
        await asyncio.sleep(0.1)

        holding = [r for r in racers if not r.closes]
        moved = [r for r in racers if any(p.get("type") == "moved" for p in r.sent)]
        self.assertEqual(len(holding), 1, "not exactly one tab holds the Terminal")
        self.assertEqual(len(moved), 1, "the other tab was not told it moved")
        self.assertIsNot(holding[0], moved[0])
        self._shell_prints("after the race\n")
        await self._until(lambda: "after the race" in holding[0].screen())

    def _row(self, terminal_id: str) -> dict:
        """The Instructor's list row for one Terminal."""
        return next(
            row for row in self.registry.terminal_service.get_terminals()
            if row["id"] == terminal_id
        )

    async def test_the_terminal_list_says_attached_or_detached_with_time_left(self) -> None:
        """A detached Terminal shows the seconds left in its grace window; attached, none."""
        with patch.object(settings, "terminal_idle_timeout", 60):
            tab, terminal_id = await self._open()
            self.assertEqual(
                (self._row(terminal_id)["attached"], self._row(terminal_id)["grace_left"]),
                (True, None),
            )

            await self._drop(tab)
            detached = self._row(terminal_id)
            self.assertFalse(detached["attached"])
            self.assertEqual(detached["grace_left"], 60)

            again = self._connect(terminal=terminal_id)
            await self._until(lambda: again.attached() is not None)
            self.assertEqual(
                (self._row(terminal_id)["attached"], self._row(terminal_id)["grace_left"]),
                (True, None),
            )

    async def test_the_seconds_left_fall_as_the_grace_window_runs_out(self) -> None:
        """The time left is worked out from the grace window, not from a fixed number."""
        with patch.object(settings, "terminal_idle_timeout", 3):
            tab, terminal_id = await self._open()
            await self._drop(tab)
            await asyncio.sleep(1.2)

            left = self._row(terminal_id)["grace_left"]
            self.assertIsInstance(left, int)
            self.assertLessEqual(left, 2)
            self.assertGreaterEqual(left, 1)


class StoppableLab(FakeLab):
    """A fake Lab the server can stop, as it does on Stop and on a Lab switch."""

    def stop(self) -> None:
        """Stop the Lab; nothing to tear down."""


def _read_available(fd: int) -> bytes:
    """Read whatever is waiting on a pty end without blocking."""
    os.set_blocking(fd, False)
    try:
        return os.read(fd, 4096)
    except BlockingIOError:
        return b""
    finally:
        os.set_blocking(fd, True)


if __name__ == "__main__":
    unittest.main()
