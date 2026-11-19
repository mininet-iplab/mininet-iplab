"""The Labs explorer, canvas toolbar and Details sidebar of Lab Authoring, in a real browser."""

import re
from typing import Any
from unittest.mock import patch

import pytest

from mniplab.tests.authoring_editors import (
    AuthoringPageCase,
    catalog_row,
    draft_menu,
    labs_explorer,
    open_details,
    open_from_explorer,
)
from mniplab.tests.browser import Locator, Page, expect, screenshots
from mniplab.tests.authoring_fixtures import PackageRecordingLauncher
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_authoring

NO_DRAFT_OPEN = "Open a Lab draft from Drafts, or create or upload a new one."


def rect_of(locator: Locator) -> Any:
    """The locator's on-screen box; the element must be rendered."""
    rect = locator.bounding_box()
    assert rect is not None, f"{locator} is not rendered"
    return rect


class LabsExplorerBrowserTests(AuthoringPageCase):
    """Where an Instructor opens, starts and acts on Lab drafts."""

    def _load(self, page: Page, width: int = 1280, height: int = 720) -> None:
        """Open Lab Authoring at a viewport size and wait for its lists."""
        page.set_viewport_size({"width": width, "height": height})
        page.goto(self.base_url + "/authoring")
        page.wait_for_function(
            "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
        )

    def _seed_drafts(self, page: Page, *names: str) -> None:
        """Create named shared drafts over HTTP, then reload on the newest."""
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

    def _saved_names(self, page: Page) -> list[str]:
        """The shared drafts' Catalog names, read back over HTTP."""
        return page.evaluate("""async () => (await (await fetch('/api/authoring/drafts')).json())
            .drafts.map(item => item.catalog.name).sort()""")

    @staticmethod
    def _place_unsaveable_node(page: Page) -> None:
        """Place a Transit Internet Gateway, which cannot be saved without downstream Routers."""
        page.locator("#palette-transit").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#status-draft-state")).to_contain_text("Can't save yet")

    def test_explorer_folds_when_a_draft_opens_and_opens_when_none_is(self) -> None:
        """No draft: the explorer and an empty canvas card; a draft folds it to a rail."""
        with self.signed_in_page() as page:
            self._load(page)
            explorer = page.get_by_role("complementary", name="Labs")
            toggle = page.get_by_role("button", name="Labs", exact=True)
            drafts = explorer.get_by_role("region", name="Drafts")
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(drafts).to_be_visible()
            expect(drafts.get_by_role("status")).to_have_text("No Lab drafts yet.")
            expect(page.get_by_role("heading", name="No Lab draft open")).to_be_visible()
            expect(page.locator("#authoring-status")).to_have_text(
                "Create a Lab draft, upload a script, or copy a lab from the Lab Catalog to begin.")
            banner = page.get_by_role("banner")
            for name in ("Drafts…", "Preview", "Publish…", "Close draft"):
                expect(banner.get_by_role("button", name=name)).to_have_count(0)
            screenshots(page, "rework-07", "no-draft-open")

            drafts.get_by_role("button", name="New draft").click()
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(drafts).to_be_hidden()
            expect(page.get_by_role("heading", name="No Lab draft open")).to_be_hidden()
            expect(explorer.get_by_text("Labs", exact=True)).to_be_visible()
            self.assertLess(rect_of(explorer)["width"], 70)
            expect(page.locator("#draft-title")).to_have_text("Untitled lab")

            toggle.focus()
            page.keyboard.press("Enter")
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(toggle).to_be_focused()
            expect(drafts).to_be_visible()
            page.keyboard.press("Enter")
            expect(toggle).to_have_attribute("aria-expanded", "false")

            page.get_by_role("button", name="Close draft").click()
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(page.locator("#authoring-status")).to_have_text(
                f"Closed Untitled lab. {NO_DRAFT_OPEN}")
            expect(drafts.get_by_role("button", name="Open Untitled lab")).to_be_focused()

    def test_the_open_draft_is_current_and_open_moves_to_the_canvas(self) -> None:
        """The open draft's row is highlighted and current; Open lands on the canvas."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha", "Beta")
            explorer = labs_explorer(page)
            alpha = explorer.get_by_role("button", name="Open Alpha", exact=True)
            beta = explorer.get_by_role("button", name="Open Beta", exact=True)
            expect(beta).to_have_attribute("aria-current", "true")
            expect(alpha).not_to_have_attribute("aria-current", "true")
            expect(explorer).not_to_contain_text("open now")
            self.assertLess(rect_of(beta)["height"], 48, "a draft row is one line")
            screenshots(page, "rework-07", "explorer-open", "#labs-explorer")

            alpha.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("button", name="Labs", exact=True)).to_have_attribute(
                "aria-expanded", "false")
            expect(page.get_by_role("region", name="Topology canvas")).to_be_focused()
            expect(page.locator("#draft-title")).to_have_text("Alpha")
            expect(labs_explorer(page).get_by_role(
                "button", name="Open Alpha", exact=True)).to_have_attribute("aria-current", "true")

    def test_opening_another_draft_saves_first_and_asks_only_for_unsaveable_edits(self) -> None:
        """Edits save before another draft opens; only edits that cannot be saved ask."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha", "Beta")
            messages: list[str] = []

            def dismiss(confirm: Any) -> None:
                messages.append(confirm.message)
                confirm.dismiss()

            page.on("dialog", dismiss)
            open_details(page)
            page.get_by_label("Catalog name").fill("Beta edited")
            open_from_explorer(page, "Alpha")
            expect(page.locator("#draft-title")).to_have_text("Alpha")
            self.assertEqual(messages, [])
            self.assertEqual(self._saved_names(page), ["Alpha", "Beta edited"])

            self._place_unsaveable_node(page)
            open_from_explorer(page, "Beta edited")
            self.assertEqual(messages, ["Discard edits that cannot be saved and open another draft?"])
            expect(page.locator("#draft-title")).to_have_text("Alpha")

            page.remove_listener("dialog", dismiss)
            page.once("dialog", lambda confirm: confirm.accept())
            open_from_explorer(page, "Beta edited")
            expect(page.locator("#draft-title")).to_have_text("Beta edited")

    def test_every_way_of_leaving_unsaveable_edits_asks_first(self) -> None:
        """New draft, Copy as new and Copy from the Lab Catalog each ask before discarding."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha", "Beta")
            self._place_unsaveable_node(page)
            messages: list[str] = []

            def dismiss(confirm: Any) -> None:
                messages.append(confirm.message)
                confirm.dismiss()

            page.on("dialog", dismiss)
            labs_explorer(page).get_by_role("button", name="New draft").click()
            draft_menu(page, "Alpha").get_by_role("button", name="Copy as new Alpha").click()
            catalog_row(page, "Static Routing", "static-lab").get_by_role(
                "button", name="Start from Canvas version of Static Routing Lab", exact=True).click()
            self.assertEqual(messages, [
                "Discard edits that cannot be saved and create another draft?",
                "Discard edits that cannot be saved and open a copy of another draft?",
                "Discard edits that cannot be saved and start from this Lab Example?",
            ])
            expect(page.locator("#draft-title")).to_have_text("Beta")
            self.assertEqual(self._saved_names(page), ["Alpha", "Beta"])

    def test_a_draft_rows_menu_is_one_at_a_time_and_closes_like_a_menu(self) -> None:
        """One ⋯ menu opens at a time; a choice, a click elsewhere or Escape closes it."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha", "Beta")
            explorer = labs_explorer(page)
            alpha_more = explorer.get_by_role("button", name="More actions for Alpha")
            beta_more = explorer.get_by_role("button", name="More actions for Beta")
            alpha_menu = draft_menu(page, "Alpha")
            expect(alpha_menu.get_by_role("button")).to_have_text(["Rename", "Copy as new", "Delete"])
            expect(alpha_more).to_have_attribute("aria-expanded", "true")
            # The menu floats over the list rather than being clipped by it.
            box = rect_of(alpha_menu)
            on_top = page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('[id]')?.id",
                                   [box["x"] + box["width"] / 2, box["y"] + box["height"] - 4])
            self.assertEqual(on_top, alpha_menu.get_attribute("id"))

            beta_menu = draft_menu(page, "Beta")
            expect(beta_menu).to_be_visible()
            expect(alpha_menu).to_be_hidden()
            expect(alpha_more).to_have_attribute("aria-expanded", "false")

            beta_menu.get_by_role("button", name="Delete Beta").focus()
            page.keyboard.press("Escape")
            expect(beta_menu).to_be_hidden()
            expect(beta_more).to_be_focused()

            draft_menu(page, "Beta")
            page.get_by_role("heading", name="Lab Catalog").click()
            expect(beta_menu).to_be_hidden()

            draft_menu(page, "Beta")
            page.locator("#drafts-list").dispatch_event("scroll")
            expect(beta_menu).to_be_hidden()

            # Tabbing out of the row closes it, so it never covers the next row's focus.
            draft_menu(page, "Beta").get_by_role("button", name="Delete Beta").focus()
            page.keyboard.press("Tab")
            expect(explorer.get_by_role("button", name="Open Alpha")).to_be_focused()
            expect(beta_menu).to_be_hidden()

    def test_delete_asks_and_moves_focus_to_the_next_draft_then_new_draft(self) -> None:
        """Delete keeps its confirmation; focus goes to the next draft, then to New draft."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha", "Beta")
            explorer = labs_explorer(page)
            draft_menu(page, "Alpha").get_by_role("button", name="Delete Alpha").focus()
            page.keyboard.press("Enter")
            confirm = page.get_by_role("alertdialog", name="Delete draft")
            expect(confirm).to_have_accessible_description(
                "Delete Alpha for all Instructors? Anything already published "
                "stays in the Lab Catalog. This cannot be undone.")
            expect(confirm.get_by_role("button", name="Cancel")).to_be_focused()
            page.keyboard.press("Enter")
            expect(explorer.get_by_role("button", name="More actions for Alpha")).to_be_focused()

            draft_menu(page, "Alpha").get_by_role("button", name="Delete Alpha").click()
            confirm.get_by_role("button", name="Delete", exact=True).click()
            drafts = explorer.get_by_role("list", name="Drafts")
            expect(drafts.get_by_role("listitem")).to_have_count(1)
            expect(explorer.get_by_role("button", name="Open Beta")).to_be_focused()
            self.assertEqual(self._saved_names(page), ["Beta"])

            # Deleting the open draft leaves the explorer open where focus is.
            draft_menu(page, "Beta").get_by_role("button", name="Delete Beta").click()
            confirm.get_by_role("button", name="Delete", exact=True).click()
            expect(drafts.get_by_role("listitem")).to_have_count(0)
            expect(explorer.get_by_role("button", name="New draft")).to_be_focused()
            expect(page.locator("#draft-form")).to_be_hidden()
            expect(page.locator("#authoring-status")).to_contain_text(
                "Create a Lab draft, upload a script, or copy a lab from the Lab Catalog to begin.")
            self.assertEqual(self._saved_names(page), [])

    def test_copy_as_new_opens_the_copy_and_a_refused_copy_keeps_focus(self) -> None:
        """Copy as new opens a separate draft; when the open one cannot save, focus stays."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha")
            draft_menu(page, "Alpha").get_by_role("button", name="Copy as new Alpha").click()
            expect(page.locator("#draft-title")).to_have_text("Copy of Alpha")
            expect(page.get_by_role("toolbar", name="Add a Node").get_by_role(
                "button", name="Add Host")).to_be_focused()
            self.assertEqual(self._saved_names(page), ["Alpha", "Copy of Alpha"])

            self._place_unsaveable_node(page)
            draft_menu(page, "Copy of Alpha").get_by_role(
                "button", name="Copy as new Copy of Alpha").click()
            expect(labs_explorer(page).get_by_role(
                "button", name="More actions for Copy of Alpha")).to_be_focused()
            expect(page.locator("#status-message")).to_contain_text("Can't save yet")
            self.assertEqual(self._saved_names(page), ["Alpha", "Copy of Alpha"])

    def test_one_lab_catalog_row_expands_at_a_time(self) -> None:
        """A Lab Example is one line that expands to its description and actions."""
        with self.signed_in_page() as page:
            self._load(page)
            explorer = labs_explorer(page)
            catalog = explorer.get_by_role("region", name="Lab Catalog")
            search = catalog.get_by_role("searchbox", name="Search labs")
            expect(search).to_have_attribute("placeholder", "Search labs")
            expect(catalog.get_by_role("status")).to_have_text(re.compile(r"^\d+ labs shown\.$"))
            search.fill("Data Center")
            expect(catalog.get_by_role("status")).to_have_text("1 lab shown.")
            dc = page.locator("#example-source-list li[data-example-id='bgp-dc-lab']")
            summary = dc.get_by_role("button", name="BGP Data Center Lab", exact=True)
            expect(summary).to_have_accessible_description("BGP · 10 Nodes · Shipped")
            expect(summary).to_have_attribute("aria-expanded", "false")
            expect(dc.get_by_role("button", name="Copy BGP Data Center Lab")).to_be_hidden()
            expect(dc.get_by_role(
                "button", name="Start from Canvas version of BGP Data Center Lab"
            )).to_be_hidden()
            self.assertLess(rect_of(summary)["height"], 56, "a Lab Example row is two short lines")

            summary.focus()
            page.keyboard.press("Enter")
            expect(summary).to_have_attribute("aria-expanded", "true")
            expect(dc.get_by_text(re.compile("leaf-spine topology"))).to_be_visible()
            expect(dc.get_by_role("button", name="Copy BGP Data Center Lab")).to_be_visible()
            expect(dc.get_by_role(
                "button", name="Start from Canvas version of BGP Data Center Lab"
            )).to_be_visible()
            expect(dc).not_to_contain_text("About")
            screenshots(page, "rework-07", "catalog-expanded", "#labs-explorer")

            search.fill("")
            static = catalog_row(page, "Static Routing Lab", "static-lab")
            expect(static.get_by_role("button", name="Copy Static Routing Lab")).to_be_visible()
            search.fill("")
            expect(summary).to_have_attribute("aria-expanded", "false")
            expect(page.locator("#example-source-list button[aria-expanded='true']")).to_have_count(1)

            search.fill("no such lab anywhere")
            expect(catalog.get_by_role("status")).to_have_text("No matching labs.")

    def test_the_section_notes_open_from_their_info_buttons(self) -> None:
        """Each section's note sits behind an (i); the upload note still describes Upload."""
        with self.signed_in_page() as page:
            self._load(page)
            explorer = labs_explorer(page)
            upload = explorer.get_by_role("button", name="Upload a script…")
            expect(upload).to_have_accessible_description(
                "Makes a separate Python draft from one .py file, with no canvas to edit.")
            drafts_note = explorer.get_by_text("Shared by all Instructors. Deleting a draft keeps "
                                               "anything already published.")
            expect(drafts_note).to_be_hidden()
            explorer.get_by_role("button", name="About Drafts").click()
            expect(drafts_note).to_be_visible()
            expect(explorer.get_by_text("Makes a separate Python draft from one .py file, "
                                        "with no canvas to edit.")).to_be_visible()

            catalog_note = explorer.get_by_text("Shipped labs publish as a new lab; authored labs "
                                                "can also update their Catalog entry.")
            expect(catalog_note).to_be_hidden()
            explorer.get_by_role("button", name="About Lab Catalog").click()
            expect(catalog_note).to_be_visible()

    def test_drafts_and_the_lab_catalog_split_30_70_and_scroll_separately(self) -> None:
        """Each section keeps its heading and controls; its own list scrolls."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, *(f"Lesson {index:02}" for index in range(14)))
            explorer = labs_explorer(page)
            drafts = explorer.get_by_role("region", name="Drafts")
            catalog = explorer.get_by_role("region", name="Lab Catalog")
            share = rect_of(drafts)["height"] / (rect_of(drafts)["height"] + rect_of(catalog)["height"])
            self.assertAlmostEqual(share, .3, delta=.03)
            new_draft = drafts.get_by_role("button", name="New draft")
            search = catalog.get_by_role("searchbox", name="Search labs")
            before = (rect_of(new_draft)["y"], rect_of(search)["y"])
            scrolled = page.evaluate("""() => {
                const drafts = document.getElementById('drafts-list');
                drafts.scrollTop = drafts.scrollHeight;
                return [drafts.scrollTop > 0, document.getElementById('example-source-list').scrollTop,
                        document.getElementById('labs-explorer').scrollTop];
            }""")
            self.assertEqual(scrolled, [True, 0, 0])
            self.assertEqual((rect_of(new_draft)["y"], rect_of(search)["y"]), before)
            catalog_scrolls = page.evaluate("""() => {
                const list = document.getElementById('example-source-list');
                return list.scrollHeight > list.clientHeight;
            }""")
            self.assertTrue(catalog_scrolls)

    def test_one_draft_is_one_row_packed_at_the_top(self) -> None:
        """A short list does not stretch its rows to fill the section."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha")
            explorer = labs_explorer(page)
            row = explorer.get_by_role("list", name="Drafts").get_by_role("listitem")
            heading = explorer.get_by_role("heading", name="Drafts")
            self.assertLess(rect_of(row)["height"], 48)
            self.assertLess(rect_of(row)["y"] - rect_of(heading)["y"], 120)

    def test_the_canvas_toolbar_holds_the_draft_and_its_actions(self) -> None:
        """Draft name and Script on the left, Preview, Publish… and Close draft on the right."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Relay lesson")
            toolbar = page.locator("#draft-toolbar")
            parts = [toolbar.get_by_text("Relay lesson", exact=True),
                     toolbar.get_by_role("button", name="Script"),
                     toolbar.get_by_role("button", name="Preview"),
                     toolbar.get_by_role("button", name="Publish…"),
                     toolbar.get_by_role("button", name="Close draft")]
            lefts = [rect_of(part)["x"] for part in parts]
            self.assertEqual(lefts, sorted(lefts))
            bar = rect_of(toolbar)
            self.assertGreater(lefts[2], bar["x"] + bar["width"] / 2, "actions sit on the right")
            canvas = rect_of(page.locator("#draft-canvas"))
            self.assertGreaterEqual(canvas["y"], bar["y"] + bar["height"] - 1)
            hint = rect_of(page.locator("#canvas-help"))
            self.assertGreaterEqual(hint["y"], bar["y"] + bar["height"] - 1)
            expect(page.get_by_role("banner").get_by_text("Relay lesson")).to_have_count(0)
            screenshots(page, "rework-07", "canvas-toolbar")

    def test_details_starts_folded_and_a_node_still_opens_for_editing(self) -> None:
        """Details is a named rail on load; Enter on a Node still edits it."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Folded")
            details = page.get_by_role("complementary", name="Details")
            toggle = page.get_by_role("button", name="Details", exact=True)
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(details.get_by_text("Details", exact=True)).to_be_visible()
            expect(page.get_by_role("tabpanel", name="Lesson")).to_be_hidden()

            page.get_by_role("button", name="Add Host", exact=True).focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            expect(page.get_by_role("dialog", name=re.compile("h1"))).to_be_visible()
            page.keyboard.press("Escape")

            toggle.click()
            expect(page.get_by_role("tabpanel", name="Lesson")).to_be_visible()

    def test_secondary_buttons_hover_light_with_dark_text(self) -> None:
        """A secondary button never takes the primary blue under its own dark text."""
        with self.signed_in_page() as page:
            self._load(page)
            upload = labs_explorer(page).get_by_role("button", name="Upload a script…")
            upload.hover()
            page.wait_for_timeout(300)
            colours = upload.evaluate("""el => {
                const style = getComputedStyle(el);
                const root = getComputedStyle(document.documentElement);
                return [style.backgroundColor, style.color,
                        root.getPropertyValue('--surface-hover').trim()];
            }""")
            probe = page.evaluate("""colour => {
                const el = document.createElement('span');
                el.style.color = colour;
                document.body.append(el);
                const resolved = getComputedStyle(el).color;
                el.remove();
                return resolved;
            }""", colours[2])
            self.assertEqual(colours[0], probe)

    def test_loading_the_lists_never_moves_focus(self) -> None:
        """The explorer fills its lists without taking focus from where the Instructor is."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha")
            held: list[Any] = []
            page.route("**/api/authoring/drafts", lambda route: held.append(route))
            page.reload()
            page.wait_for_function("() => document.readyState === 'complete'")
            link = page.get_by_role("link", name="Return to Labs")
            link.focus()
            for route in held:
                route.continue_()
            page.unroute("**/api/authoring/drafts")
            expect(page.locator("#draft-form")).to_be_visible()
            expect(link).to_be_focused()

    def test_phone_width_stacks_the_explorer_above_the_canvas(self) -> None:
        """At 375 px the explorer sits above the canvas card and nothing scrolls sideways."""
        with self.signed_in_page() as page:
            self._load(page, 375, 800)
            self._seed_drafts(page, "Phone lesson")
            explorer = labs_explorer(page)
            canvas = page.locator("#draft-canvas")
            self.assertLessEqual(rect_of(explorer)["y"] + rect_of(explorer)["height"],
                                 rect_of(canvas)["y"])
            self.assertAlmostEqual(rect_of(explorer)["width"], 375 - 2 * 12, delta=14)
            overflow = page.evaluate(
                "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth")
            self.assertEqual(overflow, 0)
            screenshots(page, "rework-07", "phone-explorer")
            page.get_by_role("button", name="Labs", exact=True).click()
            self.assertLess(rect_of(explorer)["height"], 60, "folded, the explorer is one row")
            screenshots(page, "rework-07", "phone-folded")

    def test_a_draft_is_renamed_from_the_toolbar_and_the_explorer(self) -> None:
        """The toolbar's name and each row's ⋯ menu rename in place; Escape keeps the name."""
        with self.signed_in_page() as page:
            self._load(page)
            self._seed_drafts(page, "Alpha", "Beta")
            title = page.locator("#draft-title")
            field = page.get_by_role("textbox", name="Lab draft name")

            rename = page.get_by_role("button", name="Rename Beta", exact=True)
            rename.focus()
            page.keyboard.press("Enter")
            expect(field).to_be_focused()
            expect(field).to_have_value("Beta")
            page.keyboard.type("Discarded")
            page.keyboard.press("Escape")
            expect(field).to_have_count(0)
            expect(title).to_have_text("Beta")
            expect(rename).to_be_focused()

            rename.click()
            field.fill("Beta renamed")
            page.keyboard.press("Enter")
            expect(title).to_have_text("Beta renamed")
            expect(page.get_by_role("button", name="Rename Beta renamed", exact=True)).to_be_focused()
            open_details(page)
            expect(page.get_by_label("Catalog name")).to_have_value("Beta renamed")
            explorer = labs_explorer(page)
            expect(explorer.get_by_role("button", name="Open Beta renamed")).to_be_visible()
            screenshots(page, "rework-07", "rename-toolbar")

            draft_menu(page, "Alpha").get_by_role("button", name="Rename Alpha").click()
            expect(field).to_be_focused()
            screenshots(page, "rework-07", "rename-explorer", "#labs-explorer")
            field.fill("Alpha renamed")
            page.keyboard.press("Enter")
            expect(explorer.get_by_role("button", name="Open Alpha renamed")).to_be_focused()
            expect(title).to_have_text("Beta renamed")
            self.assertEqual(self._saved_names(page), ["Alpha renamed", "Beta renamed"])

            # Renaming the open draft from its row renames it everywhere.
            draft_menu(page, "Beta renamed").get_by_role("button", name="Rename Beta renamed").click()
            field.fill("Beta again")
            field.blur()
            expect(title).to_have_text("Beta again")
            expect(page.get_by_label("Catalog name")).to_have_value("Beta again")

            # A name typed in Details shows in the explorer as it is typed.
            page.get_by_label("Catalog name").fill("Gamma")
            expect(explorer.get_by_role("button", name="Open Gamma")).to_be_visible()
            expect(page.locator("#status-draft-state")).to_have_text("Saved")
            self.assertEqual(self._saved_names(page), ["Alpha renamed", "Gamma"])


class AuthoredPythonExplorerBrowserTests(AuthoringPageCase):
    """Python examples expose the same independent Copy and Revise actions."""

    def test_python_catalog_copy_and_revise_by_keyboard(self) -> None:
        """Origins are announced and Python Copy opens a fresh publication identity."""
        with patch.object(registry.lab_service, "launcher", PackageRecordingLauncher()):
            with self.signed_in_page() as page:
                page.goto(self.base_url + "/authoring")
                original = page.evaluate(r"""async () => {
                    const call = async (url, body, method = 'POST') => {
                        const response = await fetch(url, {method,
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(body)});
                        if (!response.ok) throw new Error(await response.text());
                        return response.json();
                    };
                    const form = new FormData();
                    form.append('file', new File(['def build_network():\n    pass\n'],
                        'Python lesson.py', {type: 'text/x-python'}));
                    let draft = await (await fetch('/api/authoring/drafts/upload',
                        {method: 'POST', body: form})).json();
                    draft.guide = '# Python lesson';
                    draft.catalog.area = 'Static';
                    draft.lesson_files = {'helpers/value.py':
                        {encoding: 'utf-8', content: 'VALUE = 7\n'}};
                    draft = await call(`/api/authoring/drafts/${draft.id}`,
                        {revision: draft.revision, document: draft}, 'PUT');
                    const preview = await call(`/api/authoring/drafts/${draft.id}/preview`,
                        {revision: draft.revision});
                    await call(`/api/labs/${preview.key}/stop`, {});
                    return call(`/api/authoring/drafts/${draft.id}/publish`,
                        {revision: draft.revision});
                }""")
                page.reload()
                page.wait_for_selector("#draft-form:not([hidden])")
                row = catalog_row(page, 'Python lesson', original['example']['id'])
                expect(row.locator('button[aria-expanded]')).to_have_accessible_description(
                    '2 Nodes · Authored · Python')
                copy = row.get_by_role('button', name='Copy Python lesson', exact=True)
                copy.focus()
                page.keyboard.press('Enter')
                expect(page.get_by_role('button', name='Labs', exact=True)).to_have_attribute(
                    'aria-expanded', 'false')
                drafts = page.evaluate("""async () =>
                    (await (await fetch('/api/authoring/drafts')).json()).drafts""")
                copied = next(draft for draft in drafts if draft['id'] != original['draft']['id'])
                self.assertIsNone(copied['catalog']['id'])
                self.assertEqual(copied['lesson_files'], original['draft']['lesson_files'])
                row = catalog_row(page, 'Python lesson', original['example']['id'])
                row.get_by_role('button', name='Revise Python lesson', exact=True).focus()
                page.keyboard.press('Enter')
                expect(page.get_by_role('button', name='Labs', exact=True)).to_have_attribute(
                    'aria-expanded', 'false')
                drafts = page.evaluate("""async () =>
                    (await (await fetch('/api/authoring/drafts')).json()).drafts""")
                revised = next(draft for draft in drafts if draft['id'] not in
                               (original['draft']['id'], copied['id']))
                self.assertEqual(revised['catalog']['id'], original['example']['id'])
                self.assertEqual(revised['python'], original['draft']['python'])
                self.assertEqual(revised['lesson_files'], original['draft']['lesson_files'])

    def test_shipped_copy_and_canvas_start_choose_distinct_sources(self) -> None:
        """Keyboard Copy opens exact Python; Canvas remains a separate starting point."""
        from pathlib import Path

        with self.signed_in_page() as page:
            page.goto(self.base_url + "/authoring")
            row = catalog_row(page, 'BGP Data Center', 'bgp-dc-lab')
            canvas = row.get_by_role('button', name='Start from Canvas version of BGP Data Center Lab')
            expect(canvas).to_have_accessible_description(
                'The prepared Canvas definition can differ from the shipped recipe.')
            row.get_by_role('button', name='Copy BGP Data Center Lab', exact=True).focus()
            page.keyboard.press('Enter')
            expect(page.get_by_role('region', name='Status', exact=True).get_by_role('status')).to_contain_text('Created a copy')
            drafts = page.evaluate("""async () =>
                (await (await fetch('/api/authoring/drafts')).json()).drafts""")
            self.assertEqual(len(drafts), 1)
            copied = drafts[0]
            self.assertEqual(copied['kind'], 'python')
            source = Path(__file__).resolve().parents[2] / 'examples/bgp-dc-lab.py'
            self.assertEqual(copied['python'].encode(), source.read_bytes())
            self.assertEqual(len(copied['files']), 7)
            self.assertIsNone(copied['catalog']['id'])
            row = catalog_row(page, 'BGP Data Center', 'bgp-dc-lab')
            row.get_by_role('button', name='Start from Canvas version of BGP Data Center Lab').focus()
            page.keyboard.press('Enter')
            expect(page.get_by_role('region', name='Status', exact=True).get_by_role('status')).to_contain_text('Canvas definition can differ')
            drafts = page.evaluate("""async () =>
                (await (await fetch('/api/authoring/drafts')).json()).drafts""")
            self.assertEqual(len(drafts), 2)
            adapted = next(item for item in drafts if item['id'] != copied['id'])
            self.assertNotIn('kind', adapted)
            self.assertTrue(any(node['name'] == 'rs1' for node in adapted['topology']['nodes']))
