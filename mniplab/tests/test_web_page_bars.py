"""The page bars around the Topology give their height to the Lab.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn server
and headless Chromium, with a fake running Lab. The page header, the topology
toolbar and the status footer are measured as a Learner would see them on a
1366x768 laptop (browser window height: screen minus about 110px of chrome),
and the controls that moved out of the toolbar are driven from where they now
live: the Lab name in the breadcrumb, Group and Ungroup in the View menu.
"""

import os
from typing import Any

import pytest

from mniplab.tests.browser import LiveServerCase, Route, box_of, expect
from mniplab.tests.fakes import FakeLab, FakeNode
from mniplab.tests.test_web_terminal_budget import FakeSpawner
from mniplab.webserver import registry
from mniplab.webserver.services import TerminalService

pytestmark = pytest.mark.browser_workspace

LAPTOP = {"width": 1366, "height": 658}

HEIGHTS = """selector => [...document.querySelectorAll(selector)]
    .filter(el => el.offsetParent !== null)
    .map(el => [el.id || el.textContent.trim(), el.getBoundingClientRect().height])"""


class PageBarTests(LiveServerCase):
    """Heights, the one-row toolbar, the breadcrumb step and the footer order."""

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

    def running_page(self, page) -> None:
        """Size the window like a laptop and wait for the running Lab's toolbar."""
        page.set_viewport_size(LAPTOP)
        page.wait_for_selector("g.node")
        expect(page.locator("#stop-lab-btn")).to_be_visible()

    def test_page_header_is_48px_with_32px_controls(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            header = box_of(page.locator("#app > header"))
            self.assertAlmostEqual(header["height"], 48, delta=0.5)
            controls = page.evaluate(
                HEIGHTS, "#app > header button, #app > header a"
            )
            # Lab Authoring, Help and the account menu; the rest are in that menu.
            self.assertGreaterEqual(len(controls), 3)
            for name, height in controls:
                with self.subTest(control=name):
                    self.assertAlmostEqual(height, 32, delta=0.5)

    def test_header_grows_under_wide_text_spacing_rather_than_clipping(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            # WCAG 1.4.12: a reader may force these spacings on any page.
            page.add_style_tag(
                content="* { line-height: 1.5 !important; letter-spacing: .12em"
                " !important; word-spacing: .16em !important; }"
            )
            page.set_viewport_size({"width": 700, "height": 658})
            page.wait_for_timeout(200)
            measured = page.locator("#app > header").evaluate(
                "h => [h.getBoundingClientRect().height, h.scrollHeight - h.clientHeight]"
            )
            self.assertGreater(measured[0], 48, "the header did not grow")
            self.assertLessEqual(measured[1], 1, "header content is clipped")

    def test_topology_toolbar_is_one_48px_row_at_the_right_on_a_laptop(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.tabManager.openTerminal('r1');
            }""")
            expect(page.locator("[data-pane='terminal']")).to_be_visible()
            page.wait_for_timeout(600)
            bar = box_of(page.locator("#lab-tabpanel .controls"))
            self.assertAlmostEqual(bar["height"], 48, delta=0.5)
            tops = page.evaluate(
                """() => [...document.querySelectorAll('#lab-tabpanel .controls > *')]
                    .filter(el => el.offsetParent !== null && el.id !== '')
                    .map(el => [el.id, el.getBoundingClientRect().top])"""
            )
            self.assertIn("lab-prompt-btn", [name for name, _ in tops])
            self.assertIn("stop-lab-btn", [name for name, _ in tops])
            self.assertEqual(len({round(top) for _, top in tops}), 1, tops)

    def test_running_lab_name_is_the_last_breadcrumb_step(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            crumbs = page.get_by_role("navigation", name="Breadcrumb")
            steps = crumbs.get_by_role("listitem")
            self.assertEqual(
                [text for text in steps.all_inner_texts() if text != "/"],
                ["Mininet-IPLab", "Labs", "Static Routing Lab"],
            )
            # The title stays the page's only h1; the Lab name is plain text after it.
            self.assertEqual(page.locator("h1:visible").count(), 1)
            expect(page.locator("h1:visible")).to_have_text("Labs")
            self.assertEqual(page.locator("h1 #lab-name, h1#lab-name").count(), 0)
            current = crumbs.locator("[aria-current='page']")
            expect(current).to_have_count(1)
            expect(current).to_have_text("Static Routing Lab")

    def test_with_no_lab_the_title_carries_the_current_page_marker(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            page.get_by_role("button", name="Stop Lab", exact=True).click()
            page.locator("#confirm-ok").click()
            expect(page.locator("#lab-crumb")).to_be_hidden()
            crumbs = page.get_by_role("navigation", name="Breadcrumb")
            current = crumbs.locator("[aria-current='page']")
            expect(current).to_have_count(1)
            expect(current).to_have_text("Labs")

    def test_stop_lab_confirmation_still_names_the_lab(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            page.get_by_role("button", name="Stop Lab", exact=True).click()
            expect(page.locator("#confirm-modal-message")).to_contain_text(
                "Stop Static Routing Lab?"
            )
            page.locator("#confirm-cancel").click()

    def test_save_as_authored_layout_still_names_the_lab_example(self) -> None:
        saved: list[Any] = []
        with self.signed_in_page() as page:
            self.running_page(page)
            page.wait_for_timeout(900)  # let the Nodes settle and pin

            def record_save(route: Route) -> None:
                saved.append(route.request.post_data_json)
                route.fulfill(json={"status": "saved"})

            page.route("**/api/save_layout", record_save)
            page.locator("#view-options-btn").click()
            page.get_by_role("button", name="Save as Authored Layout").click()
            page.wait_for_timeout(300)
        self.assertEqual([body["example"] for body in saved], ["static-lab"])

    def test_lab_prompt_stays_in_the_topology_toolbar(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            prompt = page.locator("#lab-tabpanel .controls #lab-prompt-btn")
            expect(prompt).to_be_visible()
            expect(prompt).to_have_accessible_name("Open Lab Prompt")

    def test_group_and_ungroup_are_view_menu_layout_actions_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            # Nothing of them is left in the toolbar, nor the separator before them.
            expect(page.locator("#lab-tabpanel .controls > #create-group-btn")).to_have_count(0)
            expect(page.locator("#lab-tabpanel .controls > #delete-group-btn")).to_have_count(0)
            expect(page.locator("#lab-tabpanel .controls > .separator-vertical")).to_have_count(1)
            layout = page.locator(
                "#view-options-menu [aria-label='Layout actions']"
            )
            expect(layout.locator("#create-group-btn")).to_have_count(1)
            expect(layout.locator("#delete-group-btn")).to_have_count(1)

            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.selectedNodes = new Set(['r1', 'r2']);
            }""")
            view = page.locator("#view-options-btn")
            view.focus()
            page.keyboard.press("Enter")
            expect(page.locator("#view-options-menu")).to_be_visible()
            group = page.get_by_role("button", name="Create a group from selected nodes")
            group.focus()
            page.keyboard.press("Enter")
            # The menu closes, as for every other action in it, and the form opens.
            expect(page.locator("#view-options-menu")).to_be_hidden()
            expect(page.locator("#input-group-name")).to_be_focused()
            page.keyboard.type("Core")
            page.keyboard.press("Enter")
            expect(page.locator("g.group-item")).to_have_count(1)

            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                const { selectGroup } = await import('/static/js/topology/groups.js');
                selectGroup(appState.groups[0]);
            }""")
            view.focus()
            page.keyboard.press("Enter")
            ungroup = page.get_by_role("button", name="Ungroup (delete the selected group)")
            expect(ungroup).to_be_visible()
            ungroup.focus()
            page.keyboard.press("Enter")
            page.locator("#confirm-ok").click()
            expect(page.locator("g.group-item")).to_have_count(0)

    def test_view_menu_rows_are_32px(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            page.locator("#view-options-btn").click()
            rows = page.evaluate(
                HEIGHTS, "#view-options-menu label, #view-options-menu button"
            )
            self.assertGreaterEqual(len(rows), 5)
            for name, height in rows:
                with self.subTest(row=name):
                    self.assertAlmostEqual(height, 32, delta=0.5)

    def test_status_footer_is_42px_with_28px_controls(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            footer = box_of(page.locator(".workspace-status-line"))
            self.assertAlmostEqual(footer["height"], 42, delta=0.5)
            controls = page.evaluate(HEIGHTS, "#topology-canvas-controls button")
            self.assertEqual(len(controls), 4)
            for name, height in controls:
                with self.subTest(control=name):
                    self.assertAlmostEqual(height, 28, delta=0.5)

    def test_connection_status_leads_the_footer_as_plain_text(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            footer = page.locator(".workspace-status-line")
            self.assertRegex(
                " ".join(footer.inner_text().split()),
                r"^Connected \| Nodes: 2 \| Learner Sessions: 0 \| Active Terminals: 0",
            )
            # No pill: no border or fill around it.
            look = page.locator("#ws-status").evaluate(
                """el => { const s = getComputedStyle(el);
                    return [s.borderTopWidth, s.backgroundColor]; }"""
            )
            self.assertEqual(look, ["0px", "rgba(0, 0, 0, 0)"])
            # A connection change is announced once: its live region is a sibling
            # of the counts' live region, never inside it (nor around it).
            nested = page.evaluate(
                """() => {
                    const ws = document.getElementById('ws-status');
                    const counts = document.getElementById('status');
                    return [counts.contains(ws), ws.contains(counts)];
                }"""
            )
            self.assertEqual(nested, [False, False])
            expect(page.locator("#ws-status")).to_have_attribute("aria-live", "polite")
            expect(page.locator("#status")).to_have_attribute("aria-live", "polite")
