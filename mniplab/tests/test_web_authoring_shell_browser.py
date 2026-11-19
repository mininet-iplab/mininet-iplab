"""The Lab Authoring page shell, driven as an Instructor in a real browser."""

from typing import Any

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, open_details
from mniplab.tests.browser import Locator, Page, expect

pytestmark = pytest.mark.browser_authoring


def rect_of(locator: Locator) -> Any:
    """The locator's on-screen box; the element must be rendered."""
    rect = locator.bounding_box()
    assert rect is not None, f"{locator} is not rendered"
    return rect


class AuthoringShellBrowserTests(AuthoringPageCase):
    """Header, palette, canvas and sidebar around a Lab Example Draft."""

    @staticmethod
    def _settled(page: Page) -> None:
        """Wait until the draft has saved itself."""
        expect(page.locator("#status-draft-state")).to_have_text("Saved")

    def _open_new_draft(self, page: Page, width: int = 1280, height: int = 720,
                        details: bool = True) -> None:
        """Open Lab Authoring at a viewport size with a fresh empty draft, Details unfolded."""
        page.set_viewport_size({"width": width, "height": height})
        page.goto(self.base_url + "/authoring")
        page.wait_for_function(
            "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
        )
        page.evaluate("""async () => {
            await fetch('/api/authoring/drafts', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: '{}'});
        }""")
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")
        if details:
            open_details(page)

    def test_details_is_folded_on_load_and_its_toggle_keeps_focus(self) -> None:
        """Opening Details narrows the canvas; folding it again leaves focus on the toggle."""
        with self.signed_in_page() as page:
            self._open_new_draft(page, details=False)
            sidebar = page.get_by_role("complementary", name="Details")
            toggle = page.get_by_role("button", name="Details", exact=True)
            lesson = page.get_by_role("tabpanel", name="Lesson")
            expect(sidebar).to_be_visible()
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(lesson).to_be_hidden()
            canvas = page.locator("#draft-canvas")
            folded_width = rect_of(canvas)["width"]

            toggle.focus()
            page.keyboard.press("Enter")

            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(toggle).to_be_focused()
            expect(lesson).to_be_visible()
            self.assertLess(rect_of(canvas)["width"], folded_width - 200)

            page.keyboard.press("Space")
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(toggle).to_be_focused()
            expect(lesson).to_be_hidden()

    def test_sidebar_tabs_hold_lesson_and_guide_and_follow_arrow_keys(self) -> None:
        """Arrow, Home and End move between tabs; each panel holds its controls."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            lesson = page.get_by_role("tab", name="Lesson")
            guide = page.get_by_role("tab", name="Guide")
            expect(page.get_by_role("tab")).to_have_text(["Lesson", "Guide"])
            expect(lesson).to_have_attribute("aria-selected", "true")
            lesson_panel = page.get_by_role("tabpanel", name="Lesson")
            for field in ("Catalog name", "Description", "Protocols or Services"):
                expect(lesson_panel.get_by_label(field)).to_be_visible()

            lesson.focus()
            page.keyboard.press("ArrowRight")
            expect(guide).to_be_focused()
            expect(guide).to_have_attribute("aria-selected", "true")
            guide_panel = page.get_by_role("tabpanel", name="Guide")
            expect(guide_panel.get_by_label("Guide (Markdown)")).to_be_visible()
            expect(guide_panel.get_by_role("button", name="Expand")).to_be_visible()
            expect(lesson_panel).to_be_hidden()

            page.keyboard.press("ArrowRight")
            expect(lesson).to_be_focused()
            page.keyboard.press("End")
            expect(guide).to_be_focused()
            page.keyboard.press("Home")
            expect(lesson).to_have_attribute("aria-selected", "true")

            # The script is in the Script pane beside the canvas, not the sidebar.
            page.get_by_role("button", name="Script", exact=True).click()
            pane = page.get_by_role("region", name="Script", exact=True)
            expect(pane.get_by_role("button", name="Download .zip")).to_be_visible()
            expect(pane.get_by_role("textbox", name="Lab script")).to_contain_text(
                "Add a Node to generate Python.")
            expect(page.locator("#status-alert")).to_be_empty()

    def test_selecting_a_node_never_switches_the_sidebar_tab(self) -> None:
        """The Guide stays open while the Instructor places and selects Nodes."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            page.get_by_role("tab", name="Guide").click()
            page.locator("#palette-host").click()
            node = page.locator("#draft-canvas .node").first
            node.click()
            expect(node).to_have_class("node selected")
            expect(page.get_by_role("tab", name="Guide")).to_have_attribute("aria-selected", "true")
            expect(page.get_by_role("tabpanel", name="Guide")).to_be_visible()

    def test_guide_expand_edits_and_previews_the_guide_in_a_large_dialog(self) -> None:
        """Expand opens a centred dialog whose edits reach the draft and whose Preview renders."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            page.get_by_role("tab", name="Guide").click()
            page.get_by_label("Guide (Markdown)").fill("# Welcome\n")
            expand = page.get_by_role("button", name="Expand")
            expand.click()

            dialog = page.get_by_role("dialog", name="Guide")
            expect(dialog).to_be_visible()
            box = rect_of(dialog)
            self.assertAlmostEqual(box["x"] + box["width"] / 2, 640, delta=2)
            self.assertGreater(box["width"], 800)
            source = dialog.get_by_label("Guide Markdown")
            expect(source).to_be_focused()
            expect(source).to_have_value("# Welcome\n")
            source.press("End")
            source.press_sequentially("\nRun **ping** from h1.")

            dialog.get_by_role("tab", name="Preview").click()
            preview = dialog.get_by_role("tabpanel", name="Preview")
            expect(preview.get_by_role("heading", name="Welcome")).to_be_visible()
            expect(preview.locator("strong")).to_have_text("ping")
            expect(source).to_be_hidden()
            dialog.get_by_role("tab", name="Edit").click()
            expect(source).to_be_visible()

            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(expand).to_be_focused()
            expect(page.get_by_label("Guide (Markdown)")).to_have_value(
                "# Welcome\n\nRun **ping** from h1."
            )
            self._settled(page)
            saved = page.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                return (await (await fetch(`/api/authoring/drafts/${id}`)).json()).guide;
            }""")
            self.assertEqual(saved, "# Welcome\n\nRun **ping** from h1.")

    def test_skip_link_reaches_the_canvas_region(self) -> None:
        """The first Tab stop skips the header and lands on the canvas region."""
        with self.signed_in_page() as page:
            self._open_new_draft(page, details=False)
            page.keyboard.press("Tab")
            skip = page.get_by_role("link", name="Skip to canvas")
            expect(skip).to_be_focused()
            page.keyboard.press("Enter")
            expect(page.get_by_role("region", name="Topology canvas")).to_be_focused()

    def _seed_drafts(self, page: Page, *names: str) -> None:
        """Create named shared drafts over HTTP, then reload the page."""
        page.evaluate("""async names => {
            for (const name of names) {
                const draft = await (await fetch('/api/authoring/drafts', {method: 'POST',
                    headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
                draft.catalog.name = name;
                await fetch(`/api/authoring/drafts/${draft.id}`, {method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({revision: draft.revision, document: draft})});
            }
        }""", list(names))
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")

    def test_header_holds_only_the_page_controls_on_one_row_at_1280(self) -> None:
        """The banner keeps the breadcrumb and page controls; the draft lives on the canvas."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(self.base_url + "/authoring")
            title = "Relay lesson with a Catalog name long enough to truncate"
            self._seed_drafts(page, title)
            banner = page.get_by_role("banner")
            expect(banner).not_to_contain_text(title)
            for name in ("Drafts…", "Preview", "Publish…", "Close draft"):
                expect(banner.get_by_role("button", name=name)).to_have_count(0)
            controls = [banner.get_by_role("link", name="Return to Labs")]
            expect(controls[-1]).to_have_attribute("href", "/")
            controls.append(banner.locator("#account-menu-btn"))
            tops = []
            for control in controls:
                expect(control).to_be_visible()
                box = rect_of(control)
                tops.append(box["y"] + box["height"] / 2)
            self.assertLess(max(tops) - min(tops), 4, "header controls wrap onto another row")
            self.assertLess(rect_of(banner)["height"], 80)
            for text in ("Mininet-IPLab", "Lab Authoring"):
                line = rect_of(page.get_by_role("banner").get_by_text(text, exact=True))
                self.assertLess(line["height"], 36, f"{text} wraps")

    def test_palette_is_one_tab_stop_moved_through_with_arrows_home_and_end(self) -> None:
        """The palette toolbar takes one Tab stop and remembers the last item."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            toolbar = page.get_by_role("toolbar", name="Add a Node")
            expect(toolbar).to_have_attribute("aria-orientation", "vertical")
            host = toolbar.get_by_role("button", name="Add Host")
            host.focus()
            page.keyboard.press("ArrowDown")
            expect(toolbar.get_by_role("button", name="Add Container Host")).to_be_focused()
            page.keyboard.press("End")
            speaker = toolbar.get_by_role("button", name="Add BGP Speaker")
            expect(speaker).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(host).to_be_focused()
            page.keyboard.press("ArrowUp")
            expect(speaker).to_be_focused()
            page.keyboard.press("Home")
            expect(host).to_be_focused()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            switch = toolbar.get_by_role("button", name="Add Switch")
            expect(switch).to_be_focused()

            page.keyboard.press("Tab")
            focused_in_toolbar = page.evaluate(
                "() => !!document.activeElement.closest('[role=toolbar][aria-label=\"Add a Node\"]')")
            self.assertFalse(focused_in_toolbar, "Tab stayed inside the palette")
            page.keyboard.press("Shift+Tab")
            expect(switch).to_be_focused()

    def test_palette_names_show_on_hover_and_focus_and_escape_dismisses_them(self) -> None:
        """Each item's name is visible on hover and focus, stays while hovered, and Escape hides it."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            tip = page.locator("#palette-tip")
            nat = page.get_by_role("button", name="Add Internet Gateway")
            nat.hover()
            expect(tip).to_be_visible()
            expect(tip).to_have_text("Internet Gateway")
            tip_box = rect_of(tip)
            page.mouse.move(tip_box["x"] + tip_box["width"] / 2, tip_box["y"] + tip_box["height"] / 2,
                            steps=4)
            page.wait_for_timeout(300)
            expect(tip).to_be_visible()
            page.keyboard.press("Escape")
            expect(tip).to_be_hidden()
            page.mouse.move(700, 400)

            transit = page.get_by_role("button", name="Add Transit Internet Gateway")
            transit.focus()
            expect(tip).to_have_text("Transit Internet Gateway")
            expect(tip).to_be_visible()
            page.keyboard.press("Escape")
            expect(tip).to_be_hidden()
            expect(transit).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(tip).to_have_text("BGP Speaker")
            expect(tip).to_be_visible()

    def _seed_topology(self, page: Page, positions: dict, links: list) -> None:
        """Save one draft with Hosts at these positions and Links between them, then reload."""
        page.evaluate("""async ({positions, links}) => {
            const draft = await (await fetch('/api/authoring/drafts', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
            const ids = Object.fromEntries(Object.keys(positions).map(name => [name, crypto.randomUUID()]));
            draft.topology.nodes = Object.keys(positions).map(name => ({id: ids[name], name,
                type: 'host', ipv4_gateway: '', ipv6_gateway: '', startup: []}));
            draft.layout.nodes = Object.fromEntries(Object.entries(positions)
                .map(([name, [x, y]]) => [ids[name], {x, y}]));
            const ports = {};
            const end = name => ({node_id: ids[name], port: (ports[name] = (ports[name] ?? -1) + 1),
                interface_id: 'if-' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
                ipv4: '', ipv6: ''});
            draft.topology.links = links.map(([a, b]) => ({id: crypto.randomUUID(), ends: [end(a), end(b)]}));
            await fetch(`/api/authoring/drafts/${draft.id}`, {method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({revision: draft.revision, document: draft})});
        }""", {"positions": positions, "links": links})
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")

    def _node_centre(self, page: Page, name: str) -> tuple[float, float]:
        box = rect_of(page.locator("#draft-canvas .node", has_text=name).locator("circle.node-body"))
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def _canvas_centre(self, page: Page) -> tuple[float, float]:
        box = rect_of(page.locator("#draft-canvas"))
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def test_canvas_opens_fitted_to_the_nodes_not_the_whole_layout(self) -> None:
        """A small Topology opens centred and enlarged, capped at 1.5 times."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(self.base_url + "/authoring")
            self._seed_topology(page, {"h1": [100, 100], "h2": [300, 100], "h3": [200, 250]}, [])
            (x1, y1), (x2, _) = self._node_centre(page, "h1"), self._node_centre(page, "h2")
            self.assertAlmostEqual(x2 - x1, 300, delta=1)
            _, y3 = self._node_centre(page, "h3")
            # The same 1.5 times scale on both axes keeps the authored proportions.
            self.assertAlmostEqual(y3 - y1, 225, delta=1)
            cx, cy = self._canvas_centre(page)
            self.assertAlmostEqual((x1 + x2) / 2, cx, delta=2)
            self.assertAlmostEqual((y1 + y3) / 2, cy, delta=2)

    def test_canvas_fits_a_spread_topology_inside_the_view(self) -> None:
        """Nodes at opposite corners open fully visible and fill the limiting dimension."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(self.base_url + "/authoring")
            self._seed_topology(page, {"h1": [0, 0], "h2": [1000, 1000]}, [["h1", "h2"]])
            canvas = rect_of(page.locator("#draft-canvas"))
            for name in ("h1", "h2"):
                box = rect_of(page.locator("#draft-canvas .node", has_text=name))
                self.assertGreaterEqual(box["y"], canvas["y"])
                self.assertLessEqual(box["y"] + box["height"], canvas["y"] + canvas["height"])
            (_, y1), (_, y2) = self._node_centre(page, "h1"), self._node_centre(page, "h2")
            self.assertGreater(y2 - y1, canvas["height"] * 0.7)

    def test_one_node_draft_opens_at_the_cap_and_an_empty_draft_at_default_zoom(self) -> None:
        """One Node is centred at 1.5 times; an empty draft opens at the Lab's default zoom."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(self.base_url + "/authoring")
            self._seed_topology(page, {"h1": [900, 100]}, [])
            body = rect_of(page.locator("#draft-canvas circle.node-body"))
            self.assertAlmostEqual(body["width"], 60 * 1.5 + 2.5 * 1.5, delta=2)
            cx, cy = self._canvas_centre(page)
            nx, ny = self._node_centre(page, "h1")
            self.assertAlmostEqual(nx, cx, delta=2)
            self.assertAlmostEqual(ny, cy, delta=2)

            self._open_new_draft(page, details=False)
            expect(page.locator("#draft-canvas .authoring-canvas-bounds")).to_have_count(0)
            scale = page.locator("#draft-canvas").evaluate("svg => d3.zoomTransform(svg).k")
            self.assertEqual(scale, 1)

    def test_canvas_draws_the_lab_grid_without_snapping(self) -> None:
        """The running-Lab grid is drawn, and a Node keeps an off-grid position."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            pattern = page.locator("#draft-grid")
            expect(pattern).to_have_attribute("width", "40")
            line = pattern.locator("path")
            expect(line).to_have_attribute("stroke-dasharray", "4 4")
            expect(line).to_have_attribute("opacity", "0.3")
            page.get_by_role("button", name="Add Host").click()
            page.keyboard.press("Enter")
            page.locator("#node-position-disclosure summary").click()
            page.locator("#host-x").fill("123")
            page.locator("#host-y").fill("457")
            page.keyboard.press("Escape")
            self._settled(page)
            layout = page.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                return Object.values((await (await fetch(`/api/authoring/drafts/${id}`)).json()).layout.nodes);
            }""")
            self.assertEqual(layout, [{"x": 123, "y": 457}])

    def test_interface_labels_show_when_readable_or_selected(self) -> None:
        """ethN labels hide below 11 px unless their Link touches the selection."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(self.base_url + "/authoring")
            self._seed_topology(page, {"h1": [0, 0], "h2": [1000, 0], "h3": [0, 1000], "h4": [1000, 1000]},
                                [["h1", "h2"], ["h3", "h4"]])
            labels = page.locator("#draft-canvas .link-label")
            expect(labels).to_have_text(["eth0", "eth0", "eth0", "eth0"])
            for index in range(4):
                expect(labels.nth(index)).to_be_hidden()

            page.locator("#draft-canvas .node", has_text="h1").click()
            first, second = page.locator("#draft-canvas .link-label-group").all()
            expect(first.locator(".link-label").first).to_be_visible()
            expect(second.locator(".link-label").first).to_be_hidden()
            font = rect_of(first.locator(".link-label").first)["height"]
            self.assertGreaterEqual(font, 11)

            for _ in range(7):
                page.get_by_role("button", name="Zoom in").click()
                page.wait_for_timeout(200)
            page.wait_for_timeout(300)
            expect(second.locator(".link-label").first).to_be_visible()

    SPREAD = {"h1": [0, 0], "h2": [500, 0], "h3": [1000, 0], "h4": [0, 1000], "h5": [1000, 1000]}

    def test_no_horizontal_page_scroll_at_desktop_tablet_and_phone_widths(self) -> None:
        """The page never scrolls sideways; the header wraps instead."""
        with self.signed_in_page() as page:
            page.goto(self.base_url + "/authoring")
            self._seed_drafts(page, "A lesson whose Catalog name is long enough to wrap")
            for width, height in ((1280, 720), (1024, 720), (375, 800)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(100)
                overflow = page.evaluate(
                    "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth")
                self.assertEqual(overflow, 0, f"sideways scroll at {width}px")
                controls = [page.get_by_role("button", name=name) for name in ("Labs", "Preview")]
                controls.append(page.locator("#account-menu-btn"))
                for control in controls:
                    box = rect_of(control)
                    self.assertLessEqual(box["x"] + box["width"], width,
                                         f"{control.inner_text()} cut off at {width}px")

    def test_a_long_draft_name_keeps_its_menu_and_an_open_note_pushes_the_catalog_down(self) -> None:
        """A long name ends in an ellipsis beside its ⋯; the Drafts note never covers the Catalog."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(self.base_url + "/authoring")
            name = "Copy of VLAN & Inter-VLAN Routing Lab with a long name"
            self._seed_drafts(page, name)
            toggle = page.locator("#labs-explorer-toggle")
            if toggle.get_attribute("aria-expanded") != "true":
                toggle.click()
            drafts = page.locator("#drafts-list")
            more = page.get_by_role("button", name=f"More actions for {name}", exact=True)
            expect(more).to_be_visible()
            self.assertLessEqual(rect_of(more)["x"] + rect_of(more)["width"],
                                 rect_of(drafts)["x"] + rect_of(drafts)["width"] + 0.5,
                                 "the ⋯ button is pushed out of the list")

            page.locator("[data-info-for=drafts-note]").click()
            expect(page.locator("#drafts-note")).to_be_visible()
            buttons = rect_of(page.locator("#drafts-section .authoring-row"))
            catalog = rect_of(page.locator("#catalog-section"))
            self.assertLessEqual(buttons["y"] + buttons["height"], catalog["y"],
                                 "the open Drafts note runs over the Lab Catalog")

    def test_sidebar_stacks_under_the_canvas_below_900_px(self) -> None:
        """Beside the canvas at 1024; under it, never over it, at 800."""
        with self.signed_in_page() as page:
            self._open_new_draft(page, 1024, 720)
            sidebar = page.get_by_role("complementary", name="Details")
            canvas = page.locator("#draft-canvas")
            self.assertGreaterEqual(rect_of(sidebar)["x"],
                                    rect_of(canvas)["x"] + rect_of(canvas)["width"] - 1)
            page.set_viewport_size({"width": 800, "height": 720})
            page.wait_for_timeout(100)
            canvas_box, sidebar_box = rect_of(canvas), rect_of(sidebar)
            self.assertGreaterEqual(sidebar_box["y"], canvas_box["y"] + canvas_box["height"] - 1)
            self.assertGreater(canvas_box["height"], 300)
            card = page.locator("#draft-form").evaluate("el => el.clientWidth")
            self.assertAlmostEqual(sidebar_box["width"], card, delta=2)

    def test_phone_width_palette_is_a_row_and_canvas_fits_every_node(self) -> None:
        """At 375 px the palette stays horizontal and the initial view fits every Node."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 375, "height": 800})
            page.goto(self.base_url + "/authoring")
            self._seed_topology(page, self.SPREAD, [["h1", "h2"]])
            toolbar = page.get_by_role("toolbar", name="Add a Node")
            expect(toolbar).to_have_attribute("aria-orientation", "horizontal")
            host = toolbar.get_by_role("button", name="Add Host")
            container = toolbar.get_by_role("button", name="Add Container Host")
            self.assertAlmostEqual(rect_of(host)["y"], rect_of(container)["y"], delta=1)
            host.focus()
            page.keyboard.press("ArrowRight")
            expect(container).to_be_focused()

            canvas = rect_of(page.locator("#draft-canvas"))
            for node in self.SPREAD:
                box = rect_of(page.locator("#draft-canvas .node", has_text=node))
                self.assertGreaterEqual(box["x"], canvas["x"] - 1)
                self.assertGreaterEqual(box["y"], canvas["y"] - 1)
                self.assertLessEqual(box["x"] + box["width"], canvas["x"] + canvas["width"] + 1)
                self.assertLessEqual(box["y"] + box["height"], canvas["y"] + canvas["height"] + 1)

            sidebar = rect_of(page.get_by_role("complementary", name="Details"))
            self.assertGreaterEqual(sidebar["y"], canvas["y"] + canvas["height"] - 1)
            card = page.locator("#draft-form").evaluate("el => el.clientWidth")
            self.assertAlmostEqual(sidebar["width"], card, delta=2)

            fit = page.get_by_role("button", name="Fit", exact=True)
            expect(fit).to_be_visible()
            expect(fit).to_have_text("Fit")
            fit.click()
            page.wait_for_timeout(300)
            for node in self.SPREAD:
                box = rect_of(page.locator("#draft-canvas .node", has_text=node))
                self.assertGreaterEqual(box["x"], canvas["x"] - 1)
                self.assertLessEqual(box["x"] + box["width"], canvas["x"] + canvas["width"] + 1)

    def test_tab_order_runs_header_labs_palette_canvas_sidebar(self) -> None:
        """Sequential focus reaches the header, the Labs explorer, then the palette, canvas and sidebar."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            page.goto(self.base_url + "/authoring")
            self._seed_topology(page, {"h1": [200, 200]}, [])
            order: list[str] = []
            for _ in range(40):
                page.keyboard.press("Tab")
                area = page.evaluate("""() => {
                    const element = document.activeElement;
                    for (const [name, selector] of [['header', 'header'], ['labs', '#labs-explorer'],
                        ['palette', '#node-palette'],
                        ['canvas', '#canvas-region'], ['sidebar', '#authoring-sidebar']]) {
                        if (element.closest(selector)) return name;
                    }
                    return null;
                }""")
                if area and (not order or order[-1] != area):
                    order.append(area)
            self.assertEqual(order[:5], ["header", "labs", "palette", "canvas", "sidebar"])

    def test_internet_gateways_draw_one_cloud_in_their_own_colour(self) -> None:
        """Internet Gateway and Transit Internet Gateway share the cloud, which differs from a Router's."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            nat_color = page.evaluate(
                "() => getComputedStyle(document.documentElement).getPropertyValue('--nat-color').trim()")
            self.assertEqual(nat_color, "#0b8a78")

            def icon(kind: str) -> tuple[str, str]:
                path = page.get_by_role("button", name=f"Add {kind}").locator("path")
                return path.get_attribute("d") or "", path.get_attribute("fill") or ""

            router, nat, transit = icon("Router"), icon("Internet Gateway"), icon("Transit Internet Gateway")
            self.assertNotEqual(router[0], nat[0])
            self.assertEqual(nat[0], transit[0])
            self.assertEqual((nat[1], transit[1]), (nat_color, nat_color))
            self.assertNotEqual(router[1], nat_color)

            page.get_by_role("button", name="Add Internet Gateway").click()
            node = page.locator("#draft-canvas .node")
            expect(node.locator(".node-icon path")).to_have_attribute("d", nat[0])
            expect(node.locator(".node-body")).to_have_attribute("stroke", nat_color)

    def test_escape_dismisses_a_palette_name_before_it_undoes_a_placement(self) -> None:
        """With the pointer still on the palette, the first Escape only hides the name."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            page.get_by_role("button", name="Add Router").focus()
            page.keyboard.press("Enter")
            node = page.locator("#draft-canvas .node")
            expect(node).to_be_focused()
            page.get_by_role("button", name="Add Internet Gateway").hover()
            tip = page.locator("#palette-tip")
            expect(tip).to_be_visible()
            expect(node).to_be_focused()
            page.keyboard.press("Escape")
            expect(tip).to_be_hidden()
            expect(node).to_have_count(1)
            page.keyboard.press("Escape")
            expect(node).to_have_count(0)


    def test_direct_authoring_visit_signs_in_and_returns_to_the_same_url(self) -> None:
        """Missing or expired cookies reach sign-in, then reload the editor URL."""
        for expired in (False, True):
            with self.subTest(expired=expired), self.signed_in_page() as page:
                page.context.clear_cookies()
                if expired:
                    page.context.add_cookies([{
                        "name": "session", "value": "expired-session", "url": self.base_url,
                    }])
                target = self.base_url + "/authoring?from=bookmark"
                page.goto(target)
                expect(page.locator("#login-form")).to_be_visible()
                expect(page.locator("#authoring-status")).to_have_count(0)
                page.locator("#username").fill("instructor1")
                page.locator("#password").fill("lab12345")
                page.get_by_role("button", name="Login", exact=True).click()
                expect(page.locator("#authoring-status")).to_be_visible()
                expect(page).to_have_url(target)
                page.reload()
                expect(page.locator("#authoring-status")).to_be_visible()
