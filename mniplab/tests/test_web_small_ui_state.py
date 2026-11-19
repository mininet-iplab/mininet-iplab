"""Browser tests: skip-link target, theme-toggle state, header layout.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn server
on a real port, driven by headless Chromium over real HTTP.

The behavior under test:

* Activating "Skip to main content" moves keyboard focus to a real, focusable
  main region — both on the login screen (where the target previously did not
  exist) and after login (where it existed but had no ``tabindex="-1"``).
* The theme-toggle button carries an ``aria-pressed`` state that flips on every
  toggle, so a screen reader reports whether dark mode is active.
* The header grows to fit its content (a minimum height, not a fixed one) so
  the WCAG 1.4.12 text-spacing stress test does not clip the Lab title and
  status pills.
* The header's Lab/Node/Terminal status block reflows into the layout at
  narrow widths instead of being hidden outright.
"""

import pytest

from mniplab.tests.browser import LiveServerCase, expect

pytestmark = pytest.mark.browser_workspace

# The canonical WCAG 1.4.12 text-spacing stress values, applied in full. The
# `p { margin-block: 200% }` rule inflates the footer's <p>, which is what
# would starve the header's row in a grid layout; the header's flex-shrink:0
# (in a flex-column #app) is what keeps it from being squeezed by that.
TEXT_SPACING_STRESS_CSS = """
    :root { font-size: 200%; }
    * {
        line-height: 200% !important;
        letter-spacing: 0.12em !important;
        word-spacing: 0.16em !important;
    }
    p {
        margin-block-start: 200% !important;
        margin-block-end: 200% !important;
    }
"""


class SmallUiStateTests(LiveServerCase):
    """Drives the skip link, theme toggle, and header layout by keyboard/CSS."""

    def _login_page(self):
        """Return a fresh, unauthenticated page showing the login screen."""
        context = self.browser.new_context()
        page = context.new_page()
        page.goto(self.base_url + "/")
        page.wait_for_selector("#login-page:not(.hidden)")
        return context, page

    def _activate_skip_link(self, page):
        page.focus(".skip-link")
        page.keyboard.press("Enter")

    def test_skip_link_on_login_screen_focuses_a_real_region(self):
        context, page = self._login_page()
        try:
            self._activate_skip_link(page)
            active_id = page.evaluate("document.activeElement.id")
            # Focus lands on a real main region, not <body> (id '').
            self.assertNotEqual(active_id, "", "focus is stranded on <body>")
            self.assertEqual(active_id, "login-page")
        finally:
            context.close()

    def test_skip_link_after_login_focuses_main(self):
        with self.signed_in_page() as page:
            self._activate_skip_link(page)
            active_id = page.evaluate("document.activeElement.id")
            self.assertNotEqual(active_id, "", "focus is stranded on <body>")
            self.assertEqual(active_id, "main")

    def test_theme_toggle_reports_aria_checked_state(self):
        with self.signed_in_page() as page:
            page.locator("#account-menu-btn").click()
            toggle = page.get_by_role("menuitemcheckbox", name="Dark mode", exact=True)
            # The Dark mode item carries an aria-checked state from the start.
            initial = toggle.get_attribute("aria-checked")
            self.assertIn(initial, ("true", "false"))

            toggle.click()
            after = toggle.get_attribute("aria-checked")
            self.assertNotEqual(after, initial, "aria-checked did not flip on toggle")

            # Toggling back flips it again.
            toggle.click()
            self.assertEqual(toggle.get_attribute("aria-checked"), initial)

    def test_the_account_menu_works_by_keyboard(self):
        with self.signed_in_page(role="instructor") as page:
            button = page.locator("#account-menu-btn")
            # One button names who is signed in; the role is plain text.
            expect(button).to_contain_text("Instructor")
            expect(button).to_have_attribute("aria-haspopup", "menu")
            expect(page.locator("#account-menu")).to_be_hidden()
            button.focus()
            page.keyboard.press("ArrowDown")
            expect(button).to_have_attribute("aria-expanded", "true")
            menu = page.get_by_role("menu")
            names = [item.inner_text().split("\n")[0].strip()
                     for item in menu.locator("[role^=menuitem]:not([hidden])").all()]
            # Server settings shows only on a `serve` server, which this one may not be.
            names = [name for name in names if name != "Server settings"]
            self.assertEqual(names, ["Sessions & Terminals", "Your Session", "Dark mode", "Log out"])
            expect(menu.get_by_role("menuitemcheckbox", name="Sessions & Terminals")).to_be_focused()
            page.keyboard.press("End")
            expect(menu.get_by_role("menuitem", name="Log out")).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(menu.get_by_role("menuitemcheckbox", name="Sessions & Terminals")).to_be_focused()
            page.keyboard.press("Escape")
            expect(page.locator("#account-menu")).to_be_hidden()
            expect(button).to_be_focused()

            # An item that opens a dialog hands focus back to the menu's button.
            page.keyboard.press("Enter")
            page.keyboard.press("ArrowDown")
            expect(menu.get_by_role("menuitem", name="Your Session", exact=False)).to_be_focused()
            page.keyboard.press("Enter")
            dialog = page.get_by_role("dialog", name="Your Session", exact=True)
            expect(dialog).to_be_visible()
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(button).to_be_focused()

    def test_a_learners_account_menu_has_only_their_own_items(self):
        with self.signed_in_page(role="learner") as page:
            expect(page.locator("#account-menu-btn")).to_contain_text("Learner")
            page.locator("#account-menu-btn").click()
            names = [item.inner_text().split("\n")[0].strip()
                     for item in page.get_by_role("menu").locator("[role^=menuitem]:not([hidden])").all()]
            self.assertEqual(names, ["Your Session", "Dark mode", "Log out"])

    def test_header_not_clipped_under_text_spacing_stress(self):
        with self.signed_in_page() as page:
            page.add_style_tag(content=TEXT_SPACING_STRESS_CSS)
            page.wait_for_timeout(200)
            clipped = page.evaluate("""() => {
                const header = document.querySelector('header');
                return header.scrollHeight - header.clientHeight;
            }""")
            # A taller header grows to fit its content rather than clipping it.
            self.assertLessEqual(clipped, 2, "header content is clipped")

    def test_header_status_block_visible_at_narrow_width(self):
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 375, "height": 700})
            page.wait_for_timeout(200)
            display = page.evaluate(
                "getComputedStyle(document.querySelector('.header-info')).display"
            )
            self.assertNotEqual(
                display, "none",
                "the Lab/Node/Terminal status block is hidden below 768px",
            )
