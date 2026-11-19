"""Browser tests for the Terminal pane and its compact tab-row controls.

A live server, Chromium and echoing ptys verify the pane stays beside the
Topology and its font, Clear and Close all controls remain usable.
"""

import os

import pytest

from mniplab.tests.browser import LiveServerCase
from mniplab.tests.fakes import FakeLab, FakeNode
from mniplab.tests.test_web_terminal_budget import FakeSpawner
from mniplab.webserver import registry
from mniplab.webserver.services import TerminalService

pytestmark = pytest.mark.browser_workspace


class TerminalPaneControlsTests(LiveServerCase):
    """The Terminal pane stays beside the Topology with one compact tab row."""

    def setUp(self) -> None:
        """Supply a running Lab and echoing ptys for the Terminal controls."""
        super().setUp()
        nodes = [FakeNode(f"r{i}") for i in range(1, 15)]
        for node in nodes:
            node.pid = os.getpid()
        old_lab = registry.lab_service.lab
        registry.lab_service.lab = FakeLab(*nodes, lab_key="fake")
        self.addCleanup(setattr, registry.lab_service, "lab", old_lab)
        spawner = FakeSpawner()
        self.addCleanup(spawner.cleanup)
        original = registry.terminal_service
        registry.terminal_service = TerminalService(spawner=spawner)
        self.addCleanup(setattr, registry, "terminal_service", original)

    def _open_terminals(self, page, names):
        """Open the named terminals and wait for their live xterm surfaces."""
        page.evaluate("""async (names) => {
            const { appState } = await import('/static/js/state/app-state.js');
            for (const n of names) { appState.tabManager.openTerminal(n); }
        }""", names)
        page.wait_for_selector(".xterm-helper-textarea", timeout=5000)
        page.wait_for_timeout(400)

    def test_the_terminal_pane_opens_with_the_topology_still_visible(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            self.assertTrue(page.locator("#terminal-r1").is_visible())
            self.assertTrue(page.locator(".main-content").is_visible())

    def test_the_tab_row_reads_tabs_then_the_controls_with_close_all_last(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2"])
            controls = page.get_by_role("group", name="Terminal controls")
            boxes = [
                page.get_by_role("tab", name="r2", exact=True).bounding_box(),
                controls.get_by_role("button", name="A-").bounding_box(),
                controls.get_by_role("button", name="A+").bounding_box(),
                controls.get_by_role("button", name="Clear terminal buffer").bounding_box(),
                controls.get_by_role("button", name="Close all terminals").bounding_box(),
            ]
            terminal = page.locator("#terminal-r2").bounding_box()

        lefts = [box["x"] for box in boxes]
        self.assertEqual(lefts, sorted(lefts), "tabs, A-, A+, Clear, Close all")
        tops = [round(box["y"] + box["height"] / 2) for box in boxes]
        self.assertLessEqual(max(tops) - min(tops), 6, "all on one row")
        self.assertLessEqual(max(box["y"] + box["height"] for box in boxes), terminal["y"])

    def test_no_toolbar_row_breadcrumb_or_terminals_label_remains(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            pane = page.locator("[data-pane='terminal']")

            self.assertEqual(pane.get_by_role("toolbar").count(), 0)
            self.assertEqual(pane.get_by_text("Terminals", exact=True).count(), 0)
            self.assertEqual(pane.get_by_text("r1", exact=True).count(), 1, "only the tab names it")
            first_row = page.locator(".terminal-tabs").bounding_box()
            content = page.locator(".terminal-content").bounding_box()
            # The Terminal starts directly under the one tab row.
            self.assertLessEqual(content["y"] - first_row["y"] - first_row["height"], 12)

    def test_every_tab_row_control_name_holds_its_visible_label(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            controls = page.get_by_role("group", name="Terminal controls")
            pairs = controls.get_by_role("button").evaluate_all(
                "els => els.map(e => [e.textContent.trim(), e.getAttribute('aria-label')])"
            )

        self.assertEqual(len(pairs), 4)
        for label, name in pairs:
            with self.subTest(control=name):
                self.assertIn(label.lower(), name.lower())

    def test_clear_buffer_is_an_icon_that_empties_the_scrollback(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            button = page.get_by_role("button", name="Clear terminal buffer")
            self.assertEqual(button.inner_text().strip(), "")

            def scrollback():
                return page.evaluate("""async () => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    return appState.tabManager.terminals.get('r1').term.buffer.active.baseY;
                }""")

            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.tabManager.terminals.get('r1').term.write('line\\r\\n'.repeat(200));
            }""")
            page.wait_for_function("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                return appState.tabManager.terminals.get('r1').term.buffer.active.baseY > 0;
            }""")
            self.assertGreater(scrollback(), 0)
            button.click()
            page.wait_for_function("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                return appState.tabManager.terminals.get('r1').term.buffer.active.baseY === 0;
            }""")

    def test_many_tabs_scroll_sideways_while_the_controls_stay_put(self):
        with self.signed_in_page() as page:
            names = [f"r{i}" for i in range(1, 15)]
            self._open_terminals(page, names)
            pane = page.locator("[data-pane='terminal']").bounding_box()
            close_all = page.get_by_role("button", name="Close all terminals").bounding_box()
            font = page.get_by_role("button", name="A+").bounding_box()
            scrolls = page.locator(".terminal-tabs").evaluate(
                "el => el.scrollWidth > el.clientWidth"
            )

        self.assertTrue(scrolls, "the tabs overflow sideways")
        self.assertLessEqual(close_all["x"] + close_all["width"], pane["x"] + pane["width"])
        self.assertLessEqual(font["x"] + font["width"], pane["x"] + pane["width"])
