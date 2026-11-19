"""Instructor Stop confirmations identify and retain their intended target."""

import pytest

from mniplab.tests.browser import Page, expect
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_classroom


class InstructorStopConfirmationTests(MultiLabBrowserCase):
    """Confirm or cancel a named Group Lab without stopping another Group."""

    def start_lab(self, page: Page, group: str, example: str) -> str:
        """Start a fake Lab for the requested Group."""
        registry.groups.assign(group, [example])
        response = page.request.post(
            self.base_url + "/api/labs",
            data={"group": group, "example_id": example},
        )
        self.assertTrue(response.ok)
        return response.json()["key"]

    def test_both_stop_controls_name_the_group_and_lab_and_allow_cancel(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        other = self.start_lab(page, "learner2", "ospf-lab")
        for entry in ("overview", "workspace"):
            with self.subTest(entry=entry):
                key = self.start_lab(page, "learner1", "static-lab")
                page.goto(self.base_url + "/")
                if entry == "overview":
                    page.get_by_role(
                        "button", name="Inspect Group learner1", exact=True
                    ).click()
                    button = page.locator(
                        "button[data-action='stop'][data-group='learner1']"
                    )
                else:
                    self.select_group(page, "learner1")
                    expect(page).to_have_url(self.base_url + "/labs/" + key)
                    button = page.get_by_role("button", name="Stop Lab", exact=True)
                button.click()
                expect(page.locator("#confirm-modal-message")).to_contain_text(
                    "Group learner1"
                )
                expect(page.locator("#confirm-modal-message")).to_contain_text(
                    "Static Routing Lab"
                )
                expect(page.locator("#confirm-cancel")).to_be_focused()
                page.keyboard.press("Enter")
                expect(page.locator("#confirm-modal")).to_be_hidden()
                self.assertTrue(page.request.get(self.base_url + "/api/labs/" + key).ok)
                button.click()
                with page.expect_response("**/api/labs/" + key + "/stop") as stopped:
                    page.locator("#confirm-ok").click()
                self.assertTrue(stopped.value.ok)
                self.assertTrue(
                    page.request.get(self.base_url + "/api/labs/" + other).ok
                )

    def test_workspace_confirmation_cannot_stop_a_newly_selected_group(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        first = self.start_lab(page, "learner1", "static-lab")
        second = self.start_lab(page, "learner2", "ospf-lab")
        page.reload()
        self.select_group(page, "learner1")
        expect(page).to_have_url(self.base_url + "/labs/" + first)
        page.get_by_role("button", name="Stop Lab", exact=True).click()
        # Model a binding change while the original confirmation is open.
        page.evaluate(
            """async () => {
            (await import('/static/js/ui/tabstrip.js')).selectGroupTab('learner2');
        }"""
        )
        expect(page).to_have_url(self.base_url + "/labs/" + second)
        stops = []
        page.on(
            "request",
            lambda request: (
                stops.append(request.url) if request.url.endswith("/stop") else None
            ),
        )
        page.locator("#confirm-ok").click()
        expect(page.locator("#confirm-modal")).to_be_hidden()
        page.wait_for_timeout(100)  # Observe any Stop dispatched by the confirmation.
        self.assertEqual(stops, [])
        self.assertTrue(page.request.get(self.base_url + "/api/labs/" + first).ok)
        self.assertTrue(page.request.get(self.base_url + "/api/labs/" + second).ok)
        expect(page.locator("#workspace-title")).to_have_text("Labs / learner2")

    def test_stop_waits_for_the_selected_groups_binding(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        first = self.start_lab(page, "learner1", "static-lab")
        second = self.start_lab(page, "learner2", "ospf-lab")
        page.reload()
        self.select_group(page, "learner1")
        expect(page).to_have_url(self.base_url + "/labs/" + first)
        delayed = []
        page.route("**/api/labs/" + second, lambda route: delayed.append(route))
        self.select_group(page, "learner2")
        expect(page.locator("#lab-tab-current")).to_have_text("learner2")
        page.get_by_role("button", name="Stop Lab", exact=True).click()
        expect(page.locator("#confirm-modal")).to_be_hidden()
        self.assertEqual(len(delayed), 1)
        delayed[0].continue_()
        expect(page).to_have_url(self.base_url + "/labs/" + second)
        page.get_by_role("button", name="Stop Lab", exact=True).click()
        expect(page.locator("#confirm-modal-message")).to_contain_text("Group learner2")
        expect(page.locator("#confirm-modal-message")).to_contain_text(
            "OSPF Routing Lab"
        )
        page.keyboard.press("Escape")
