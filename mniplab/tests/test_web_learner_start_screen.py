"""Browser tests for the Learner's start screen and the consolidated sidebar.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn
server in Multi-Lab Mode with three accounts, Labs starting as fake
processes, driven by headless Chromium over real HTTP.  The Learner's
pre-start surface is the start screen — "No Lab running" plus the Group's
Assignment as cards, the sidebar closed — and the sidebar is a per-Lab
surface, identical for both roles.
"""

import os

import pytest

from mniplab.tests.browser import Page
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_classroom


class LearnerStartScreenTests(MultiLabBrowserCase):
    """With no Lab running, the Learner's tab is the start screen: "No Lab
    running" plus the Group's Assignment as cards, the sidebar closed."""

    def test_the_learner_sees_no_lab_running_plus_the_assignment_cards(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab", "static-lab"])
        page: Page = self.signed_in_as("learner1", "learner")

        page.wait_for_selector("#start-screen:not([style*='display: none'])")
        self.assertEqual(page.locator("#start-screen h2").inner_text(), "No Lab running")
        cards = page.locator("#start-screen-cards .catalog-card")
        self.assertEqual(cards.count(), 2)
        names = [c.locator(".catalog-card-name").inner_text() for c in cards.all()]
        self.assertIn("OSPF Routing Lab", names)
        self.assertIn("Static Routing Lab", names)

        # The start screen replaces the empty state, which is not shown.
        self.assertEqual(
            page.evaluate(
                "() => document.getElementById('topology-empty-state').style.display"
            ),
            "none",
        )

    def test_the_sidebar_is_closed_on_the_start_screen(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("learner1", "learner")
        page.wait_for_selector("#start-screen:not([style*='display: none'])")
        self.assertEqual(
            page.locator(".sidebar").get_attribute("data-sidebar-view"), "closed"
        )

    def test_the_assignment_cards_carry_the_persistent_config_note(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab", "static-lab", "dns-lab"])
        os.makedirs(
            os.path.join(registry.config_copies.copy_dir("learner1", "ospf-lab"), "r1")
        )
        page: Page = self.signed_in_as("learner1", "learner")
        page.wait_for_selector("#start-screen-cards .catalog-card")

        rows = {
            card.locator(".catalog-card-name").inner_text(): card
            for card in page.locator("#start-screen-cards .catalog-card").all()
        }
        ospf, static, dns = (
            rows["OSPF Routing Lab"],
            rows["Static Routing Lab"],
            rows["DNS Lab"],
        )
        self.assertEqual(
            ospf.locator(".sidebar-lab-config").inner_text(),
            "Next start resumes your Group's saved Router Configs.",
        )
        self.assertEqual(ospf.locator(".start-screen-start-clean").count(), 1)
        self.assertEqual(
            static.locator(".sidebar-lab-config").inner_text(),
            "Next start begins from the seed.",
        )
        self.assertEqual(static.locator(".start-screen-start-clean").count(), 0)
        self.assertEqual(dns.locator(".sidebar-lab-config").count(), 0)
        self.assertEqual(dns.locator(".start-screen-start-clean").count(), 0)

    def test_one_click_start_launches_the_lab(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("learner1", "learner")
        page.wait_for_selector("#start-screen-cards .start-screen-start")
        page.locator("#start-screen-cards .start-screen-start").click()
        page.wait_for_selector("#stop-lab-btn:not([style*='display: none'])")
        self.assertEqual(page.locator("#lab-name").inner_text(), "OSPF Routing Lab")
        self.assertEqual(len(self.launcher.children), 1)
        self.assertEqual(
            registry.lab_service.labs()[0].group, "learner1"
        )
        # The start screen is gone; the topology takes its place.
        self.assertEqual(
            page.evaluate("() => document.getElementById('start-screen').style.display"),
            "none",
        )

    def test_after_start_the_learner_sees_the_guide_pane(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("learner1", "learner")
        page.wait_for_selector("#start-screen-cards .start-screen-start")
        page.locator("#start-screen-cards .start-screen-start").click()
        page.wait_for_selector("#stop-lab-btn:not([style*='display: none'])")
        self.assertFalse(page.locator(".sidebar").is_visible())
        self.assertFalse(page.locator("#toggle-sidebar").is_visible())
        self.assertTrue(page.locator("#lab-guide-container").is_visible())


class SidebarConsolidationTests(MultiLabBrowserCase):
    """The sidebar is a per-Lab surface, identical for both roles: Nodes/Guide
    tabs, no Instructor-only content, no Lab Examples listing."""

    def _instructor_on_a_running_group_tab(self, page: Page) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner1']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner1']")
        self.select_group(page, "learner1")
        page.wait_for_function("() => location.pathname.startsWith('/labs/')")
        page.wait_for_selector(".sidebar", state="hidden")

    def test_the_instructors_sidebar_has_no_lab_examples_listing(self) -> None:
        page: Page = self.signed_in_as("instructor1", "instructor")
        self._instructor_on_a_running_group_tab(page)
        self.assertFalse(page.locator(".sidebar").is_visible())

    def test_the_instructors_sidebar_has_no_instructor_only_content(self) -> None:
        page: Page = self.signed_in_as("instructor1", "instructor")
        self._instructor_on_a_running_group_tab(page)
        self.assertEqual(page.locator(".sidebar .connection-session").count(), 0)
        self.assertEqual(
            page.locator(".sidebar #instructor-connection-list").count(), 0
        )
