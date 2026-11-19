"""Fresh instructor sign-in starts from the class rather than an old Lab."""

import time

import pytest

from mniplab.tests.browser import Page, Route, expect, choose_account_item
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import auth, registry

pytestmark = pytest.mark.browser_classroom


class InstructorLoginTests(MultiLabBrowserCase):
    """Explicit sign-in clears Group context; authenticated navigation retains it."""

    def start_group_lab(self, page: Page, group: str) -> str:
        """Start a fake Group Lab through the production API."""
        registry.groups.assign(group, ["static-lab"])
        response = page.request.post(
            self.base_url + "/api/labs",
            data={"group": group, "example_id": "static-lab"},
        )
        self.assertTrue(response.ok)
        return response.json()["key"]

    def sign_in(self, page: Page, username: str = "instructor1") -> None:
        """Complete the actual login form."""
        page.get_by_label("Username", exact=True).fill(username)
        page.get_by_label("Password", exact=True).fill("lab12345")
        page.get_by_role("button", name="Login", exact=True).click()

    def assert_overview(self, page: Page) -> None:
        """No Group or Lab target is selected after fresh instructor sign-in."""
        expect(page.get_by_role("tab", name="Overview", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page).to_have_url(self.base_url + "/")
        expect(page.locator("#lab-tab-current")).to_be_hidden()
        expect(page.locator("#overview-group-details")).to_be_hidden()
        expect(
            page.get_by_role("combobox", name="Select Group", exact=True)
        ).to_have_value("")
        expect(
            page.get_by_role("combobox", name="Filter Groups", exact=True)
        ).to_have_value("All Groups")
        self.assertIsNone(
            page.evaluate(
                "async () => (await import('/static/js/state/app-state.js')).appState.boundGroup"
            )
        )

    def test_logout_then_login_clears_the_last_group_with_or_without_own_lab(
        self,
    ) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        key = self.start_group_lab(page, "learner1")
        for own_lab in (False, True):
            with self.subTest(own_lab=own_lab):
                if own_lab:
                    self.start_group_lab(page, "instructor1")
                page.reload()
                self.select_group(page, "learner1")
                expect(page).to_have_url(self.base_url + "/labs/" + key)
                choose_account_item(page, "Log out")
                expect(page.locator("#login-page")).to_be_visible()
                expect(page).to_have_url(self.base_url + "/")
                self.sign_in(page)
                self.assert_overview(page)
                page.reload()
                self.assert_overview(page)

    def test_expired_session_and_old_lab_url_do_not_restore_a_group_after_login(
        self,
    ) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        self.start_group_lab(page, "instructor1")
        key = self.start_group_lab(page, "learner1")
        page.goto(self.base_url + "/labs/" + key)
        expect(page.locator("#lab-tab-current")).to_have_text("learner1")
        token = page.context.cookies()[0]["value"]
        auth.active_sessions.pop(token, None)
        page.reload()
        expect(page.locator("#login-page")).to_be_visible()
        expect(page).to_have_url(self.base_url + "/labs/" + key)
        self.sign_in(page)
        self.assert_overview(page)

    def test_authenticated_refresh_keeps_group_and_overview_keeps_root_url(
        self,
    ) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        self.start_group_lab(page, "instructor1")
        key = self.start_group_lab(page, "learner1")
        page.goto(self.base_url + "/labs/" + key)
        status_requests = 0

        def delay_initial_status(route) -> None:
            nonlocal status_requests
            status_requests += 1
            if status_requests == 2:
                response = route.fetch()
                time.sleep(0.3)  # Let any premature Group binding finish first.
                route.fulfill(response=response)
            else:
                route.continue_()

        page.route("**/api/status", delay_initial_status)
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#lab-tab-current")).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page.locator("#lab-tab-current")).to_have_text("learner1")
        expect(page).to_have_url(self.base_url + "/labs/" + key)
        page.get_by_role("tab", name="Overview", exact=True).click()
        expect(page).to_have_url(self.base_url + "/")
        page.reload()
        expect(page.get_by_role("tab", name="Overview", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )

    def test_overview_reload_never_paints_the_instructors_running_lab(self) -> None:
        """Restoring Overview must not expose the own-Lab workspace first."""
        page = self.signed_in_as("instructor1", "instructor")
        key = self.start_group_lab(page, "instructor1")
        page.goto(self.base_url + "/labs/" + key)
        expect(page.locator("#lab-tab-current")).to_have_attribute(
            "aria-selected", "true"
        )
        page.get_by_role("tab", name="Overview", exact=True).click()
        expect(page).to_have_url(self.base_url + "/")
        page.add_init_script(
            """
            window.unexpectedWorkspaceFrames = [];
            function sampleWorkspace() {
                const app = document.getElementById('app');
                const main = document.getElementById('main');
                if (app && !app.classList.contains('hidden') && main
                    && getComputedStyle(main).visibility !== 'hidden'
                    && main.getBoundingClientRect().height > 100) {
                    window.unexpectedWorkspaceFrames.push(performance.now());
                }
                requestAnimationFrame(sampleWorkspace);
            }
            requestAnimationFrame(sampleWorkspace);
        """
        )

        def slow_dashboard(route: Route) -> None:
            response = route.fetch()
            time.sleep(0.2)
            route.fulfill(response=response)

        page.route("**/api/admin/dashboard", slow_dashboard)
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("tab", name="Overview", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        self.assertEqual(page.evaluate("window.unexpectedWorkspaceFrames"), [])

    def test_overview_resize_preserves_the_hidden_canvas_until_return(self) -> None:
        """Overview resizes must not feed zero or negative dimensions to D3."""
        page = self.signed_in_as("instructor1", "instructor")
        key = self.start_group_lab(page, "instructor1")
        page.goto(self.base_url + "/labs/" + key)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "() => Number(document.getElementById('topology').getAttribute('height')) > 100"
        )
        height = page.locator("#topology").get_attribute("height")
        errors = []
        page.on(
            "console",
            lambda message: (
                errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.get_by_role("tab", name="Overview", exact=True).click()
        page.set_viewport_size({"width": 1000, "height": 700})
        page.evaluate(
            """() => new Promise(resolve => {
            requestAnimationFrame(() => requestAnimationFrame(resolve));
        })"""
        )
        self.assertEqual(errors, [])
        expect(page.locator("#topology")).to_have_attribute("height", height)
        page.get_by_role("tab", name="instructor1", exact=True).click()
        page.wait_for_function(
            "() => Number(document.getElementById('topology').getAttribute('height')) > 100"
        )
        expect(page.locator(".topology-container")).to_be_visible()
        expect(page).to_have_url(self.base_url + "/labs/" + key)

    def test_overview_reload_does_not_fetch_topology_without_a_lab_key(self) -> None:
        """Network Tools wait for a chosen Lab before fetching their nodes."""
        page = self.signed_in_as("instructor1", "instructor")
        self.start_group_lab(page, "instructor1")
        requests = []
        page.on(
            "request",
            lambda request: (
                requests.append(request.url)
                if "/api/labs/-/topology" in request.url
                else None
            ),
        )
        page.reload()
        page.wait_for_load_state("networkidle")
        self.assertEqual(requests, [])
