"""Class Overview bulk Stop confirms a fixed set and reports partial failures."""

import pytest

from mniplab.tests.browser import Page, expect, choose_account_item
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import registry
from mniplab.webserver.auth import active_sessions, create_session

pytestmark = pytest.mark.browser_classroom


class StopAllLabsTests(MultiLabBrowserCase):
    """Stop the confirmed Labs without logging out the class or newer Labs."""

    def start_lab(self, page: Page, group: str) -> str:
        """Start a fake Lab for one Group through the authenticated API."""
        registry.groups.assign(group, ["static-lab"])
        response = page.request.post(
            self.base_url + "/api/labs",
            data={"group": group, "example_id": "static-lab"},
        )
        self.assertTrue(response.ok)
        return response.json()["key"]

    def test_confirmed_set_includes_instructor_and_ignores_filters_and_new_labs(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        learner_session = create_session("learner1", "learner")
        first = self.start_lab(page, "learner1")
        own = self.start_lab(page, "instructor1")
        page.reload()
        button = page.get_by_role("button", name="Stop all Labs…", exact=True)
        expect(button).to_be_enabled()
        page.locator("#overview-presence-filter").select_option("no-sessions")
        button.click()
        message = page.locator("#confirm-modal-message")
        expect(message).to_contain_text("2 running Labs")
        expect(message).to_contain_text("instructor1")
        expect(message).to_contain_text("learner1")
        expect(message).to_contain_text("temporary Lab configurations")
        expect(page.locator("#confirm-cancel")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#confirm-modal")).to_be_hidden()
        self.assertTrue(page.request.get(self.base_url + "/api/labs/" + first).ok)
        button.click()
        expect(message).to_contain_text("2 running Labs")
        newer = self.start_lab(page, "learner2")
        page.locator("#confirm-ok").click()
        expect(page.locator("#overview-stop-all-status")).to_have_text("Stopped 2 of 2 Labs.")
        for key in (first, own):
            self.assertEqual(page.request.get(self.base_url + "/api/labs/" + key).status, 404)
        self.assertTrue(page.request.get(self.base_url + "/api/labs/" + newer).ok)
        self.assertTrue(page.request.get(self.base_url + "/api/me").ok)
        self.assertIn(learner_session, active_sessions)

    def test_progress_disables_duplicate_stop_and_retry_targets_only_failed_lab(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        first = self.start_lab(page, "learner1")
        second = self.start_lab(page, "learner2")
        page.reload()
        delayed = []
        page.route("**/api/labs/" + first + "/stop", lambda route: delayed.append(route))
        page.route(
            "**/api/labs/" + second + "/stop",
            lambda route: route.fulfill(status=503, json={"detail": "Try again"}),
        )
        button = page.get_by_role("button", name="Stop all Labs…", exact=True)
        button.click()
        page.locator("#confirm-ok").click()
        expect(page.locator("#overview-stop-all-status")).to_contain_text("Stopping Labs: 0 of 2")
        expect(button).to_be_disabled()
        self.assertEqual(len(delayed), 1)
        delayed[0].continue_()
        status = page.locator("#overview-stop-all-status")
        expect(status).to_contain_text("Stopped 1 of 2 Labs.")
        expect(status).to_contain_text("learner2")
        retry = page.get_by_role("button", name="Retry failed stops…", exact=True)
        expect(retry).to_be_visible()
        newer = self.start_lab(page, "instructor1")
        page.unroute("**/api/labs/" + second + "/stop")
        retry.click()
        expect(page.locator("#confirm-modal-message")).to_contain_text("1 running Lab")
        page.locator("#confirm-ok").click()
        expect(status).to_have_text("Stopped 1 of 1 Labs.")
        expect(retry).to_be_hidden()
        self.assertTrue(page.request.get(self.base_url + "/api/labs/" + newer).ok)

    def test_no_running_labs_disables_bulk_stop_and_failed_refresh_stops_nothing(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        button = page.get_by_role("button", name="Stop all Labs…", exact=True)
        expect(button).to_be_disabled()
        key = self.start_lab(page, "learner1")
        page.reload()
        expect(button).to_be_enabled()
        page.route("**/api/admin/dashboard", lambda route: route.abort())
        button.click()
        expect(page.locator("#overview-stop-all-status")).to_contain_text("Could not load running Labs")
        expect(page.locator("#confirm-modal")).to_be_hidden()
        self.assertTrue(page.request.get(self.base_url + "/api/labs/" + key).ok)

    def test_learner_has_no_bulk_stop_control(self) -> None:
        page = self.signed_in_as("learner1", "learner")
        expect(page.get_by_role("button", name="Stop all Labs…", exact=True)).to_be_hidden()

    def test_a_replacement_for_the_same_group_is_excluded(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        first = self.start_lab(page, "learner1")
        page.reload()
        page.get_by_role("button", name="Stop all Labs…", exact=True).click()
        expect(page.locator("#confirm-modal-message")).to_contain_text("learner1")
        page.request.post(self.base_url + "/api/labs/" + first + "/stop")
        replacement = self.start_lab(page, "learner1")
        page.locator("#confirm-ok").click()
        expect(page.locator("#overview-stop-all-status")).to_have_text("Stopped 1 of 1 Labs.")
        self.assertTrue(page.request.get(self.base_url + "/api/labs/" + replacement).ok)

    def test_logout_cancels_stops_not_yet_sent(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        first = self.start_lab(page, "learner1")
        second = self.start_lab(page, "learner2")
        page.reload()
        delayed = []
        page.route("**/api/labs/" + first + "/stop", lambda route: delayed.append(route))
        page.get_by_role("button", name="Stop all Labs…", exact=True).click()
        page.locator("#confirm-ok").click()
        expect(page.locator("#overview-stop-all-status")).to_contain_text("Stopping Labs")
        choose_account_item(page, "Log out")
        expect(page.locator("#login-form")).to_be_visible()
        self.assertEqual(len(delayed), 1)
        delayed[0].fulfill(json={"status": "stopped"})
        page.wait_for_timeout(100)  # Observe any next request from the old operation.
        self.assertIsNotNone(registry.lab_service.get(second))
