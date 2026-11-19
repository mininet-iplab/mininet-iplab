"""Browser tests for modal focus containment and the Create-Group Escape key.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``:
a real uvicorn server on a real port, driven by headless Chromium over real
HTTP. While any modal
(Help, Link Config, Create-Group) is open, Tab must cycle only within that modal's
own focusable elements and never lands on the page rendered behind the
overlay, that closing a modal returns focus to what triggered it, and that the
Create-Group modal's Escape closes it no matter which element inside it holds
focus.
"""

import pytest

from mniplab.tests.browser import LiveServerCase, Page

pytestmark = pytest.mark.browser_workspace


class ModalFocusTrapTests(LiveServerCase):
    """Drives each modal by keyboard alone and asserts focus stays inside it."""

    def _tab_cycle_stays_inside(
        self,
        page: Page,
        modal_selector: str,
        what: str,
        key: str = "Tab",
        max_tabs: int = 100,
    ) -> None:
        """Tab around the modal's tab order and assert focus never leaves it.

        Presses *key* until focus wraps back to the element that held it at the
        start (proving a full cycle) and fails if any intermediate stop lands
        outside the modal. Pass ``key="Shift+Tab"`` to walk the cycle backwards,
        which the trap must contain symmetrically.
        """
        start_handle = page.evaluate_handle("() => document.activeElement")
        for i in range(max_tabs):
            page.keyboard.press(key)
            inside = page.evaluate(
                "(sel) => document.querySelector(sel).contains(document.activeElement)",
                modal_selector,
            )
            self.assertTrue(
                inside,
                f"{key} press {i + 1} moved focus out of {what} "
                f"(activeElement={page.evaluate('() => document.activeElement.outerHTML.slice(0, 80)')})",
            )
            still_start = page.evaluate(
                "(el) => el === document.activeElement", start_handle
            )
            if i > 0 and still_start:
                return  # full cycle completed
        self.fail(
            f"focus did not cycle back to its start within {max_tabs} "
            f"{key} presses in {what}"
        )

    def _tab_cycle_stays_inside_both_ways(
        self, page: Page, modal_selector: str, what: str
    ) -> None:
        """Assert containment walking the tab order forwards and then backwards."""
        self._tab_cycle_stays_inside(page, modal_selector, what)
        self._tab_cycle_stays_inside(page, modal_selector, what, key="Shift+Tab")

    def test_help_modal_traps_focus_and_restores_on_close(self):
        with self.signed_in_page() as page:
            help_btn = page.locator("#help-btn")
            help_btn.focus()
            help_btn.click()
            page.wait_for_selector("#help-modal:not(.hidden)")

            # Focus lands inside the modal, not on the trigger or the page.
            self.assertTrue(
                page.evaluate(
                    "() => document.querySelector('#help-modal') "
                    ".contains(document.activeElement)"
                ),
                "focus did not move into the Help modal when it opened",
            )
            self._tab_cycle_stays_inside_both_ways(page, "#help-modal", "the Help modal")

            # Closing returns focus to the element that opened it.
            page.keyboard.press("Escape")
            page.wait_for_function(
                "() => document.getElementById('help-modal').classList.contains('hidden')"
            )
            self.assertTrue(
                page.evaluate(
                    "() => document.querySelector('#help-btn') === document.activeElement"
                ),
                "closing the Help modal did not return focus to the help button",
            )

    def test_link_config_modal_traps_focus_and_closes_on_escape(self):
        with self.signed_in_page() as page:
            # The Configure Link button lives in the link context menu, which
            # only appears after a link is clicked. With no running lab there
            # are no topology links, so reveal the menu directly (the same DOM
            # state a link click produces) and seed its node inputs.
            page.evaluate("""() => {
                document.getElementById('link-node1').value = 'r1';
                document.getElementById('link-node2').value = 'r2';
                const menu = document.getElementById('link-context-menu');
                menu.classList.remove('hidden');
                menu.setAttribute('aria-hidden', 'false');
            }""")

            config_btn = page.locator("#link-config-btn")
            config_btn.focus()
            config_btn.click()
            page.wait_for_selector("#link-modal[style*='flex']")

            self.assertTrue(
                page.evaluate(
                    "() => document.querySelector('#link-modal') "
                    ".contains(document.activeElement)"
                ),
                "focus did not move into the Link Config modal when it opened",
            )
            self._tab_cycle_stays_inside_both_ways(page, "#link-modal", "the Link Config modal")

            # Escape closes the modal, and focus is not stranded inside the
            # (now hidden) modal. Focus-return-to-trigger is asserted in the
            # Help modal test, where the trigger is a stable, always-visible
            # button; the Link Config trigger is a transient context-menu item.
            page.keyboard.press("Escape")
            page.wait_for_function(
                "() => document.getElementById('link-modal').style.display === 'none'"
            )
            self.assertFalse(
                page.evaluate(
                    "() => document.querySelector('#link-modal') "
                    ".contains(document.activeElement)"
                ),
                "focus was left inside the closed Link Config modal",
            )

    def test_create_group_modal_escaping_from_cancel_button_closes_it(self):
        with self.signed_in_page() as page:
            # Give the modal a real selection so it opens the name form.
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.selectedNodes = new Set(['r1', 'r2']);
            }""")

            # Group lives in the View menu's layout actions.
            page.locator("#view-options-btn").click()
            create_btn = page.locator("#create-group-btn")
            create_btn.focus()
            create_btn.click()
            page.wait_for_selector("#input-modal[style*='flex']")

            # The name field holds focus on open.
            self.assertTrue(
                page.evaluate(
                    "() => document.activeElement && "
                    "document.activeElement.id === 'input-group-name'"
                ),
                "the Create Group name field did not receive focus on open",
            )

            # Tab to the Cancel button (focus leaves the name field) — the
            # exact condition the ticket calls out: Escape must still work
            # after Tab has moved focus off the name field.
            for _ in range(5):
                page.keyboard.press("Tab")
                if page.evaluate("() => document.activeElement.id === 'input-cancel'"):
                    break
            self.assertTrue(
                page.evaluate("() => document.activeElement.id === 'input-cancel'"),
                "could not Tab to the Cancel button",
            )

            # Escape from the Cancel button (not the name field) closes it.
            page.keyboard.press("Escape")
            page.wait_for_function(
                "() => document.getElementById('input-modal').style.display === 'none'"
            )

    def test_create_group_modal_traps_focus(self):
        with self.signed_in_page() as page:
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.selectedNodes = new Set(['r1', 'r2']);
            }""")

            # Group lives in the View menu's layout actions.
            page.locator("#view-options-btn").click()
            create_btn = page.locator("#create-group-btn")
            create_btn.focus()
            create_btn.click()
            page.wait_for_selector("#input-modal[style*='flex']")

            self._tab_cycle_stays_inside_both_ways(page, "#input-modal", "the Create Group modal")


MODALS = (
    "session-identity-modal tools-modal link-modal capture-modal dhcp-leases-modal "
    "dhcp-relay-modal dns-modal resolver-modal speaker-modal switch-modal "
    "assignment-modal config-reset-modal help-modal input-modal confirm-modal"
).split()

# The WCAG 1.4.12 text-spacing overrides a Learner's stylesheet may apply.
TEXT_SPACING = (
    "* { line-height: 1.5 !important; letter-spacing: 0.12em !important; "
    "word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }"
)


class ModalDensityTests(LiveServerCase):
    """Every running-Lab dialog's inputs, selects and buttons share 2rem (32px) and survives text spacing."""

    MEASURE = """(id) => {
        const overlay = document.getElementById(id);
        overlay.classList.remove('hidden');
        overlay.style.display = 'flex';
        const content = overlay.querySelector('.modal-content');
        content.style.animation = 'none';
        const box = content.getBoundingClientRect();
        const body = content.querySelector('.modal-body');
        const clipped = [...content.querySelectorAll('.modal-header *, .modal-actions *')]
            .filter(el => el.getBoundingClientRect().width)
            .filter(el => { const r = el.getBoundingClientRect();
                return r.right > box.right + 1 || r.left < box.left - 1; }).length;
        const tall = [...content.querySelectorAll(
            '.modal-header button, .modal-actions button, .modal-body > .btn-primary, '
            + '.modal-body select, .modal-body input:not([type=checkbox], [type=radio], '
            + '[type=range], [type=hidden])')]
            .filter(el => el.getBoundingClientRect().height)
            .map(el => el.getBoundingClientRect().height);
        const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
        const result = { tall: tall.map(h => h / rem), clipped, bodyWide: body.scrollWidth > body.clientWidth + 1,
            fits: box.top >= 0 && box.bottom <= innerHeight };
        overlay.style.display = 'none';
        return result;
    }"""

    def test_dialog_buttons_are_32px_and_text_spacing_clips_nothing(self) -> None:
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1366, "height": 658})
            plain = {m: page.evaluate(self.MEASURE, m) for m in MODALS}
            page.add_style_tag(content=TEXT_SPACING)
            spaced = {m: page.evaluate(self.MEASURE, m) for m in MODALS}

        for modal, seen in plain.items():
            self.assertTrue(all(abs(h - 2) < 0.05 for h in seen["tall"]), (modal, seen["tall"]))
            self.assertTrue(seen["fits"], modal)
        for modal, seen in spaced.items():
            self.assertEqual(seen["clipped"], 0, modal)
            self.assertFalse(seen["bodyWide"], modal)
            self.assertTrue(seen["fits"], modal)


class ToolsModalResizeTests(LiveServerCase):
    """The Network Tools dialog widens and heightens by keyboard, within the window."""

    OPEN = """async () => {
        const { appState } = await import('/static/js/state/app-state.js');
        const modal = document.getElementById('tools-modal');
        modal.querySelector('.modal-content').style.animation = 'none';
        appState.networkConsole.openModal(modal);
    }"""

    BOX = """() => {
        const box = document.querySelector('#tools-modal .modal-content').getBoundingClientRect();
        const out = document.getElementById('ping-output');
        out.textContent = 'PING r2 (10.0.0.2) 56(84) bytes of data.';
        return { left: box.left, right: box.right, width: box.width, height: box.height,
                 bottom: box.bottom, output: out.getBoundingClientRect().height };
    }"""

    def test_edges_resize_the_dialog_by_keyboard_and_stop_at_the_window(self) -> None:
        """The left edge widens it leftwards, the bottom edge heightens it, within the window."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1366, "height": 768})
            page.evaluate(self.OPEN)
            before = page.evaluate(self.BOX)

            width_edge = page.get_by_role("separator", name="Resize Network Tools width")
            width_edge.focus()
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            wider = page.evaluate(self.BOX)
            self.assertGreater(wider["width"], before["width"] + 50)
            # It opens top-right, so the right edge stays and it widens leftwards.
            self.assertAlmostEqual(wider["right"], before["right"], delta=1)
            self.assertIn("pixels wide", width_edge.get_attribute("aria-valuetext") or "")

            page.keyboard.press("End")
            widest = page.evaluate(self.BOX)
            self.assertAlmostEqual(widest["left"], 8, delta=1)
            self.assertAlmostEqual(widest["right"], 1366 - 8, delta=1)

            height_edge = page.get_by_role("separator", name="Resize Network Tools height")
            height_edge.focus()
            page.keyboard.press("End")
            tallest = page.evaluate(self.BOX)
            self.assertLessEqual(tallest["bottom"], 768)
            self.assertGreater(tallest["height"], before["height"])
            # The open tab's output takes the added height, past its 250px cap.
            self.assertGreater(tallest["output"], 250)

            page.keyboard.press("Home")
            smallest = page.evaluate(self.BOX)
            self.assertAlmostEqual(smallest["height"], 240, delta=1)

    def test_a_dialog_dragged_to_the_left_still_widens_rightwards(self) -> None:
        """With no room left of it, the dialog widens towards the window's right."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1366, "height": 768})
            page.evaluate(self.OPEN)
            page.evaluate("""() => { const c = document.querySelector('#tools-modal .modal-content');
                c.style.left = '20px'; c.style.right = 'auto'; }""")
            before = page.evaluate(self.BOX)

            page.get_by_role("separator", name="Resize Network Tools width").focus()
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            wider = page.evaluate(self.BOX)
            self.assertGreater(wider["width"], before["width"] + 50)
            self.assertAlmostEqual(wider["left"], 8, delta=1)
