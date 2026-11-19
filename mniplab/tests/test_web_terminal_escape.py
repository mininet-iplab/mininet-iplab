"""Browser tests for the Terminal pane Escape / Ctrl+W focus guard.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``:
a real uvicorn server on a real port, driven by headless Chromium over real HTTP.

The regression under test: pressing Escape or Ctrl+W while focus is on
the Terminal pane's own chrome — specifically a terminal's tab button, one ordinary
Tab press away from the live session — used to destroy terminals with zero
confirmation. The corrected behavior:

* Escape / Ctrl+W on a terminal tab button asks for confirmation before any
  terminal is destroyed (no silent close, no silent close-all).
* Escape / Ctrl+W inside the live xterm session close nothing — xterm.js stops the
  event before it reaches the page-level handler. This is the case a future
  refactor is most likely to break by "simplifying" the focus check, so it is
  asserted explicitly.
* Ctrl+B / Ctrl+T (sidebar / Terminal pane toggle) are focus-aware: they do not
  fire while focus is anywhere in the Terminal pane, matching the guard the ``?``
  help shortcut already applies to input elements.
"""

import pytest

from mniplab.tests.browser import LiveServerCase

pytestmark = pytest.mark.browser_workspace


class TerminalPaneEscapeGuardTests(LiveServerCase):
    """Drives the Terminal pane's destructive shortcuts by keyboard alone."""

    def _open_terminals(self, page, names):
        """Open the named terminals and wait for their live xterm surfaces."""
        page.evaluate("""async (names) => {
            const { appState } = await import('/static/js/state/app-state.js');
            appState.isLabRunning = true;
            for (const n of names) { appState.tabManager.openTerminal(n); }
        }""", names)
        page.wait_for_selector(".xterm-helper-textarea", timeout=5000)
        page.wait_for_timeout(400)

    def _count(self, page):
        return page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            return appState.tabManager.getTerminalCount();
        }""")

    def _modal_visible(self, page):
        return page.evaluate(
            "() => { const m = document.getElementById('confirm-modal');"
            " return !!(m && m.style.display !== 'none'); }"
        )

    def test_escape_on_tab_button_confirms_before_closing(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            self.assertEqual(self._count(page), 1)

            page.locator("#tab-r1").focus()
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            # No silent close: the terminal is still open and a confirmation was
            # raised instead of destroying it outright.
            self.assertEqual(self._count(page), 1)
            self.assertTrue(self._modal_visible(page))

    def test_ctrl_w_on_tab_button_confirms_before_closing(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            self.assertEqual(self._count(page), 1)

            page.locator("#tab-r1").focus()
            page.keyboard.press("Control+w")
            page.wait_for_timeout(200)

            self.assertEqual(self._count(page), 1)
            self.assertTrue(self._modal_visible(page))

    def test_confirm_close_actually_closes_the_terminal(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            self.assertEqual(self._count(page), 1)

            page.locator("#tab-r1").focus()
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            self.assertTrue(self._modal_visible(page))

            page.click("#confirm-ok")
            page.wait_for_timeout(200)

            self.assertFalse(self._modal_visible(page))
            self.assertEqual(self._count(page), 0)

    def test_escape_in_live_xterm_closes_nothing(self):
        """Negative case: focus inside the live session must not close anything.

        xterm.js stops Escape before it reaches the page-level handler, so the
        terminal survives and no confirmation is raised. This is the exact case a
        refactor that "simplifies" the focus check is most likely to break.
        """
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            self.assertEqual(self._count(page), 1)

            page.locator(".xterm-helper-textarea").focus()
            page.wait_for_timeout(150)
            self.assertEqual(
                page.evaluate("() => document.activeElement.id"), "terminal-input-r1"
            )

            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            self.assertEqual(self._count(page), 1)
            self.assertFalse(self._modal_visible(page))

            # Ctrl+W in the live session is likewise a no-op for the app.
            page.keyboard.press("Control+w")
            page.wait_for_timeout(200)
            self.assertEqual(self._count(page), 1)
            self.assertFalse(self._modal_visible(page))

    def test_ctrl_b_in_terminal_chrome_does_not_toggle_sidebar(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            before = page.evaluate(
                "async () => (await import('/static/js/state/app-state.js')).appState.sidebarView"
            )
            self.assertNotEqual(before, "closed")

            page.locator("#tab-r1").focus()
            page.keyboard.press("Control+b")
            page.wait_for_timeout(200)

            after = page.evaluate(
                "async () => (await import('/static/js/state/app-state.js')).appState.sidebarView"
            )
            self.assertEqual(after, before)

    def test_ctrl_t_in_terminal_chrome_does_not_toggle_panel(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            self.assertEqual(
                page.locator("[data-pane='terminal']").get_attribute("data-pane-shown"), "true"
            )

            page.locator("#tab-r1").focus()
            page.keyboard.press("Control+t")
            page.wait_for_timeout(200)

            self.assertEqual(
                page.locator("[data-pane='terminal']").get_attribute("data-pane-shown"), "true"
            )

    def test_ctrl_b_still_works_outside_the_terminal_pane(self):
        """Positive control: the guard must not disable the shortcut elsewhere."""
        with self.signed_in_page() as page:
            before = page.evaluate(
                "async () => (await import('/static/js/state/app-state.js')).appState.sidebarView"
            )
            self.assertNotEqual(before, "closed")

            page.evaluate("() => document.body.focus()")
            page.keyboard.press("Control+b")
            page.wait_for_timeout(200)

            after = page.evaluate(
                "async () => (await import('/static/js/state/app-state.js')).appState.sidebarView"
            )
            self.assertEqual(after, "closed")

    def test_question_mark_in_terminal_chrome_does_not_open_help(self):
        """The ? shortcut carries the same panel guard as Ctrl+B / Ctrl+T."""
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            page.locator("#tab-r1").focus()
            page.keyboard.press("?")
            page.wait_for_timeout(200)

            self.assertTrue(
                page.evaluate(
                    "() => document.getElementById('help-modal')"
                    ".classList.contains('hidden')"
                ),
                "? opened the Help modal from a terminal tab button",
            )

    def test_ctrl_b_while_typing_in_a_field_does_not_toggle_sidebar(self):
        """The toggles carry the same input guard as the ? shortcut."""
        with self.signed_in_page() as page:
            before = page.evaluate(
                "async () => (await import('/static/js/state/app-state.js')).appState.sidebarView"
            )
            self.assertNotEqual(before, "closed")

            search = page.locator("#sidebar-lab-search")
            search.focus()
            page.keyboard.press("Control+b")
            page.wait_for_timeout(200)

            after = page.evaluate(
                "async () => (await import('/static/js/state/app-state.js')).appState.sidebarView"
            )
            self.assertEqual(
                after, before,
                "Ctrl+B toggled the sidebar while the user was typing in a field",
            )
