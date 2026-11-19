"""Browser test: the Terminal toggle tracks whether a lab is running.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``:
a real uvicorn server on a real port, driven by headless Chromium over real
HTTP. No lab is ever started in this server process, so /api/status and
/api/topology answer for real — no interception needed. Showing the Terminal
pane with no lab running produces an empty, purposeless shell (a title bar, an
empty tab strip, and a "Close all terminals" button with nothing to act on), so
neither the toolbar button nor its Ctrl+T shortcut should do anything until a
lab is running.
"""

import unittest


import pytest

from mniplab.tests.browser import LiveServerCase

pytestmark = pytest.mark.browser_workspace

TERMINAL_PANE = "[data-pane='terminal']"


class TerminalToggleTests(LiveServerCase):
    """Drives the Terminal toggle against a server with no lab running."""

    def test_toggle_is_disabled_and_shortcut_is_inert_with_no_lab_running(self):
        with self.signed_in_page() as page:
            self.assertTrue(
                page.is_disabled("#toggle-terminal-pane"),
                "the Terminal toggle should be disabled when no lab is running",
            )

            page.keyboard.press("Control+t")
            page.wait_for_timeout(100)
            self.assertEqual(
                page.get_attribute(TERMINAL_PANE, "data-pane-shown"), "false",
                "Ctrl+T should not show the Terminal pane when no lab is running",
            )

    def test_the_toggle_and_its_shortcut_are_named_for_the_terminal_pane(self):
        with self.signed_in_page() as page:
            toggle = page.locator("#toggle-terminal-pane")
            name = toggle.get_attribute("aria-label")
            title = toggle.get_attribute("title")
            shortcuts = page.locator("#help-modal .shortcut-item").evaluate_all(
                "items => items.map(i => i.textContent.replace(/\\s+/g, ' ').trim())"
            )

        self.assertEqual(name, "Show or hide the Terminal pane (Ctrl+T)")
        self.assertEqual(title, name)
        self.assertIn("Ctrl + T Show or hide the Terminal pane", shortcuts)

    def test_toggle_becomes_usable_when_a_lab_starts_after_page_load(self):
        with self.signed_in_page() as page:
            self.assertTrue(page.is_disabled("#toggle-terminal-pane"))

            # A lab starts after page load — the same status transition a
            # cross-session WebSocket push delivers via updateStatusFromData.
            page.evaluate("""async () => {
                const { updateStatusFromData } = await import('/static/js/lab/lifecycle.js');
                await updateStatusFromData({
                    running: true, example: 'test-lab', nodes: 2, active_terminals: 0,
                });
            }""")

            self.assertFalse(
                page.is_disabled("#toggle-terminal-pane"),
                "the Terminal toggle should become usable once a lab is running",
            )

            page.click("#toggle-terminal-pane")
            self.assertEqual(
                page.get_attribute(TERMINAL_PANE, "data-pane-shown"), "false",
            )


if __name__ == "__main__":
    unittest.main()
