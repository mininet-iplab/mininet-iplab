"""Browser tests for terminal-tab keyboard parity and target size.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn server
on a real port, driven by headless Chromium over real HTTP.

The behavior under test makes the terminal tab bar consistent with
the WAI-ARIA tabs pattern and easier to operate
with imprecise pointing:

* The terminal tabs use a roving tabindex — only the selected tab sits in the
  Tab order (``tabindex="0"``), the rest are ``tabindex="-1"`` — so a
  keyboard user reaches the tab strip in one Tab press and then roves with
  ArrowLeft/ArrowRight, instead of Tabbing through every open tab.
* ArrowLeft/ArrowRight, Home and End move the selection (and focus) between
  tabs.
* The per-tab close ("×") button's rendered hit target is at least 24×24 CSS
  px, so closing a tab does not require precise pointer placement.
* Each close button sits beside its tab rather than inside it, the tab list
  holds the tabs and none of the controls around them, and the tab panel is
  named by the selected tab.
"""

import pytest

from mniplab.tests.browser import LiveServerCase

pytestmark = pytest.mark.browser_workspace


class TerminalTabParityTests(LiveServerCase):
    """Drives the terminal tab bar by keyboard alone and measures its targets."""

    def _open_terminals(self, page, names):
        """Open the named terminals and wait for their live xterm surfaces."""
        page.evaluate("""async (names) => {
            const { appState } = await import('/static/js/state/app-state.js');
            // The Terminal pane is on show only while a Lab runs.
            appState.isLabRunning = true;
            for (const n of names) { appState.tabManager.openTerminal(n); }
        }""", names)
        page.wait_for_selector(".xterm-helper-textarea", timeout=5000)
        page.wait_for_timeout(400)

    def _tabindex_by_node(self, page):
        """Return a {nodeName: tabIndex} map for every terminal tab."""
        return page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            const out = {};
            for (const [node, data] of appState.tabManager.terminals) {
                out[node] = data.tabElement.tabIndex;
            }
            return out;
        }""")

    def _selected_node(self, page):
        return page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            return appState.tabManager.activeTab;
        }""")

    def test_only_selected_tab_is_in_tab_order(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2", "r3"])
            self.assertEqual(self._selected_node(page), "r3")

            indexes = self._tabindex_by_node(page)
            self.assertEqual(indexes["r3"], 0)
            self.assertEqual(indexes["r1"], -1)
            self.assertEqual(indexes["r2"], -1)

    def test_arrow_keys_rove_selection_between_tabs(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2", "r3"])
            self.assertEqual(self._selected_node(page), "r3")

            # ArrowLeft from the last tab moves selection to the previous one.
            page.locator("#tab-r3").focus()
            page.keyboard.press("ArrowLeft")
            page.wait_for_timeout(150)
            self.assertEqual(self._selected_node(page), "r2")
            self.assertEqual(self._tabindex_by_node(page)["r2"], 0)
            self.assertEqual(self._tabindex_by_node(page)["r3"], -1)

            # ArrowRight moves it back.
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(150)
            self.assertEqual(self._selected_node(page), "r3")
            self.assertEqual(self._tabindex_by_node(page)["r3"], 0)

    def test_roving_wraps_around_at_the_ends(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2", "r3"])

            # ArrowLeft from the first tab wraps to the last.
            page.locator("#tab-r1").focus()
            page.keyboard.press("ArrowLeft")
            page.wait_for_timeout(150)
            self.assertEqual(self._selected_node(page), "r3")

            # ArrowRight from the last tab wraps to the first.
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(150)
            self.assertEqual(self._selected_node(page), "r1")

    def test_home_and_end_select_the_first_and_last_tab(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2", "r3"])

            page.locator("#tab-r2").focus()
            page.keyboard.press("Home")
            page.wait_for_timeout(150)
            self.assertEqual(self._selected_node(page), "r1")
            self.assertEqual(self._tabindex_by_node(page)["r1"], 0)

            page.keyboard.press("End")
            page.wait_for_timeout(150)
            self.assertEqual(self._selected_node(page), "r3")
            self.assertEqual(self._tabindex_by_node(page)["r3"], 0)

    def test_a_tab_close_control_sits_beside_its_tab_not_inside_it(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2"])

            nested = page.evaluate(
                "() => [...document.querySelectorAll('.terminal-tab')]"
                ".filter(tab => tab.querySelector('button')).length"
            )
            beside = page.evaluate("""() => {
                const tab = document.getElementById('tab-r1');
                const close = document.querySelector('button[aria-label="Close r1 terminal"]');
                return tab.nextElementSibling === close;
            }""")
            page.get_by_role("button", name="Close r1 terminal").click()
            remaining = self._owned_tab_names(page)

        self.assertEqual(nested, 0, "no tab may contain another control")
        self.assertTrue(beside, "the close control follows its tab")
        self.assertEqual(remaining, [["tab", "r2"]])

    def _owned_tab_names(self, page):
        """Return the text of every element the Terminal tablist owns, in order.

        The tablist owns its tabs by id (``aria-owns``), so this reads what
        assistive technology sees under it rather than where the tabs sit in
        the page.
        """
        return page.get_by_role("tablist", name="Open terminals").evaluate("""el => {
            const owned = (el.getAttribute('aria-owns') || '').split(' ').filter(Boolean);
            return owned.map(id => {
                const target = document.getElementById(id);
                return [target.getAttribute('role'), target.textContent.trim()];
            });
        }""")

    def test_the_tab_list_owns_only_tabs_and_none_of_the_controls_around_them(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2"])

            tablist = page.get_by_role("tablist", name="Open terminals")
            owned = self._owned_tab_names(page)
            snapshot = tablist.aria_snapshot()
            contains_controls = tablist.evaluate("el => el.querySelector('button') !== null")
            close_all_in_tree = page.get_by_role("tablist", name="Open terminals").get_by_role(
                "button", name="Close all terminals"
            ).count()
            close_all_visible = page.get_by_role("button", name="Close all terminals").is_visible()

        self.assertEqual(owned, [["tab", "r1"], ["tab", "r2"]])
        self.assertIn('tab "r1"', snapshot)
        self.assertIn('tab "r2"', snapshot)
        self.assertNotIn("button", snapshot, "no close control is owned by the tablist")
        self.assertFalse(contains_controls)
        self.assertEqual(close_all_in_tree, 0)
        self.assertTrue(close_all_visible)

    def test_the_tab_list_keeps_owning_exactly_the_tabs_as_they_come_and_go(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2", "r3"])
            page.get_by_role("button", name="Close r2 terminal").click()
            after_close = self._owned_tab_names(page)
            page.get_by_role("button", name="Close r1 terminal").click()
            after_two = self._owned_tab_names(page)
            page.get_by_role("button", name="Close r3 terminal").click()
            # With no Terminal left the Terminal pane takes no room, so read the attribute off the page.
            owns_when_empty = page.evaluate(
                "() => document.querySelector('.terminal-tablist').getAttribute('aria-owns')"
            )

        self.assertEqual(after_close, [["tab", "r1"], ["tab", "r3"]])
        self.assertEqual(after_two, [["tab", "r3"]])
        self.assertIsNone(owns_when_empty)

    def test_the_tab_panel_is_named_by_the_selected_tab(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1", "r2"])

            panel = page.get_by_role("tabpanel", name="r2", exact=True)
            self.assertEqual(panel.count(), 1)
            page.get_by_role("tab", name="r1", exact=True).click()
            page.wait_for_timeout(150)
            renamed = page.get_by_role("tabpanel", name="r1", exact=True).count()

        self.assertEqual(renamed, 1)

    def test_dragging_a_tab_reorders_it_together_with_its_close_control(self):
        with self.signed_in_page() as page:
            # The Terminal pane is a share of the window, so give the three
            # tabs room: a tab scrolled partly out of the strip has its far
            # edge under the Terminal controls, where nothing can be dropped.
            page.set_viewport_size({"width": 1600, "height": 900})
            self._open_terminals(page, ["r1", "r2", "r3"])
            target = page.locator("#tab-r3")
            width = target.bounding_box()["width"]

            page.locator("#tab-r1").drag_to(target, target_position={"x": width - 4, "y": 8})
            order = page.evaluate(
                "() => [...document.querySelectorAll('.terminal-tab-item')]"
                ".map(item => [item.children[0].textContent.trim(), item.children[1].getAttribute('aria-label')])"
            )
            owned = self._owned_tab_names(page)

        self.assertEqual(
            order,
            [["r2", "Close r2 terminal"], ["r3", "Close r3 terminal"], ["r1", "Close r1 terminal"]],
        )
        self.assertEqual(owned, [["tab", "r2"], ["tab", "r3"], ["tab", "r1"]])

    def test_close_button_meets_minimum_target_size(self):
        with self.signed_in_page() as page:
            self._open_terminals(page, ["r1"])
            box = page.get_by_role("button", name="Close r1 terminal").bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["width"], 24)
            self.assertGreaterEqual(box["height"], 24)

    def test_lab_prompt_button_opens_prompt_tab(self):
        """Clicking the Lab Prompt button opens a tab with the Lab Prompt."""
        with self.signed_in_page() as page:
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                // The Lab Prompt is offered, and its pane shown, while a Lab runs.
                appState.isLabRunning = true;
                const btn = document.getElementById('lab-prompt-btn');
                if (btn) btn.style.display = '';
            }""")
            prompt_btn = page.locator("#lab-prompt-btn")
            self.assertTrue(prompt_btn.is_visible())
            prompt_btn.click()
            page.wait_for_selector("#tab-lab-prompt", timeout=5000)

            self.assertEqual(self._selected_node(page), "lab-prompt")
            tab = page.locator("#tab-lab-prompt")
            self.assertIn("Lab Prompt", tab.inner_text())
            self.assertEqual(
                page.get_by_role("button", name="Close Lab Prompt terminal").count(), 1
            )
