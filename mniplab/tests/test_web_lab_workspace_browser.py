"""Lab workspace behavior, observed through the production browser UI."""

import os

import pytest

from mniplab.tests.browser import LiveServerCase, box_of, expect, run_async, screenshots, choose_account_item
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.tests.fakes import FakeLab, FakeNode
from mniplab.tests.test_web_terminal_budget import FakeSpawner
from mniplab.webserver import registry
from mniplab.webserver.services import TerminalService
from mniplab.webserver.auth import create_session

pytestmark = pytest.mark.browser_classroom


class FocusedGroupWorkspaceTests(MultiLabBrowserCase):
    """Group selection keeps actions visibly scoped to the selected Group."""

    def test_selected_group_toggles_details_with_mouse_and_keyboard(self) -> None:
        """A second activation closes details; another Group replaces them."""
        page = self.signed_in_as("instructor1", "instructor")
        details = page.locator("#overview-group-details")
        first = page.get_by_role("button", name="Inspect Group learner1", exact=True)
        second = page.get_by_role("button", name="Inspect Group learner2", exact=True)
        for width in (1440, 320):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 900})
                cell = page.locator(
                    '.group-summary[data-group-name="learner1"] .summary-lab'
                )
                cell.click()
                expect(details).to_be_visible()
                expect(first).to_have_attribute("aria-expanded", "true")
                cell.click()
                expect(details).to_be_hidden()
                expect(first).to_have_attribute("aria-expanded", "false")
                expect(first).to_be_focused()
                first.press("Enter")
                expect(details).to_be_visible()
                second.press("Enter")
                expect(page.locator("#overview-group-heading")).to_have_text(
                    "Group learner2"
                )
                expect(first).to_have_attribute("aria-expanded", "false")
                expect(second).to_have_attribute("aria-expanded", "true")
                second.press("Space")
                expect(details).to_be_hidden()
                expect(second).to_be_focused()
                expect(second).to_have_attribute("aria-expanded", "false")
                picker = page.get_by_role("combobox", name="Filter Groups", exact=True)
                picker.fill("learner1")
                picker.press("Enter")
                expect(details).to_be_hidden()
                picker.fill("All Groups")
                picker.press("Enter")

    def test_a_group_summary_opens_details_from_its_lab_cell(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        for width in (1440, 320):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 900})
                row = page.locator('.group-summary[data-group-name="learner2"]')
                row.locator(".summary-lab").click()
                expect(page.locator("#overview-group-details")).to_be_visible()
                expect(row).to_have_attribute("data-selected", "true")
                button = row.get_by_role(
                    "button", name="Inspect Group learner2", exact=True
                )
                expect(button).to_have_attribute("aria-expanded", "true")
                page.get_by_role(
                    "button", name="Close Group details", exact=True
                ).click()
                expect(button).to_be_focused()
                button.press("Enter")
                expect(page.locator("#overview-group-heading")).to_have_text(
                    "Group learner2"
                )
                page.get_by_role(
                    "button", name="Close Group details", exact=True
                ).click()
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth"), width
                )

    def test_hovered_group_text_is_readable_in_both_themes(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        group = page.get_by_role("button", name="Inspect Group learner2", exact=True)
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                page.evaluate(
                    "theme => document.documentElement.dataset.theme = theme", theme
                )
                group.hover()
                page.wait_for_timeout(350)
                colors = group.evaluate(
                    r"""button => [getComputedStyle(button).backgroundColor,
                        ...[button, ...button.querySelectorAll('strong, span')]
                            .map(node => getComputedStyle(node).color)]
                        .map(color => color.match(/\d+/g).map(Number))"""
                )
                luminances = []
                for color in colors:
                    channels = [channel / 255 for channel in color[:3]]
                    linear = [
                        (
                            channel / 12.92
                            if channel <= 0.04045
                            else ((channel + 0.055) / 1.055) ** 2.4
                        )
                        for channel in channels
                    ]
                    luminances.append(
                        sum(
                            c * weight
                            for c, weight in zip(linear, (0.2126, 0.7152, 0.0722))
                        )
                    )
                background = luminances[0]
                for foreground in luminances[1:]:
                    contrast = (max(background, foreground) + 0.05) / (
                        min(background, foreground) + 0.05
                    )
                    self.assertGreaterEqual(contrast, 4.5, f"{theme} Group hover")

    def test_selecting_a_group_shows_its_actions_without_opening_a_lab(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        select = page.get_by_role("button", name="Inspect Group learner2", exact=True)
        select.click()
        expect(page.locator(".group-card:visible")).to_have_count(1)
        expect(page.locator(".group-card:visible")).to_have_attribute(
            "aria-label", "Group learner2"
        )
        expect(page.locator("#lab-tab-overview")).to_have_attribute(
            "aria-selected", "true"
        )
        page.get_by_role("button", name="Edit Assignment of learner2").click()
        expect(page.locator("#assignment-modal-title")).to_have_text(
            "Assignment for learner2"
        )
        expect(page.locator("#assignment-modal")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#assignment-modal")).not_to_be_visible()
        screenshots(page, "lab-workspace", "overview")

    def test_mobile_group_selector_is_usable_without_horizontal_page_overflow(
        self,
    ) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        page.set_viewport_size({"width": 320, "height": 812})
        select = page.get_by_label("Select Group", exact=True)
        self.select_group(page, "learner2")
        expect(page.locator("#overview-group-details")).to_be_visible()
        expect(page.locator("#overview-group-heading")).to_have_text("Group learner2")
        select.focus()
        expect(select).to_be_focused()
        box = select.bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 320)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 320)

    def test_running_group_monitoring_is_scoped_and_overview_retains_class_access(
        self,
    ) -> None:
        registry.groups.assign("learner2", ["static-lab"])
        page = self.signed_in_as("instructor1", "instructor")
        page.get_by_role("button", name="Inspect Group learner2", exact=True).click()
        page.get_by_role("button", name="Start Lab for learner2", exact=True).click()
        page.get_by_role("button", name="Go to the Lab of learner2", exact=True).click()
        expect(page.locator("#topology-canvas-controls")).to_be_visible()
        choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
        expect(page.locator("#topology-canvas-controls")).not_to_be_visible()
        expect(page.locator("#monitor-scope")).to_contain_text("learner2")
        other = create_session("learner1", "learner")
        own = create_session("learner2", "learner")
        expect(
            page.locator(
                f'#instructor-connection-list details[data-session-id="{own}"]'
            )
        ).to_be_visible(timeout=15000)
        expect(
            page.locator(
                f'#instructor-connection-list details[data-session-id="{other}"]'
            )
        ).to_have_count(0)
        page.get_by_role("button", name="Back to topology", exact=True).click()
        expect(page.locator("#account-menu-btn")).to_be_focused()
        page.locator("#lab-tab-overview").click()
        expect(page.locator("#topology-canvas-controls")).not_to_be_visible()
        page.get_by_role("button", name="Inspect Group learner1", exact=True).click()
        session = page.locator(
            f'#overview-connection-details details[data-session-id="{other}"]'
        )
        expect(session).to_be_visible()
        session.locator("summary").click()
        session.get_by_role("button", name="Sign out Session", exact=False).click()
        page.locator("#confirm-ok").click()
        expect(session).to_have_count(0)

    def test_multi_lab_learner_has_nodes_and_guide_without_monitoring(self) -> None:
        page = self.signed_in_as("learner1", "learner")
        expect(page.locator("#workspace-monitor-btn")).not_to_be_visible()
        expect(page.locator("#workspace-monitor")).not_to_be_visible()

    def test_group_switching_fetches_authored_positions_for_each_lab(self) -> None:
        registry.groups.assign("learner1", ["static-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])
        page = self.signed_in_as("instructor1", "instructor")
        page.route(
            "**/api/load_layout?*",
            lambda route: route.fulfill(
                json={
                    "layout": {
                        "nodes": {
                            "r1": {
                                "x": 300 if "static-lab" in route.request.url else 900,
                                "y": 200,
                            },
                            "r2": {"x": 500, "y": 400},
                        }
                    }
                }
            ),
        )
        for group in ("learner1", "learner2"):
            page.get_by_role(
                "button", name=f"Inspect Group {group}", exact=True
            ).click()
            page.get_by_role(
                "button", name=f"Start Lab for {group}", exact=True
            ).click()
            expect(
                page.get_by_role("button", name=f"Stop Lab of {group}", exact=True)
            ).to_be_visible()
        self.select_group(page, "learner1")
        node = page.locator("g.node", has_text="r1")
        expect(node).to_have_attribute("transform", "translate(300,200)")
        node.focus()
        page.keyboard.press("ArrowRight")
        expect(node).to_have_attribute("transform", "translate(320,200)")
        self.select_group(page, "learner2")
        expect(node).to_have_attribute("transform", "translate(900,200)")
        self.select_group(page, "learner1")
        expect(node).to_have_attribute("transform", "translate(300,200)")


class StoppedWorkspaceTests(LiveServerCase):
    """Signed-in Sessions remain available independently of a running Lab."""

    def test_single_lab_instructor_can_inspect_sessions_while_stopped(self) -> None:
        with self.signed_in_page() as page:
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            expect(page.locator("#workspace-monitor")).to_be_visible()
            expect(
                page.locator("#instructor-connection-list .connection-session")
            ).to_be_visible()
            page.get_by_role("button", name="Back to topology", exact=True).click()
            expect(page.locator("#topology-empty-state")).to_be_visible()

    def test_single_lab_learner_cannot_open_monitoring(self) -> None:
        with self.signed_in_page(role="learner") as page:
            expect(page.locator("#workspace-monitor-btn")).not_to_be_visible()
            expect(page.locator("#workspace-monitor")).not_to_be_visible()


class MonitoringWorkspaceTests(LiveServerCase):
    """Monitoring leaves the current Topology and terminal connection intact."""

    def setUp(self) -> None:
        """Attach test Nodes and a real PTY without entering namespaces."""
        super().setUp()
        old_lab = registry.lab_service.lab
        nodes = [FakeNode("r1"), FakeNode("r2")]
        for node in nodes:
            node.pid = os.getpid()
        registry.lab_service.lab = FakeLab(*nodes, lab_key="fake")
        registry.lab_service.lab_example = "static-lab"
        self.addCleanup(setattr, registry.lab_service, "lab", old_lab)
        spawner = FakeSpawner()
        self.addCleanup(spawner.cleanup)
        original = registry.terminal_service
        registry.terminal_service = TerminalService(spawner=spawner)
        self.addCleanup(setattr, registry, "terminal_service", original)

    def test_instructor_stop_clears_nodes_in_both_open_browsers(self) -> None:
        """The shared Lab's old Nodes disappear without a learner reload."""
        with self.signed_in_page(role="learner") as learner:
            expect(learner.locator("svg#topology g.node")).to_have_count(2)
            token = create_session("stop-instructor", "instructor")
            assert self.browser is not None
            context = self.browser.new_context()
            self.addCleanup(context.close)
            context.add_cookies(
                [{"name": "session", "value": token, "url": self.base_url}]
            )
            instructor = context.new_page()
            instructor.goto(self.base_url + "/")
            expect(instructor.locator("svg#topology g.node")).to_have_count(2)
            instructor.get_by_role("button", name="Stop Lab", exact=True).click()
            instructor.locator("#confirm-ok").click()
            expect(learner.locator("#topology-empty-state")).to_be_visible()
            expect(learner.locator("svg#topology g.node")).to_have_count(0)
            expect(instructor.locator("svg#topology g.node")).to_have_count(0)

    def test_delayed_topology_cannot_restore_nodes_after_instructor_stop(self) -> None:
        """An in-flight topology response must not resurrect a stopped Lab."""
        with self.signed_in_page(role="learner") as learner:
            expect(learner.locator("svg#topology g.node")).to_have_count(2)
            topology = learner.request.get(
                self.base_url + "/api/labs/fake/topology"
            ).json()
            pending = []
            learner.route(
                "**/api/labs/fake/topology", lambda route: pending.append(route)
            )
            with learner.expect_request("**/api/labs/fake/topology"):
                learner.evaluate(
                    """() => {
                    window.pendingTopology = import('/static/js/lab/lifecycle.js')
                        .then(module => module.loadTopology());
                }"""
                )
            instructor_token = create_session("stop-instructor", "instructor")
            response = learner.request.post(
                self.base_url + "/api/labs/fake/stop",
                headers={"Cookie": f"session={instructor_token}"},
            )
            self.assertTrue(response.ok)
            expect(learner.locator("#topology-empty-state")).to_be_visible()
            self.assertEqual(len(pending), 1)
            pending[0].fulfill(json=topology)
            learner.evaluate("() => window.pendingTopology")
            expect(learner.locator("svg#topology g.node")).to_have_count(0)

    def test_mobile_terminal_has_scrollable_height_without_covering_topology(
        self,
    ) -> None:
        for role in ("learner", "instructor"):
            with self.subTest(role=role), self.signed_in_page(role=role) as page:
                page.set_viewport_size({"width": 375, "height": 812})
                node = page.locator("g.node", has_text="r1")
                node.focus()
                page.keyboard.press("Enter")
                terminal = page.locator("#terminal-r1")
                expect(terminal.locator(".xterm-helper-textarea")).to_be_visible()
                page.wait_for_timeout(1200)
                main = box_of(page.locator(".main-content"))
                terminal_pane = box_of(page.locator("[data-pane='terminal']"))
                self.assertGreaterEqual(terminal_pane["y"], main["y"] + main["height"])
                self.assertGreaterEqual(
                    box_of(page.locator(".terminal-content"))["height"], 150
                )
                terminal.locator(".xterm-screen").click()
                expect(terminal.locator(".xterm-helper-textarea")).to_be_focused()
                page.keyboard.type("mobile-stacked")
                expect(terminal.locator(".xterm-rows")).to_contain_text("mobile-stacked")
                mounted = terminal.element_handle()
                if role == "instructor":
                    choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
                    expect(terminal).not_to_be_visible()
                    page.locator("#back-to-topology").click()
                self.assertTrue(
                    mounted.evaluate(
                        "el => el === document.getElementById('terminal-r1')"
                    )
                )
                terminal.locator(".xterm-screen").click()
                page.keyboard.type(" mobile-return")
                expect(terminal.locator(".xterm-rows")).to_contain_text("mobile-return")
                screenshots(page, "lab-workspace", f"mobile-terminal-{role}")
                page.locator("#close-all-terminals").click()
                expect(terminal).to_have_count(0)
                expect(page.locator("[data-pane='terminal']")).not_to_be_visible()
                panel_height = page.locator("#lab-tabpanel").evaluate(
                    "el => el.clientHeight"
                )
                visible_height = (
                    box_of(page.locator("#lab-guide-container"))["height"]
                    + box_of(page.locator(".main-content"))["height"]
                )
                self.assertLessEqual(
                    page.locator("#lab-tabpanel").evaluate("el => el.scrollHeight"),
                    max(panel_height, visible_height + 24),
                )

    def test_initial_open_uses_the_labs_layout_before_the_guide_loads(self) -> None:
        with self.signed_in_page() as page:
            guides = []
            page.route("**/api/load_guide?*", lambda route: guides.append(route))
            page.route(
                "**/api/load_layout?*",
                lambda route: route.fulfill(
                    json={
                        "layout": (
                            {
                                "nodes": {
                                    "r1": {"x": 300, "y": 200},
                                    "r2": {"x": 500, "y": 200},
                                }
                            }
                            if "example=static-lab" in route.request.url
                            else None
                        )
                    }
                ),
            )
            page.reload()
            node = page.locator("g.node", has_text="r1")
            expect(node).to_have_attribute("transform", "translate(300,200)")
            self.assertTrue(guides, "Guide response must still be pending")
            expect(page.locator("#lab-name")).to_have_text("Static Routing Lab")
            page.unroute("**/api/load_guide?*")
            for route in guides:
                route.fulfill(content_type="text/plain", body="# Static Lab\n")
            expect(page.locator("#lab-guide-status")).to_have_text("Guide loaded.")
            expect(node).to_be_visible()
            expect(node).to_have_attribute("transform", "translate(300,200)")

    def test_monitoring_round_trip_keeps_positions_zoom_and_live_terminal(self) -> None:
        layout = {"nodes": {"r1": {"x": 300, "y": 200}, "r2": {"x": 500, "y": 200}}}
        registry.lab_service.lab_example = "vlan-lab"
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1440, "height": 900})
            title = page.locator("#lab-name")
            expect(title).to_have_text("VLAN & Inter-VLAN Routing Lab")
            self.assertTrue(
                title.evaluate("node => node.scrollWidth <= node.clientWidth")
            )
            controls = page.locator(".workspace-status-line #topology-canvas-controls")
            expect(controls).to_be_visible()
            page.route(
                "**/api/load_layout?*",
                lambda route: route.fulfill(json={"layout": layout}),
            )
            page.reload()
            node = page.locator("g.node", has_text="r1")
            expect(node).to_have_attribute("transform", "translate(300,200)")
            page.wait_for_timeout(900)
            node.click()
            expect(page.locator("#tab-r1")).to_be_visible()
            # Opening the Terminal pane schedules the existing fit transition.
            page.wait_for_timeout(1200)
            page.locator("#zoom-in").click()
            page.wait_for_timeout(900)
            node.focus()
            page.keyboard.press("ArrowRight")
            positions = page.locator("g.node").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('transform'))"
            )
            transform = page.locator("#topology > g").first.get_attribute("transform")
            terminal = page.locator("#terminal-r1").element_handle()
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            expect(page.locator("#workspace-monitor")).to_be_visible()
            expect(controls).not_to_be_visible()
            expect(
                page.locator("#instructor-connection-list .connection-session")
            ).to_be_visible()
            expect(node).not_to_be_visible()
            expect(page.locator("#terminal-r1")).not_to_be_visible()
            run_async(registry.broadcast_topology_update("fake"))
            page.wait_for_timeout(200)
            page.get_by_role("button", name="Back to topology", exact=True).click()
            expect(controls).to_be_visible()
            expect(node).to_be_visible()
            self.assertEqual(
                page.locator("g.node").evaluate_all(
                    "nodes => nodes.map(node => node.getAttribute('transform'))"
                ),
                positions,
            )
            self.assertEqual(
                page.locator("#topology > g").first.get_attribute("transform"),
                transform,
            )
            self.assertTrue(
                terminal.evaluate(
                    "node => node === document.getElementById('terminal-r1')"
                )
            )
            page.locator("#terminal-r1 .xterm-screen").click()
            expect(page.locator("#terminal-r1 .xterm-helper-textarea")).to_be_focused()
            page.locator("#terminal-r1 .xterm-helper-textarea").press_sequentially(
                "show ip route"
            )
            expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text(
                "show ip route"
            )
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            screenshots(page, "lab-workspace", "monitoring")

    def test_missing_and_partial_layouts_keep_existing_placement_and_fit(self) -> None:
        for layout in (None, {"nodes": {"r1": {"x": 300, "y": 200}}}):
            with self.subTest(layout=layout), self.signed_in_page() as page:
                page.route(
                    "**/api/load_layout?*",
                    lambda route: route.fulfill(json={"layout": layout}),
                )
                page.reload()
                expect(page.locator("g.node.pinned")).to_have_count(2, timeout=10000)
                if layout:
                    expect(page.locator("g.node", has_text="r1")).to_have_attribute(
                        "transform", "translate(300,200)"
                    )
                positions = page.locator("g.node").evaluate_all(
                    "nodes => nodes.map(node => node.getAttribute('transform'))"
                )
                self.assertTrue(
                    all(position and "NaN" not in position for position in positions)
                )
                page.locator("#fit-view").click()
                page.wait_for_timeout(900)
                self.assertEqual(
                    page.locator("g.node").evaluate_all(
                        "nodes => nodes.map(node => node.getAttribute('transform'))"
                    ),
                    positions,
                )


class PhoneLabsListTests(LiveServerCase):
    """With no Lab running, a phone still shows the Labs to start."""

    def test_phone_shows_the_labs_list_with_room_for_labs_before_a_lab_runs(self) -> None:
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 390, "height": 664})
            page.wait_for_selector(".sidebar-lab-item")
            sidebar = box_of(page.locator("#lab-tabpanel > .sidebar"))
            listing = box_of(page.locator(".sidebar-lab-list"))
            first = box_of(page.locator(".sidebar-lab-item").first)

        self.assertGreaterEqual(sidebar["height"], 300)
        self.assertGreaterEqual(listing["height"], 120)
        self.assertGreater(first["height"], 0)
        self.assertLessEqual(first["y"], listing["y"] + listing["height"])
