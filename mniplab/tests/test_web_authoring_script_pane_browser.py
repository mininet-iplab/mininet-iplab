"""The Script pane beside the canvas: one editor whose Code section is the band to type in."""

import re
from typing import Any
from unittest.mock import patch

import pytest

from mniplab.labprocess import LabStartError
from mniplab.tests.authoring_editors import (
    ROUTED, ROUTED_LINKS, AuthoringPageCase, open_from_explorer,
)
from mniplab.tests.browser import Locator, Page, box_of, expect, screenshots
from mniplab.webserver import registry
from mniplab.webserver.authoring import AuthoringRepository

pytestmark = pytest.mark.browser_authoring


class ScriptPaneBrowserTests(AuthoringPageCase):
    """An Instructor reads the script and writes its Code section beside the canvas."""

    @staticmethod
    def _toggle(page: Page) -> Locator:
        return page.get_by_role("button", name="Script", exact=True)

    @staticmethod
    def _pane(page: Page) -> Locator:
        return page.get_by_role("region", name="Script", exact=True)

    @staticmethod
    def _script(page: Page) -> Locator:
        return page.get_by_role("textbox", name="Lab script", exact=True)

    def _source(self, page: Page) -> str:
        """The saved draft's script, as a Preview runs it and Download .zip packs it."""
        draft = self._saved_draft(page)
        return page.request.get(
            f"{self.base_url}/api/authoring/drafts/{draft['id']}/python").json()["source"]

    @staticmethod
    def _cursor_line(page: Page) -> str:
        """The line holding the script's cursor, focused or not."""
        return page.evaluate("""async () => {
            const { EditorView } = await import('/static/js/lib/codemirror.bundle.js');
            const view = EditorView.findFromDOM(document.querySelector('#script-editor .cm-editor'));
            return view.state.doc.lineAt(view.state.selection.main.head).text;
        }""")

    def test_the_script_toggle_opens_one_pane_beside_the_canvas_and_folds_the_sidebar(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            toggle = self._toggle(page)
            pane = self._pane(page)
            sidebar_toggle = page.get_by_role("button", name="Details", exact=True)
            expect(toggle).to_have_attribute("aria-pressed", "false")
            expect(pane).to_be_hidden()
            # The sidebar holds Selection, Lesson and Guide; the script is not in it.
            expect(page.get_by_role("tab")).to_have_text(["Lesson", "Guide"])
            expect(page.get_by_role("tab", name="Python")).to_have_count(0)

            toggle.focus()
            page.keyboard.press("Enter")
            expect(toggle).to_have_attribute("aria-pressed", "true")
            expect(toggle).to_be_focused()
            expect(pane).to_be_visible()
            expect(sidebar_toggle).to_have_attribute("aria-expanded", "false")
            canvas = page.locator("#draft-canvas-drop").bounding_box()
            box = pane.bounding_box()
            rail = page.get_by_role("complementary", name="Details").bounding_box()
            assert canvas is not None and box is not None and rail is not None
            self.assertAlmostEqual(box["x"], canvas["x"] + canvas["width"], delta=2)
            self.assertAlmostEqual(box["x"] + box["width"], rail["x"], delta=2)
            self.assertLess(rail["width"], 60)
            self.assertGreater(canvas["width"], 320)
            self.assertGreater(box["width"], 320)
            expect(pane.get_by_role("button", name="Download .zip", exact=True)).to_be_visible()

            page.keyboard.press("Space")
            expect(pane).to_be_hidden()
            expect(toggle).to_have_attribute("aria-pressed", "false")
            expect(toggle).to_be_focused()
            expect(sidebar_toggle).to_have_attribute("aria-expanded", "true")

            # A sidebar folded by hand stays folded when the pane closes.
            sidebar_toggle.click()
            toggle.click()
            toggle.click()
            expect(sidebar_toggle).to_have_attribute("aria-expanded", "false")

    def test_the_canvas_fits_again_when_the_pane_opens_and_closes(self) -> None:
        spread: list[tuple[str, int, int, str, dict[str, Any]]] = [
            ("r1", 50, 100, "router", {}), ("h1", 950, 100, "host", {}),
            ("h2", 950, 600, "host", {})]
        with self.signed_in_page() as page:
            self._seed(page, spread, [("h1", "r1")], address_family="ipv4")
            transform = page.locator("#draft-canvas-g")
            opened_with = transform.get_attribute("transform")

            def all_in_view() -> None:
                canvas = page.locator("#draft-canvas-drop").bounding_box()
                assert canvas is not None
                for name in ("r1", "h1", "h2"):
                    box = self._node(page, name).bounding_box()
                    assert box is not None
                    self.assertGreaterEqual(box["x"], canvas["x"] - 1, name)
                    self.assertLessEqual(box["x"] + box["width"], canvas["x"] + canvas["width"] + 1, name)

            self._toggle(page).click()
            expect(transform).not_to_have_attribute("transform", opened_with or "")
            all_in_view()
            self._toggle(page).click()
            expect(transform).to_have_attribute("transform", opened_with or "")
            all_in_view()

    def test_the_canvas_fits_again_when_the_sidebar_is_resized(self) -> None:
        spread: list[tuple[str, int, int, str, dict[str, Any]]] = [
            ("r1", 50, 100, "router", {}), ("h1", 950, 100, "host", {}),
            ("h2", 950, 600, "host", {})]
        with self.signed_in_page() as page:
            self._seed(page, spread, [("h1", "r1")], address_family="ipv4")
            transform = page.locator("#draft-canvas-g")

            def fits_after(change) -> None:
                before = transform.get_attribute("transform")
                change()
                expect(transform).not_to_have_attribute("transform", before or "")
                canvas = page.locator("#draft-canvas-drop").bounding_box()
                assert canvas is not None
                for name in ("r1", "h1", "h2"):
                    box = self._node(page, name).bounding_box()
                    assert box is not None
                    self.assertGreaterEqual(box["x"], canvas["x"] - 1, name)
                    self.assertLessEqual(box["x"] + box["width"],
                                         canvas["x"] + canvas["width"] + 1, name)

            divider = page.get_by_role("separator", name="Sidebar width")

            def focus_then_end() -> None:
                divider.focus()
                page.keyboard.press("End")

            fits_after(focus_then_end)
            fits_after(lambda: page.get_by_role("button", name="Narrower").click())
            fits_after(lambda: page.get_by_role("button", name="Details", exact=True).click())
            fits_after(lambda: page.get_by_role("button", name="Details", exact=True).click())

    def test_the_divider_resizes_the_pane_by_pointer_and_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            self._toggle(page).click()
            pane = self._pane(page)
            divider = page.get_by_role("separator", name="Script width")
            expect(divider).to_have_attribute("aria-orientation", "vertical")
            width = lambda: box_of(pane)["width"]  # noqa: E731
            initial = width()
            expect(divider).to_have_attribute("aria-valuenow", str(round(initial)))

            divider.focus()
            page.keyboard.press("ArrowLeft")
            self.assertAlmostEqual(width(), initial + 40, delta=1)
            page.keyboard.press("ArrowRight")
            self.assertAlmostEqual(width(), initial, delta=1)
            page.keyboard.press("Home")
            self.assertAlmostEqual(width(), 320, delta=1)
            expect(divider).to_have_attribute("aria-valuenow", "320")
            page.keyboard.press("End")
            self.assertGreaterEqual(box_of(page.locator("#draft-canvas-drop"))["width"], 319)
            expect(divider).to_have_attribute("aria-valuemax", str(round(width())))
            page.keyboard.press("Tab")
            expect(divider).not_to_be_focused()

            page.keyboard.press("Shift+Tab")
            page.keyboard.press("Home")
            handle = divider.bounding_box()
            assert handle is not None
            page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
            page.mouse.down()
            page.mouse.move(handle["x"] + handle["width"] / 2 - 100,
                            handle["y"] + handle["height"] / 2, steps=5)
            page.mouse.up()
            self.assertAlmostEqual(width(), 420, delta=2)

    def test_below_1024_px_the_canvas_and_the_pane_stack(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            self._toggle(page).click()
            for width in (1000, 900, 375):
                page.set_viewport_size({"width": width, "height": 720})
                canvas = page.locator("#draft-canvas-drop").bounding_box()
                pane = self._pane(page).bounding_box()
                assert canvas is not None and pane is not None
                self.assertGreaterEqual(pane["y"], canvas["y"] + canvas["height"] - 1, width)
                self.assertGreater(pane["height"], 200, width)
                expect(page.get_by_role("separator", name="Script width")).to_be_hidden()
                self.assertTrue(page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth"), width)
                self._script(page).scroll_into_view_if_needed()
                expect(self._script(page)).to_be_visible()

    def test_only_the_code_section_band_is_editable(self) -> None:
        with self.signed_in_page() as page:
            page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                           origin=self.base_url)
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            self._toggle(page).click()
            script = self._script(page)
            expect(script).to_contain_text("r1 = net.addRouter('r1')")
            expect(script).to_contain_text("run_lab(")
            band = page.locator(".cm-code-band")
            expect(band).to_have_count(1)

            # The pane opens on the Code section, with the cursor in it.
            page.keyboard.press("Tab")
            while not page.evaluate(
                    "() => document.activeElement?.getAttribute('aria-label') === 'Lab script'"):
                page.keyboard.press("Tab")
            expect(script).to_be_focused()
            page.keyboard.type("r1.cmd('true')")
            page.keyboard.press("Enter")
            page.keyboard.type("r1.cmd('echo')")
            expect(page.locator("#code-section-marker")).to_be_visible()
            self.assertEqual(self._saved_draft(page)["code_section"],
                             "r1.cmd('true')\nr1.cmd('echo')")
            source = self._source(page)
            lines = source.split("\n")
            first = lines.index("    r1.cmd('true')") + 1
            expect(band).to_have_count(2)
            expect(page.locator("#code-section-band")).to_have_text(
                f"Code section · lines {first}–{first + 1} are yours; "
                "the rest is generated from the canvas.")
            expect(script).to_have_accessible_description(re.compile(
                f"lines {first}–{first + 1} are yours"))
            # Real line numbers: the band's gutter numbers are the script's.
            gutter = page.locator(".cm-lineNumbers .cm-gutterElement").filter(
                has_text=re.compile(f"^{first}$"))
            expect(gutter).to_have_count(1)
            screenshots(page, "rework-05", "script-pane", "#code-section-band")
            script.focus()

            # Typing on a generated line is refused and says where to type.
            page.keyboard.press("Control+Home")
            page.keyboard.type("x")
            expect(page.locator(".cm-line").first).to_have_text(lines[0])
            expect(page.locator("#status-message")).to_have_text(
                f"Generated from the canvas: type in the Code section, lines {first}–{first + 1}.")
            page.keyboard.press("Control+End")
            page.keyboard.press("Backspace")
            expect(page.locator("#status-message")).to_have_text(
                f"Generated from the canvas: type in the Code section, lines {first}–{first + 1}.")

            # Ctrl+A / Ctrl+C take the whole script, as it runs.
            page.keyboard.press("Control+a")
            page.keyboard.press("Control+c")
            self.assertEqual(page.evaluate("() => navigator.clipboard.readText()"), source)

            # Tab leaves the editor; it never traps focus.
            page.keyboard.press("Control+Home")
            page.keyboard.press("Tab")
            expect(script).not_to_be_focused()

            # A canvas edit regenerates the generated lines and keeps the band.
            self._node(page, "r1").focus()
            page.keyboard.press("F2")
            page.get_by_role("textbox", name="Rename r1").fill("core1")
            page.keyboard.press("Enter")
            expect(script).to_contain_text("core1 = net.addRouter('core1')")
            expect(band.first).to_have_text("    r1.cmd('true')")
            self.assertEqual(self._saved_draft(page)["code_section"],
                             "r1.cmd('true')\nr1.cmd('echo')")

    def test_code_pasted_into_the_band_keeps_its_indentation(self) -> None:
        loop = ("for index in range(1, 4):\n"
                "    leaf = net.addRouter(f'leaf{index}')\n"
                "    net.addLink(r1, leaf)")
        with self.signed_in_page() as page:
            page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                           origin=self.base_url)
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            self._toggle(page).click()
            page.evaluate("text => navigator.clipboard.writeText(text)", loop)
            self._script(page).focus()
            page.keyboard.press("Control+v")
            expect(page.locator(".cm-code-band")).to_have_text([
                "    for index in range(1, 4):",
                "        leaf = net.addRouter(f'leaf{index}')",
                "        net.addLink(r1, leaf)",
            ])
            self.assertEqual(self._saved_draft(page)["code_section"], loop)
            expect(page.get_by_role("button", name=re.compile(
                r"^Router leaf3, from Code section, read-only"))).to_have_count(1)

    def test_a_block_typed_from_the_bands_left_edge_keeps_its_shape(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            self._toggle(page).click()
            self._script(page).focus()
            page.keyboard.press("Backspace")
            page.keyboard.type("if True:")
            page.keyboard.press("Enter")
            page.keyboard.type("r1.cmd('true')")
            self.assertEqual(self._saved_draft(page)["code_section"],
                             "if True:\n    r1.cmd('true')")

    def test_show_in_code_section_lands_on_its_line_before_the_script_loads(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            self._toggle(page).click()
            self._script(page).focus()
            page.keyboard.type("net.addLink(r1, net.addRouter('r9'))\nr1.cmd('true')")
            expect(page.get_by_role("button", name=re.compile(r"^Router r9, from"))).to_be_visible()
            held: list = []
            page.route("**/python", lambda route: held.append(route))
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            greyed = page.get_by_role("button", name=re.compile(r"^Router r9, from"))
            greyed.focus()
            page.keyboard.press("Enter")
            expect(self._script(page)).to_be_focused()
            page.unroute("**/python")
            for route in held:
                route.fallback()
            expect(self._script(page)).to_contain_text("return net")
            self.assertEqual(self._cursor_line(page), "    net.addLink(r1, net.addRouter('r9'))")

    def test_completion_and_api_help_work_in_the_code_section_band(self) -> None:
        """Lab API suggestions after a dot, in their Python spelling, never in a string; F1 explains."""
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            self._toggle(page).click()
            script = self._script(page)
            script.focus()
            expect(script).to_have_accessible_description(re.compile(r"F1 may be taken by the browser"))
            page.keyboard.type("net.")
            page.keyboard.press("Control+Space")
            expect(page.get_by_role("option", name=re.compile("addRouter"))).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("listbox", name="Completions")).to_have_count(0)
            expect(script).to_be_focused()
            page.keyboard.type("addR")
            page.keyboard.press("Control+Space")
            choice = page.get_by_role("option", name=re.compile("addRouter"))
            expect(choice).to_be_visible()
            choice.click()
            expect(page.locator(".cm-code-band")).to_contain_text("net.addRouter")
            page.keyboard.type("('r9')")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("F1")
            help_region = page.get_by_role("region", name="Mininet-IPLab API help")
            expect(help_region).to_contain_text("Add a router to the network with FRR routing support.")
            expect(help_region).to_be_focused()
            self.assertIn("net.addRouter('r9')", self._saved_draft(page)["code_section"])

            pane = self._pane(page)
            pane.get_by_role("button", name="Show API help", exact=True).focus()
            page.keyboard.press("Enter")
            expect(help_region).to_contain_text("addRouter(name")
            expect(help_region).to_be_focused()

            script.focus()
            page.keyboard.press("End")
            page.keyboard.press("Enter")
            page.keyboard.type("net.addNat")
            page.keyboard.press("Control+Space")
            choice = page.get_by_role("option", name=re.compile("addNAT"))
            expect(choice).to_be_visible()
            expect(page.get_by_text(re.compile(r"addNAT\(name, subnet=None"))).to_be_visible()
            choice.click()
            expect(page.locator(".cm-code-band").last).to_contain_text("net.addNAT")

            page.keyboard.press("Enter")
            page.keyboard.type('message = "net.addR')
            page.keyboard.press("Control+Space")
            expect(page.get_by_role("option", name=re.compile("addRouter"))).to_have_count(0)
            page.keyboard.press("F1")
            expect(help_region).to_contain_text("Place the cursor on a supported mnIPLab method")

    def _python_draft(self, page: Page) -> None:
        """Open a Python draft of the seeded draft's script with one Seed, as Keep leaves one."""
        self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
        python = page.evaluate("""async () => {
            const drafts = (await (await fetch('/api/authoring/drafts')).json()).drafts;
            return (await (await fetch(`/api/authoring/drafts/${drafts[0].id}/python`)).json()).source;
        }""")
        AuthoringRepository(self.tmp.name).create_python(
            {"name": "Routed lesson", "description": "", "protocols": []}, python,
            {"frr-config/lesson/r1/frr.conf": "router ospf\n"}, "", {"version": 1, "nodes": {}})
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")

    def test_a_python_draft_opens_with_its_files_in_the_pane(self) -> None:
        with self.signed_in_page() as page:
            self._python_draft(page)
            pane = self._pane(page)
            expect(pane).to_be_visible()
            expect(pane).to_contain_text("Edit the script and Seeds here")
            expect(self._toggle(page)).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_role("button", name="Details", exact=True)).to_have_attribute(
                "aria-expanded", "false")

            # One tab strip names lab.py once and each Seed; line 1 sits under it.
            files = pane.get_by_role("tablist", name="Files")
            tabs = files.get_by_role("tab")
            expect(tabs).to_have_text(["lab.py", "lesson/r1/frr.conf"])
            expect(tabs.first).to_have_attribute("aria-selected", "true")
            expect(pane.get_by_text("lab.py", exact=True)).to_have_count(1)
            expect(pane.get_by_text("Lab script")).to_have_count(0)
            strip = page.locator(".authoring-file-strip").bounding_box()
            first_line = page.locator("#python-code-editor .cm-line").first.bounding_box()
            assert strip is not None and first_line is not None
            self.assertLess(first_line["y"] - (strip["y"] + strip["height"]), 12)
            script = self._script(page)
            expect(script).to_have_accessible_description(re.compile("Ctrl\\+F searches"))
            expect(page.get_by_role("button", name=re.compile(r"^Router r1, read-only"))).to_be_visible()
            screenshots(page, "rework-05", "script-pane-python", fit=True)

            # The help and key hints fold into one Keys tip.
            keys = pane.get_by_role("button", name="About Keys")
            expect(keys).to_have_text("Keys")
            expect(keys).to_have_attribute("aria-expanded", "false")
            hint = page.locator("#script-keys")
            expect(hint).to_be_hidden()
            keys.focus()
            page.keyboard.press("Enter")
            expect(hint).to_be_visible()
            expect(hint).to_contain_text("A Seed file is a Router's frr.conf")
            expect(hint).to_contain_text("starts with an empty configuration")
            expect(hint).not_to_contain_text("Ctrl+Space")
            expect(page.locator("#code-section-help")).to_be_hidden()
            screenshots(page, "rework-05", "script-pane-keys", "#script-keys")
            keys.click()

            # Arrow keys move through the files; a Seed has no API help.
            tabs.first.focus()
            page.keyboard.press("ArrowRight")
            expect(tabs.nth(1)).to_be_focused()
            expect(tabs.nth(1)).to_have_attribute("aria-selected", "true")
            seed = page.get_by_role("textbox", name="Seed file frr-config/lesson/r1/frr.conf")
            expect(seed).to_contain_text("router ospf")
            expect(seed).to_be_editable()
            expect(seed).to_have_accessible_description(
                "Ctrl+F searches · Tab leaves the editor")
            expect(pane.get_by_role("tabpanel")).to_have_accessible_name("lesson/r1/frr.conf")
            expect(pane.get_by_role("button", name="Show API help")).to_be_hidden()
            page.keyboard.press("Home")
            expect(tabs.first).to_be_focused()
            expect(script).to_be_visible()
            page.keyboard.press("Tab")
            expect(script).to_be_focused()

    def test_python_edits_during_a_delayed_save_keep_the_latest_source_and_seed(self) -> None:
        with self.signed_in_page() as page:
            self._python_draft(page)
            source = self._source(page)
            script = self._script(page)
            held: list = []
            page.route("**/api/authoring/drafts/*", lambda route:
                       held.append(route) if route.request.method == "PUT" else route.fallback())
            script.focus()
            page.keyboard.press("Control+End")
            with page.expect_request(lambda request: request.method == "PUT"
                                     and "/api/authoring/drafts/" in request.url):
                page.keyboard.type("\ndef unfinished(")
            page.wait_for_timeout(50)
            self.assertEqual(len(held), 1)
            response = held[0].fetch()
            self.assertEqual(response.status, 200)
            script.focus()
            page.keyboard.press("Control+a")
            page.keyboard.insert_text(source + "\n# newest source\n")
            page.get_by_role("tab", name="lesson/r1/frr.conf", exact=True).click()
            seed = page.get_by_role("textbox", name="Seed file frr-config/lesson/r1/frr.conf")
            seed.focus()
            page.keyboard.press("Control+End")
            page.keyboard.type("! newest seed")
            held[0].fulfill(response=response)
            page.unroute("**/api/authoring/drafts/*")
            expect(seed).to_be_focused()
            expect(seed).to_contain_text("! newest seed")
            saved = self._saved_draft(page)
            self.assertEqual(saved["python"], source + "\n# newest source\n")
            self.assertEqual(saved["files"]["frr-config/lesson/r1/frr.conf"],
                             "router ospf\n! newest seed")
            self.assertIsNone(saved["python_error"])
            page.get_by_role("button", name="Close draft", exact=True).click()
            open_from_explorer(page, saved["catalog"]["name"] + ", Python")
            expect(page.get_by_role("tab", name="lesson/r1/frr.conf", exact=True)).to_have_attribute(
                "aria-selected", "true")
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            self.assertEqual(self._source(page), source + "\n# newest source\n")
            expect(page.get_by_role("tab", name="lesson/r1/frr.conf", exact=True)).to_have_attribute(
                "aria-selected", "true")
            page.get_by_role("tab", name="lab.py", exact=True).click()
            script = self._script(page)
            script.focus()
            page.keyboard.press("Control+End")
            page.keyboard.type("def unfinished(")
            saved = self._saved_draft(page)
            self.assertIsNotNone(saved["python_error"])
            expect(page.locator("#python-source-error")).to_contain_text("Fix the script")
            page.keyboard.press("Tab")
            expect(script).not_to_be_focused()

    def test_selecting_a_greyed_node_moves_the_cursor_only_in_an_open_pane(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            toggle = self._toggle(page)
            toggle.click()
            self._script(page).focus()
            page.keyboard.type("r1.cmd('true')\nnet.addLink(r1, net.addRouter('r9'))")
            greyed = page.get_by_role("button", name=re.compile(r"^Router r9, from Code section"))
            expect(greyed).to_be_visible()
            page.keyboard.press("Control+Home")
            page.keyboard.press("ArrowDown")

            greyed.click()
            expect(greyed).to_be_focused()
            self.assertEqual(self._cursor_line(page), "    net.addLink(r1, net.addRouter('r9'))")
            page.keyboard.press("Control+Home")
            self._node(page, "r1").click()
            self.assertEqual(self._cursor_line(page), "    net.addLink(r1, net.addRouter('r9'))")

            # With the pane closed, selecting opens nothing; Enter opens it at the line.
            toggle.click()
            greyed.focus()
            expect(self._pane(page)).to_be_hidden()
            page.keyboard.press("Enter")
            expect(self._pane(page)).to_be_visible()
            expect(self._script(page)).to_be_focused()
            self.assertEqual(self._cursor_line(page), "    net.addLink(r1, net.addRouter('r9'))")

    def test_a_preview_failing_in_the_code_section_opens_the_pane_at_its_line(self) -> None:
        failure: list = []

        async def failing(*_args: Any, **_kwargs: Any) -> None:
            raise LabStartError(failure[0])

        swap = patch.object(registry.lab_service, "launcher", failing)
        swap.start()
        self.addCleanup(swap.stop)
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            toggle = self._toggle(page)
            toggle.click()
            script = self._script(page)
            script.focus()
            page.keyboard.type("r1.cmd('true')\nr1.cmd('echo')")
            lines = self._source(page).split("\n")
            failing_line = lines.index("    r1.cmd('echo')") + 1
            failure.append(f"r1-eth9 not found (line {failing_line} of lab.py: r1.cmd('echo'))")
            toggle.click()

            preview = page.get_by_role("button", name="Preview", exact=True)
            preview.focus()
            page.keyboard.press("Enter")
            expect(page.locator("#status-preview-state").get_by_role("alert")).to_contain_text(
                "Code section line 2")
            expect(self._pane(page)).to_be_visible()
            expect(preview).to_be_focused()
            self.assertEqual(self._cursor_line(page), "    r1.cmd('echo')")
            expect(script).to_have_attribute("aria-invalid", "true")
            expect(page.get_by_text(f"⚠ {failing_line}", exact=True)).to_be_visible()
            expect(script).to_have_accessible_description(re.compile("Code section line 2"))
            screenshots(page, "rework-05", "script-pane-error", "#code-section-error")

            # A note found while typing marks its line but never moves the cursor.
            script.focus()
            page.keyboard.press("End")
            page.keyboard.type("\nr9.cmd('x')")
            expect(page.locator("#drawing-note")).to_contain_text("Code section line 3")
            page.keyboard.press("ArrowUp")
            page.keyboard.press("End")
            with page.expect_response("**/drawing"):
                page.keyboard.type("  # here")
            expect(page.locator("#drawing-note")).to_contain_text("Code section line 3")
            self.assertEqual(self._cursor_line(page), "    r1.cmd('echo')  # here")
            page.keyboard.press("Control+z")
            page.keyboard.press("Control+z")
            expect(page.locator("#drawing-note")).to_be_hidden()

            # Editing the Code section drops the mark until the next failure.
            script.focus()
            page.keyboard.press("End")
            page.keyboard.type("  # checked")
            expect(script).to_have_attribute("aria-invalid", "false")
            expect(page.get_by_text(f"⚠ {failing_line}", exact=True)).to_have_count(0)
            expect(page.locator("#code-section-error")).to_be_hidden()

    def test_a_drawing_note_marks_its_line_without_opening_the_pane(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            toggle = self._toggle(page)
            toggle.click()
            self._script(page).focus()
            page.keyboard.type("h1.cmd('true')\nr1.cmd('true')")
            expect(page.locator("#status-draft-state")).to_have_text("Saved")
            toggle.click()

            # A rename on the canvas breaks line 2; the pane stays closed.
            self._node(page, "r1").focus()
            page.keyboard.press("F2")
            page.get_by_role("textbox", name="Rename r1").fill("core1")
            page.keyboard.press("Enter")
            expect(page.locator("#drawing-note")).to_contain_text("Code section line 2")
            expect(self._pane(page)).to_be_hidden()
            expect(toggle).to_have_attribute("aria-pressed", "false")

            # Opening it shows the marked line, with the cursor on it.
            toggle.click()
            script = self._script(page)
            expect(script).to_have_attribute("aria-invalid", "true")
            self.assertEqual(self._cursor_line(page), "    r1.cmd('true')")
            expect(page.locator("#code-section-error")).to_contain_text("Code section line 2")

    def test_unedited_script_and_seeds_keep_bom_and_mixed_newlines(self) -> None:
        """Saving other fields never normalizes the source editor's raw inputs."""
        with self.signed_in_page() as page:
            self._seed(page, [], address_family="ipv4")
            source = ("\ufeff# source\r\nfrom mniplab import mnIPLab\r\n"
                      "def build_network():\r    return mnIPLab()\n")
            seed = "\ufeff! seed\r\nrouter ospf\r! mixed\n"
            AuthoringRepository(self.tmp.name).create_python(
                {"name": "Exact source", "description": "", "protocols": []}, source,
                {"frr-config/r1/frr.conf": seed}, "", {"version": 1, "nodes": {}})
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            page.get_by_role("tab", name="r1/frr.conf", exact=True).click()
            page.get_by_role("button", name="Details", exact=True).click()
            page.get_by_label("Catalog name").fill("Exact source retained")
            saved = self._saved_draft(page)
            self.assertEqual(saved["python"].encode("utf-8"), source.encode("utf-8"))
            self.assertEqual(saved["files"]["frr-config/r1/frr.conf"].encode("utf-8"),
                             seed.encode("utf-8"))
            page.reload()
            saved = self._saved_draft(page)
            self.assertEqual(saved["python"], source)
            self.assertEqual(saved["files"]["frr-config/r1/frr.conf"], seed)

    def test_keys_help_has_readable_text_when_expanded_or_hovered(self) -> None:
        """Keys help keeps readable text when expanded or hovered in either theme."""
        with self.signed_in_page() as page:
            self._python_draft(page)
            keys = page.get_by_role("button", name="About Keys")
            keys.press("Enter")
            expect(keys).to_have_attribute("aria-expanded", "true")
            page.add_style_tag(
                content="*, *::before, *::after { transition: none !important; }"
            )
            for theme in ("light", "dark"):
                for expanded in (True, False):
                    with self.subTest(theme=theme, expanded=expanded):
                        page.evaluate(
                            "theme => document.documentElement.dataset.theme = theme", theme
                        )
                        if keys.get_attribute("aria-expanded") != str(expanded).lower():
                            keys.press("Enter")
                        keys.hover()
                        colors = keys.evaluate("""element => {
                            const style = getComputedStyle(element);
                            return [style.color, style.backgroundColor];
                        }""")
                        luminances = []
                        for color in colors:
                            channels = [
                                int(value) / 255 for value in re.findall(r"\d+", color)[:3]
                            ]
                            linear = [
                                value / 12.92 if value <= 0.04045
                                else ((value + 0.055) / 1.055) ** 2.4
                                for value in channels
                            ]
                            luminances.append(sum(
                                value * weight
                                for value, weight in zip(linear, (0.2126, 0.7152, 0.0722))
                            ))
                        ratio = (max(luminances) + 0.05) / (min(luminances) + 0.05)
                        self.assertGreaterEqual(ratio, 4.5, f"{theme} Keys help, expanded={expanded}")
