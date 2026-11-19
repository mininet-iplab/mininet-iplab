"""Terminals come back after a reload, in a real browser.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn
server and headless Chromium, with a fake running Lab whose Terminals are
real ptys.  What the shell prints is written to the pty's far end; what the
page shows is read from the Terminal on screen.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import re
import struct
import termios
from unittest.mock import patch

import pytest

from mniplab.tests.browser import BrowserContext, LiveServerCase, Page, expect, choose_account_item
from mniplab.tests.test_web_capture import two_node_lab
from mniplab.tests.test_web_pane_arrangement import LAPTOP, wait_until
from mniplab.tests.test_web_terminal_budget import FakeSpawner
from mniplab.webserver import registry
from mniplab.webserver.auth import active_sessions, create_session
from mniplab.webserver.config import settings
from mniplab.webserver.services import TerminalService

pytestmark = pytest.mark.browser_workspace


class TerminalReattachBrowserTests(LiveServerCase):
    """A reload, a closed Terminal, an expired one and a fresh tab."""

    def setUp(self) -> None:
        """A running two-Node Lab whose Terminals are real ptys."""
        super().setUp()
        lab = two_node_lab()
        for node in lab.hosts:
            node.pid = os.getpid()
        old_lab = registry.lab_service.lab
        registry.lab_service.lab = lab
        registry.lab_service.lab_example = "static-lab"
        self.addCleanup(setattr, registry.lab_service, "lab", old_lab)

        self.spawner = FakeSpawner()
        self.addCleanup(self.spawner.cleanup)
        terminals = registry.terminal_service
        self.service = TerminalService(spawner=self.spawner)
        registry.terminal_service = self.service
        self.addCleanup(setattr, registry, "terminal_service", terminals)
        self.addCleanup(self._close_terminals)

    def _close_terminals(self) -> None:
        """End what a test left, on the server's loop, before its fds are reused."""
        for terminal in list(self.service.terminals.values()):
            if terminal.read_task is None:
                continue
            asyncio.run_coroutine_threadsafe(
                self.service.close_terminal(terminal.id), terminal.read_task.get_loop()
            ).result(timeout=10)

    def running_page(self, page: Page) -> None:
        """Wait for the running Lab's Topology."""
        page.set_viewport_size(LAPTOP)
        page.wait_for_selector("g.node")

    def open_terminal(self, page: Page, name: str) -> None:
        """Open a Node's Terminal and wait until the server has attached it."""
        page.evaluate("""async (name) => {
            const { appState } = await import('/static/js/state/app-state.js');
            appState.tabManager.openTerminal(name);
        }""", name)
        self.wait_for_screen(page, name, f"Connected to {name}")

    def tabs(self, page: Page) -> list[str]:
        """The Terminal tabs, in the order shown."""
        return page.eval_on_selector_all(
            ".terminal-tab", "tabs => tabs.map(t => t.dataset.node)"
        )

    def selected(self, page: Page) -> str | None:
        """The selected Terminal tab's Node."""
        return page.get_attribute(".terminal-tab[aria-selected='true']", "data-node")

    def wait_for_screen(self, page: Page, name: str, text: str) -> None:
        """Wait until the Node's Terminal shows the text."""
        page.wait_for_function(
            """([name, text]) => {
                const rows = document.querySelector(`#terminal-${name} .xterm-rows`);
                return !!rows && rows.textContent.includes(text);
            }""",
            arg=[name, text],
            timeout=10000,
        )

    def shell_prints(self, index: int, text: str) -> None:
        """Make the shell behind the index-th Terminal print a line."""
        os.write(self.spawner.slaves[index], text.encode())

    def pty_size(self, index: int) -> tuple[int, int]:
        """The window size a shell on the pty would read: (cols, rows)."""
        packed = fcntl.ioctl(self.spawner.slaves[index], termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, _, _ = struct.unpack("HHHH", packed)
        return cols, rows

    def terminal_size(self, page: Page, name: str) -> tuple[int, int]:
        """The columns and rows the Terminal shows."""
        return tuple(page.evaluate("""async (name) => {
            const { appState } = await import('/static/js/state/app-state.js');
            const term = appState.tabManager.terminals.get(name).term;
            return [term.cols, term.rows];
        }""", name))

    def all_detached(self) -> bool:
        """Whether no Terminal has a page attached."""
        return all(t.websocket is None for t in self.service.terminals.values())

    def leave_and_return(self, page: Page, while_away=None) -> None:
        """Navigate away until the server has detached, then come back."""
        page.goto("about:blank")
        wait_until(self.all_detached)
        if while_away is not None:
            while_away()
        page.goto(self.base_url + "/")
        self.running_page(page)

    def test_a_reload_brings_the_terminals_back_in_order_with_their_output(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")
            self.open_terminal(page, "h1")
            page.click("#tab-r1")
            self.shell_prints(0, "ping-before-the-reload\r\n")
            self.wait_for_screen(page, "r1", "ping-before-the-reload")

            page.reload()
            self.running_page(page)

            self.wait_for_screen(page, "r1", "Reattached to the running shell")
            self.wait_for_screen(page, "r1", "ping-before-the-reload")
            self.wait_for_screen(page, "h1", "Reattached to the running shell")
            self.assertEqual(self.tabs(page), ["r1", "h1"])
            self.assertEqual(self.selected(page), "r1")
            self.assertEqual(len(self.spawner.children), 2, "a reload opened new shells")

            page.locator("#terminal-r1 .xterm-helper-textarea").focus()
            page.keyboard.type("typed-after-reload")
            self.wait_for_screen(page, "r1", "typed-after-reload")
            wait_until(lambda: self.pty_size(0) == self.terminal_size(page, "r1"))

    def test_output_printed_while_the_page_was_away_is_shown(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")

            self.leave_and_return(page, lambda: self.shell_prints(0, "64-bytes-while-away\r\n"))

            self.wait_for_screen(page, "r1", "64-bytes-while-away")
            self.shell_prints(0, "64-bytes-after\r\n")
            self.wait_for_screen(page, "r1", "64-bytes-after")

    def test_a_terminal_closed_on_purpose_does_not_come_back(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")
            self.open_terminal(page, "h1")

            page.click("button[aria-label='Close r1 terminal']")
            wait_until(lambda: self.spawner.children[0].poll() is not None)
            page.reload()
            self.running_page(page)

            self.wait_for_screen(page, "h1", "Reattached to the running shell")
            self.assertEqual(self.tabs(page), ["h1"])
            self.assertEqual(len(self.service.terminals), 1)

    def test_a_terminal_that_ended_while_away_says_so_and_a_key_opens_a_new_one(self) -> None:
        with patch.object(settings, "terminal_idle_timeout", 0.5):
            with self.signed_in_page() as page:
                self.running_page(page)
                self.open_terminal(page, "r1")

                self.leave_and_return(
                    page, lambda: wait_until(lambda: not self.service.terminals)
                )

                self.wait_for_screen(page, "r1", "after the page was away")
                self.wait_for_screen(page, "r1", "Press any key")
                page.locator("#terminal-r1 .xterm-helper-textarea").focus()
                page.keyboard.press("Enter")
                wait_until(lambda: len(self.spawner.children) == 2)
                expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text(
                    "Connected to r1"
                )

    def test_a_fresh_browser_tab_does_not_take_another_tabs_terminals(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")

            fresh = page.context.new_page()
            fresh.goto(self.base_url + "/")
            self.running_page(fresh)
            fresh.wait_for_timeout(1000)

            self.assertEqual(self.tabs(fresh), [])
            self.assertFalse(self.all_detached(), "the first tab lost its Terminal")

    def test_a_duplicated_tab_takes_the_terminals_and_the_first_is_told(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")
            # A browser's Duplicate Tab copies sessionStorage; do the same
            # before the second page's scripts run.
            stored = page.evaluate("() => ({ ...sessionStorage })")
            duplicate = page.context.new_page()
            duplicate.add_init_script(
                f"for (const [k, v] of Object.entries({json.dumps(stored)})) "
                "sessionStorage.setItem(k, v);"
            )
            duplicate.goto(self.base_url + "/")
            self.running_page(duplicate)

            self.wait_for_screen(duplicate, "r1", "Reattached to the running shell")
            self.wait_for_screen(page, "r1", "moved to another tab")
            page.wait_for_timeout(1500)
            self.wait_for_screen(page, "r1", "Click here")
            self.assertEqual(len(self.spawner.children), 1, "a takeover opened a new shell")
            self.shell_prints(0, "still-in-the-duplicate\r\n")
            self.wait_for_screen(duplicate, "r1", "still-in-the-duplicate")

            page.reload()
            self.running_page(page)
            page.wait_for_timeout(1000)

            self.assertEqual(self.tabs(page), [], "the first tab took the Terminal back")
            self.shell_prints(0, "the-duplicate-kept-it\r\n")
            self.wait_for_screen(duplicate, "r1", "the-duplicate-kept-it")

    def test_reset_browser_preferences_brings_the_terminals_back(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")

            page.get_by_role("button", name="Help", exact=True).click()
            page.get_by_role("button", name="Reset browser preferences…", exact=True).click()
            with page.expect_navigation(wait_until="domcontentloaded"):
                page.get_by_role("button", name="Reset and reload", exact=True).click()
            self.running_page(page)

            self.wait_for_screen(page, "r1", "Reattached to the running shell")
            self.assertEqual(self.tabs(page), ["r1"])
            self.assertEqual(len(self.spawner.children), 1, "the reset opened a new shell")

    def drop_connection(self, page: Page, name: str) -> None:
        """Drop a Terminal's socket as a network outage would, keeping its tab."""
        page.evaluate("""async (name) => {
            const { appState } = await import('/static/js/state/app-state.js');
            appState.tabManager.terminals.get(name).ws.close();
        }""", name)

    def test_a_dropped_connection_reattaches_by_itself(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")
            self.open_terminal(page, "h1")

            self.drop_connection(page, "r1")
            self.drop_connection(page, "h1")

            self.wait_for_screen(page, "r1", "Reattached to the running shell")
            self.wait_for_screen(page, "h1", "Reattached to the running shell")
            self.assertNotIn("Press any key", page.inner_text("#terminal-r1 .xterm-rows"))
            self.assertEqual(len(self.spawner.children), 2, "a drop opened new shells")
            self.shell_prints(0, "after-the-drop\r\n")
            self.wait_for_screen(page, "r1", "after-the-drop")

    def test_closing_the_tab_of_a_dropped_terminal_ends_its_shell_at_once(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")

            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                const manager = appState.tabManager;
                manager.terminals.get('r1').ws.close();
                manager.closeTab('r1');
            }""")

            wait_until(lambda: self.spawner.children[0].poll() is not None)
            self.assertEqual(len(self.service.terminals), 0)

    def remembered_terminal_keys(self, page: Page) -> list[str]:
        """The sessionStorage keys that remember this tab's Terminals."""
        return page.evaluate(
            "() => Object.keys(sessionStorage).filter(k => k.startsWith('mniplab-terminals:'))"
        )

    def test_a_lab_stopped_elsewhere_leaves_no_terminals_to_come_back(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")
            self.assertTrue(self.remembered_terminal_keys(page))

            page.evaluate("""async () => {
                const { updateStatusFromData } = await import('/static/js/lab/lifecycle.js');
                await updateStatusFromData({ running: false });
            }""")

            self.assertEqual(self.remembered_terminal_keys(page), [])

    def test_logging_out_forgets_the_terminals(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page, "r1")
            self.assertTrue(self.remembered_terminal_keys(page))

            with page.expect_navigation(wait_until="domcontentloaded"):
                choose_account_item(page, "Log out")

            self.assertEqual(self.remembered_terminal_keys(page), [])

    def learner_page(self) -> tuple[str, BrowserContext, Page]:
        """A second browser, in its own context, signed in as a Learner."""
        token = create_session("learner-bob", "learner")
        assert self.browser is not None
        context = self.browser.new_context()
        context.add_cookies([{"name": "session", "value": token, "url": self.base_url}])
        page = context.new_page()
        page.goto(self.base_url + "/")
        self.running_page(page)
        return token, context, page

    def test_the_instructor_view_shows_a_terminal_detached_and_attached_again(self) -> None:
        with patch.object(settings, "terminal_idle_timeout", 60):
            with self.signed_in_page() as instructor:
                self.running_page(instructor)
                token, context, learner = self.learner_page()
                try:
                    self.open_terminal(learner, "r1")
                    code = active_sessions[token]["connection_code"]
                    choose_account_item(instructor, "Sessions & Terminals", "menuitemcheckbox")
                    instructor.get_by_label("Find a Session", exact=True).fill(code)
                    instructor.locator("#instructor-connection-list summary").click()
                    row = instructor.locator("#instructor-connection-list .terminal-row", has_text="r1")
                    expect(row).to_contain_text("Attached", timeout=15000)

                    learner.goto("about:blank")
                    wait_until(self.all_detached)
                    expect(row).to_contain_text(re.compile(r"Detached, \d+ s left"), timeout=15000)

                    learner.goto(self.base_url + "/")
                    self.running_page(learner)
                    self.wait_for_screen(learner, "r1", "Reattached to the running shell")
                    expect(row).to_contain_text("Attached", timeout=15000)
                finally:
                    context.close()

    def test_the_lab_prompt_tab_comes_back_after_a_reload(self) -> None:
        master, slave = pty.openpty()
        self.addCleanup(os.close, slave)
        lab_key = str(registry.lab_key)
        registry.prompt(lab_key).attach(master)
        self.addCleanup(registry.prompt(lab_key).detach)
        self.addCleanup(os.close, master)
        with self.signed_in_page() as page:
            self.running_page(page)
            page.get_by_role("button", name="Open Lab Prompt", exact=True).click()
            expect(page.locator("#tab-lab-prompt")).to_have_attribute("aria-selected", "true")
            os.write(slave, b"lab-prompt-line\r\n")
            self.wait_for_screen(page, "lab-prompt", "lab-prompt-line")

            page.reload()
            self.running_page(page)

            expect(page.locator("#tab-lab-prompt")).to_have_attribute("aria-selected", "true")
            self.wait_for_screen(page, "lab-prompt", "lab-prompt-line")
