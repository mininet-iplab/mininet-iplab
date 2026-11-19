"""Classroom capacity is visible beside Start and recovers when a slot opens."""

from unittest.mock import patch

import pytest

from mniplab.tests.authoring_editors import audit
from mniplab.tests.browser import expect
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import registry
from mniplab.webserver.config import settings

pytestmark = pytest.mark.browser_classroom


class InstructorLabCapacityTests(MultiLabBrowserCase):
    """One contextual notice explains a full classroom without repeated alerts."""

    def setUp(self) -> None:
        super().setUp()
        cap = patch.object(settings, "lab_cap", 1)
        cap.start()
        self.addCleanup(cap.stop)
        registry.groups.assign("learner1", ["static-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        self.page = self.signed_in_as("instructor1", "instructor")

    def occupy_slot(self) -> str:
        """Fill the server's single Lab slot without refreshing the browser."""
        response = self.page.request.post(
            self.base_url + "/api/labs",
            data={"group": "learner1", "example_id": "static-lab"},
        )
        self.assertTrue(response.ok)
        return response.json()["key"]

    def refresh_dashboard(self) -> None:
        """Use the production dashboard refresh, including its capacity update."""
        self.page.evaluate(
            """async () => {
            await (await import('/static/js/lab/instructor-dashboard.js')).refreshInstructorDashboard();
        }"""
        )

    def test_full_capacity_has_one_notice_and_recovers_without_replacing_selection(
        self,
    ) -> None:
        page = self.page
        key = self.occupy_slot()
        page.reload()
        expect(page.locator("#instructor-dashboard-cap")).to_contain_text("Full")
        page.get_by_role("button", name="Inspect Group instructor1", exact=True).click()
        button = page.get_by_role(
            "button", name="Start Lab for instructor1", exact=True
        )
        notice = page.locator("#overview-capacity-notice")
        expect(button).to_be_disabled()
        expect(notice).to_have_text(
            "Capacity full (1/1). Start becomes available when a Lab stops."
        )
        expect(notice).to_be_visible()
        expect(button).to_have_attribute("aria-describedby", "overview-capacity-notice")
        expect(page.locator(".lab-capacity-notice:visible")).to_have_count(1)
        select = page.get_by_label("Lab Example to start for instructor1", exact=True)
        select.select_option("static-lab")
        select.focus()
        audit(self, page, "lab-capacity", check_focus=True)
        original = select.element_handle()
        self.refresh_dashboard()
        self.assertTrue(
            select.evaluate("(node, original) => node === original", original)
        )
        expect(select).to_have_value("static-lab")
        expect(page.locator(".lab-capacity-notice:visible")).to_have_count(1)
        self.assertTrue(
            page.request.post(self.base_url + "/api/labs/" + key + "/stop").ok
        )
        self.refresh_dashboard()
        expect(page.locator("#instructor-dashboard-cap")).not_to_contain_text("Full")
        expect(button).to_be_enabled()
        expect(notice).to_be_hidden()
        expect(select).to_have_value("static-lab")

    def test_capacity_notice_does_not_disable_stop_or_override_missing_assignment(
        self,
    ) -> None:
        page = self.page
        key = self.occupy_slot()
        registry.groups.assign("learner2", [])
        page.reload()
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        expect(
            page.get_by_role("button", name="Stop Lab of learner1", exact=True)
        ).to_be_enabled()
        expect(page.locator(".lab-capacity-notice:visible")).to_have_count(0)
        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        button = page.get_by_role("button", name="Start Lab for learner2", exact=True)
        expect(button).to_be_disabled()
        expect(page.locator("#overview-capacity-notice")).to_be_visible()
        self.assertTrue(
            page.request.post(self.base_url + "/api/labs/" + key + "/stop").ok
        )
        self.refresh_dashboard()
        expect(button).to_be_disabled()
        expect(page.locator("#overview-capacity-notice")).to_be_hidden()

    def test_slot_taken_during_start_shows_the_same_persistent_notice(self) -> None:
        page = self.page
        page.get_by_role("button", name="Inspect Group instructor1", exact=True).click()
        button = page.get_by_role(
            "button", name="Start Lab for instructor1", exact=True
        )
        expect(button).to_be_enabled()
        self.occupy_slot()
        with page.expect_response("**/api/labs") as refused:
            button.click()
        self.assertEqual(refused.value.status, 409)
        expect(page.locator("#overview-capacity-notice")).to_be_visible()
        expect(button).to_be_disabled()
        expect(page.locator("#instructor-dashboard-cap")).to_contain_text("Full")

    def test_refresh_does_not_enable_a_start_already_in_progress(self) -> None:
        page = self.page
        page.get_by_role("button", name="Inspect Group instructor1", exact=True).click()
        pending = []
        page.route("**/api/labs", lambda route: pending.append(route))
        button = page.get_by_role(
            "button", name="Start Lab for instructor1", exact=True
        )
        button.click()
        expect(button).to_be_disabled()
        self.refresh_dashboard()
        expect(button).to_be_disabled()
        self.assertEqual(len(pending), 1)
        pending[0].fulfill(status=503, json={"detail": "Temporary start failure"})
        expect(button).to_be_enabled()
        expect(page.locator("#overview-capacity-notice")).to_be_hidden()
