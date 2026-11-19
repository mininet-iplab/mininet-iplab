"""A Canvas draft's Code section in the Script pane and on the canvas."""

import re

import pytest

from mniplab.tests.authoring_editors import (
    ROUTED, ROUTED_LINKS, AuthoringPageCase, TEXT_CONTRAST,
    open_details, open_from_explorer, upload_script,
)
from mniplab.tests.browser import Locator, Page, expect, screenshots

pytestmark = pytest.mark.browser_authoring


class CodeSectionBrowserTests(AuthoringPageCase):
    """An Instructor adds a little Python without leaving the Canvas draft."""

    @staticmethod
    def _script_pane(page: Page) -> Locator:
        """Open the Script pane and return its script."""
        page.get_by_role("button", name="Script", exact=True).click()
        return page.get_by_role("textbox", name="Lab script", exact=True)

    def test_the_code_section_is_edited_in_place_and_survives_canvas_edits(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            marker = page.locator("#code-section-marker")
            expect(marker).to_be_hidden()
            script = self._script_pane(page)
            expect(script).to_contain_text("r1 = net.addRouter('r1')")
            expect(script).to_contain_text("return net")

            # The script opens with the cursor at the end of its Code section.
            script.focus()
            expect(script).to_have_accessible_description(re.compile("end of build_network"))
            page.keyboard.type("r1.cmd('true')\n")
            expect(marker).to_be_visible()
            expect(marker).to_have_text("+ Code section")
            expect(page.locator("#draft-canvas-description")).to_contain_text(
                "The Lab also has a Code section.")
            self.assertEqual(self._saved_draft(page)["code_section"], "r1.cmd('true')\n")
            # The band names its lines as the downloaded script numbers them.
            source = page.request.get(
                f"{self.base_url}/api/authoring/drafts/{self._saved_draft(page)['id']}/python"
            ).json()["source"]
            first = source.splitlines().index("    r1.cmd('true')") + 1
            expect(page.locator("#code-section-band")).to_contain_text(f"lines {first}–{first + 1}")

            screenshots(page, "rework-05", "code-section", "#script-editor")

            # A canvas rename regenerates the script and leaves the Code section as written.
            self._node(page, "r1").focus()
            page.keyboard.press("F2")
            page.get_by_role("textbox", name="Rename r1").fill("core1")
            page.keyboard.press("Enter")
            expect(script).to_contain_text("core1 = net.addRouter('core1')")
            expect(page.locator(".cm-code-band").first).to_have_text("    r1.cmd('true')")
            self.assertEqual(self._saved_draft(page)["code_section"], "r1.cmd('true')\n")
            # The line naming r1 is marked, with the cursor put on it.
            expect(script).to_have_attribute("aria-invalid", "true")

            # Emptying it removes the marker; Backspace stops at the band's start.
            script.focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("End")
            for _ in range(40):
                if marker.is_hidden():
                    break
                page.keyboard.press("Backspace")
            expect(marker).to_be_hidden()
            self.assertEqual(self._saved_draft(page)["code_section"], "")


    def test_continue_in_python_saves_pending_code_and_keeps_independent_drafts(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            original = self._saved_draft(page)
            original["catalog"]["name"] = "Canvas original"
            original["guide"] = "Lesson guide"
            original["lesson_files"] = {
                "data.txt": {"encoding": "utf-8", "content": "lesson data", "role": "build"}}
            response = page.request.put(
                f"{self.base_url}/api/authoring/drafts/{original['id']}",
                data={"revision": original["revision"], "document": original})
            self.assertEqual(response.status, 200)
            page.reload()
            script = self._script_pane(page)
            script.focus()
            page.keyboard.type("r1.cmd('true')")
            action = page.get_by_role("button", name="Continue in Python", exact=True)
            expect(action).to_have_accessible_description(re.compile("do not synchronize"))
            action.focus()
            page.keyboard.press("Enter")
            expect(page.locator("#canvas-heading")).to_have_text("Python Lab Example")
            expect(script).to_be_focused()
            expect(script).to_contain_text("r1.cmd('true')")
            expect(page.locator("#status-message")).to_contain_text("do not synchronize")
            expect(page.locator("#status-message")).to_contain_text("Nothing was published")
            python = self._saved_draft(page)
            self.assertNotEqual(python["id"], original["id"])
            self.assertEqual(python["lesson_files"], original["lesson_files"])
            self.assertEqual(python["guide"], "Lesson guide")
            self.assertIsNone(python["published_revision"])
            page.keyboard.press("Control+End")
            page.keyboard.type("\n# independent Python edit\n")
            self.assertIn("# independent Python edit", self._saved_draft(page)["python"])
            open_from_explorer(page, "Canvas original")
            expect(page.locator("#canvas-heading")).to_have_text("Topology canvas")
            expect(script).to_contain_text("r1.cmd('true')")
            expect(script).not_to_contain_text("independent Python edit")
            saved_original = page.request.get(
                f"{self.base_url}/api/authoring/drafts/{original['id']}").json()
            self.assertEqual(saved_original["code_section"], "r1.cmd('true')")
            self.assertEqual(saved_original["lesson_files"], original["lesson_files"])
            for width in (1280, 375):
                page.set_viewport_size({"width": width, "height": 720})
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)
                self.assertEqual(page.evaluate(TEXT_CONTRAST), [])
            screenshots(page, "authoring08", "canvas-original", "#script-continue-python")
            open_from_explorer(page, "Copy of Canvas original, Python")
            expect(script).to_contain_text("# independent Python edit")
            page.reload()
            expect(script).to_contain_text("# independent Python edit")

    def test_continuation_refuses_local_and_saved_source_conflicts(self) -> None:
        for pending_code in (True, False):
            with self.subTest(pending_code=pending_code), self.signed_in_page() as page:
                self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
                original = self._saved_draft(page)
                count = len(page.request.get(
                    f"{self.base_url}/api/authoring/drafts").json()["drafts"])
                script = self._script_pane(page)
                # A real second save makes either local autosave or the
                # continuation operation itself reject the old revision.
                response = page.request.put(
                    f"{self.base_url}/api/authoring/drafts/{original['id']}",
                    data={"revision": original["revision"], "document": original})
                self.assertEqual(response.status, 200)
                if pending_code:
                    script.focus()
                    page.keyboard.type("r1.cmd('retained local work')")
                action = page.get_by_role("button", name="Continue in Python", exact=True)
                action.focus()
                page.keyboard.press("Space")
                expect(page.locator("#status-alert")).to_contain_text("Could not continue in Python")
                expect(page.locator("#status-alert")).to_contain_text("Canvas draft")
                expect(action).to_be_focused()
                expect(page.locator("#canvas-heading")).to_have_text("Topology canvas")
                if pending_code:
                    expect(script).to_contain_text("retained local work")
                    expect(page.locator("#status-draft-state")).to_contain_text(
                        "Someone else changed this draft")
                self.assertEqual(len(page.request.get(
                    f"{self.base_url}/api/authoring/drafts").json()["drafts"]), count)
                saved = page.request.get(
                    f"{self.base_url}/api/authoring/drafts/{original['id']}").json()
                self.assertEqual(saved, response.json())

    def test_continuation_keeps_unsaveable_edits_and_recovers_from_request_failure(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            original = self._saved_draft(page)
            script = self._script_pane(page)
            # Catalog metadata and Code section share the save boundary.
            open_details(page)
            page.get_by_label("Protocols or Services", exact=True).fill("x" * 81)
            script.focus()
            page.keyboard.type("r1.cmd('kept after failure')")
            action = page.get_by_role("button", name="Continue in Python", exact=True)
            action.click()
            expect(page.locator("#status-alert")).to_contain_text("Correct the save problem")
            expect(script).to_contain_text("kept after failure")
            expect(action).to_be_focused()
            page.get_by_label("Protocols or Services", exact=True).fill("BGP")
            self._saved_draft(page)
            url = f"**/api/authoring/drafts/{original['id']}/continue-in-python"
            page.route(url, lambda route: route.abort())
            action.click()
            expect(page.locator("#status-alert")).to_contain_text("Could not continue in Python")
            expect(action).to_be_focused()
            expect(script).to_contain_text("kept after failure")
            expect(page.locator("#canvas-heading")).to_have_text("Topology canvas")
            page.unroute(url)
            # The failed transition left a usable original; retrying the saved
            # recipe opens its own editable Python source.
            action.click()
            expect(page.locator("#canvas-heading")).to_have_text("Python Lab Example")
            expect(script).to_be_focused()
            expect(action).to_be_hidden()
            page.keyboard.press("Tab")
            expect(script).not_to_be_focused()
            self.assertEqual(page.request.get(
                f"{self.base_url}/api/authoring/drafts/{original['id']}").json().get("kind", "canvas"), "canvas")
            screenshots(page, "authoring08", "python-continuation", "#python-source")

    def test_nodes_the_code_section_adds_are_greyed_read_only_and_arranged(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
            code = self._script_pane(page)
            code.focus()
            page.keyboard.type("r1.cmd('true')\nnet.addLink(r1, net.addRouter('r9'))\n")
            greyed = page.get_by_role("button", name=re.compile(
                r"^Router r9, from Code section, read-only, at x"))
            expect(greyed).to_be_visible()
            expect(greyed).to_contain_text("from Code section")
            expect(greyed).not_to_have_attribute("aria-haspopup", "dialog")
            expect(greyed).to_have_accessible_description(re.compile("Read-only"))
            expect(page.get_by_role("button", name=re.compile(
                r"^Link between r1 and r9, .*, from Code section, read-only$"))).to_have_count(1)
            expect(page.locator("#draft-canvas-description")).to_contain_text(
                "The Lab also has a Code section, which adds 1 Router and 1 Link, read-only.")
            # Closing the pane brings the sidebar, with its Selection, back.
            page.get_by_role("button", name="Script", exact=True).click()

            # Reached with arrows like any Node, moved, never edited.
            self._node(page, "r1").focus()
            page.keyboard.press("End")
            expect(greyed).to_be_focused()
            selection = page.get_by_role("region", name="Selection")
            expect(selection).to_contain_text("The Code section makes it at line 2.")
            for key in ("Delete", "F2"):
                page.keyboard.press(key)
                expect(page.locator("#status-message")).to_have_text(
                    "Router r9 comes from the Code section; change it there.")
            page.keyboard.press("Shift+ArrowDown")
            saved = self._saved_draft(page)
            self.assertIn("code:r9", saved["layout"]["nodes"])

            # Dragging a canvas Node's + onto it adds no Link either.
            source = self._node(page, "r1")
            source.hover()
            handle = source.locator(".link-handle").bounding_box()
            target = greyed.bounding_box()
            assert handle is not None and target is not None
            page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
            page.mouse.down()
            page.mouse.move(target["x"] + target["width"] / 2, target["y"] + target["height"] / 2,
                            steps=5)
            page.mouse.up()
            expect(page.locator("#status-message")).to_have_text(
                "Router r9 comes from the Code section; change it there.")
            self.assertEqual(len(self._saved_draft(page)["topology"]["links"]), 1)
            self.assertEqual([node["name"] for node in saved["topology"]["nodes"]], ["r1", "h1"])
            screenshots(page, "rework-05", "code-section-nodes", fit=True)

            # Enter points to the Code section's line.
            greyed.focus()
            page.keyboard.press("Enter")
            expect(code).to_be_focused()
            self.assertEqual(page.evaluate(
                "() => getSelection().focusNode.parentElement.closest('.cm-line').textContent"),
                "    net.addLink(r1, net.addRouter('r9'))")

            # A name the canvas no longer has is reported at its line.
            self._node(page, "r1").focus()
            page.keyboard.press("F2")
            page.get_by_role("textbox", name="Rename r1").fill("core1")
            page.keyboard.press("Enter")
            expect(page.locator("#drawing-note")).to_have_text(
                "Could not draw the script: name 'r1' is not defined "
                "(Code section line 1: r1.cmd('true')). Start a Preview to draw it.")
            expect(greyed).to_have_count(0)


DRAWN = (
    "COUNT = 3\n\n"
    "from mininet.node import Host\n"
    "from mniplab import mnIPLab\n\n\n"
    "class Probe(Host):\n"
    "    pass\n\n\n"
    "def build_network():\n"
    "    net = mnIPLab(topo=None, controller=None)\n"
    "    s1 = net.addSwitch('s1')\n"
    "    for index in range(1, COUNT + 1):\n"
    "        net.addLink(net.addRouter(f'r{index}'), s1)\n"
    "    net.addHost('probe', cls=Probe)\n"
    "    return net\n"
)


class PythonDraftDrawingBrowserTests(AuthoringPageCase):
    """A Python draft's script is drawn read-only after each save."""

    def _open_python_draft(self, page: Page, python: str) -> None:
        self._seed(page, ROUTED, ROUTED_LINKS, address_family="ipv4")
        upload_script(page, "lab.py", python)
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")
        expect(page.get_by_role("textbox", name="Lab script", exact=True)).to_be_visible()

    def test_a_python_draft_is_drawn_read_only_and_arranged_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._open_python_draft(page, DRAWN)
            router = page.get_by_role("button", name=re.compile(r"^Router r1, read-only, at x"))
            expect(router).to_be_visible()
            expect(router).not_to_have_attribute("aria-haspopup", "dialog")
            expect(page.get_by_role("button", name=re.compile(r"^Switch s1, read-only"))).to_be_visible()
            probe = page.get_by_role("button", name=re.compile(r"^Node probe, read-only"))
            expect(probe).to_be_visible()
            expect(probe.locator(".node-icon path")).not_to_have_attribute("d", re.compile("."))
            expect(page.get_by_role("toolbar", name="Add a Node")).to_be_hidden()
            # A Python draft opens with its script shown; Details shows the Selection.
            toggle = page.get_by_role("button", name="Script", exact=True)
            expect(toggle).to_have_attribute("aria-pressed", "true")
            toggle.click()
            open_details(page)

            router.focus()
            expect(router).to_have_accessible_description(re.compile("Read-only"))
            selection = page.get_by_role("region", name="Selection")
            expect(selection).to_contain_text("The script makes it at line 15.")
            for hidden in ("Addresses…", "Connect…", "Remove"):
                expect(selection.get_by_role("button", name=hidden)).to_be_hidden()
            page.keyboard.press("Delete")
            expect(page.locator("#status-message")).to_have_text(
                "Router r1 comes from the script; change it there.")
            expect(router).to_be_visible()
            page.keyboard.press("Shift+ArrowRight")
            moved = self._saved_draft(page)["layout"]["nodes"]["r1"]
            self.assertEqual(sorted(moved), ["x", "y"])
            screenshots(page, "rework-05", "python-draft-drawing", fit=True)

            # Selection points into the script.
            router.focus()
            selection.get_by_role("button", name="Show in script").click()
            script = page.get_by_role("textbox", name="Lab script", exact=True)
            expect(script).to_be_focused()
            self.assertEqual(page.evaluate("""async () => {
                const { EditorView } = await import('/static/js/lib/codemirror.bundle.js');
                const view = EditorView.findFromDOM(document.querySelector('#python-code-editor .cm-editor'));
                return view.state.doc.lineAt(view.state.selection.main.head).text;
            }"""), "        net.addLink(net.addRouter(f'r{index}'), s1)")
            self.assertEqual(self._saved_draft(page)["layout"]["nodes"]["r1"], moved)

            # A script that cannot be recorded says why.
            upload_script(page, "unrecordable.py", DRAWN.replace("COUNT = 3", "COUNT = x"))
            page.reload()
            note = page.locator("#drawing-note")
            expect(note).to_have_text(
                "Could not draw the script: name 'x' is not defined (line 1 of lab.py: COUNT = x). "
                "Start a Preview to draw it.")
            expect(page.locator("#draft-canvas .node")).to_have_count(0)
            screenshots(page, "rework-05", "python-draft-note", "#drawing-note")
