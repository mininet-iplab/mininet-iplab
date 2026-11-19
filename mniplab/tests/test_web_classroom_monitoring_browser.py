"""Classroom monitoring stays compact and identifies targeted connections."""

import time
from unittest.mock import patch

import pytest

from mniplab.tests.browser import LiveServerCase, expect, choose_account_item
from mniplab.tests.authoring_editors import audit
from mniplab.tests.fakes import FakeLab, FakeNode
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import auth, registry
from mniplab.webserver.services import Terminal
from mniplab.webserver.config import settings

pytestmark = pytest.mark.browser_classroom


class ClassroomMonitoringTests(MultiLabBrowserCase):
    """Real UI behavior with the classroom's 40 Sessions and 80 Terminals."""

    USERS = "instructor1:lab12345:instructor," + ",".join(
        f"group{i}:lab12345:learner" for i in range(8)
    )

    def test_overview_reflows_and_preserves_focus_during_refresh(self) -> None:
        """Summary rows survive polls and remain usable across themes/widths."""
        page = self.signed_in_as("instructor1", "instructor")
        group = page.get_by_role("button", name="Inspect Group group2", exact=True)
        group.focus()
        original = group.element_handle()
        page.evaluate(
            """async () => {
            const { refreshInstructorDashboard } = await import('/static/js/lab/instructor-dashboard.js');
            await refreshInstructorDashboard();
        }"""
        )
        self.assertTrue(
            group.evaluate("(node, original) => node === original", original)
        )
        expect(group).to_be_focused()
        audit(self, page, "instructor-groups", check_focus=True)

    def test_group_search_keeps_normal_text_caret_keyboard_behavior(self) -> None:
        """The tab-strip arrow handler does not intercept search-field keys."""
        page = self.signed_in_as("instructor1", "instructor")
        search = page.get_by_role("combobox", name="Select Group", exact=True)
        search.fill("goup")
        search.press("Home")
        search.press("ArrowRight")
        search.press("r")
        expect(search).to_have_value("group")

    def test_group_picker_search_select_cancel_and_refresh(self) -> None:
        """One control finds a Group without navigating on unfinished text."""
        page = self.signed_in_as("instructor1", "instructor")
        picker = page.get_by_role("combobox", name="Select Group", exact=True)
        expect(page.get_by_label("Find Group", exact=True)).to_have_count(0)
        picker.fill("group2")
        expect(
            page.locator("#group-navigation-select-options").get_by_role("option")
        ).to_have_count(1)
        page.evaluate(
            """async () => {
            await (await import('/static/js/lab/instructor-dashboard.js')).refreshInstructorDashboard();
        }"""
        )
        expect(picker).to_have_value("group2")
        picker.press("ArrowDown")
        picker.press("Enter")
        expect(page.locator("#overview-group-heading")).to_have_text("Group group2")
        picker.fill("missing-group")
        expect(
            page.locator("#group-navigation-select-options").get_by_role("option")
        ).to_have_count(0)
        expect(
            page.locator(".group-navigation-controls").get_by_text(
                "No Groups found.", exact=True
            )
        ).to_be_visible()
        picker.press("Escape")
        expect(picker).to_have_value("group2")
        expect(picker).to_have_attribute("aria-expanded", "false")
        picker.fill("group3")
        picker.press("Tab")
        expect(picker).to_have_value("group2")
        expect(page.locator("#overview-group-heading")).to_have_text("Group group2")

    def test_overview_filters_one_group_or_all_and_combines_presence(self) -> None:
        """Group choice filters rows without opening details or losing other filters."""
        page = self.signed_in_as("instructor1", "instructor")
        picker = page.get_by_role("combobox", name="Filter Groups", exact=True)
        rows = page.locator(".group-summary:visible")
        expect(picker).to_have_value("All Groups")
        expect(rows).to_have_count(9)
        page.get_by_role("button", name="Inspect Group group1", exact=True).click()
        expect(page.locator("#overview-group-details")).to_be_visible()
        picker.fill("group2")
        expect(rows).to_have_count(9)
        picker.press("Enter")
        expect(rows).to_have_count(1)
        expect(rows).to_have_attribute("data-group-name", "group2")
        expect(page.locator("#overview-group-details")).to_be_hidden()
        page.get_by_label("Presence", exact=True).select_option("signed-in")
        expect(rows).to_have_count(0)
        expect(page.locator("#overview-no-matches")).to_be_visible()
        page.get_by_label("Presence", exact=True).select_option("")
        page.evaluate(
            """async () => {
            await (await import('/static/js/lab/instructor-dashboard.js')).refreshInstructorDashboard();
        }"""
        )
        expect(picker).to_have_value("group2")
        expect(rows).to_have_count(1)
        picker.fill("All Groups")
        picker.press("Enter")
        expect(rows).to_have_count(9)

    def test_group_picker_popups_reflow_with_visible_keyboard_focus(self) -> None:
        """Both editable selectors remain readable in each theme and at text zoom."""
        page = self.signed_in_as("instructor1", "instructor")
        for label in ("Select Group", "Filter Groups"):
            picker = page.get_by_role("combobox", name=label, exact=True)
            picker.fill("group")
            picker.press("ArrowUp")
            active = picker.get_attribute("aria-activedescendant")
            self.assertIsNotNone(active)
            expect(page.locator("#" + active)).to_have_accessible_name("group7")
            audit(
                self,
                page,
                "group-picker-" + label.lower().replace(" ", "-"),
                check_focus=True,
            )
            picker.press("Escape")

    def test_overview_separates_lab_status_presence_and_learner_input(self) -> None:
        """A running Lab need not have learners, and presence is not typing."""
        page = self.signed_in_as("instructor1", "instructor")
        registry.groups.assign("group1", ["static-lab"])
        response = page.request.post(
            self.base_url + "/api/labs",
            data={
                "group": "group1",
                "example_id": "static-lab",
            },
        )
        self.assertTrue(response.ok)
        page.reload()
        for heading in (
            "Group",
            "Lab",
            "Lab status",
            "Sessions",
            "Terminals",
            "Last learner input",
        ):
            expect(
                page.get_by_role("columnheader", name=heading, exact=True)
            ).to_have_count(1)
        first = page.locator(".group-summary").filter(
            has=page.get_by_role("button", name="Inspect Group group1", exact=True)
        )
        second = page.locator(".group-summary").filter(
            has=page.get_by_role("button", name="Inspect Group group2", exact=True)
        )
        expect(first.get_by_role("cell", name="Running", exact=True)).to_be_visible()
        expect(
            first.get_by_role("cell", name="No sessions", exact=True)
        ).to_be_visible()
        expect(second.get_by_role("cell", name="Stopped", exact=True)).to_be_visible()
        token = auth.create_session("group1", "learner")
        page.evaluate(
            """async () => {
            await (await import('/static/js/lab/instructor-dashboard.js')).refreshInstructorDashboard();
        }"""
        )
        expect(first).to_contain_text("1 signed in")
        expect(first).to_contain_text("No input yet")
        auth.record_terminal_input(token)
        page.evaluate(
            """async () => {
            await (await import('/static/js/lab/instructor-dashboard.js')).refreshInstructorDashboard();
        }"""
        )
        expect(first.locator(".summary-input")).to_contain_text("ago")
        page.get_by_label("Lab status", exact=True).select_option("stopped")
        expect(first).to_be_hidden()
        expect(second).to_be_visible()
        page.get_by_label("Lab status", exact=True).select_option("")
        page.get_by_label("Presence", exact=True).select_option("signed-in")
        expect(first).to_be_visible()
        expect(second).to_be_hidden()
        page.get_by_role("button", name="Inspect Group group1", exact=True).click()
        page.get_by_role("button", name="Close Group details", exact=True).click()
        expect(
            page.get_by_role("button", name="Inspect Group group1", exact=True)
        ).to_be_focused()

    def test_latest_group_selection_ignores_a_delayed_lab_response(self) -> None:
        """A slow Lab response cannot change the selected Group's target."""
        page = self.signed_in_as("instructor1", "instructor")
        keys = {}
        for group in ("group1", "group2"):
            registry.groups.assign(group, ["static-lab"])
            response = page.request.post(
                self.base_url + "/api/labs",
                data={"group": group, "example_id": "static-lab"},
            )
            self.assertTrue(response.ok)
            keys[group] = response.json()["key"]
        page.reload()
        self.select_group(page, "group1")
        expect(page).to_have_url(self.base_url + "/labs/" + keys["group1"])
        delayed = []
        page.route("**/api/labs/" + keys["group2"], lambda route: delayed.append(route))
        self.select_group(page, "group2")
        expect(page.locator("#lab-tab-current")).to_have_text("group2")
        self.select_group(page, "group1")
        self.assertEqual(len(delayed), 1)
        delayed[0].continue_()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#lab-tab-current")).to_have_text("group1")
        expect(page).to_have_url(self.base_url + "/labs/" + keys["group1"])
        choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
        expect(page.locator("#monitor-scope")).to_contain_text("group1")

    def test_stopped_selected_lab_returns_to_group_overview(self) -> None:
        """A stopped Lab loses its tab, URL and workspace binding."""
        page = self.signed_in_as("instructor1", "instructor")
        registry.groups.assign("group1", ["static-lab"])
        response = page.request.post(
            self.base_url + "/api/labs",
            data={"group": "group1", "example_id": "static-lab"},
        )
        self.assertTrue(response.ok)
        key = response.json()["key"]
        page.reload()
        self.select_group(page, "group1")
        expect(page).to_have_url(self.base_url + "/labs/" + key)
        self.assertTrue(
            page.request.post(self.base_url + "/api/labs/" + key + "/stop").ok
        )
        page.evaluate(
            """async () => {
            const { refreshInstructorDashboard } = await import('/static/js/lab/instructor-dashboard.js');
            await refreshInstructorDashboard();
        }"""
        )
        expect(page.locator("#lab-tab-current")).to_be_hidden()
        expect(page.locator("#lab-tab-overview")).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page.locator("#overview-group-heading")).to_have_text("Group group1")
        expect(page).to_have_url(self.base_url + "/")
        self.assertIsNone(
            page.evaluate(
                """async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            return appState.boundGroup;
        }"""
            )
        )

    def test_class_summary_expands_only_the_selected_groups_connections(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        page.set_viewport_size({"width": 1440, "height": 787})
        cap = patch.object(settings, "lab_cap", 8)
        cap.start()
        self.addCleanup(cap.stop)
        previous = registry.terminal_service.terminals
        registry.terminal_service.terminals = {}
        self.addCleanup(setattr, registry.terminal_service, "terminals", previous)
        for group in range(8):
            registry.groups.assign(f"group{group}", ["static-lab"])
            response = page.request.post(
                self.base_url + "/api/labs",
                data={
                    "group": f"group{group}",
                    "example_id": "static-lab",
                },
            )
            self.assertTrue(response.ok)
            lab_key = response.json()["key"]
            for index in range(5):
                token = auth.create_session(f"group{group}", "learner")
                auth.active_sessions[token]["nickname"] = f"Learner {index}"
                for node in ("h1", "r1"):
                    key = f"{group}-{index}-{node}"
                    registry.terminal_service.terminals[key] = Terminal(
                        id=key,
                        node=node,
                        client_id=token,
                        username=f"group{group}",
                        lab_key=lab_key,
                        terminal_fd=999999,
                        child_pid=999999,
                        created_at=time.time(),
                        last_activity=time.time(),
                    )
        page.reload()
        expect(page.locator(".group-summary")).to_have_count(9)
        summary = page.locator('.group-summary[data-group-name="group2"]')
        expect(summary.locator(".summary-sessions")).to_have_text("5 signed in")
        expect(summary.locator(".summary-terminals")).to_have_text("10")
        classroom = page.get_by_role("group", name="Classroom totals", exact=True)
        expect(classroom).to_be_visible()
        expect(classroom).to_contain_text("8 running Labs")
        expect(classroom).to_contain_text("40 learner Sessions")
        expect(classroom).to_contain_text("80 active Terminals")
        expect(page.locator("#status")).to_be_hidden()
        page.evaluate(
            """async () => {
            await (await import('/static/js/lab/lifecycle.js')).updateStatus();
        }"""
        )
        expect(classroom).to_contain_text("80 active Terminals")
        picker = page.get_by_role("combobox", name="Filter Groups", exact=True)
        picker.fill("group2")
        picker.press("Enter")
        expect(classroom).to_contain_text("40 learner Sessions")
        expect(classroom).to_contain_text("80 active Terminals")
        picker.fill("All Groups")
        picker.press("Enter")
        expect(page.locator(".connection-session:visible")).to_have_count(0)
        page.get_by_role("button", name="Inspect Group group2", exact=True).click()
        expect(
            page.locator("#overview-connection-details .connection-session")
        ).to_have_count(5)
        expect(
            page.locator("#overview-connection-details .terminal-row:visible")
        ).to_have_count(0)
        session = page.locator("#overview-connection-details .connection-session").first
        session.locator("summary").click()
        expect(session.locator(".terminal-row")).to_have_count(2)
        expect(page.locator("#overview-group-summary")).to_have_text(
            "5 Sessions · 10 Terminals"
        )
        heading = page.locator("#overview-group-heading")
        header_top = heading.bounding_box()["y"]
        session.locator("summary").focus()
        page.keyboard.press("PageDown")
        page.wait_for_function(
            "() => document.getElementById('overview-group-details-body').scrollTop > 0"
        )
        page.wait_for_timeout(250)  # Let Chromium's keyboard scroll animation settle.
        self.assertAlmostEqual(heading.bounding_box()["y"], header_top, delta=1)
        expect(
            page.get_by_role("button", name="Close Group details", exact=True)
        ).to_be_in_viewport()
        body = page.locator("#overview-group-details-body")
        scroll = body.evaluate("node => node.scrollTop")
        page.evaluate(
            """async () => {
            await (await import('/static/js/lab/instructor-dashboard.js')).refreshInstructorDashboard();
        }"""
        )
        self.assertAlmostEqual(body.evaluate("node => node.scrollTop"), scroll, delta=1)
        expect(session).to_have_attribute("open", "")
        code = session.locator(".connection-code").inner_text()
        expect(session).to_contain_text("Learner 0")
        session.get_by_role("button", name="Sign out Session", exact=False).click()
        expect(page.locator("#confirm-modal-message")).to_contain_text(code)
        expect(page.locator("#confirm-modal-message")).to_contain_text("2 Terminals")
        page.keyboard.press("Escape")
        expect(
            session.get_by_role("button", name="Sign out Session", exact=False)
        ).to_be_focused()
        page.get_by_role("button", name="Inspect Group group3", exact=True).click()
        self.assertEqual(body.evaluate("node => node.scrollTop"), 0)
        session.locator("summary").press("Enter")
        audit(self, page, "group-details", check_focus=True)
        self.select_group(page, "group1")
        expect(classroom).to_be_hidden()
        expect(page.locator("#status")).to_be_visible()
        expect(page.locator("#terminal-count")).to_have_text("10")
        page.get_by_role("tab", name="Overview", exact=True).click()
        expect(classroom).to_be_visible()
        expect(page.locator("#status")).to_be_hidden()
        registry.terminal_service.terminals.pop("0-0-h1")
        page.evaluate("""async () => {
            await (await import('/static/js/lab/instructor-dashboard.js')).refreshInstructorDashboard();
        }""")
        expect(classroom).to_contain_text("79 active Terminals")

    def test_selected_lab_monitoring_excludes_other_groups(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        for group in ("group1", "group2"):
            registry.groups.assign(group, ["static-lab"])
            response = page.request.post(
                self.base_url + "/api/labs",
                data={
                    "group": group,
                    "example_id": "static-lab",
                },
            )
            self.assertTrue(response.ok)
            token = auth.create_session(group, "learner")
            auth.active_sessions[token]["nickname"] = f"Person in {group}"
        page.reload()
        self.select_group(page, "group1")
        choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
        expect(page.locator("#monitor-scope")).to_contain_text("group1")
        expect(page.locator("#instructor-connection-list")).to_contain_text(
            "Person in group1"
        )
        expect(page.locator("#instructor-connection-list")).not_to_contain_text(
            "Person in group2"
        )
        self.assertLessEqual(page.locator("#lab-tabstrip [role=tab]").count(), 2)


class SingleLabConnectionTests(LiveServerCase):
    """One shared Lab remains searchable with 40 Learner Sessions."""

    def test_single_lab_connections_are_collapsed_and_searchable_by_code(self) -> None:
        previous_lab = registry.lab_service.lab
        previous_terminals = registry.terminal_service.terminals
        registry.lab_service.lab = FakeLab(FakeNode("r1"), lab_key="fake")
        registry.terminal_service.terminals = {}
        self.addCleanup(setattr, registry.lab_service, "lab", previous_lab)
        self.addCleanup(
            setattr, registry.terminal_service, "terminals", previous_terminals
        )
        with self.signed_in_page() as page:
            target = None
            for index in range(40):
                token = auth.create_session("shared-class", "learner")
                for node in ("h1", "r1"):
                    key = f"{index}-{node}"
                    registry.terminal_service.terminals[key] = Terminal(
                        id=key,
                        node=node,
                        client_id=token,
                        username="shared-class",
                        lab_key="fake",
                        terminal_fd=999999,
                        child_pid=999999,
                        created_at=time.time(),
                        last_activity=time.time(),
                    )
                target = auth.active_sessions[token]["connection_code"]
            page.reload()
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            expect(page.locator("#connection-counts")).to_have_text(
                "41 of 41 Sessions · 80 Terminals"
            )
            expect(page.locator(".terminal-row:visible")).to_have_count(0)
            assert target is not None
            page.get_by_label("Find a Session", exact=True).fill(target)
            expect(
                page.locator("#instructor-connection-list .connection-session")
            ).to_have_count(1)
            page.locator("#instructor-connection-list summary").click()
            expect(page.locator(".terminal-row:visible")).to_have_count(2)
