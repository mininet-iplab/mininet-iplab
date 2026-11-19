"""A Learner can recognize, copy and rename its own Session in the real UI."""

import re

import pytest

from mniplab.tests.browser import expect, choose_account_item
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase

pytestmark = pytest.mark.browser_classroom


class SessionIdentityBrowserTests(MultiLabBrowserCase):
    """Session identity stays recognizable through renaming and page reloads."""

    def test_learner_renames_session_and_keeps_code_on_reload(self) -> None:
        page = self.signed_in_as("learner1", "learner")
        choose_account_item(page, "Your Session", exact=False)
        dialog = page.get_by_role("dialog", name="Your Session", exact=True)
        expect(dialog).to_be_visible()
        code = dialog.locator("#session-code").inner_text()
        expect(dialog.locator("#session-details")).to_have_value(
            re.compile(r"learner1.*" + code)
        )
        dialog.get_by_label("Session nickname", exact=True).fill("Sari <router>")
        dialog.get_by_role("button", name="Save nickname", exact=True).click()
        expect(dialog.locator("#session-identity-status")).to_have_text(
            "Nickname saved."
        )
        expect(dialog.locator("#session-code")).to_have_text(code)
        page.keyboard.press("Escape")
        expect(page.locator("#account-menu-btn")).to_be_focused()
        page.reload()
        choose_account_item(page, "Your Session", exact=False)
        expect(dialog.get_by_label("Session nickname", exact=True)).to_have_value(
            "Sari <router>"
        )
        expect(dialog.locator("#session-code")).to_have_text(code)
        self.assertEqual(dialog.locator("router").count(), 0)
