"""Complete lesson files through authenticated HTTP and real temporary storage."""

from typing import Any, Dict

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, PackageRecordingLauncher


@pytest.mark.unit
class LessonFilesAPITests(AuthoringAPIFixture):
    """Both source modes preserve authored bytes without duplicate recipes."""

    async def _python_draft(self, client: httpx2.AsyncClient, publishable: bool = False) -> Dict[str, Any]:
        uploaded = await client.post("/api/authoring/drafts/upload", files={
            "file": ("lesson.py", b"def build_network():\n    return None\n", "text/x-python")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        draft = uploaded.json()
        if not publishable:
            return draft
        draft["catalog"]["area"] = "Static"
        saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
            "revision": draft["revision"], "document": draft})
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    def test_save_reopen_and_copy_preserve_text_and_binary_in_both_modes(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            for draft in (await self._router_draft(client), await self._python_draft(client)):
                with self.subTest(kind=draft.get("kind", "canvas")):
                    files = {
                        "helpers/routes.py": {"encoding": "utf-8", "content": "# café\r\nN = 2\r\n"},
                        "config/topology.json": {"encoding": "utf-8", "content": '{"hosts": 2}\n'},
                        "presentation/diagram.png": {"encoding": "base64", "content": "iVBORw0KGgr/AA==",
                                                     "role": "presentation"},
                    }
                    saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                        "revision": draft["revision"], "document": {**draft, "lesson_files": files}})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    reopened = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
                    copied = await client.post("/api/authoring/drafts", json={"source_id": draft["id"]})
                    self.assertEqual(copied.status_code, 201, copied.text)
                    self.assertEqual(reopened["lesson_files"], files)
                    self.assertEqual(copied.json()["lesson_files"], files)
                    self.assertEqual(reopened["guide"], draft["guide"])
                    self.assertEqual(reopened["layout"], draft["layout"])
                    if draft.get("kind") == "python":
                        self.assertEqual(reopened["python"], draft["python"])
                        self.assertEqual(reopened["files"], draft["files"])
                    else:
                        self.assertEqual(reopened["topology"], draft["topology"])

        self._as("instructor", exercise)

    def test_file_operations_download_exact_bytes_and_conflict_atomically(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            root = f"/api/authoring/drafts/{draft['id']}"
            record = {"encoding": "base64", "content": "AP9cXG4NCg=="}
            added = await client.put(root + "/file", json={
                "revision": draft["revision"], "path": "data/raw (1).bin", "file": record})
            self.assertEqual(added.status_code, 200, added.text)
            draft = added.json()
            listed = await client.get(root + "/files")
            self.assertEqual(listed.json(), {"revision": 1, "files": [{
                "path": "data/raw (1).bin", "encoding": "base64", "role": "build", "size": 7}]})
            downloaded = await client.get(root + "/file", params={"path": "data/raw (1).bin"})
            self.assertEqual(downloaded.status_code, 200, downloaded.text)
            self.assertEqual(downloaded.content, b"\x00\xff\\\\n\r\n")
            self.assertEqual(downloaded.headers["content-type"], "application/octet-stream")
            self.assertEqual(downloaded.headers["x-content-type-options"], "nosniff")
            self.assertIn("attachment", downloaded.headers["content-disposition"])
            for method in ("put", "delete"):
                if method == "put":
                    refused = await client.put(root + "/file", json={
                        "revision": 0, "path": "data/raw (1).bin",
                        "file": {"encoding": "utf-8", "content": "overwritten"}})
                else:
                    refused = await client.delete(root + "/file", params={
                        "revision": 0, "path": "data/raw (1).bin"})
                self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual((await client.get(root)).json(), draft)
            replaced = await client.put(root + "/file", json={
                "revision": 1, "path": "data/raw (1).bin",
                "file": {"encoding": "utf-8", "content": "new\r\n"}})
            self.assertEqual(replaced.status_code, 200, replaced.text)
            self.assertEqual((await client.get(root + "/file", params={
                "path": "data/raw (1).bin"})).content, b"new\r\n")
            removed = await client.delete(root + "/file", params={
                "revision": 2, "path": "data/raw (1).bin"})
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json()["revision"], 3)
            self.assertEqual(removed.json()["lesson_files"], {})
            for method in ("get", "delete"):
                missing = await getattr(client, method)(root + "/file", params={
                    "revision": 3, "path": "data/raw (1).bin"})
                self.assertEqual(missing.status_code, 404, missing.text)

        self._as("instructor", exercise)

    def test_unsafe_paths_and_generated_sources_cannot_create_a_second_recipe(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            for draft in (await self._router_draft(client), await self._python_draft(client)):
                root = f"/api/authoring/drafts/{draft['id']}"
                for path in (
                    "../escape", "/tmp/escape", "dir/../escape", "dir//file", "./file",
                    "dir/./file", "dir\\file", "dir/%2e%2e/file", "C:/file", "dir/file\n",
                    "dir/file.", "dir/file ", ".git/config", "__pycache__/x.pyc", "café.txt",
                    "lab.py", "LAB.PY", "definition.json", "README.md", "iplab-package.json",
                    "guide.md", "layout.json",
                    "examples/guides/lesson.md", "examples/layouts/lesson.json",
                    "frr-config/r1/frr.conf", "frr-config/any/depth/daemons",
                    "frr-config/r1/vtysh.conf", "frr-config/r1/support_bundle_commands.conf",
                    "lesson.py" if draft.get("kind") else "routed-lesson.py",
                ):
                    with self.subTest(kind=draft.get("kind", "canvas"), path=path):
                        refused = await client.put(root + "/file", json={
                            "revision": draft["revision"], "path": path,
                            "file": {"encoding": "utf-8", "content": "overwrite"}})
                        self.assertEqual(refused.status_code, 422, refused.text)
                        self.assertEqual((await client.get(root)).json(), draft)
                for path in ("../escape", "dir//file"):
                    for method in ("get", "delete"):
                        refused = await getattr(client, method)(root + "/file", params={
                            "revision": draft["revision"], "path": path})
                        self.assertEqual(refused.status_code, 422, refused.text)

        self._as("instructor", exercise)

    def test_colliding_paths_and_untrusted_presentation_roles_are_refused(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            root = f"/api/authoring/drafts/{draft['id']}"
            record = {"encoding": "utf-8", "content": "N = 2\n"}
            for files in (
                {"helpers/a.py": record, "HELPERS/b.py": record},
                {"helpers/a.py": record, "helpers/A.py": record},
                {"data/input": record, "data/input/extra": record},
                {"frr-config": record}, {"examples": record}, {"lab.py/extra": record},
            ):
                refused = await client.put(root, json={"revision": draft["revision"],
                    "document": {**draft, "lesson_files": files}})
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertEqual((await client.get(root)).json(), draft)
            for path in ("helpers/a.py", "presentation/a.py", "presentation/a.json",
                         "presentation/a.yaml", "presentation/a.conf", "presentation/a.sh",
                         "docs/guide.md", "data/diagram.png"):
                refused = await client.put(root + "/file", json={
                    "revision": draft["revision"], "path": path,
                    "file": {**record, "role": "presentation"}})
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertEqual((await client.get(root)).json(), draft)
            saved = await client.put(root + "/file", json={"revision": 0,
                "path": "presentation/lesson.md", "file": {**record, "role": "presentation"}})
            self.assertEqual(saved.status_code, 200, saved.text)
            listed = (await client.get(root + "/files")).json()
            self.assertEqual(listed["files"][0]["role"], "presentation")
            saved = await client.put(root + "/file", json={"revision": 1,
                "path": "presentation/diagram.png", "file": {"encoding": "base64", "content": "AP8="}})
            self.assertEqual(saved.status_code, 200, saved.text)
            listed = (await client.get(root + "/files")).json()
            self.assertEqual(listed["files"][0]["role"], "build")

        self._as("instructor", exercise)

    def test_invalid_encoding_shape_and_limits_leave_saved_bytes_intact(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            root = f"/api/authoring/drafts/{draft['id']}"
            record = {"encoding": "utf-8", "content": "safe\r\n"}
            saved = await client.put(root + "/file", json={
                "revision": 0, "path": "data.txt", "file": record})
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            for bad in (
                {"encoding": "hex", "content": "ff"},
                {"encoding": "base64", "content": "AA=\n="},
                {"encoding": "base64", "content": "AB=="},
                {"encoding": "utf-8", "content": 3},
                {"encoding": "utf-8", "content": "x", "role": "seed"},
                {"encoding": "utf-8", "content": "x", "executable": True},
                {"encoding": "utf-8", "content": "é" * 1_000_001},
                {"encoding": "base64", "content": "AAAA" * 666_667},
            ):
                refused = await client.put(root + "/file", json={
                    "revision": 1, "path": "data.txt", "file": bad})
                self.assertEqual(refused.status_code, 422, refused.text[:500])
                self.assertEqual((await client.get(root)).json(), draft)
            for files in (
                {f"data/{index}.txt": record for index in range(201)},
                {f"data/{index}.txt": {"encoding": "utf-8", "content": "a" * 2_000_000}
                 for index in range(11)},
            ):
                refused = await client.put(root, json={"revision": 1,
                    "document": {**draft, "lesson_files": files}})
                self.assertEqual(refused.status_code, 422, refused.text[:500])
                self.assertEqual((await client.get(root)).json(), draft)
            for revision in (True, -1, "1", 1.5):
                refused = await client.put(root + "/file", json={
                    "revision": revision, "path": "data.txt", "file": record})
                self.assertEqual(refused.status_code, 422, refused.text)
            self.assertEqual((await client.get(root + "/file", params={"path": "data.txt"})).content,
                             b"safe\r\n")

        self._as("instructor", exercise)

    def test_learners_and_unauthenticated_clients_cannot_read_or_mutate_files(self) -> None:
        drafts = []

        async def prepare(client: httpx2.AsyncClient) -> None:
            drafts.append(await self._router_draft(client))

        self._as("instructor", prepare)
        draft = drafts[0]
        root = f"/api/authoring/drafts/{draft['id']}"

        async def exercise(client: httpx2.AsyncClient) -> None:
            for expected in (403, 401):
                for method, url, kwargs in (
                    ("get", root + "/files", {}),
                    ("get", root + "/file", {"params": {"path": "data.txt"}}),
                    ("put", root + "/file", {"json": {"revision": draft["revision"],
                        "path": "data.txt", "file": {"encoding": "utf-8", "content": "x"}}}),
                    ("delete", root + "/file", {"params": {"path": "data.txt",
                                                             "revision": draft["revision"]}}),
                ):
                    refused = await getattr(client, method)(url, **kwargs)
                    self.assertEqual(refused.status_code, expected, refused.text)
                client.cookies.clear()

        self._as("learner", exercise)

        async def verify(client: httpx2.AsyncClient) -> None:
            self.assertEqual((await client.get(root)).json(), draft)

        self._as("instructor", verify)

    def test_legacy_and_immutable_published_snapshots_reopen_without_migration(self) -> None:
        self._launch_with("multi", PackageRecordingLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            for draft in (await self._router_draft(client), await self._python_draft(client, publishable=True)):
                root = f"/api/authoring/drafts/{draft['id']}"
                self.assertNotIn("lesson_files", draft)
                self.assertEqual((await client.get(root + "/files")).json(),
                                 {"revision": draft["revision"], "files": []})
                self.assertEqual((await client.get(root)).json(), draft)
                for files in (None, {"helpers/new.py": {"encoding": "utf-8", "content": "N = 3\n"}}):
                    if files is not None:
                        saved = await client.put(root, json={"revision": draft["revision"],
                            "document": {**draft, "lesson_files": files}})
                        self.assertEqual(saved.status_code, 200, saved.text)
                        draft = saved.json()
                    preview = await client.post(root + "/preview", json={"revision": draft["revision"]})
                    self.assertEqual(preview.status_code, 200, preview.text)
                    await client.post(f"/api/labs/{preview.json()['key']}/stop")
                    published = await client.post(root + "/publish", json={"revision": draft["revision"]})
                    self.assertEqual(published.status_code, 200, published.text)
                    draft = published.json()["draft"]
                    example_id = published.json()["example"]["id"]
                    if files is not None:
                        edited = await client.put(root + "/file", json={"revision": draft["revision"],
                            "path": "helpers/new.py", "file": {"encoding": "utf-8", "content": "N = 9\n"}})
                        self.assertEqual(edited.status_code, 200, edited.text)
                        draft = edited.json()
                    revised = await client.post("/api/authoring/drafts", json={
                        "example_id": example_id, "revise": True})
                    self.assertEqual(revised.status_code, 201, revised.text)
                    if files is None:
                        self.assertNotIn("lesson_files", revised.json())
                    else:
                        self.assertEqual(revised.json()["lesson_files"], files)
                    self.assertEqual(revised.json()["schema_version"], 3)
                    if draft.get("kind") == "python":
                        self.assertEqual(revised.json()["files"], draft["files"])
                        self.assertEqual(revised.json()["python"], draft["python"])
                    else:
                        self.assertEqual(revised.json()["topology"], draft["topology"])

        self._as("instructor", exercise)

    def test_catalog_rename_and_bad_query_revisions_cannot_overwrite_files(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            root = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(root + "/file", json={"revision": 0, "path": "helpers.py",
                "file": {"encoding": "utf-8", "content": "N = 2\n"}})
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            refused = await client.put(root, json={"revision": 1, "document": {
                **draft, "catalog": {**draft["catalog"], "name": "Helpers"}}})
            self.assertEqual(refused.status_code, 422, refused.text)
            for revision in ("1.0", "01", "-1", "true"):
                refused = await client.delete(root + "/file", params={"revision": revision,
                                                                         "path": "helpers.py"})
                self.assertEqual(refused.status_code, 422, refused.text)
            self.assertEqual((await client.get(root)).json(), draft)

        self._as("instructor", exercise)
    def test_saved_bundle_draws_and_rebuilds_without_deleted_or_renamed_files(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            for draft in (await self._python_draft(client), await self._router_draft(client)):
                root = f"/api/authoring/drafts/{draft['id']}"
                code = "from lesson_helper import add\nadd(net)\n"
                changes = ({"python": "from mniplab import mnIPLab\ndef build_network():\n    net = mnIPLab()\n    " + code.replace("\n", "\n    ").rstrip() + "\n    return net\n"}
                           if draft.get("kind") == "python" else {"code_section": code})
                files = {"lesson_helper.py": {"encoding": "utf-8", "content":
                    "from pathlib import Path\ndef add(net):\n    net.addHost(Path('data/name.txt').read_text())\n"},
                    "data/name.txt": {"encoding": "base64", "content": "cGFja2FnZWQ="}}
                saved = await client.put(root, json={"revision": draft["revision"],
                    "document": {**draft, **changes, "lesson_files": files}})
                self.assertEqual(saved.status_code, 200, saved.text)
                draft = saved.json()
                drawing = (await client.get(root + "/drawing")).json()
                self.assertEqual(drawing["source"], "script", drawing)
                self.assertIn("packaged", [node["name"] for node in drawing["nodes"]])
                files["data/renamed.txt"] = files.pop("data/name.txt")
                saved = await client.put(root, json={"revision": draft["revision"],
                    "document": {**draft, "lesson_files": files}})
                self.assertEqual(saved.status_code, 200, saved.text)
                draft = saved.json()
                drawing = (await client.get(root + "/drawing")).json()
                self.assertEqual(drawing["source"], "none", drawing)
                self.assertIn("name.txt", drawing["note"])
                removed = await client.delete(root + "/file", params={
                    "revision": draft["revision"], "path": "lesson_helper.py"})
                self.assertEqual(removed.status_code, 200, removed.text)
                drawing = (await client.get(root + "/drawing")).json()
                self.assertEqual(drawing["source"], "none", drawing)
                self.assertIn("No module named 'lesson_helper'", drawing["note"])
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        checkout = str(Path(__file__).resolve().parents[2])
        with tempfile.TemporaryDirectory(dir=checkout, prefix=".bundle-conflict-") as conflict:
            Path(conflict, "lesson_helper.py").write_text("raise RuntimeError('checkout fallback')\n")
            with patch.dict(os.environ, {"PYTHONPATH": conflict + os.pathsep + checkout}):
                self._as("instructor", exercise)

    def test_preview_and_published_starts_keep_complete_packages_in_both_modes(self) -> None:
        import os
        from mniplab.webserver.authoring_record import record_script

        test = self

        class BundleLauncher(PackageRecordingLauncher):
            async def __call__(self, example_id: str, lab_file: str,
                               config_copy: str | None = None, timeout: float | None = None,
                               cwd: str | None = None) -> Any:
                test.assertEqual(cwd, os.path.dirname(lab_file))
                assert cwd is not None
                drawing = await record_script(cwd, lab_file)
                test.assertIn("bundled", [node["name"] for node in drawing["nodes"]])
                return await super().__call__(example_id, lab_file, config_copy, timeout, cwd)

        launcher = BundleLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client: httpx2.AsyncClient) -> None:
            for draft in (await self._python_draft(client, publishable=True), await self._router_draft(client)):
                root = f"/api/authoring/drafts/{draft['id']}"
                code = "from bundled_helper import add\nadd(net)\n"
                changes = ({"python": "from mniplab import mnIPLab\ndef build_network():\n    net = mnIPLab()\n    " + code.replace("\n", "\n    ").rstrip() + "\n    return net\n"}
                           if draft.get("kind") == "python" else {"code_section": code})
                draft = (await client.put(root, json={"revision": draft["revision"],
                    "document": {**draft, **changes, "lesson_files": {
                        "bundled_helper.py": {"encoding": "utf-8", "content":
                            "from pathlib import Path\ndef add(net):\n    net.addHost(Path('host.txt').read_text())\n"},
                        "host.txt": {"encoding": "utf-8", "content": "bundled"}}}})).json()
                preview = await client.post(root + "/preview", json={"revision": draft["revision"]})
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertTrue(os.path.isfile(os.path.join(launcher.runs[-1]["cwd"], "host.txt")))
                await client.post(f"/api/labs/{preview.json()['key']}/stop")
                published = await client.post(root + "/publish", json={"revision": draft["revision"]})
                self.assertEqual(published.status_code, 200, published.text)
                started = await client.post("/api/labs", json={"example_id": published.json()["example"]["id"]})
                self.assertEqual(started.status_code, 200, started.text)
                await client.post(f"/api/labs/{started.json()['key']}/stop")
        self._as("instructor", exercise)

    def test_build_roles_invalidate_preview_and_conservative_copy_compatibility(self) -> None:
        from mniplab.webserver.authoring import AuthoringRepository

        self._launch_with("multi", PackageRecordingLauncher())
        repository = AuthoringRepository(self.storage.name)

        async def exercise(client: httpx2.AsyncClient) -> None:
            for draft in (await self._python_draft(client), await self._router_draft(client)):
                root = f"/api/authoring/drafts/{draft['id']}"
                legacy_fingerprint = repository.fingerprint(draft)
                legacy_structure = repository.persistent_structure(draft)
                for files, changed in (
                    ({}, False),
                    ({"presentation/notes.txt": {"encoding": "utf-8", "content": "one", "role": "presentation"}}, False),
                    ({"presentation/new.txt": {"encoding": "utf-8", "content": "two", "role": "presentation"}}, False),
                    ({"presentation/new.txt": {"encoding": "utf-8", "content": "two", "role": "build"}}, True),
                    ({"data.bin": {"encoding": "base64", "content": "AP8="}}, True),
                    ({"data.bin": {"encoding": "base64", "content": "AAA="}}, True),
                    ({"data.bin": {"encoding": "utf-8", "content": "\x00\x00", "role": "build"}}, False),
                    ({"renamed.bin": {"encoding": "base64", "content": "AAA="}}, True),
                    ({}, True),
                ):
                    preview = await client.post(root + "/preview", json={"revision": draft["revision"]})
                    self.assertEqual(preview.status_code, 200, preview.text)
                    await client.post(f"/api/labs/{preview.json()['key']}/stop")
                    before = repository.persistent_structure(draft)
                    saved = await client.put(root, json={"revision": draft["revision"],
                        "document": {**draft, "lesson_files": files}})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    draft = saved.json()
                    self.assertEqual(draft["preview_valid"], not changed)
                    after = repository.persistent_structure(draft)
                    self.assertEqual(before != after, changed)
                self.assertEqual(repository.fingerprint(draft), legacy_fingerprint)
                self.assertEqual(repository.persistent_structure(draft), legacy_structure)
        self._as("instructor", exercise)

    def test_seed_only_revision_keeps_group_copy_and_build_revision_requires_reset(self) -> None:
        from pathlib import Path
        from mniplab.tests.authoring_fixtures import SeedingLauncher
        from mniplab.webserver import registry
        from mniplab.webserver.authoring import AuthoringRepository

        self._launch_with("multi", SeedingLauncher())
        repository = AuthoringRepository(self.storage.name)
        created = repository.create_python(
            {"name": "Group lesson", "description": "", "protocols": [], "area": "Static"},
            "from mniplab import mnIPLab\ndef build_network():\n    net = mnIPLab()\n"
            "    net.addRouter('r1', frr_dir='frr-config/r1')\n    return net\n",
            {"frr-config/r1/frr.conf": "! original\n"}, "", {"version": 1, "nodes": {}})

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.get(f"/api/authoring/drafts/{created['id']}")).json()
            root = f"/api/authoring/drafts/{draft['id']}"
            example_id = None
            marker = None
            for change, incompatible in (({}, False), ({"files": {"frr-config/r1/frr.conf": "! revised\n"}, "lesson_files": {}}, False),
                    ({"lesson_files": {"helper.py": {"encoding": "utf-8", "content": "N = 2\n"}}}, True)):
                if change:
                    draft = (await client.put(root, json={"revision": draft["revision"],
                        "document": {**draft, **change}})).json()
                    self.assertFalse(draft["preview_valid"])
                preview = await client.post(root + "/preview", json={"revision": draft["revision"]})
                self.assertEqual(preview.status_code, 200, preview.text)
                await client.post(f"/api/labs/{preview.json()['key']}/stop")
                publication = await client.post(root + "/publish", json={"revision": draft["revision"],
                    "mode": "update_existing" if example_id else "save_as_new"})
                self.assertEqual(publication.status_code, 200, publication.text)
                result = publication.json()
                draft, example_id = result["draft"], result["example"]["id"]
                self.assertEqual(result["incompatible_groups"], ["instructor1"] if incompatible else [])
                started = await client.post("/api/labs", json={"example_id": example_id})
                self.assertEqual(started.status_code, 409 if incompatible else 200, started.text)
                if not incompatible:
                    await client.post(f"/api/labs/{started.json()['key']}/stop")
                if marker is None:
                    marker = Path(registry.config_copies.copy_dir("instructor1", example_id), "learner-work.txt")
                    marker.write_text("preserved")
                self.assertEqual(marker.read_text(), "preserved")
        self._as("instructor", exercise)


@pytest.mark.unit
@pytest.mark.parametrize("source_checkout", [False, True])
def test_installed_library_keeps_installed_dependencies_importable(
    tmp_path: Any, source_checkout: bool,
) -> None:
    """Package isolation in a wheel install retains neighboring dependencies."""
    import subprocess
    import sys

    installed = tmp_path / ".venv" / "site-packages"
    installed.mkdir(parents=True)
    library_root = tmp_path if source_checkout else installed
    if source_checkout:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    (installed / "lesson_dependency.py").write_text("VALUE = 'installed'\n")
    package = tmp_path / "lesson"
    package.mkdir()
    script = (
        "import sys\nimport mniplab.runner as runner\n"
        f"sys.path.insert(0, {str(installed)!r})\n"
        f"runner.__file__ = {str(library_root / 'mniplab' / 'runner.py')!r}\n"
        f"runner.isolate_package_imports({str(package)!r})\n"
        "import lesson_dependency\nassert lesson_dependency.VALUE == 'installed'\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
