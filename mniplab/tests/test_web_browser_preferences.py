"""Browser preference reset through Help without changing server Lab data."""

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase
from mniplab.tests.browser import Page, expect, choose_account_item

pytestmark = pytest.mark.browser_workspace


class BrowserPreferencesBrowserTests(AuthoringPageCase):
    """Reset only this application's preferences after explicit confirmation."""

    PREFERENCES = {
        "mniplab-test-preference": "reset dash prefix",
        "mniplab.test-preference": "reset dot prefix",
        "mniplab.authoring.draftClosed": "true",
        "theme": "dark",
        "collapsedGroups": "{}",
        "topologyLayout": "{}",
        "nodePositions": "{}",
        "unrelated-setting": "keep unrelated setting",
        "mniplabish-setting": "keep similar prefix",
    }

    def _seed_preferences(self, page: Page) -> None:
        """Populate both stores in this test's isolated browser context."""
        page.evaluate("""preferences => {
            for (const storage of [localStorage, sessionStorage]) {
                for (const [key, value] of Object.entries(preferences)) storage.setItem(key, value);
            }
        }""", self.PREFERENCES)

    def _preferences(self, page: Page) -> dict:
        """Read only the known test keys, without accessing authentication data."""
        return page.evaluate("""keys => Object.fromEntries(
            ['localStorage', 'sessionStorage'].map(name => [name,
                Object.fromEntries(keys.map(key => [key, window[name].getItem(key)]))]))
        """, list(self.PREFERENCES))

    def test_help_offers_browser_preference_reset(self) -> None:
        """The reset is reachable from Help rather than requiring browser tools."""
        with self.signed_in_page() as page:
            page.get_by_role("button", name="Help", exact=True).click()
            expect(page.get_by_role("button", name="Reset browser preferences…", exact=True)).to_be_visible(
                timeout=1000
            )

    def test_cancel_and_escape_restore_help_focus_without_resetting(self) -> None:
        """The nested confirmation traps focus and safely returns to Help."""
        with self.signed_in_page() as page:
            self._seed_preferences(page)
            before = self._preferences(page)
            page.get_by_role("button", name="Help", exact=True).focus()
            page.keyboard.press("Enter")
            reset = page.get_by_role("button", name="Reset browser preferences…", exact=True)
            for dismiss in ("Enter", "Escape"):
                with self.subTest(dismiss=dismiss):
                    reset.focus()
                    page.keyboard.press("Enter")
                    confirm = page.get_by_role("dialog", name="Reset browser preferences", exact=True)
                    expect(confirm).to_be_visible()
                    cancel = confirm.get_by_role("button", name="Cancel", exact=True)
                    expect(cancel).to_be_focused()
                    page.keyboard.press("Shift+Tab")
                    expect(confirm.get_by_role("button", name="Reset and reload", exact=True)).to_be_focused()
                    page.keyboard.press("Tab")
                    expect(cancel).to_be_focused()
                    page.keyboard.press(dismiss)
                    expect(confirm).to_be_hidden()
                    expect(page.locator("#help-modal")).to_be_visible()
                    expect(reset).to_be_focused()
                    self.assertEqual(self._preferences(page), before)

    def test_reset_clears_only_app_preferences_and_preserves_saved_draft(self) -> None:
        """Reset reloads the default theme without touching server data or login."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", -125.5, 100), ("h2", 3500, 600)])
            saved = self._saved_draft(page)
            page.goto(self.base_url + "/")
            page.wait_for_selector("#app:not(.hidden)")
            choose_account_item(page, "Dark mode", "menuitemcheckbox")
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            self._seed_preferences(page)
            page.get_by_role("button", name="Help", exact=True).click()
            page.get_by_role("button", name="Reset browser preferences…", exact=True).click()
            with page.expect_navigation(wait_until="domcontentloaded"):
                page.get_by_role("button", name="Reset and reload", exact=True).click()
            page.wait_for_selector("#app:not(.hidden)")
            expect(page.locator("html")).to_have_attribute("data-theme", "light")
            expected = {key: value if key in ("unrelated-setting", "mniplabish-setting") else None
                        for key, value in self.PREFERENCES.items()}
            self.assertEqual(self._preferences(page), {name: expected for name in
                                                       ("localStorage", "sessionStorage")})
            response = page.request.get(self.base_url + f"/api/authoring/drafts/{saved['id']}")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.json(), saved)

    def test_blocked_storage_reports_an_accessible_error_without_reload(self) -> None:
        """A browser storage failure keeps Help open with an announced error."""
        with self.signed_in_page() as page:
            self._seed_preferences(page)
            before = self._preferences(page)
            page.evaluate("""() => {
                window.__preferencesPage = true;
                Storage.prototype.removeItem = () => { throw new DOMException('Storage blocked', 'SecurityError'); };
            }""")
            page.get_by_role("button", name="Help", exact=True).click()
            page.get_by_role("button", name="Reset browser preferences…", exact=True).click()
            page.get_by_role("button", name="Reset and reload", exact=True).click()
            error = page.locator("#browser-preferences-error")
            expect(error).to_be_visible()
            expect(error).to_have_attribute("role", "alert")
            expect(error).to_contain_text("Could not reset browser preferences")
            expect(page.locator("#help-modal")).to_be_visible()
            expect(page.locator("#confirm-modal")).to_be_hidden()
            self.assertTrue(page.evaluate("() => window.__preferencesPage === true"))
            self.assertEqual(self._preferences(page), before)
