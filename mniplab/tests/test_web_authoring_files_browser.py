"""Complete supporting files through the authenticated authoring workspace."""

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, TEXT_CONTRAST, upload_script
from mniplab.tests.browser import Page, expect, screenshots
from mniplab.tests.authoring_fixtures import PackageRecordingLauncher
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_authoring


class LessonFilesBrowserTests(AuthoringPageCase):
    """Authors preserve supporting text beside the source-owned controls."""

    def test_text_files_are_editable_renamable_and_restore_selection(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, [], address_family="ipv4")
            page.get_by_role("button", name="Script", exact=True).click()
            page.get_by_text("Supporting lesson files", exact=True).click()
            page.get_by_label("New file path").fill("helpers/route (v1)+test.py")
            page.get_by_role("button", name="Create text file", exact=True).click()
            expect(page.get_by_label("New file path")).to_have_attribute("aria-invalid", "false")
            editor = page.get_by_label("Supporting file content")
            editor.fill("VALUE = 'kept'\n")
            saved = self._saved_draft(page)
            self.assertEqual(saved["lesson_files"]["helpers/route (v1)+test.py"]["content"],
                             "VALUE = 'kept'\n")
            page.get_by_label("Selected file path").fill("helpers/renamed.py")
            page.get_by_role("button", name="Rename selected file helpers/route (v1)+test.py", exact=True).click()
            saved = self._saved_draft(page)
            self.assertNotIn("helpers/route (v1)+test.py", saved["lesson_files"])
            page.reload()
            if page.locator("#script-pane").is_hidden():
                page.get_by_role("button", name="Script", exact=True).click()
            expect(page.get_by_label("Supporting file", exact=True)).to_have_value("helpers/renamed.py")
            expect(editor).to_have_value("VALUE = 'kept'\n")
            screenshots(page, "authoring04", "text-open", "#lesson-files > summary")
            screenshots(page, "authoring04", "text-editor", "#lesson-content")

    def test_binary_assets_errors_cancellation_and_keyboard_in_both_modes(self) -> None:
        for mode in ("canvas", "python"):
            with self.subTest(mode=mode), self.signed_in_page() as page:
                self._seed(page, [], address_family="ipv4")
                if mode == "python":
                    upload_script(page, "lesson.py", "from mniplab import mnIPLab\n\n"
                                  "def build_network():\n    return mnIPLab()\n")
                    page.reload()
                    page.wait_for_selector("#draft-form:not([hidden])")
                if page.locator("#script-pane").is_hidden():
                    page.get_by_role("button", name="Script", exact=True).click()
                page.get_by_text("Supporting lesson files", exact=True).click()
                new_path = page.get_by_label("New file path")
                new_path.fill("../escape.txt")
                page.get_by_role("button", name="Create text file", exact=True).click()
                expect(new_path).to_have_attribute("aria-invalid", "true")
                expect(new_path).to_be_focused()
                for reserved in ("lab.py", "guide.md", "frr-config/r1/daemons"):
                    new_path.fill(reserved)
                    page.get_by_role("button", name="Create text file", exact=True).click()
                    expect(page.locator("#lesson-feedback")).to_contain_text("belongs to")
                new_path.fill("presentation/map (v1)+image.png")
                payload = b"\x89PNG\x00\xff\r\nopaque"
                page.locator("#lesson-upload-input").set_input_files(
                    {"name": "map.png", "mimeType": "image/png", "buffer": payload})
                expect(page.get_by_label("Supporting file", exact=True)).to_have_value(
                    "presentation/map (v1)+image.png")
                expect(page.get_by_label("Supporting file content")).to_be_hidden()
                expect(page.locator("#lesson-content-label")).to_be_hidden()
                saved = self._saved_draft(page)
                path = "presentation/map (v1)+image.png"
                record = saved["lesson_files"][path]
                self.assertEqual(record["encoding"], "base64")
                self.assertEqual(base64.b64decode(record["content"]), payload)
                page.get_by_label("File role", exact=True).select_option("presentation")
                saved = self._saved_draft(page)
                self.assertEqual(saved["lesson_files"][path]["role"], "presentation")
                download = page.get_by_role("button", name=f"Download selected file {path}", exact=True)
                with page.expect_download() as pending:
                    download.click()
                self.assertEqual(pending.value.suggested_filename, "map (v1)+image.png")
                self.assertEqual(Path(pending.value.path()).read_bytes(), payload)
                delete = page.get_by_role("button", name=f"Delete selected file {path}", exact=True)
                delete.click()
                page.get_by_role("button", name="Cancel", exact=True).click()
                expect(delete).to_be_focused()
                expect(page.get_by_label("Supporting file", exact=True)).to_have_value(path)
                # Replacement is explicit and preserves exact bytes and classification.
                replacement = b"\x00replacement\xff"
                page.locator("#lesson-replace-input").set_input_files(
                    {"name": "new.png", "mimeType": "image/png", "buffer": replacement})
                saved = self._saved_draft(page)
                self.assertEqual(base64.b64decode(saved["lesson_files"][path]["content"]), replacement)
                self.assertEqual(saved["lesson_files"][path]["role"], "presentation")
                page.locator("#lesson-replace-input").set_input_files([])
                expect(page.get_by_label("Supporting file", exact=True)).to_have_value(path)
                new_path.fill("notes.txt")
                page.get_by_role("button", name="Create text file", exact=True).click()
                editor = page.get_by_label("Supporting file content")
                editor.fill("Do not lose this text")
                page.keyboard.press("Tab")
                expect(editor).not_to_be_focused()
                page.get_by_label("Selected file path").fill("bad/name%20.txt")
                page.get_by_role("button", name="Rename selected file notes.txt", exact=True).click()
                expect(page.get_by_label("Selected file path")).to_have_attribute("aria-invalid", "true")
                expect(editor).to_have_value("Do not lose this text")
                saved = self._saved_draft(page)
                self.assertEqual(saved["lesson_files"]["notes.txt"]["content"], "Do not lose this text")
                screenshots(page, "authoring04", mode, "#lesson-files")
                for width in (1280, 375):
                    page.set_viewport_size({"width": width, "height": 720})
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)
                    self.assertEqual(page.evaluate(TEXT_CONTRAST), [])
                    for field in (new_path, editor, page.get_by_label("Selected file path")):
                        field.scroll_into_view_if_needed()
                        box = field.bounding_box()
                        assert box is not None
                        self.assertGreaterEqual(box["x"], 0)
                        self.assertLessEqual(box["x"] + box["width"], width)
                page.get_by_role("button", name="Delete selected file notes.txt", exact=True).click()
                page.get_by_role("button", name="Delete file", exact=True).click()
                expect(page.get_by_label("Supporting file", exact=True)).to_be_focused()
                self.assertNotIn("notes.txt", self._saved_draft(page)["lesson_files"])

    def test_pending_text_and_rename_survive_a_delayed_save_and_conflict(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, [], address_family="ipv4")
            page.get_by_role("button", name="Script", exact=True).click()
            page.get_by_text("Supporting lesson files", exact=True).click()
            page.get_by_label("New file path").fill("__proto__")
            page.get_by_role("button", name="Create text file", exact=True).click()
            editor = page.get_by_label("Supporting file content")
            editor.fill("first text")
            before = self._saved_draft(page)
            # The browser bridge coerces special object keys; raw HTTP JSON
            # preserves the exact collection returned to normal clients.
            before = page.request.get(
                f"{self.base_url}/api/authoring/drafts/{before['id']}").json()
            self.assertEqual(before["lesson_files"]["__proto__"]["content"], "first text")
            held = []
            url = f"**/api/authoring/drafts/{before['id']}"
            page.route(url, lambda route: held.append(route)
                       if route.request.method == "PUT" else route.fallback())
            with page.expect_request(lambda request: request.method == "PUT"
                                     and "/api/authoring/drafts/" in request.url):
                editor.fill("submitted text")
            expect(page.locator("#status-draft-state")).to_have_text("Saving…")
            response = held[0].fetch()
            self.assertEqual(response.status, 200)
            editor.fill("latest text\n")
            page.get_by_label("Selected file path").fill("latest.txt")
            page.get_by_role("button", name="Rename selected file __proto__", exact=True).click()
            held[0].fulfill(response=response)
            page.unroute(url)
            saved = self._saved_draft(page)
            self.assertNotIn("__proto__", saved["lesson_files"])
            self.assertEqual(saved["lesson_files"]["latest.txt"]["content"], "latest text\n")
            # Another Instructor's real save makes this revision stale. The
            # refused local edit must remain visible and downloadable.
            response = page.request.put(f"{self.base_url}/api/authoring/drafts/{saved['id']}",
                                        data={"revision": saved["revision"], "document": saved})
            self.assertEqual(response.status, 200)
            editor.fill("unsaved work retained")
            expect(page.locator("#status-draft-state")).to_contain_text("Someone else changed this draft")
            expect(editor).to_have_value("unsaved work retained")
            with page.expect_download() as pending:
                page.get_by_role("button", name="Download selected file latest.txt", exact=True).click()
            self.assertEqual(Path(pending.value.path()).read_text(), "unsaved work retained")

    def test_uploaded_utf8_text_stays_editable_and_downloads_exact_bytes(self) -> None:
        """Upload preserves BOM/newlines and exposes text for ordinary editing."""
        with self.signed_in_page() as page:
            self._seed(page, [], address_family="ipv4")
            page.get_by_role("button", name="Script", exact=True).click()
            page.get_by_text("Supporting lesson files", exact=True).click()
            for path, payload in (
                ("helpers/text.txt", b"\xef\xbb\xbfhello \xc3\xa9\r\nnext\rlast\n"),
                ("helpers/empty.txt", b""),
            ):
                with self.subTest(path=path):
                    page.get_by_label("New file path").fill(path)
                    page.locator("#lesson-upload-input").set_input_files(
                        {"name": "text.txt", "mimeType": "text/plain", "buffer": payload})
                    expect(page.get_by_label("Supporting file", exact=True)).to_have_value(path)
                    editor = page.get_by_label("Supporting file content")
                    expect(editor).to_be_editable()
                    saved = self._saved_draft(page)
                    record = saved["lesson_files"][path]
                    self.assertEqual(record["encoding"], "utf-8")
                    self.assertEqual(record["content"].encode("utf-8"), payload)
                    with page.expect_download() as pending:
                        page.get_by_role("button", name=f"Download selected file {path}", exact=True).click()
                    self.assertEqual(Path(pending.value.path()).read_bytes(), payload)
                    editor.fill("edited text\n")
                    self.assertEqual(self._saved_draft(page)["lesson_files"][path]["content"],
                                     "edited text\n")

    def test_utf8_control_bytes_are_preserved_as_binary_assets(self) -> None:
        """Decodable control bytes are assets, never editable source text."""
        with self.signed_in_page() as page:
            self._seed(page, [], address_family="ipv4")
            page.get_by_role("button", name="Script", exact=True).click()
            page.get_by_text("Supporting lesson files", exact=True).click()
            for payload in (b"binary\x00data", b"control\x1bdata"):
                with self.subTest(payload=payload):
                    page.get_by_label("New file path").fill("data.bin")
                    page.locator("#lesson-upload-input").set_input_files(
                        {"name": "data.bin", "mimeType": "application/octet-stream", "buffer": payload})
                    expect(page.get_by_label("Supporting file", exact=True)).to_have_value("data.bin")
                    expect(page.get_by_label("Supporting file content")).to_be_hidden()
                    record = self._saved_draft(page)["lesson_files"]["data.bin"]
                    self.assertEqual(record["encoding"], "base64")
                    self.assertEqual(base64.b64decode(record["content"]), payload)
                    page.get_by_role("button", name="Delete selected file data.bin", exact=True).click()
                    page.get_by_role("button", name="Delete file", exact=True).click()
                    self._saved_draft(page)


    def test_open_supporting_files_leave_the_python_editor_visible(self) -> None:
        """An expanded file manager leaves room to edit the lesson's Python."""
        with self.signed_in_page() as page:
            self._seed(page, [], address_family="ipv4")
            upload_script(page, "lesson.py", "def build_network():\n    return None\n")
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            page.get_by_text("Supporting lesson files", exact=True).click()
            page.get_by_label("New file path").fill("helpers/value.py")
            page.get_by_role("button", name="Create text file", exact=True).click()
            self._saved_draft(page)
            script = page.get_by_role("textbox", name="Lab script", exact=True)
            for width in (1280, 640, 375, 320):
                with self.subTest(width=width):
                    page.set_viewport_size({"width": width, "height": 800})
                    script.focus()
                    script.scroll_into_view_if_needed()
                    visible = script.evaluate("""element => {
                        const line = element.querySelector('.cm-line').getBoundingClientRect();
                        const scroller = element.closest('.cm-scroller').getBoundingClientRect();
                        return Math.min(line.bottom, scroller.bottom, innerHeight)
                            - Math.max(line.top, scroller.top, 0);
                    }""")
                    self.assertGreater(visible, 0, "Focused Python text must remain visible")


class ReplaceScriptBrowserTests(AuthoringPageCase):
    """Explicit script replacement retains the open lesson and local work."""

    def _python_lesson(self, page: Page) -> dict:
        self._seed(page, [], address_family="ipv4")
        expect(page.get_by_role("button", name="Replace script…", exact=True)).to_be_hidden()
        upload_script(page, "lesson.py", "def build_network():\n    return None\n")
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")
        draft = self._saved_draft(page)
        draft["files"] = {"frr-config/r1/frr.conf": "! preserved Seed\n"}
        draft["guide"] = "# Preserved Guide\n"
        draft["catalog"]["area"] = "Static"
        draft["lesson_files"] = {"helpers/value.py": {
            "encoding": "utf-8", "role": "build", "content": "VALUE = 7\n"}}
        response = page.request.put(f"{self.base_url}/api/authoring/drafts/{draft['id']}",
                                    data={"revision": draft["revision"], "document": draft})
        self.assertEqual(response.status, 200)
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")
        return response.json()

    def test_replacement_saves_pending_edits_preserves_lesson_and_focuses_script(self) -> None:
        with self.signed_in_page() as page:
            original = self._python_lesson(page)
            seed = page.get_by_role("tab", name="r1/frr.conf", exact=True)
            seed.click()
            page.get_by_role("textbox", name="Seed file frr-config/r1/frr.conf", exact=True).fill("! pending Seed\n")
            button = page.get_by_role("button", name="Replace script…", exact=True)
            button.focus()
            with page.expect_file_chooser() as chooser:
                button.press("Enter")
            source = "def build_network(:\n    pass\n"
            chooser.value.set_files({"name": "replacement.py", "mimeType": "text/x-python", "buffer": source.encode()})
            expect(page.locator("#status-message")).to_contain_text("Replaced script")
            expect(page.get_by_role("textbox", name="Lab script", exact=True)).to_be_focused()
            expect(page.locator("#python-source-error")).to_contain_text("Fix the script here")
            saved = self._saved_draft(page)
            self.assertEqual(saved["python"], source)
            self.assertEqual(saved["files"], {"frr-config/r1/frr.conf": "! pending Seed\n"})
            for field in ("id", "kind", "guide", "layout", "catalog", "lesson_files", "published_revision"):
                self.assertEqual(saved.get(field), original.get(field), field)
            if page.locator("#lesson-files").evaluate("element => element.open"):
                page.get_by_text("Supporting lesson files", exact=True).click()
            screenshots(page, "authoring10", "replaced", "#replace-script")
            screenshots(page, "authoring10", "source", "#python-source")
            for width in (1280, 375):
                page.set_viewport_size({"width": width, "height": 720})
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)
                self.assertEqual(page.evaluate(TEXT_CONTRAST), [])
                button.scroll_into_view_if_needed()
                box = button.bounding_box()
                assert box is not None
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], width)

    def test_cancel_invalid_and_stale_replacement_preserve_local_source(self) -> None:
        with self.signed_in_page() as page:
            original = self._python_lesson(page)
            editor = page.get_by_role("textbox", name="Lab script", exact=True)
            editor.fill("# local source\n")
            page.locator("#replace-script-file").set_input_files([])
            expect(editor).to_have_text("# local source")
            page.locator("#replace-script-file").set_input_files(
                {"name": "invalid.txt", "mimeType": "text/plain", "buffer": b"invalid"})
            expect(page.locator("#status-alert")).not_to_be_empty()
            expect(page.get_by_role("button", name="Replace script…", exact=True)).to_be_focused()
            expect(editor).to_have_text("# local source")
            saved = self._saved_draft(page)
            self.assertEqual(saved["python"], "# local source\n")
            # A real competing save makes the replacement revision stale.
            held = []
            url = f"**/api/authoring/drafts/{original['id']}/script"
            page.route(url, lambda route: held.append(route))
            with page.expect_request(lambda request: request.url.endswith("/script") and request.method == "PUT"):
                page.locator("#replace-script-file").set_input_files(
                    {"name": "valid.py", "mimeType": "text/x-python", "buffer": b"# replaced\n"})
            saved = page.request.get(f"{self.base_url}/api/authoring/drafts/{original['id']}").json()
            response = page.request.put(f"{self.base_url}/api/authoring/drafts/{saved['id']}",
                                        data={"revision": saved["revision"], "document": saved})
            self.assertEqual(response.status, 200)
            editor.fill("# latest unsaved local source\n")
            held[0].continue_()
            expect(page.locator("#status-alert")).to_contain_text("Someone else changed")
            expect(editor).to_have_text("# latest unsaved local source")
            expect(page.locator("#status-draft-state")).to_contain_text("Someone else changed")
            # Refuse the next replacement without sending it or losing local text.
            page.locator("#replace-script-file").set_input_files(
                {"name": "valid.py", "mimeType": "text/x-python", "buffer": b"# again\n"})
            expect(page.locator("#status-alert")).to_contain_text("Save your current edits")
            expect(editor).to_have_text("# latest unsaved local source")
            stored = page.request.get(f"{self.base_url}/api/authoring/drafts/{saved['id']}").json()
            self.assertEqual(stored, response.json())

    def test_edits_during_replacement_response_are_preserved_and_saved(self) -> None:
        with self.signed_in_page() as page:
            original = self._python_lesson(page)
            held = []
            url = f"**/api/authoring/drafts/{original['id']}/script"
            page.route(url, lambda route: held.append(route))
            with page.expect_request(lambda request: request.url.endswith("/script") and request.method == "PUT"):
                page.locator("#replace-script-file").set_input_files(
                    {"name": "valid.py", "mimeType": "text/x-python", "buffer": b"# replacement\n"})
            self.assertEqual(page.request.get(
                f"{self.base_url}/api/authoring/drafts/{original['id']}").json(), original)
            response = held[0].fetch()
            self.assertEqual(response.status, 200)
            editor = page.get_by_role("textbox", name="Lab script", exact=True)
            editor.fill("# newer local source\n")
            page.get_by_role("tab", name="r1/frr.conf", exact=True).click()
            page.get_by_role("textbox", name="Seed file frr-config/r1/frr.conf", exact=True).fill("! newer Seed\n")
            held[0].fulfill(response=response)
            expect(page.locator("#status-message")).to_contain_text("Replaced script")
            expect(editor).to_be_focused()
            expect(editor).to_have_text("# newer local source")
            saved = self._saved_draft(page)
            self.assertEqual(saved["python"], "# newer local source\n")
            self.assertEqual(saved["files"], {"frr-config/r1/frr.conf": "! newer Seed\n"})
            self.assertEqual(saved["lesson_files"], original["lesson_files"])

    def test_identical_replacement_and_cancel_preserve_preview_evidence(self) -> None:
        with self.signed_in_page() as page:
            original = self._python_lesson(page)
            with patch.object(registry.lab_service, "launcher", PackageRecordingLauncher()):
                preview = page.request.post(f"{self.base_url}/api/authoring/drafts/{original['id']}/preview",
                                            data={"revision": original["revision"]})
            self.assertEqual(preview.status, 200, preview.text())
            self.assertTrue(preview.json()["preview_valid"])
            page.request.post(f"{self.base_url}/api/labs/{preview.json()['key']}/stop")
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            before = self._saved_draft(page)
            page.locator("#replace-script-file").set_input_files([])
            self.assertEqual(self._saved_draft(page), before)
            page.locator("#replace-script-file").set_input_files(
                {"name": "same.py", "mimeType": "text/x-python", "buffer": before["python"].encode()})
            expect(page.locator("#status-message")).to_contain_text("Script unchanged; Preview retained")
            self.assertEqual(self._saved_draft(page), before)
            page.locator("#replace-script-file").set_input_files(
                {"name": "changed.py", "mimeType": "text/x-python", "buffer": b"# changed script\n"})
            expect(page.locator("#status-message")).to_contain_text("rebuild Preview before publishing")
            self.assertFalse(self._saved_draft(page)["preview_valid"])

    def test_unsaveable_pending_edits_refuse_replacement(self) -> None:
        with self.signed_in_page() as page:
            original = self._python_lesson(page)
            page.get_by_role("button", name="Details", exact=True).click()
            page.locator("#catalog-protocols").fill("x" * 81)
            page.get_by_role("textbox", name="Lab script", exact=True).fill("# local unfinished work\n")
            page.locator("#replace-script-file").set_input_files(
                {"name": "new.py", "mimeType": "text/x-python", "buffer": b"# replacement\n"})
            expect(page.locator("#status-alert")).to_contain_text("Save your current edits")
            expect(page.get_by_role("textbox", name="Lab script", exact=True)).to_have_text("# local unfinished work")
            self.assertEqual(page.request.get(
                f"{self.base_url}/api/authoring/drafts/{original['id']}").json(), original)
