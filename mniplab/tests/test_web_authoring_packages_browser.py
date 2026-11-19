"""Native package journeys through the browser and real authenticated HTTP."""

import io
import json
import zipfile
from typing import Any

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, TEXT_CONTRAST, labs_explorer, upload_script
from mniplab.tests.browser import Page, expect, screenshots

pytestmark = pytest.mark.browser_authoring


class PackageImportBrowserTests(AuthoringPageCase):
    """A package opens a fresh draft with complete, source-owned contents."""

    def _package(self, page: Page, mode: str) -> tuple[dict[str, Any], bytes]:
        self._seed(page, [("r1", 200, 200, "router", {})], address_family="ipv4", persistent_config=True)
        if mode == "python":
            upload_script(page, "lesson.py", "# café\r\ndef build_network():\r\n    return None\r\n")
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
        draft = self._saved_draft(page)
        draft["guide"] = "# Packaged Guide\r\nKeep this teaching material.\r\n"
        draft["lesson_files"] = {
            "helpers/read.py": {"encoding": "utf-8", "role": "build", "content": "# café\r\nVALUE = 7\r\n"},
            "presentation/map.png": {"encoding": "base64", "content": "AP8NCg==", "role": "presentation"},
        }
        if mode == "canvas":
            draft["code_section"] = "# Keep Code\nnet.note = 'lesson'\n"
        else:
            draft["files"] = {"frr-config/r1/frr.conf": "! exact Seed\r\n"}
        response = page.request.put(f"{self.base_url}/api/authoring/drafts/{draft['id']}",
                                    data={"revision": draft["revision"], "document": draft})
        self.assertEqual(response.status, 200, response.text())
        draft = response.json()
        package = page.request.get(f"{self.base_url}/api/authoring/drafts/{draft['id']}/python.zip")
        self.assertEqual(package.status, 200)
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")
        return draft, package.body()

    @staticmethod
    def _import(page: Page, data: bytes) -> None:
        labs_explorer(page)
        page.locator("#import-package-file").set_input_files(
            {"name": "lesson.zip", "mimeType": "application/zip", "buffer": data})

    def test_complete_package_roundtrips_in_original_mode(self) -> None:
        for mode in ("canvas", "python"):
            with self.subTest(mode=mode), self.signed_in_page() as page:
                original, data = self._package(page, mode)
                labs_explorer(page)
                button = page.get_by_role("button", name="Import package…", exact=True)
                button.focus()
                with page.expect_file_chooser() as chooser:
                    button.press("Enter")
                chooser.value.set_files({"name": "lesson.zip", "mimeType": "application/zip", "buffer": data})
                expect(page.locator("#status-message")).to_contain_text("Imported lesson.zip")
                imported = self._saved_draft(page)
                self.assertNotEqual(imported["id"], original["id"])
                self.assertEqual(imported.get("kind", "canvas"), original.get("kind", "canvas"))
                for field in ("guide", "lesson_files", "layout"):
                    self.assertEqual(imported[field], original[field])
                for field in (("python", "files") if mode == "python" else ("topology", "code_section")):
                    self.assertEqual(imported[field], original[field])
                self.assertIsNone(imported.get("published_revision"))
                self.assertFalse(imported["preview_valid"])
                reexport = page.request.get(f"{self.base_url}/api/authoring/drafts/{imported['id']}/python.zip")
                self.assertEqual(reexport.status, 200)
                # Complete export is available from the opened imported draft too.
                with zipfile.ZipFile(io.BytesIO(data)) as before, zipfile.ZipFile(io.BytesIO(reexport.body())) as after:
                    self.assertEqual({name: before.read(name) for name in before.namelist()},
                                     {name: after.read(name) for name in after.namelist()})
                if mode == "canvas":
                    expect(page.locator("#canvas-region")).to_be_focused()
                labs_explorer(page)
                screenshots(page, "authoring13", mode + "-import", "#import-package")

    def test_changed_generated_source_requires_choice_and_cancel_preserves_work(self) -> None:
        with self.signed_in_page() as page:
            original, data = self._package(page, "canvas")
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            manifest = json.loads(next(body for name, body in members.items()
                                       if name.endswith("/iplab-package.json")))
            entry = manifest["root"] + "/" + manifest["entrypoint"]
            changed_source = members[entry] + b"\n# external edit: caf\xc3\xa9\r\n"
            members[entry] = changed_source
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, body in members.items():
                    archive.writestr(name, body)
            changed = output.getvalue()
            self._import(page, changed)
            dialog = page.get_by_role("alertdialog", name="Import changed generated files")
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
            screenshots(page, "authoring13", "changed-choice", "#confirm-dialog")
            for width in (1280, 375):
                page.set_viewport_size({"width": width, "height": 720})
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)
                self.assertEqual(page.evaluate(TEXT_CONTRAST), [])
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(page.get_by_role("button", name="Import package…", exact=True)).to_be_focused()
            expect(page.locator("#status-message")).to_contain_text("cancelled")
            self.assertEqual(self._saved_draft(page), original)
            self._import(page, changed)
            expect(dialog).to_be_visible()
            # Keyboard-only selection: initial Cancel, previous control is Import.
            expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
            page.keyboard.press("Shift+Tab")
            expect(dialog.get_by_role("button", name="Import as Python", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            expect(page.locator("#status-message")).to_contain_text("new Python draft")
            imported = self._saved_draft(page)
            self.assertNotEqual(imported["id"], original["id"])
            self.assertEqual(imported["kind"], "python")
            self.assertEqual(imported["python"].encode(), changed_source)
            for field in ("guide", "lesson_files"):
                self.assertEqual(imported[field], original[field])
            seed = next(body for name, body in members.items() if name.endswith("/r1/frr.conf"))
            self.assertEqual(imported["files"]["frr-config/r1/frr.conf"].encode(), seed)
            self.assertFalse(imported["preview_valid"])
            self.assertIsNone(imported["published_revision"])
            expect(page.get_by_role("textbox", name="Lab script", exact=True)).to_be_focused()
            preserved = page.request.get(f"{self.base_url}/api/authoring/drafts/{original['id']}").json()
            self.assertEqual(preserved, original)

    def test_malformed_packages_and_unsaveable_edits_keep_current_draft(self) -> None:
        with self.signed_in_page() as page:
            original, data = self._package(page, "canvas")
            self._import(page, b"not a native zip")
            expect(page.locator("#status-alert")).not_to_be_empty()
            expect(page.get_by_role("button", name="Import package…", exact=True)).to_be_focused()
            self.assertEqual(self._saved_draft(page), original)
            page.locator("#palette-transit").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#status-draft-state")).to_contain_text("Can't save yet")
            nodes = page.locator("#draft-canvas .node").count()
            self._import(page, data)
            expect(page.locator("#status-alert")).to_contain_text("Save your current edits")
            self.assertEqual(page.locator("#draft-canvas .node").count(), nodes)
            self.assertEqual(page.locator("#draft-id").input_value(), original["id"])
            # A cancelled file chooser never touches either authority or local edits.
            page.locator("#import-package-file").set_input_files([])
            self.assertEqual(page.locator("#draft-canvas .node").count(), nodes)
            self.assertEqual(page.request.get(
                f"{self.base_url}/api/authoring/drafts/{original['id']}").json(), original)

    def test_conflicted_local_work_is_never_discarded_to_import(self) -> None:
        with self.signed_in_page() as page:
            original, data = self._package(page, "python")
            if page.locator("#script-pane").is_hidden():
                page.get_by_role("button", name="Script", exact=True).click()
            if not page.locator("#lesson-files").evaluate("element => element.open"):
                page.get_by_text("Supporting lesson files", exact=True).click()
            editor = page.get_by_label("Supporting file content")
            expect(editor).to_have_value("# café\nVALUE = 7\n")
            response = page.request.put(f"{self.base_url}/api/authoring/drafts/{original['id']}",
                                        data={"revision": original["revision"], "document": original})
            self.assertEqual(response.status, 200)
            editor.fill("unsaved local helper\n")
            expect(page.locator("#status-draft-state")).to_contain_text("Someone else changed this draft")
            self._import(page, data)
            expect(page.locator("#status-alert")).to_contain_text("Save your current edits")
            expect(editor).to_have_value("unsaved local helper\n")
            self.assertEqual(page.locator("#draft-id").input_value(), original["id"])
            drafts = page.request.get(f"{self.base_url}/api/authoring/drafts").json()["drafts"]
            self.assertEqual(len(drafts), 2)  # Canvas fixture and its uploaded Python lesson.

    def test_edits_made_during_import_stay_open_and_import_is_available_separately(self) -> None:
        with self.signed_in_page() as page:
            original, data = self._package(page, "canvas")
            held = []
            url = "**/api/authoring/drafts/import"
            page.route(url, lambda route: held.append(route))
            with page.expect_request(lambda request: request.method == "POST"
                                     and request.url.endswith("/drafts/import")):
                self._import(page, data)
            # Playwright emits the request before dispatching the route callback.
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(10)
            self.assertTrue(held, "The package request reached the held HTTP boundary")
            response = held[0].fetch()
            self.assertEqual(response.status, 201)
            page.locator("#palette-transit").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#status-draft-state")).to_contain_text("Can't save yet")
            nodes = page.locator("#draft-canvas .node").count()
            held[0].fulfill(response=response)
            # Refreshing Drafts is part of import completion, after the held
            # response arrives. Judge the outcome once that action has settled.
            expect(page.get_by_role("button", name="Import package…", exact=True)).to_be_enabled(
                timeout=30000)
            expect(page.locator("#status-alert")).to_contain_text("Package imported as a separate draft")
            # Keep interception alive through response delivery and refresh;
            # disabling Chromium Fetch while those settle can strand a request.
            page.unroute(url)
            self.assertEqual(page.locator("#draft-id").input_value(), original["id"])
            self.assertEqual(page.locator("#draft-canvas .node").count(), nodes)
            drafts = page.request.get(f"{self.base_url}/api/authoring/drafts").json()["drafts"]
            self.assertEqual(len(drafts), 2)
            imported = next(draft for draft in drafts if draft["id"] != original["id"])
            self.assertFalse(imported["preview_valid"])
            self.assertEqual(imported["topology"], original["topology"])
