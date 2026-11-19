"""Browser tests for Multi-Lab Mode: the Group page and the Instructor's dashboard.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn
server in Multi-Lab Mode with three accounts, Labs starting as fake
processes, driven by headless Chromium over real HTTP.  What a Learner sees
as their Group, and what the Instructor sees of the class, is what the
tests read off the page.
"""

import os
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from mniplab.persistent_config import ConfigCopies
from mniplab.tests.browser import LiveServerCase, Page, expect
from mniplab.tests.test_web_multi_lab import FakeLauncher
from mniplab.webserver import auth, registry
from mniplab.webserver.config import settings
from mniplab.webserver.groups import Groups
from mniplab.webserver.repositories import GuideRepository

pytestmark = pytest.mark.browser_classroom


class MultiLabBrowserCase(LiveServerCase):
    """A live server in Multi-Lab Mode, its Labs faked, its Assignments in a temp file."""

    USERS = "learner1:lab12345:learner,learner2:lab54321:learner,instructor1:lab12345:instructor"

    def setUp(self) -> None:
        super().setUp()
        patches = [
            patch.object(settings, "lab_mode", "multi"),
            patch.object(auth.settings, "users", self.USERS),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous_groups = registry.groups
        registry.groups = Groups(os.path.join(self.tmp.name, "assignments.json"))
        self.addCleanup(setattr, registry, "groups", previous_groups)
        previous_copies = registry.config_copies
        registry.config_copies = ConfigCopies(
            os.path.join(self.tmp.name, "group-config")
        )
        self.addCleanup(setattr, registry, "config_copies", previous_copies)
        self.launcher = FakeLauncher()
        launcher = patch.object(registry.lab_service, "launcher", self.launcher)
        launcher.start()
        self.addCleanup(launcher.stop)
        registry.lab_service.forget_all()
        self.addCleanup(registry.lab_service.forget_all)

    def select_group(self, page: Page, name: str) -> None:
        """Find and open a Group through the navigation combobox."""
        picker = page.get_by_role("combobox", name="Select Group", exact=True)
        picker.fill(name)
        page.locator("#group-navigation-select-options").get_by_role(
            "option", name=name, exact=True
        ).click()

    def signed_in_as(self, username: str, role: str):
        """A page signed in as a named account, so the Group is that login."""
        from mniplab.webserver.auth import active_sessions, create_session

        assert self.browser is not None
        active_sessions.clear()
        session_id = create_session(username, role)
        context = self.browser.new_context()
        context.add_cookies(
            [{"name": "session", "value": session_id, "url": self.base_url}]
        )
        page = context.new_page()
        page.goto(self.base_url + "/")
        page.wait_for_selector("#app:not(.hidden)")
        self.addCleanup(context.close)
        return page


class GroupPageTests(MultiLabBrowserCase):
    """A Learner's page is their Group's: its Assignment, its Lab, its Terminals."""

    def test_a_group_starts_and_stops_its_own_lab_from_its_assignment(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("learner1", "learner")

        expect(page.locator("#workspace-title")).to_have_text("Labs / learner1")
        expect(page.locator("#group-badge")).to_have_count(0)
        # The account menu names the login in Multi-Lab Mode too.
        expect(page.locator("#account-menu-btn")).to_contain_text("learner1")
        page.wait_for_selector("#start-screen-cards .start-screen-card")
        names = page.locator("#start-screen-cards .catalog-card-name").all_inner_texts()
        self.assertEqual(len(names), 1)
        self.assertIn("OSPF", names[0])

        page.locator("#start-screen-cards .start-screen-start").click()
        page.wait_for_selector("#stop-lab-btn:not([style*='display: none'])")
        self.assertEqual(page.locator("#lab-name").inner_text(), "OSPF Routing Lab")
        # The Lab follows the Group in the breadcrumb and is the current step.
        steps = page.get_by_role("navigation", name="Breadcrumb").get_by_role(
            "listitem"
        )
        self.assertEqual(
            [text for text in steps.all_inner_texts() if text != "/"],
            ["Mininet-IPLab", "Labs / learner1", "OSPF Routing Lab"],
        )
        expect(page.locator("#lab-crumb")).to_have_attribute("aria-current", "page")
        # The Lab Prompt is the Instructor's: a Learner is offered no way in.
        self.assertFalse(page.locator("#lab-prompt-btn").is_visible())
        self.assertEqual(len(self.launcher.children), 1)
        self.assertEqual(registry.lab_service.labs()[0].group, "learner1")

        page.locator("#stop-lab-btn").click()
        page.locator("#confirm-ok").click()
        page.wait_for_selector("#start-screen-cards .start-screen-card")
        self.assertTrue(self.launcher.children[0].stopped)
        self.assertEqual(page.locator("#lab-name").inner_text(), "No lab running")
        # With no Lab the step is gone and the title carries the marker again.
        expect(page.locator("#lab-crumb")).to_be_hidden()
        expect(page.locator("#workspace-title").locator("xpath=..")).to_have_attribute(
            "aria-current", "page"
        )

    def test_the_lab_key_is_in_the_page_url_and_survives_a_reload(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("learner1", "learner")

        # No Lab yet: the URL is the root.
        self.assertEqual(urlparse(page.url).path, "/")

        page.wait_for_selector("#start-screen-cards .start-screen-card")
        page.locator("#start-screen-cards .start-screen-start").click()
        page.wait_for_selector("#stop-lab-btn:not([style*='display: none'])")
        key = self.launcher.children[0].key
        self.assertEqual(urlparse(page.url).path, f"/labs/{key}")

        # A reload lands on the same URL and reconnects to the same Lab.
        page.reload()
        page.wait_for_selector("#stop-lab-btn:not([style*='display: none'])")
        expect(page.locator("#lab-name")).to_have_text("OSPF Routing Lab")
        self.assertEqual(urlparse(page.url).path, f"/labs/{key}")
        self.assertEqual(page.locator("#lab-name").inner_text(), "OSPF Routing Lab")

        # Navigating straight to the Lab's URL in a fresh tab lands on it too.
        fresh = page.context.new_page()
        fresh.goto(f"{self.base_url}/labs/{key}")
        fresh.wait_for_selector("#stop-lab-btn:not([style*='display: none'])")
        expect(fresh.locator("#lab-name")).to_have_text("OSPF Routing Lab")
        self.assertEqual(urlparse(fresh.url).path, f"/labs/{key}")
        self.assertEqual(fresh.locator("#lab-name").inner_text(), "OSPF Routing Lab")
        fresh.close()

        # Stopping returns the URL to the root.
        page.locator("#stop-lab-btn").click()
        page.locator("#confirm-ok").click()
        page.wait_for_selector("#start-screen-cards .start-screen-card")
        self.assertEqual(urlparse(page.url).path, "/")

    def test_the_group_page_says_whether_the_next_start_resumes_or_seeds(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab", "static-lab", "dns-lab"])
        os.makedirs(
            os.path.join(registry.config_copies.copy_dir("learner1", "ospf-lab"), "r1")
        )
        page: Page = self.signed_in_as("learner1", "learner")
        page.wait_for_selector("#start-screen-cards .start-screen-card")

        rows = {
            row.locator(".catalog-card-name").inner_text(): row
            for row in page.locator("#start-screen-cards .start-screen-card").all()
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

        # "Start clean" is confirmed, then discards the copy and starts.
        ospf.locator(".start-screen-start-clean").click()
        page.wait_for_selector("#confirm-modal", state="visible")
        message = page.locator("#confirm-modal-message").inner_text()
        # The dialog names what is lost in the note's words, not the mechanism's.
        self.assertIn("your Group's saved Router Configs for OSPF Routing Lab", message)
        self.assertIn("seed", message)
        page.locator("#confirm-ok").click()
        page.wait_for_selector("#stop-lab-btn:not([style*='display: none'])")
        self.assertEqual(self.launcher.children[0].example, "ospf-lab")
        self.assertFalse(registry.config_copies.exists("learner1", "ospf-lab"))


class InstructorDashboardTests(MultiLabBrowserCase):
    """The Instructor sees every Group, edits Assignments, and stops any Lab."""

    def test_dashboard_lists_groups_and_the_assignment_editor_is_a_dialog(self) -> None:
        registry.groups.assign("learner2", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")

        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        self.assertEqual(
            page.locator("#instructor-dashboard .group-summary").count(), 3
        )
        self.assertEqual(
            page.locator("#instructor-dashboard-cap").inner_text(),
            f"0 of {settings.lab_cap} Labs alive",
        )

        # The Lab can be Running while learner2 has no signed-in Sessions.
        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner2']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner2']")
        self.assertEqual(
            page.locator(
                "article[aria-label='Group learner2'] .activity-dot"
            ).get_attribute("class"),
            "activity-dot activity-dot-active",
        )

        # The Assignment editor is a labelled dialog; focus lands inside; Escape returns it.
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='assign'][data-group='learner1']").focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#assignment-modal", state="visible")
        modal = page.locator("#assignment-modal")
        self.assertEqual(modal.get_attribute("role"), "dialog")
        self.assertEqual(
            modal.get_attribute("aria-labelledby"), "assignment-modal-title"
        )
        self.assertEqual(
            page.locator("#assignment-modal-title").inner_text(),
            "Assignment for learner1",
        )
        self.assertTrue(
            page.evaluate(
                "() => document.getElementById('assignment-modal').contains(document.activeElement)"
            )
        )
        page.locator("#assignment-list input[value='static-lab']").check()
        page.locator("#assignment-save").click()
        page.wait_for_selector("#assignment-modal", state="hidden")
        self.assertEqual(registry.groups.assignment("learner1"), ["static-lab"])
        self.assertEqual(
            page.evaluate("() => document.activeElement.dataset.action"), "assign"
        )

        # Stop learner2's Lab from the dashboard.
        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        page.locator("button[data-action='stop'][data-group='learner2']").click()
        page.locator("#confirm-ok").click()
        page.wait_for_selector("button[data-action='start'][data-group='learner2']")
        self.assertTrue(self.launcher.children[0].stopped)

    def test_lab_prompt_shortcut_opens_the_groups_terminal_tab(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        expect(page.locator("#instructor-dashboard")).to_be_visible()
        self.assertEqual(
            page.locator("button[data-action='prompt'][data-group='learner1']").count(),
            0,
        )
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.get_by_role("button", name="Start Lab for learner1", exact=True).click()
        prompt = page.get_by_role(
            "button", name="Open Lab Prompt of learner1", exact=True
        )
        expect(prompt).to_be_visible()
        key1 = self.launcher.children[0].key
        sockets = []
        page.on("websocket", lambda socket: sockets.append(urlparse(socket.url).path))

        # Keyboard activation leaves Overview for the explicitly named Group.
        prompt.focus()
        with page.expect_websocket(
            lambda socket: urlparse(socket.url).path == f"/ws/labs/{key1}/prompt"
        ):
            page.keyboard.press("Enter")
        expect(page.locator("#lab-tab-current")).to_have_text("learner1")
        expect(page.locator("#lab-tab-current")).to_have_attribute(
            "aria-selected", "true"
        )
        page.wait_for_url(self.base_url + f"/labs/{key1}")
        tab = page.locator("#tab-lab-prompt")
        expect(tab).to_have_attribute("aria-selected", "true")
        expect(tab).to_be_focused()
        expect(page.locator("#lab-prompt-modal")).to_have_count(0)
        page.wait_for_function(
            "document.querySelector('#terminal-lab-prompt .xterm-screen') !== null"
        )
        self.assertIn(f"/ws/labs/{key1}/prompt", sockets)

        # The normal header button and Overview shortcut reuse that same tab.
        page.get_by_role("button", name="Open Lab Prompt", exact=True).click()
        expect(page.locator("#tab-lab-prompt")).to_have_count(1)
        page.get_by_role("tab", name="Overview", exact=True).click()
        prompt.click()
        expect(page.locator("#tab-lab-prompt")).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page.locator("#tab-lab-prompt")).to_have_count(1)
        self.assertEqual(sockets.count(f"/ws/labs/{key1}/prompt"), 1)

        # Switching to another Group must bind before opening its prompt.
        page.get_by_role("tab", name="Overview", exact=True).click()
        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        page.get_by_role("button", name="Start Lab for learner2", exact=True).click()
        expect(
            page.get_by_role("button", name="Open Lab Prompt of learner2", exact=True)
        ).to_be_visible()
        key2 = next(
            lab.key for lab in registry.lab_service.labs() if lab.group == "learner2"
        )
        with page.expect_websocket(
            lambda socket: urlparse(socket.url).path == f"/ws/labs/{key2}/prompt"
        ):
            page.get_by_role(
                "button", name="Open Lab Prompt of learner2", exact=True
            ).click()
        page.wait_for_url(self.base_url + f"/labs/{key2}")
        expect(page.locator("#lab-tab-current")).to_have_text("learner2")
        expect(page.locator("#tab-lab-prompt")).to_have_attribute(
            "aria-selected", "true"
        )
        page.wait_for_function(
            "document.querySelector('#terminal-lab-prompt .xterm-screen') !== null"
        )
        self.assertIn(f"/ws/labs/{key2}/prompt", sockets)
        self.assertEqual(len(self.launcher.children), 2)

    def test_a_delayed_prompt_shortcut_does_not_override_later_navigation(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        result = page.request.post(
            self.base_url + "/api/labs",
            data={"group": "learner1", "example_id": "ospf-lab"},
        )
        self.assertTrue(result.ok)
        key = result.json()["key"]
        page.reload()
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        held = []
        page.route(f"**/api/labs/{key}", lambda route: held.append(route))
        page.get_by_role("button", name="Open Lab Prompt of learner1", exact=True).click()
        expect(page.locator("#lab-tab-current")).to_have_attribute(
            "aria-selected", "true"
        )
        page.get_by_role("tab", name="Overview", exact=True).click()
        self.assertEqual(len(held), 1)
        held[0].fulfill(
            json={"key": key, "example": "ospf-lab", "running": True, "nodes": 2}
        )
        page.wait_for_function(
            "url => performance.getEntriesByName(url).length > 0",
            arg=self.base_url + f"/api/labs/{key}",
        )
        page.unroute(f"**/api/labs/{key}")
        expect(page.locator("#lab-tab-overview")).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page.locator("#tab-lab-prompt")).to_have_count(0)
        self.assertEqual(urlparse(page.url).path, "/")

    def test_a_prompt_shortcut_for_a_stopped_lab_opens_no_prompt(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        result = page.request.post(
            self.base_url + "/api/labs",
            data={"group": "learner1", "example_id": "ospf-lab"},
        )
        self.assertTrue(result.ok)
        key = result.json()["key"]
        page.reload()
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        expect(
            page.get_by_role("button", name="Open Lab Prompt of learner1", exact=True)
        ).to_be_visible()
        self.assertTrue(page.request.post(self.base_url + f"/api/labs/{key}/stop").ok)
        page.get_by_role("button", name="Open Lab Prompt of learner1", exact=True).click()
        expect(page.locator("#lab-prompt-modal")).to_have_count(0)
        expect(page.locator("#tab-lab-prompt")).to_have_count(0)
        expect(page.locator("#lab-prompt-btn")).to_be_hidden()

    def test_a_reset_from_the_dialog_discards_only_that_groups_copy(self) -> None:
        """The named row resets that Group alone, and says so when it is done."""
        registry.groups.assign("learner1", ["ospf-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        for group in ("learner1", "learner2"):
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir(group, "ospf-lab"), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        ).click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        row = page.locator(
            "#config-reset-modal button[data-action='reset'][data-example='ospf-lab']"
        )
        self.assertEqual(row.inner_text(), "Reset ospf-lab")
        self.assertEqual(
            row.get_attribute("aria-label"), "Reset ospf-lab for learner1"
        )
        row.click()
        page.wait_for_selector("#confirm-modal", state="visible")
        page.locator("#confirm-ok").click()

        page.wait_for_function(
            "() => document.getElementById('instructor-dashboard-status')"
            ".textContent.includes('reset to the seed')"
        )
        self.assertFalse(registry.config_copies.exists("learner1", "ospf-lab"))
        self.assertTrue(registry.config_copies.exists("learner2", "ospf-lab"))

    def test_a_group_card_carries_one_reset_control_however_many_copies(self) -> None:
        """One ``Reset Config…`` per card, never one button per Config Copy.

        A Group that has worked through four assigned Lab Examples holds
        four Config Copies.  Rendering a reset per Copy puts four controls
        side by side that read alike, so the Instructor picks between them
        by guessing and a wrong guess discards another Lab's saved work.
        """
        examples = ["ospf-lab", "bgp-lab", "static-lab", "bgp-path-lab"]
        registry.groups.assign("learner1", examples)
        for example in examples:
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir("learner1", example), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")

        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        card = page.locator("article[aria-label='Group learner1']")
        opener = card.locator("button[data-action='reset-config']")
        self.assertEqual(opener.count(), 1)
        self.assertEqual(opener.inner_text(), "Reset Router Config…")
        # The Copies are reached through the dialog, never from the card.
        self.assertEqual(card.locator("button[data-action='reset']").count(), 0)

    def test_the_group_reset_dialog_names_each_lab_example_in_view(self) -> None:
        """Each row names its Lab Example in visible text, not only in aria."""
        examples = ["ospf-lab", "bgp-lab"]
        registry.groups.assign("learner1", examples)
        for example in examples:
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir("learner1", example), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        ).click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        rows = page.locator("#config-reset-modal button[data-action='reset']")
        self.assertEqual(rows.count(), 2)
        self.assertEqual(
            sorted(rows.nth(i).inner_text() for i in range(rows.count())),
            ["Reset bgp-lab", "Reset ospf-lab"],
        )

    def test_a_row_whose_lab_is_alive_is_disabled_with_its_reason(self) -> None:
        """A reset that must fail is refused up front, not after a confirm.

        The Lab on that Lab Example holds the Config Copy open, so the
        server refuses the reset.  Offering the row, taking a confirmation
        for an irreversible act and only then reporting the refusal makes
        the confirmation theatre.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        os.makedirs(
            os.path.join(registry.config_copies.copy_dir("learner1", "ospf-lab"), "r1")
        )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner1']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner1']")

        page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        ).click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        row = page.locator(
            "#config-reset-modal button[data-action='reset'][data-example='ospf-lab']"
        )
        self.assertTrue(row.is_disabled())
        self.assertIn(
            "stop it first",
            page.locator("#config-reset-modal .config-reset-reason").inner_text(),
        )

    def test_the_reset_rows_read_as_destructive_and_carry_no_markers(self) -> None:
        """A row that discards saved work never looks like a benign control.

        The rows sit in a list, so the default markers would render beside
        them; and a reset that renders like Assign… gives the Instructor no
        signal that this one cannot be undone.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        os.makedirs(
            os.path.join(registry.config_copies.copy_dir("learner1", "ospf-lab"), "r1")
        )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        ).click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        markers = page.locator("#config-reset-list").evaluate(
            "el => getComputedStyle(el).listStyleType"
        )
        self.assertEqual(markers, "none")

        row_colour = page.locator(
            "#config-reset-modal button[data-action='reset']"
        ).first.evaluate("el => getComputedStyle(el).color")
        benign_colour = page.locator(
            "button[data-action='assign'][data-group='learner1']"
        ).evaluate("el => getComputedStyle(el).color")
        self.assertNotEqual(row_colour, benign_colour)

    def test_a_reset_row_leaves_the_open_dialog_once_its_copy_is_gone(self) -> None:
        """The dialog reflects the reset without being closed and reopened.

        A row still offering a Config Copy that no longer exists invites a
        second reset of nothing, and tells the Instructor the first one did
        not work.
        """
        examples = ["ospf-lab", "bgp-lab"]
        registry.groups.assign("learner1", examples)
        for example in examples:
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir("learner1", example), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        ).click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        page.locator(
            "#config-reset-modal button[data-action='reset'][data-example='ospf-lab']"
        ).click()
        page.wait_for_selector("#confirm-modal", state="visible")
        page.locator("#confirm-ok").click()
        # The status is announced before the refresh that re-renders the
        # dialog, so wait on the list rather than racing it.
        page.wait_for_function(
            "() => document.querySelectorAll("
            "'#config-reset-modal button[data-action=\\'reset\\']').length === 1"
        )

        # Still open, and now showing only the Copy that survives.
        rows = page.locator("#config-reset-modal button[data-action='reset']")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first.inner_text(), "Reset bgp-lab")

    def test_the_class_wide_dialog_lists_only_examples_a_copy_exists_of(self) -> None:
        """Driven by the Config Copies that exist, not the catalogue's flag.

        ``bgp-lab`` declares Persistent Config but no Group has started it,
        so there is nothing to reset and it has no row.  Listing every
        Lab Example that *could* hold a Copy is what put eleven identical
        controls on the old Catalog.
        """
        registry.groups.assign("learner1", ["ospf-lab", "bgp-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        for group in ("learner1", "learner2"):
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir(group, "ospf-lab"), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")

        page.locator("#overview-config-reset").click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        rows = page.locator("#config-reset-modal button[data-action='reset-all']")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first.inner_text(), "Reset ospf-lab")
        self.assertEqual(
            page.locator(
                "#config-reset-modal button[data-example='bgp-lab']"
            ).count(),
            0,
        )

    def test_a_class_wide_reset_discards_every_groups_copy(self) -> None:
        """One row, every Group's Copy of that Lab Example, in one act."""
        registry.groups.assign("learner1", ["ospf-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        for group in ("learner1", "learner2"):
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir(group, "ospf-lab"), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.locator("#overview-config-reset").click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        page.locator(
            "#config-reset-modal button[data-action='reset-all'][data-example='ospf-lab']"
        ).click()
        page.wait_for_selector("#confirm-modal", state="visible")
        page.locator("#confirm-ok").click()
        page.wait_for_function(
            "() => document.getElementById('instructor-dashboard-status')"
            ".textContent.includes('seed')"
        )

        self.assertFalse(registry.config_copies.exists("learner1", "ospf-lab"))
        self.assertFalse(registry.config_copies.exists("learner2", "ospf-lab"))

    def test_a_class_wide_row_is_blocked_by_any_one_live_lab(self) -> None:
        """One Group's Lab blocks the whole row, and the row says whose.

        A class-wide reset is all or nothing: the server refuses it, with
        nothing reset, while any named Group's Lab on that Lab Example is
        alive.  So the Instructor cannot reset the other Groups by pressing
        on, and a row that stayed enabled would take a confirmation for an
        act that cannot happen.  Naming the Group is what makes the refusal
        actionable — there is nothing on the row to tell the Instructor
        which of the Groups to go and stop.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        for group in ("learner1", "learner2"):
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir(group, "ospf-lab"), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner2']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner2']")

        page.locator("#overview-config-reset").click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        row = page.locator(
            "#config-reset-modal button[data-action='reset-all'][data-example='ospf-lab']"
        )
        self.assertTrue(row.is_disabled())
        reason = page.locator("#config-reset-modal .config-reset-reason").inner_text()
        self.assertIn("learner2", reason)
        self.assertIn("stop it first", reason)

    def test_a_class_wide_row_names_every_group_holding_it_open(self) -> None:
        """Two live Labs, both named, and the sentence agrees with them.

        Naming only the first would send the Instructor back for a second
        refusal, so every Group holding the row open is listed.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        for group in ("learner1", "learner2"):
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir(group, "ospf-lab"), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        for group in ("learner1", "learner2"):
            page.get_by_role("button", name=f"Inspect Group {group}", exact=True).click()
            page.locator(f"button[data-action='start'][data-group='{group}']").click()
            page.wait_for_selector(f"button[data-action='stop'][data-group='{group}']")

        page.locator("#overview-config-reset").click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        reason = page.locator("#config-reset-modal .config-reset-reason").inner_text()
        self.assertIn("learner1", reason)
        self.assertIn("learner2", reason)
        self.assertIn("run this Lab", reason)
        self.assertIn("stop them first", reason)

    def test_the_class_wide_dialog_drops_a_row_once_no_copy_remains(self) -> None:
        """The class dialog refreshes like the per-Group one, not on reopen."""
        registry.groups.assign("learner1", ["ospf-lab", "bgp-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        os.makedirs(
            os.path.join(registry.config_copies.copy_dir("learner1", "bgp-lab"), "r1")
        )
        for group in ("learner1", "learner2"):
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir(group, "ospf-lab"), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.locator("#overview-config-reset").click()
        page.wait_for_selector("#config-reset-modal", state="visible")
        self.assertEqual(
            page.locator("#config-reset-modal button[data-action='reset-all']").count(), 2
        )

        page.locator(
            "#config-reset-modal button[data-action='reset-all'][data-example='ospf-lab']"
        ).click()
        page.wait_for_selector("#confirm-modal", state="visible")
        page.locator("#confirm-ok").click()
        page.wait_for_function(
            "() => document.querySelectorAll("
            "'#config-reset-modal button[data-action=\\'reset-all\\']').length === 1"
        )

        rows = page.locator("#config-reset-modal button[data-action='reset-all']")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first.inner_text(), "Reset bgp-lab")

    def test_the_dialog_reports_its_own_reset(self) -> None:
        """What happened is readable without closing the dialog.

        The dashboard's status line sits behind the overlay, so a reset
        driven from the dialog would otherwise report into a region the
        Instructor cannot see.
        """
        examples = ["ospf-lab", "bgp-lab"]
        registry.groups.assign("learner1", examples)
        for example in examples:
            os.makedirs(
                os.path.join(registry.config_copies.copy_dir("learner1", example), "r1")
            )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        ).click()
        page.wait_for_selector("#config-reset-modal", state="visible")
        page.locator(
            "#config-reset-modal button[data-action='reset'][data-example='ospf-lab']"
        ).click()
        page.wait_for_selector("#confirm-modal", state="visible")
        page.locator("#confirm-ok").click()

        page.wait_for_function(
            "() => document.getElementById('config-reset-status')"
            ".textContent.includes('ospf-lab')"
        )
        self.assertIn(
            "seed", page.locator("#config-reset-status").inner_text()
        )

    def test_the_class_wide_opener_names_its_scope_on_its_face(self) -> None:
        """The header's opener never reads the same as a card's.

        Both open the same dialog at different scopes.  Sharing a label
        leaves the scope readable only after the dialog is open, which is
        the confusion the naming rule exists to prevent.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        os.makedirs(
            os.path.join(registry.config_copies.copy_dir("learner1", "ospf-lab"), "r1")
        )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")

        header = page.locator("#overview-config-reset")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        card = page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        )
        self.assertEqual(header.inner_text(), "Reset every Group's Router Config…")
        self.assertNotEqual(header.inner_text(), card.inner_text())

    def test_the_reset_dialog_leaves_the_dashboard_legible_behind_it(self) -> None:
        """No blur behind this dialog: the class state is the context for it.

        A reset changes the cards behind — a Group loses its opener when its
        last Config Copy goes — and the Instructor should be able to read
        that happening rather than watch a blurred rectangle.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        os.makedirs(
            os.path.join(registry.config_copies.copy_dir("learner1", "ospf-lab"), "r1")
        )
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator(
            "button[data-action='reset-config'][data-group='learner1']"
        ).click()
        page.wait_for_selector("#config-reset-modal", state="visible")

        blur = page.locator("#config-reset-modal").evaluate(
            "el => getComputedStyle(el).backdropFilter"
        )
        self.assertEqual(blur, "none")

    def test_the_assignment_editor_carries_the_facts_and_the_guide(self) -> None:
        """The one listing shows what the Catalog used to: facts and a Guide.

        With no Catalog surface this is where an Instructor compares Lab
        Examples, so the protocols, node count and Guide belong on the row
        being chosen rather than on a listing somewhere else.
        """
        registry.groups.assign("learner1", [])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='assign'][data-group='learner1']").click()
        page.wait_for_selector("#assignment-modal", state="visible")

        row = page.locator("#assignment-list li", has_text="OSPF Routing Lab")
        meta = row.locator(".assignment-example-meta")
        self.assertIn("OSPF", meta.inner_text())
        self.assertIn("18 nodes", meta.inner_text())
        # The name and the facts must not run together into "LabBGP".
        self.assertEqual(
            meta.evaluate("el => getComputedStyle(el).display"), "block"
        )

        # The Guide control rides on a row that ships one, addressed by its
        # Lab Example id since several names share a prefix.
        guide = page.locator(
            "#assignment-list button[data-action='guide'][data-example='static-lab']"
        )
        # Secondary to the Lab Example's name, not louder than it.
        guide_size = guide.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
        body_size = row.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
        self.assertLess(guide_size, body_size)

    def test_the_running_example_chip_opens_that_examples_guide(self) -> None:
        """A Lab Example with a Guide links to its actual readable content.

        With no Catalog to scroll to, the name on the card is the closest
        thing to hand when the Instructor wants to know what a Group is
        working on.
        """
        registry.groups.assign("learner1", ["static-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner1']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner1']")

        chip = page.locator(
            "article[aria-label='Group learner1'] a.group-card-lab"
        )
        self.assertEqual(chip.inner_text(), "Static Routing Lab")
        href = chip.get_attribute("href")
        assert href is not None
        self.assertIn("/guide?example=static-lab", href)
        self.assertEqual(chip.get_attribute("target"), "_blank")
        with page.expect_popup() as opened:
            chip.click()
        guide = opened.value
        expect(guide.locator("#guide-content")).to_contain_text("What you will learn")
        guide.close()

    def test_no_catalog_surface_remains(self) -> None:
        """One listing of Lab Examples for the Instructor: the Assignment editor.

        A second listing duplicated the surface the Instructor already
        chooses from, and carried a column of reset controls that named
        nothing.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")

        self.assertEqual(page.locator("#instructor-dashboard-browse").count(), 0)
        self.assertEqual(page.locator("#catalog-modal").count(), 0)
        self.assertEqual(page.locator("#overview-subtab-catalog").count(), 0)
        self.assertEqual(page.locator("#overview-catalog-list").count(), 0)

    def test_the_assignment_editor_filters_as_the_instructor_types(self) -> None:
        """Search narrows the one listing, and never hides a ticked choice.

        This is the only listing of Lab Examples and it grows with the
        catalogue, so assigning one means scrolling past every other.  A
        filter that hid an already-ticked row would hide a decision the
        Instructor cannot see they have made.
        """
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='assign'][data-group='learner1']").click()
        page.wait_for_selector("#assignment-modal", state="visible")

        rows = page.locator("#assignment-list li")
        everything = rows.count()
        self.assertGreater(everything, 5)

        page.locator("#assignment-search").fill("path selection")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#assignment-list li')]"
            ".filter(li => li.offsetParent !== null).length === 2"
        )
        shown = [
            rows.nth(i).inner_text()
            for i in range(rows.count())
            if rows.nth(i).is_visible()
        ]
        # The match, and the ticked ospf-lab which the filter must not hide.
        self.assertTrue(any("Path Selection" in row for row in shown), shown)
        self.assertTrue(any("OSPF Routing Lab" in row for row in shown), shown)

        page.locator("#assignment-search").fill("")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#assignment-list li')]"
            f".filter(li => li.offsetParent !== null).length === {everything}"
        )

    def test_open_guide_renders_beside_the_list(self) -> None:
        """The Guide reads in the editor, not in a window away from it.

        The Instructor is choosing between Lab Examples; sending them to
        another tab to read one costs the comparison they were making.
        """
        registry.groups.assign("learner1", [])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='assign'][data-group='learner1']").click()
        page.wait_for_selector("#assignment-modal", state="visible")

        page.locator(
            "#assignment-list button[data-action='guide'][data-example='static-lab']"
        ).click()
        page.wait_for_selector("#assignment-guide-body h1, #assignment-guide-body h2")

        pane = page.locator("#assignment-guide")
        self.assertTrue(pane.is_visible())
        # The Guide is the thing being read, so it is not the narrower half.
        pane_box = pane.bounding_box()
        chooser_box = page.locator("#assignment-modal .assignment-fieldset").bounding_box()
        assert pane_box is not None and chooser_box is not None
        self.assertGreaterEqual(pane_box["width"], chooser_box["width"])
        self.assertIn(
            "static-lab", page.locator("#assignment-guide-title").inner_text()
        )
        # Rendered markdown, not the raw source.
        self.assertGreater(page.locator("#assignment-guide-body p").count(), 0)

    def test_open_guide_appears_only_where_a_guide_exists(self) -> None:
        """No control that leads nowhere.

        An Example without a guide must not offer Open Guide: that
        promises teaching material that is not there.
        """
        registry.groups.assign("learner1", [])
        shipped = GuideRepository.available

        def without_ospf_guide(example, *args, **kwargs):
            # Every shipped Example has a Guide now; pretend one has none.
            return False if example == "ospf-lab" else shipped(example, *args, **kwargs)

        patcher = patch.object(GuideRepository, "available", side_effect=without_ospf_guide)
        patcher.start()
        self.addCleanup(patcher.stop)
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='assign'][data-group='learner1']").click()
        page.wait_for_selector("#assignment-modal", state="visible")

        # static-lab ships examples/guides/static-lab.md; ospf-lab is made to have none.
        self.assertEqual(
            page.locator(
                "#assignment-list button[data-action='guide'][data-example='static-lab']"
            ).count(),
            1,
        )
        self.assertEqual(
            page.locator(
                "#assignment-list button[data-action='guide'][data-example='ospf-lab']"
            ).count(),
            0,
        )

    def test_the_guide_pane_closes_and_hands_focus_back(self) -> None:
        """The pane can be dismissed without closing the whole editor.

        Reading a Guide is a detour inside choosing; leaving it open is not
        the only way back, and the keyboard must return to where it left.
        """
        registry.groups.assign("learner1", [])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='assign'][data-group='learner1']").click()
        page.wait_for_selector("#assignment-modal", state="visible")

        opener = page.locator(
            "#assignment-list button[data-action='guide'][data-example='static-lab']"
        )
        opener.click()
        page.wait_for_selector("#assignment-guide-body h1, #assignment-guide-body h2")
        wide = page.locator("#assignment-modal .modal-content").bounding_box()

        page.locator("#assignment-guide-close").click()
        page.wait_for_selector("#assignment-guide", state="hidden")

        # The chooser has the modal to itself again.
        narrow = page.locator("#assignment-modal .modal-content").bounding_box()
        assert wide is not None and narrow is not None
        self.assertLess(narrow["width"], wide["width"])
        # Focus returns to the control that opened the pane.
        self.assertEqual(
            page.evaluate("() => document.activeElement.dataset.example"), "static-lab"
        )

    def test_group_cards_show_lab_status_independent_of_presence(self) -> None:
        """A Lab's status stays clear even when its Group has no Sessions."""
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")

        expect(page.locator(".group-summary")).to_have_count(3)
        page.get_by_role("button", name="Inspect Group instructor1", exact=True).click()

        # Both signed-in and absent Groups have stopped Labs initially.
        instructor = page.locator("#instructor-dashboard .group-card")
        self.assertEqual(
            instructor.locator(".activity-dot").get_attribute("class"),
            "activity-dot activity-dot-stopped",
        )
        self.assertEqual(
            instructor.locator(".activity-dot").get_attribute("aria-label"),
            "Lab stopped",
        )
        self.assertEqual(
            instructor.locator(".group-card-summary").inner_text(), "stopped"
        )
        for learner in ("learner1", "learner2"):
            page.get_by_role(
                "button", name=f"Inspect Group {learner}", exact=True
            ).click()
            self.assertEqual(
                page.locator("#instructor-dashboard .group-card")
                .locator(".activity-dot")
                .get_attribute("class"),
                "activity-dot activity-dot-stopped",
            )
            self.assertEqual(
                page.locator("#instructor-dashboard .group-card")
                .locator(".group-card-summary")
                .inner_text(),
                "stopped",
            )

        # Starting a Lab shows Running while presence remains No sessions.
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner1']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner1']")
        card = page.locator("article[aria-label='Group learner1']")
        self.assertEqual(
            card.locator(".activity-dot").get_attribute("class"),
            "activity-dot activity-dot-active",
        )
        self.assertRegex(
            card.locator(".group-card-summary").inner_text(), r" · \d+ terminals?$"
        )

    def test_legacy_idle_activity_does_not_change_the_lab_status(self) -> None:
        """Legacy connection activity does not masquerade as learner input."""
        threshold = patch.object(settings, "activity_idle_threshold", -1)
        threshold.start()
        self.addCleanup(threshold.stop)
        registry.groups.assign("instructor1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group instructor1", exact=True).click()
        page.locator("button[data-action='start'][data-group='instructor1']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='instructor1']")

        card = page.locator("article[aria-label='Group instructor1']")
        summary = card.locator(".group-card-summary").inner_text()
        self.assertIn("Running", summary)
        self.assertNotIn("idle", summary)
        expect(card.locator(".activity-dot")).to_have_attribute(
            "aria-label", "Lab running"
        )
        self.assertRegex(summary, r" · \d+ terminals?$")

    def test_a_running_labs_card_shows_its_key_to_match_host_tools(self) -> None:
        registry.groups.assign("learner2", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        card = page.locator("article[aria-label='Group learner2']")
        self.assertEqual(card.locator(".group-card-key").count(), 0)

        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner2']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner2']")

        # The token `ovs-vsctl show` and `docker ps` print on the host.
        key = self.launcher.children[0].key
        self.assertEqual(card.locator(".group-card-key").inner_text(), f"Lab key {key}")
        self.assertEqual(card.locator(".group-card-key code").inner_text(), key)


class OverviewSubtabsKeyboardTests(MultiLabBrowserCase):
    """Arrow keys move the Overview subtab strip, per the ARIA Tabs pattern:
    real key presses, not clicks — WCAG 2.2 AA operability for the subtabs."""

    def test_keyboard_opens_and_closes_group_details(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        group = page.get_by_role("button", name="Inspect Group learner1", exact=True)
        group.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#overview-group-details")).to_be_visible()
        expect(group).to_have_attribute("aria-expanded", "true")
        page.get_by_role("button", name="Close Group details", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.locator("#overview-group-details")).not_to_be_visible()
        expect(group).to_be_focused()
        expect(group).to_have_attribute("aria-expanded", "false")


class SingleLabModeUnchangedTests(LiveServerCase):
    """With the mode unset, no Group or Assignment control is rendered."""

    def test_neither_group_badge_nor_dashboard_appears(self) -> None:
        with self.signed_in_page(role="instructor") as page:
            expect(page.locator("#group-badge")).to_have_count(0)
            self.assertEqual(
                page.evaluate(
                    "() => document.getElementById('instructor-dashboard').style.display"
                ),
                "none",
            )
            expect(page.locator("#workspace-monitor")).not_to_be_visible()
            page.locator("#account-menu-btn").click()
            expect(page.locator("#workspace-monitor-btn")).to_be_visible()


class OverviewControlHeightTests(MultiLabBrowserCase):
    """Every control in a row on the Class Overview is the same 32px tall."""

    HEIGHTS = """sel => [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null)
        .map(e => [e.getAttribute('aria-label') || e.textContent.trim(), e.getBoundingClientRect().height])"""

    def test_group_card_and_overview_controls_are_32px(self) -> None:
        registry.groups.assign("learner2", ["static-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        expect(page.get_by_role("button", name="Start Lab for learner2", exact=True)).to_be_visible()
        controls = page.evaluate(self.HEIGHTS, ", ".join((
            ".group-card button", ".group-card select",
            ".instructor-panel-header button", ".classroom-filters input",
            ".classroom-filters select", ".group-navigation-controls input",
            "#overview-details-close",
        )))
        self.assertGreaterEqual(len(controls), 8)
        for name, height in controls:
            with self.subTest(control=name):
                self.assertAlmostEqual(height, 32, delta=0.5)


class LabTabstripTests(MultiLabBrowserCase):
    """Instructor Group navigation and the Learner's own Group binding."""

    def test_the_instructor_sees_bounded_tabs_and_all_groups_in_selector(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        expect(page.get_by_role("tablist", name="Labs", exact=True)).to_be_visible()
        expect(page.locator("#group-badge")).to_have_count(0)
        expect(page.locator("#lab-tabstrip .lab-tab")).to_have_count(2)
        self.assertEqual(
            page.locator("#lab-tabstrip .lab-tab:visible").all_inner_texts(),
            ["Overview"],
        )
        page.get_by_role("combobox", name="Select Group", exact=True).click()
        choices = page.locator("#group-navigation-select-options [role=option]")
        self.assertEqual(choices.count(), 3)
        for name in ("instructor1", "learner1", "learner2"):
            expect(choices.filter(has_text=name)).to_have_count(1)
        expect(page.locator("#lab-tab-overview")).to_have_attribute(
            "aria-selected", "true"
        )

    def test_selecting_a_group_tab_hides_the_listing_and_binds_to_its_lab(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner1']").click()
        page.wait_for_selector("button[data-action='stop'][data-group='learner1']")
        key = self.launcher.children[0].key

        self.select_group(page, "learner1")
        page.wait_for_function(f"() => location.pathname === '/labs/{key}'")
        self.assertEqual(
            page.evaluate(
                "() => document.getElementById('instructor-dashboard').style.display"
            ),
            "none",
        )
        self.assertEqual(page.locator("#lab-name").inner_text(), "OSPF Routing Lab")

    def test_the_switch_button_on_a_card_selects_the_groups_tab(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.wait_for_selector("#instructor-dashboard .group-summary:visible")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("button[data-action='start'][data-group='learner1']").click()
        page.wait_for_selector(
            "button[data-action='switch-tab'][data-group='learner1']"
        )
        key = self.launcher.children[0].key

        page.locator("button[data-action='switch-tab'][data-group='learner1']").click()
        page.wait_for_function(f"() => location.pathname === '/labs/{key}'")
        self.assertEqual(
            page.evaluate(
                "() => document.getElementById('instructor-dashboard').style.display"
            ),
            "none",
        )
        self.assertEqual(page.locator("#lab-name").inner_text(), "OSPF Routing Lab")

    def test_selecting_overview_shows_the_listing_again(self) -> None:
        """Overview brings the class-wide listing back after a Group tab hid it."""
        page: Page = self.signed_in_as("instructor1", "instructor")
        page.request.post(
            self.base_url + "/api/labs",
            data={"example_id": "static-lab", "group": "instructor1"},
        )
        page.reload()
        self.select_group(page, "instructor1")
        self.assertEqual(
            page.evaluate(
                "() => document.getElementById('instructor-dashboard').style.display"
            ),
            "none",
        )

        page.locator("#lab-tabstrip .lab-tab", has_text="Overview").click()
        self.assertEqual(
            page.evaluate(
                "() => document.getElementById('instructor-dashboard').style.display"
            ),
            "",
        )
        self.assertTrue(page.locator("#instructor-dashboard").is_visible())

    def test_the_learner_sees_their_group_and_login_without_group_navigation(self) -> None:
        registry.groups.assign("learner1", ["ospf-lab"])
        page: Page = self.signed_in_as("learner1", "learner")
        expect(page.locator("#workspace-title")).to_have_text("Labs / learner1")
        expect(page.locator("#lab-tabstrip")).not_to_be_visible()
        expect(page.locator("#group-badge")).to_have_count(0)
        # The breadcrumb names the Group; the account menu names the login,
        # as it does in Single-Lab Mode.
        expect(page.locator("#account-menu-btn")).to_contain_text("learner1")
        self.assertEqual(page.locator("#app > header").inner_text().count("learner1"), 2)

    def test_arrow_keys_move_the_lab_tabstrip_selection(self) -> None:
        """Real key presses, not clicks — the ARIA Tabs pattern the tabstrip
        implements (roving tabindex, ArrowLeft/ArrowRight) actually works."""
        page: Page = self.signed_in_as("instructor1", "instructor")
        response = page.request.post(
            self.base_url + "/api/labs",
            data={"example_id": "static-lab", "group": "instructor1"},
        )
        page.goto(self.base_url + "/labs/" + response.json()["key"])
        page.locator("#lab-tab-overview").click()
        tab_ids = page.locator("#lab-tabstrip .lab-tab").evaluate_all(
            "els => els.map(el => el.id)"
        )
        self.assertGreaterEqual(len(tab_ids), 2)
        self.assertEqual(tab_ids[0], "lab-tab-overview")

        page.locator(f"#{tab_ids[0]}").focus()
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.evaluate("() => document.activeElement.id"), tab_ids[1])
        self.assertEqual(
            page.locator(f"#{tab_ids[1]}").get_attribute("aria-selected"), "true"
        )
        self.assertEqual(
            page.locator(f"#{tab_ids[0]}").get_attribute("aria-selected"), "false"
        )
        self.assertEqual(page.locator(f"#{tab_ids[0]}").get_attribute("tabindex"), "-1")
        # Selecting a Group tab via the keyboard hides the Overview panel,
        # exactly as a click does.
        self.assertEqual(
            page.evaluate(
                "() => document.getElementById('instructor-dashboard').style.display"
            ),
            "none",
        )

        # ArrowLeft from the first Group tab wraps back to the last tab.
        page.locator(f"#{tab_ids[0]}").focus()
        page.keyboard.press("ArrowLeft")
        self.assertEqual(page.evaluate("() => document.activeElement.id"), tab_ids[-1])
        self.assertEqual(
            page.locator(f"#{tab_ids[-1]}").get_attribute("aria-selected"), "true"
        )
        self.assertEqual(page.locator(f"#{tab_ids[-1]}").get_attribute("tabindex"), "0")

    def test_own_lab_link_selects_its_workspace_and_root_keeps_overview(self) -> None:
        """A direct own-Lab link reveals its controls without changing root login."""
        page: Page = self.signed_in_as("instructor1", "instructor")
        response = page.request.post(
            self.base_url + "/api/labs",
            data={"example_id": "static-lab", "group": "instructor1"},
        )
        self.assertEqual(response.status, 200)
        key = response.json()["key"]
        page.goto(f"{self.base_url}/labs/{key}")
        own = page.get_by_role("tab", name="instructor1", exact=True)
        overview = page.get_by_role("tab", name="Overview", exact=True)
        expect(own).to_have_attribute("aria-selected", "true")
        expect(own).to_have_attribute("tabindex", "0")
        expect(overview).to_have_attribute("aria-selected", "false")
        expect(page.locator("#workspace-title")).to_have_text("Labs / instructor1")
        expect(page.locator("#lab-crumb")).to_have_text("Static Routing Lab")
        expect(page.locator("#lab-crumb")).to_have_attribute("aria-current", "page")
        expect(
            page.get_by_role("img", name="Network topology visualization")
        ).to_be_visible()
        page.reload()
        expect(own).to_have_attribute("aria-selected", "true")
        page.goto(self.base_url + "/")
        expect(overview).to_have_attribute("aria-selected", "true")
        expect(page.locator("#workspace-title")).to_have_text("Class Overview")
        # The Overview is not a Lab: no Lab step, the title is the current page.
        expect(page.locator("#lab-crumb")).to_be_hidden()
        expect(page.locator("#lab-crumb")).not_to_have_attribute("aria-current", "page")
        expect(page.locator("#workspace-title").locator("xpath=..")).to_have_attribute(
            "aria-current", "page"
        )


class SingleLabModeTabstripTests(LiveServerCase):
    """In Single-Lab Mode the strip stays hidden."""

    def test_the_tabstrip_stays_hidden(self) -> None:
        with self.signed_in_page(role="instructor") as page:
            self.assertFalse(page.locator("#lab-tabstrip").is_visible())


class SessionsAndTerminalsTests(MultiLabBrowserCase):
    """The Sessions & Terminals subtab: every signed-in Session and every
    open Terminal, each with its own force-out control."""

    def test_group_sessions_are_identifiable_and_confirmed_logout_ends_the_target(
        self,
    ) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        token = auth.create_session("learner1", "learner")
        auth.active_sessions[token]["nickname"] = "Sari"
        page.reload()
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        session = page.locator(
            f'#overview-connection-details details[data-session-id="{token}"]'
        )
        expect(session).to_contain_text("Sari")
        expect(session).to_contain_text(auth.active_sessions[token]["connection_code"])
        session.locator("summary").click()
        session.get_by_role("button", name="Sign out Session", exact=False).click()
        expect(page.locator("#confirm-modal")).to_be_visible()
        page.locator("#confirm-ok").click()
        expect(session).to_have_count(0)
        self.assertNotIn(token, auth.active_sessions)

    def test_the_terminals_list_shows_open_terminals_and_close_ends_them(self) -> None:
        import time
        from unittest.mock import AsyncMock
        from mniplab.webserver.services import Terminal, TerminalService

        term = Terminal(
            id="fake-term-1",
            node="r1",
            client_id="browser-client",
            username="learner1",
            lab_key="fake-lab",
            terminal_fd=999999,
            child_pid=1,
            created_at=time.time(),
            last_activity=time.time(),
        )
        registry.terminal_service.terminals["fake-term-1"] = term
        registry.terminal_service.session_terminals["browser-client"].add("fake-term-1")
        reap = patch.object(TerminalService, "_reap_child", new=AsyncMock())
        reap.start()
        self.addCleanup(reap.stop)

        page: Page = self.signed_in_as("instructor1", "instructor")
        token = auth.create_session("learner1", "learner")
        term.client_id = token
        page.reload()
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        page.locator("#overview-connection-details .connection-session summary").click()
        row = page.locator("#overview-connection-details .terminal-row")
        expect(row.locator("td").first).to_have_text("r1")
        row.get_by_role("button", name="Close Terminal", exact=False).click()
        page.wait_for_selector("#confirm-modal", state="visible")
        page.locator("#confirm-ok").click()
        page.wait_for_selector(
            "#overview-connection-details .terminal-row", state="detached"
        )
        self.assertNotIn("fake-term-1", registry.terminal_service.terminals)

    def test_lab_choices_and_preview_cards_show_names_without_changing_ids(self) -> None:
        """Display catalog and draft names while preserving operational IDs."""
        page: Page = self.signed_in_as("instructor1", "instructor")
        dashboard = page.request.get(self.base_url + "/api/admin/dashboard").json()
        dashboard["example_names"] = {
            "ospf-lab": "OSPF Routing Lab",
            "authored-lesson": "Published <BGP> & Routes",
            "preview-draft": "Preview BGP Lesson",
        }
        for group in dashboard["groups"]:
            if group["name"] == "learner1":
                group["assignment"] = ["ospf-lab", "authored-lesson", "missing-lesson"]
            if group["name"] == "instructor1":
                group["lab"] = {"key": "abcd", "example": "preview-draft"}
        page.route("**/api/admin/dashboard", lambda route: route.fulfill(json=dashboard))
        page.evaluate("""async () => {
            const { refreshInstructorDashboard } = await import('/static/js/lab/instructor-dashboard.js');
            await refreshInstructorDashboard();
        }""")
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        self.assertEqual(page.locator("#dashboard-start-learner1 option").all_text_contents(), [
            "OSPF Routing Lab", "Published <BGP> & Routes", "missing-lesson"
        ])
        page.locator("#dashboard-start-learner1").select_option("authored-lesson")
        self.assertEqual(page.locator("#dashboard-start-learner1").input_value(), "authored-lesson")
        page.get_by_role("button", name="Inspect Group instructor1", exact=True).click()
        guide = page.locator("article[data-group='instructor1'] .group-card-lab")
        self.assertEqual(guide.text_content(), "Preview BGP Lesson")
        href = guide.get_attribute("href")
        assert href is not None
        self.assertIn("example=preview-draft", href)
        page.evaluate("""async () => {
            const { rememberLab } = await import('/static/js/lab/lifecycle.js');
            rememberLab({running: true, key: 'abcd', example: 'authored-lesson',
                example_name: 'Published <BGP> & Routes'});
        }""")
        self.assertEqual(page.locator("#lab-name").text_content(), "Published <BGP> & Routes")
        self.assertEqual(page.evaluate("""async () => {
            const { getCurrentLabName } = await import('/static/js/utils/dom.js');
            return getCurrentLabName();
        }"""), "authored-lesson")
