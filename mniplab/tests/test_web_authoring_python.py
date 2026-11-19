"""Generated Python and Python-based Lab Examples through the HTTP API."""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from typing import Any
from unittest.mock import patch
from pathlib import Path

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import (
    AuthoringAPIFixture, PackageRecordingLauncher, SeedingLauncher, SourceRecordingLauncher,
)
from mniplab.webserver.authoring import AuthoringRepository
from mniplab.webserver import registry


@pytest.mark.unit
class GeneratedPythonAPITests(AuthoringAPIFixture):
    """The Python an Instructor reads is the Python a Lab runs."""

    def test_catalog_start_of_the_published_example_runs_the_same_python(self) -> None:
        launcher = SourceRecordingLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            draft = await self._router_draft(client)
            shown = (await client.get(f"/api/authoring/drafts/{draft['id']}/python")).json()
            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            await client.post(f"/api/labs/{preview.json()['key']}/stop")
            published = await client.post(f"/api/authoring/drafts/{draft['id']}/publish",
                                          json={"revision": draft["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            started = await client.post("/api/labs", json={
                "example_id": published.json()["example"]["id"]})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(launcher.sources, [shown["source"], shown["source"]])

        self._as("instructor", exercise)

    def test_each_interface_address_is_declared_once_on_its_link_end(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            source = (await client.get(f"/api/authoring/drafts/{draft['id']}/python")).json()["source"]
            self.assertIn("h1 = net.addHost('h1', defaultRoute='via 10.0.0.1', "
                          "defaultRoute6='via 2001:db8::1')", source)
            self.assertIn("params2={'ip': '10.0.0.2/30', 'ip6': '2001:db8::2/64'}", source)

            created = await client.post("/api/authoring/drafts", json={"example_id": "static-lab-nat"})
            self.assertEqual(created.status_code, 201, created.text)
            nat = created.json()
            source = (await client.get(f"/api/authoring/drafts/{nat['id']}/python")).json()["source"]
            self.assertIn("nat1 = net.addNAT('nat1', subnet='100.64.0.0/23')", source)
            self.assertIn("net.addLink(\n        nat1, r1, port1=0, port2=0, params1={'ip': '100.64.0.1/24'},",
                          source)
            self.assertNotRegex(source, r"\bip=")

        self._as("instructor", exercise)

    def test_download_unpacks_away_from_the_checkout_with_its_own_lesson_assets(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            draft["guide"] = "# Routed lesson\n\nPing h1 from r1.\n"
            draft["layout"]["nodes"] = {"r": {"x": 100, "y": 200}, "h": {"x": 700, "y": 200}}
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}",
                                     json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            shown = (await client.get(f"/api/authoring/drafts/{draft['id']}/python")).json()
            download = await client.get(f"/api/authoring/drafts/{draft['id']}/python.zip")
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.headers["content-type"], "application/zip")
            self.assertIn('filename="routed-lesson.zip"',
                          download.headers["content-disposition"])
            return shown, download.content

        results = []

        async def run(client):
            results.append(await exercise(client))

        self._as("instructor", run)
        shown, archive = results[0]
        with tempfile.TemporaryDirectory() as elsewhere:
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                package.extractall(elsewhere)
            root = os.path.join(elsewhere, "routed-lesson")
            with open(os.path.join(root, "routed-lesson.py"), encoding="utf-8") as script:
                self.assertEqual(script.read(), shown["source"])
            self.assertEqual(os.listdir(os.path.join(root, "frr-config", "r1")), ["frr.conf"])
            with open(os.path.join(root, "frr-config", "r1", "frr.conf"), encoding="utf-8") as seed:
                self.assertIn("network 10.0.0.0/30 area 0", seed.read())
            with open(os.path.join(root, "examples", "guides", "routed-lesson.md"),
                      encoding="utf-8") as guide:
                self.assertEqual(guide.read(), "# Routed lesson\n\nPing h1 from r1.\n")
            with open(os.path.join(root, "examples", "layouts", "routed-lesson.json"),
                      encoding="utf-8") as layout:
                self.assertEqual(json.load(layout)["nodes"],
                                 {"r1": {"x": 100, "y": 200}, "h1": {"x": 700, "y": 200}})
            self.assertTrue(os.path.isfile(os.path.join(root, "README.md")))
            checkout = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            completed = subprocess.run(
                [sys.executable, "routed-lesson.py", "--help"], cwd=root,
                env={**os.environ, "PYTHONPATH": checkout},
                capture_output=True, text=True, timeout=60, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Routed lesson", completed.stdout)
            self.assertIn("--enable-web", completed.stdout)


@pytest.mark.unit
class UploadAPITests(AuthoringAPIFixture):
    """Upload a script… makes a new Python draft, and nothing else changes its script."""

    SCRIPT = (
        "from mniplab import mnIPLab\n\n\n"
        "def build_network():\n"
        "    net = mnIPLab(topo=None, controller=None)\n"
        "    net.addRouter('r1', frr_dir='./frr-config/lesson/%(name)s')\n"
        "    return net\n"
    )

    async def _upload(self, client, name="ospf_lesson.py", content=None):
        data = (self.SCRIPT if content is None else content)
        return await client.post("/api/authoring/drafts/upload", files={
            "file": (name, data.encode("utf-8") if isinstance(data, str) else data,
                     "text/x-python")})

    async def _replace(self, client, draft, name="replacement.py", content=None, revision=None):
        data = self.SCRIPT if content is None else content
        return await client.put(f"/api/authoring/drafts/{draft['id']}/script",
                                data={"revision": str(draft["revision"] if revision is None else revision)},
                                files={"file": (name, data.encode("utf-8") if isinstance(data, str) else data,
                                                 "text/x-python")})

    def test_an_uploaded_script_becomes_a_new_python_draft(self) -> None:
        async def exercise(client):
            visual = await self._router_draft(client)
            uploaded = await self._upload(client)
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            draft = uploaded.json()
            self.assertEqual(draft["kind"], "python")
            self.assertEqual(draft["python"], self.SCRIPT)
            self.assertEqual(draft["files"], {})
            self.assertEqual(draft["catalog"], {"id": None, "name": "ospf_lesson",
                                                "description": "", "protocols": []})
            self.assertIsNone(draft["python_error"])
            self.assertFalse(draft["preview_valid"])
            listed = {item["id"] for item in
                      (await client.get("/api/authoring/drafts")).json()["drafts"]}
            self.assertEqual(listed, {visual["id"], draft["id"]})

        self._as("instructor", exercise)

    def test_anything_but_one_python_script_within_the_limit_is_refused(self) -> None:
        async def exercise(client):
            for name, content, reason in (
                ("lesson.txt", self.SCRIPT, ".py"),
                ("lesson.py", b"\xff\xfe\x00binary", "UTF-8"),
                ("lesson.py", "#" * 500_001, "500 KB"),
            ):
                with self.subTest(name=name, reason=reason):
                    refused = await self._upload(client, name, content)
                    self.assertEqual(refused.status_code, 422, refused.text)
                    self.assertIn(reason, refused.json()["detail"])
            self.assertEqual((await client.get("/api/authoring/drafts")).json()["drafts"], [])

        self._as("instructor", exercise)

    def test_a_script_that_does_not_compile_is_created_with_its_line_and_never_previews(self) -> None:
        launcher = SourceRecordingLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            uploaded = await self._upload(client, content="def build_network(:\n    pass\n")
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            draft = uploaded.json()
            self.assertEqual(draft["python_error"]["line"], 1)
            refused = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(refused.status_code, 422)
            self.assertIn("line 1", refused.json()["detail"])
            self.assertEqual(launcher.sources, [])

        self._as("instructor", exercise)

    def test_explicit_replacement_changes_only_python_and_invalidates_preview(self) -> None:
        async def exercise(client):
            draft = (await self._upload(client)).json()
            draft = (await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": {
                    **draft,
                    "catalog": {**draft["catalog"], "name": "Named lab", "description": "Teach",
                                "protocols": ["OSPF"]},
                    "guide": "# Keep this\n", "layout": {"version": 1, "nodes": {"r1": {"x": 3, "y": 4}}},
                    "lesson_files": {"helper.py": {"encoding": "utf-8", "content": "VALUE = 3\n"}},
                }})).json()
            repository = AuthoringRepository(self.storage.name)
            repository.record_preview(draft, "preview-key", 1)
            draft["preview_valid"] = repository.preview_status(draft)["current"]
            self.assertTrue(draft["preview_valid"])
            before = {key: draft[key] for key in (
                "id", "kind", "catalog", "guide", "layout", "files", "lesson_files",
                "created_at", "published_revision")}
            replacement = "def build_network(:\n    pass\n"
            saved = await self._replace(client, draft, content=replacement)
            self.assertEqual(saved.status_code, 200, saved.text)
            updated = saved.json()
            self.assertEqual(updated["python"], replacement)
            self.assertEqual({key: updated[key] for key in before}, before)
            self.assertEqual(updated["revision"], draft["revision"] + 1)
            self.assertFalse(updated["preview_valid"])
            self.assertEqual(updated["python_error"]["line"], 1)
            self.assertEqual((await client.get(f"/api/authoring/drafts/{draft['id']}")).json()["python"],
                             replacement)

        self._as("instructor", exercise)

    def test_invalid_and_stale_replacement_requests_leave_stored_bytes_unchanged(self) -> None:
        async def exercise(client):
            draft = (await self._upload(client)).json()
            path = os.path.join(self.storage.name, "drafts", draft["id"], "definition.json")
            with open(path, "rb") as source:
                original = source.read()
            for name, content, reason in (
                ("script.txt", self.SCRIPT, ".py"),
                ("script.py", b"\xff", "UTF-8"),
                ("script.py", b"#" * 500_001, "500 KB"),
            ):
                with self.subTest(name=name, reason=reason):
                    response = await self._replace(client, draft, name, content)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn(reason, response.json()["detail"])
                    with open(path, "rb") as source:
                        self.assertEqual(source.read(), original)
            stale = await self._replace(client, draft, content="# stale\n", revision=draft["revision"] + 1)
            self.assertEqual(stale.status_code, 409, stale.text)
            with open(path, "rb") as source:
                self.assertEqual(source.read(), original)

            visual = await self._router_draft(client)
            visual_path = os.path.join(self.storage.name, "drafts", visual["id"], "definition.json")
            with open(visual_path, "rb") as source:
                visual_bytes = source.read()
            refused = await self._replace(client, visual, content="# wrong kind\n")
            self.assertEqual(refused.status_code, 422, refused.text)
            with open(visual_path, "rb") as source:
                self.assertEqual(source.read(), visual_bytes)

        self._as("instructor", exercise)

    def test_learners_cannot_replace_a_python_script(self) -> None:
        async def exercise(client):
            draft = AuthoringRepository(self.storage.name).upload("lesson.py", self.SCRIPT.encode())
            path = os.path.join(self.storage.name, "drafts", draft["id"], "definition.json")
            with open(path, "rb") as source:
                original = source.read()
            refused = await self._replace(client, draft, content="# forbidden\n")
            self.assertEqual(refused.status_code, 403, refused.text)
            with open(path, "rb") as source:
                self.assertEqual(source.read(), original)

        self._as("learner", exercise)

    def test_a_save_keeps_python_source_seeds_and_rejects_kind_changes(self) -> None:
        async def exercise(client):
            draft = (await self._upload(client)).json()
            original = dict(draft)
            source = "def build_network(:\n    pass\n"
            seeds = {"frr-config/lesson/r1/frr.conf": "router ospf\n"}
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"],
                "document": {**draft, "python": source, "files": seeds}})
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            reopened = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
            self.assertEqual((reopened["python"], reopened["files"]), (source, seeds))
            self.assertEqual(reopened["catalog"]["id"], original["catalog"]["id"])
            self.assertEqual(reopened["python_error"]["line"], 1)
            self.assertIsInstance(reopened["python_error"]["column"], int)
            stale = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": original["revision"], "document": original})
            self.assertEqual(stale.status_code, 409, stale.text)
            refused = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                       json={"revision": draft["revision"]})
            self.assertEqual(refused.status_code, 422, refused.text)
            visual = await self._router_draft(client)
            as_python = {key: value for key, value in draft.items()
                         if key not in ("preview_valid", "python_error")}
            refused = await client.put(f"/api/authoring/drafts/{visual['id']}", json={
                "revision": visual["revision"], "document": {**as_python, "id": visual["id"]}})
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertIn("kind", refused.json()["detail"])

            lesson = {"catalog": {**draft["catalog"], "name": "OSPF lesson",
                                  "description": "Two areas"},
                      "guide": "# OSPF\n", "layout": {"version": 1, "nodes": {"r1": {"x": 5, "y": 6}}}}
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": {**draft, **lesson}})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["python"], source)

        self._as("instructor", exercise)

    def test_a_downloaded_canvas_script_uploads_as_a_separate_python_draft(self) -> None:
        launcher = PackageRecordingLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            visual = await self._router_draft(client)
            before = self._stored(visual["id"])
            package = await client.get(f"/api/authoring/drafts/{visual['id']}/python.zip")
            with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
                script = archive.read("routed-lesson/routed-lesson.py").decode()
                self.assertIn("routed-lesson/frr-config/r1/frr.conf", archive.namelist())
            uploaded = await self._upload(client, "routed-lesson.py", script)
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            draft = uploaded.json()
            self.assertEqual((draft["python"], draft["files"]), (script, {}))
            self.assertIsNone(draft["catalog"]["id"])
            self.assertEqual(self._stored(visual["id"]), before)

            started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual((launcher.runs[-1]["source"], launcher.runs[-1]["seeds"]),
                             (script, {}))

        self._as("instructor", exercise)

    def _stored(self, draft_id):
        """The draft's stored definition bytes."""
        with open(os.path.join(self.storage.name, "drafts", draft_id, "definition.json"),
                  "rb") as source:
            return source.read()


class PythonDraftFixture(AuthoringAPIFixture):
    """An uploaded Python draft's script and Seed, and the calls its tests share."""

    SCRIPT = (
        "import os\n\nfrom mniplab import mnIPLab\n\n\n"
        "def build_network():\n"
        "    net = mnIPLab(topo=None, controller=None)\n"
        "    net.addRouter('r1', frr_dir='./frr-config/lesson/%(name)s')\n"
        "    return net\n"
    )

    def setUp(self) -> None:
        super().setUp()
        self.launcher = SeedingLauncher()
        self._launch_with("multi", self.launcher)

    SEED = {"frr-config/lesson/r1/frr.conf": "router ospf\n ospf router-id 9.9.9.9\n"}

    async def _python_draft(self, client, name="lesson.py"):
        """Upload :attr:`SCRIPT` as a new Python draft."""
        created = await client.post("/api/authoring/drafts/upload", files={
            "file": (name, self.SCRIPT.encode("utf-8"), "text/x-python")})
        self.assertEqual(created.status_code, 201, created.text)
        draft = created.json()
        draft["catalog"]["area"] = "Intra-domain"
        return await self._save(client, draft)

    async def _seeded_draft(self, client):
        """A Python draft holding a Router Seed, as Keep will leave one."""
        created = AuthoringRepository(self.storage.name).create_python(
            {"name": "Seeded lesson", "description": "", "protocols": [], "area": "Intra-domain"},
            self.SCRIPT, self.SEED, "", {"version": 1, "nodes": {}})
        return (await client.get(f"/api/authoring/drafts/{created['id']}")).json()

    async def _save(self, client, draft, **changes):
        document = {**draft, **changes}
        saved = await client.put(f"/api/authoring/drafts/{draft['id']}",
                                 json={"revision": draft["revision"], "document": document})
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    async def _preview(self, client, draft):
        started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                    json={"revision": draft["revision"]})
        self.assertEqual(started.status_code, 200, started.text)
        await client.post(f"/api/labs/{started.json()['key']}/stop")
        return started.json()


@pytest.mark.unit
class PythonDraftAPITests(PythonDraftFixture):
    """Python-based drafts save, Preview and publish through the Catalog rules."""

    def test_a_seed_file_other_than_frr_conf_is_refused(self) -> None:
        repository = AuthoringRepository(self.storage.name)
        for path in ("frr-config/lesson/r1/daemons", "frr-config/lesson/r1/vtysh.conf",
                     "frr-config/lesson/r1/frr.conf.bak"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "frr.conf"):
                repository.create_python(
                    {"name": "Seeded", "description": "", "protocols": []},
                    self.SCRIPT, {path: "bfdd=yes\n"}, "", {"version": 1, "nodes": {}})

    def test_preview_runs_the_script_from_its_own_folder_with_isolated_config(self) -> None:
        async def exercise(client):
            draft = await self._seeded_draft(client)
            started = await self._preview(client, draft)
            self.assertTrue(started["preview_valid"])
            run = self.launcher.runs[-1]
            self.assertEqual(run["source"], self.SCRIPT)
            self.assertEqual(os.path.dirname(run["lab_file"]), run["cwd"])
            self.assertEqual(run["seeds"], {
                os.path.join("frr-config", "lesson", "r1", "frr.conf"):
                    "router ospf\n ospf router-id 9.9.9.9\n"})
            self.assertTrue(run["config_copy"].startswith(
                os.path.join(self.storage.name, "previews")))

            presented = await self._save(client, draft, guide="# New Guide\n",
                                         layout={"version": 1, "nodes": {"r1": {"x": 1, "y": 2}}})
            self.assertTrue(presented["preview_valid"])

        self._as("instructor", exercise)

    def test_a_learner_cannot_read_or_edit_python_source(self) -> None:
        draft = AuthoringRepository(self.storage.name).create_python(
            {"name": "Private source", "description": "", "protocols": []},
            self.SCRIPT, self.SEED, "", {"version": 1, "nodes": {}})

        async def exercise(client):
            path = f"/api/authoring/drafts/{draft['id']}"
            for suffix in ("", "/python", "/python.zip"):
                self.assertEqual((await client.get(path + suffix)).status_code, 403)
            saved = await client.put(path, json={
                "revision": draft["revision"], "document": {**draft, "python": "changed"}})
            self.assertEqual(saved.status_code, 403, saved.text)

        self._as("learner", exercise)

    def test_script_and_seed_edits_each_require_a_new_preview(self) -> None:
        async def exercise(client):
            draft = await self._seeded_draft(client)
            await self._preview(client, draft)
            seed = {"frr-config/lesson/r1/frr.conf": "router ospf\n ospf router-id 1.1.1.1\n"}
            draft = await self._save(client, draft, files=seed)
            self.assertFalse(draft["preview_valid"])
            await self._preview(client, draft)
            draft = await self._save(client, draft, python=self.SCRIPT + "# edited\n")
            self.assertFalse(draft["preview_valid"])
            reopened = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
            self.assertEqual(reopened["files"], seed)
            self.assertEqual(reopened["python"], self.SCRIPT + "# edited\n")

        self._as("instructor", exercise)

    def test_single_lab_preview_is_isolated_in_its_own_folder(self) -> None:
        self._launch_with("single", self.launcher)

        async def exercise(client):
            draft = await self._python_draft(client)
            await self._preview(client, draft)
            run = self.launcher.runs[-1]
            previews = os.path.join(self.storage.name, "previews", "config")
            self.assertTrue(run["config_copy"].startswith(previews + os.sep))
            self.assertEqual(run["cwd"], os.path.join(run["config_copy"], ".package"))
            self.assertFalse(os.path.exists(run["config_copy"]))
            self.assertFalse(os.path.exists(os.path.join(self.storage.name, "group-config")))

        self._as("instructor", exercise)

    def test_keep_fills_an_uploaded_scripts_empty_seed_and_invalidates_preview(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                       json={"revision": draft["revision"]})
            self.assertEqual(started.status_code, 200, started.text)
            self.running_configs["r1"] = "interface lo\n ip address 192.0.2.9/32\nexit\n"
            reviewed = await client.get(f"/api/authoring/drafts/{draft['id']}/keep")
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            row = next(row for row in reviewed.json()["routers"] if row["name"] == "r1")
            self.assertEqual(row["seed"], "frr-config/lesson/r1/frr.conf")
            kept = await client.post(f"/api/authoring/drafts/{draft['id']}/keep", json={
                "revision": draft["revision"], "selections": [{
                    "node_id": row["node_id"], "digest": row["digest"],
                    "sections": [section["id"] for section in row["sections"]]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            saved = kept.json()
            self.assertEqual(saved["files"], {"frr-config/lesson/r1/frr.conf":
                                             self.running_configs["r1"]})
            self.assertEqual(saved["python"], draft["python"])
            self.assertFalse(saved["preview_valid"])
            self.assertEqual(saved["revision"], draft["revision"] + 1)
            again = await client.get(f"/api/authoring/drafts/{draft['id']}/keep")
            self.assertEqual(again.json()["routers"][0]["sections"], [])
            self.assertTrue(again.json()["has_kept_changes"])
            await client.post(f"/api/labs/{started.json()['key']}/stop")
            fresh_start = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": saved["revision"]})
            self.assertEqual(fresh_start.status_code, 200, fresh_start.text)
            self.assertEqual(self.launcher.runs[-1]["seeds"], saved["files"])
            fresh = await client.get(f"/api/authoring/drafts/{draft['id']}/keep")
            self.assertEqual(fresh.status_code, 200, fresh.text)
            self.assertFalse(fresh.json()["has_kept_changes"])
            await client.post(f"/api/labs/{fresh_start.json()['key']}/stop")

        self._as("instructor", exercise)

    def test_keep_explains_inline_missing_and_unsafe_seed_folders_and_refuses_them(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            for options, fix in (
                ("", "frr_dir"),
                (", frr_dir='./frr-config/%(name)s', frr_config='router ospf\\n'", "inline"),
                (", frr_dir='../outside/%(name)s'", "under frr-config"),
                (", frr_dir='/tmp/outside/%(name)s'", "under frr-config"),
            ):
                with self.subTest(options=options):
                    source = ("from mniplab import mnIPLab\n"
                              "def build_network():\n"
                              "    net = mnIPLab(topo=None, controller=None)\n"
                              f"    net.addRouter('r1'{options})\n"
                              "    return net\n")
                    draft = (await client.post("/api/authoring/drafts/upload", files={
                        "file": ("lesson.py", source, "text/x-python")})).json()
                    self.running_configs.clear()
                    started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                               json={"revision": draft["revision"]})
                    self.assertEqual(started.status_code, 200, started.text)
                    self.running_configs["r1"] = "ip route 192.0.2.0/24 Null0\n"
                    row = (await client.get(f"/api/authoring/drafts/{draft['id']}/keep")).json()["routers"][0]
                    self.assertIsNone(row["seed"])
                    self.assertIn(fix, row["not_keepable"])
                    refused = await client.post(f"/api/authoring/drafts/{draft['id']}/keep", json={
                        "revision": draft["revision"], "selections": [{
                            "node_id": row["node_id"], "digest": row["digest"],
                            "sections": [section["id"] for section in row["sections"]]}]})
                    self.assertEqual(refused.status_code, 422, refused.text)
                    unchanged = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
                    self.assertEqual((unchanged["revision"], unchanged["files"]), (0, {}))
                    await client.post(f"/api/labs/{started.json()['key']}/stop")

        self._as("instructor", exercise)

    def test_python_service_changes_are_listed_for_script_edits_and_cannot_be_kept(self) -> None:
        from mniplab.tests.authoring_fixtures import dhcp_pool
        pool = dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")
        reads: list[Any] = [
            patch("mniplab.labhandle.LocalLabHandle._op_topology", return_value={
                "nodes": [{"id": "r1", "type": "router", "services": ["dhcp"],
                           "service_details": []}], "links": []}),
            patch("mniplab.labhandle.LocalLabHandle._op_get_node_pools",
                  lambda _handle, _name: {"pools": [dict(pool)]}),
        ]
        for read in reads:
            read.start()
            self.addCleanup(read.stop)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                       json={"revision": draft["revision"]})
            self.assertEqual(started.status_code, 200, started.text)
            pool["lease_time"] = 300
            review = await client.get(f"/api/authoring/drafts/{draft['id']}/keep")
            self.assertEqual(review.status_code, 200, review.text)
            feature = review.json()["features"][0]
            self.assertEqual(feature["kind"], "dhcp_pool")
            self.assertEqual(feature["not_keepable"], "change in your script")
            refused = await client.post(f"/api/authoring/drafts/{draft['id']}/keep", json={
                "revision": draft["revision"], "selections": [],
                "features": [{"id": feature["id"], "digest": feature["digest"]}]})
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertIn("change in your script", refused.json()["detail"])

        self._as("instructor", exercise)


@pytest.mark.unit
class PythonPublicationAPITests(PythonDraftFixture):
    """Published Python-based examples run from their own version folder."""

    async def _published(self, client):
        draft = await self._seeded_draft(client)
        await self._preview(client, draft)
        published = await client.post(f"/api/authoring/drafts/{draft['id']}/publish",
                                      json={"revision": draft["revision"]})
        self.assertEqual(published.status_code, 200, published.text)
        return published.json()

    async def _revise(self, client, example_id, **changes):
        """Publish an Update existing revision of an authored Python example."""
        created = await client.post("/api/authoring/drafts",
                                    json={"example_id": example_id, "revise": True})
        self.assertEqual(created.status_code, 201, created.text)
        draft = await self._save(client, created.json(), **changes)
        await self._preview(client, draft)
        published = await client.post(f"/api/authoring/drafts/{draft['id']}/publish",
                                      json={"revision": draft["revision"],
                                            "mode": "update_existing"})
        self.assertEqual(published.status_code, 200, published.text)
        return published.json()

    async def _start(self, client, example_id, clean=False):
        started = await client.post("/api/labs", json={"example_id": example_id, "clean": clean})
        if started.status_code == 200:
            await client.post(f"/api/labs/{started.json()['key']}/stop")
        return started

    def test_replacing_a_revision_does_not_mutate_its_published_base(self) -> None:
        async def exercise(client):
            published = await self._published(client)
            example_id = published["example"]["id"]
            created = await client.post("/api/authoring/drafts",
                                        json={"example_id": example_id, "revise": True})
            self.assertEqual(created.status_code, 201, created.text)
            draft = created.json()
            repo = AuthoringRepository(self.storage.name)
            original_definition = Path(repo.published_file(example_id, "definition.json")).read_bytes()
            original_script = Path(repo.published_file(example_id, "lab.py")).read_bytes()
            updated = await client.put(f"/api/authoring/drafts/{draft['id']}/script",
                                       data={"revision": str(draft["revision"])},
                                       files={"file": ("replacement.py", b"unfinished = (\n", "text/x-python")})
            self.assertEqual(updated.status_code, 200, updated.text)
            body = updated.json()
            self.assertEqual(body["catalog"]["id"], example_id)
            self.assertEqual(body["published_revision"], draft["published_revision"])
            self.assertIsNotNone(body["python_error"])
            self.assertEqual(Path(repo.published_file(example_id, "definition.json")).read_bytes(),
                             original_definition)
            self.assertEqual(Path(repo.published_file(example_id, "lab.py")).read_bytes(), original_script)

        self._as("instructor", exercise)

    # Inherited PythonDraftAPITests cases run again under this launcher.

    def test_published_python_example_runs_its_own_version_folder(self) -> None:
        async def exercise(client):
            published = await self._published(client)
            example = published["example"]
            self.assertEqual(example["kind"], "python")
            self.assertEqual(example["node_count"], 2)
            self.assertTrue(example["persistent_config"])
            started = await self._start(client, example["id"])
            self.assertEqual(started.status_code, 200, started.text)
            run = self.launcher.runs[-1]
            self.assertEqual(run["source"], self.SCRIPT)
            self.assertEqual(run["cwd"], os.path.dirname(run["lab_file"]))
            self.assertTrue(run["cwd"].startswith(os.path.join(self.storage.name, "published")))
            self.assertEqual(list(run["seeds"]), [os.path.join("frr-config", "lesson", "r1",
                                                               "frr.conf")])
            refused = await client.post(
                f"/api/authoring/drafts/{published['draft']['id']}/publish",
                json={"revision": published["draft"]["revision"], "mode": "update_existing"})
            self.assertEqual(refused.status_code, 409)

        self._as("instructor", exercise)

    def test_a_revise_changes_only_lesson_details_and_keeps_config_copies(self) -> None:
        async def exercise(client):
            example_id = (await self._published(client))["example"]["id"]
            self.assertEqual((await self._start(client, example_id)).status_code, 200)

            revised = await self._revise(client, example_id, guide="# Revised\n")
            self.assertEqual(revised["incompatible_groups"], [])
            self.assertEqual(revised["example"]["id"], example_id)
            self.assertEqual((await self._start(client, example_id)).status_code, 200)

            draft = (await client.post("/api/authoring/drafts",
                                       json={"example_id": example_id, "revise": True})).json()
            for change in ({"python": self.SCRIPT + "# revised\n"},
                           {"files": {"frr-config/other/r1/frr.conf": "router ospf\n"}}):
                with self.subTest(change=list(change)):
                    saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                        "revision": draft["revision"], "document": {**draft, **change}})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    draft = saved.json()
                    self.assertEqual(draft["catalog"]["id"], example_id)
                    self.assertFalse(draft["preview_valid"])

        self._as("instructor", exercise)

    def test_copy_and_source_revision_preserve_files_and_refuse_stale_publication(self) -> None:
        async def exercise(client):
            source = await self._seeded_draft(client)
            source = await self._save(client, source, guide="# Original\n", lesson_files={
                "helpers/value.py": {"encoding": "utf-8", "content": "VALUE = 7\n"},
                "presentation/image.png": {"encoding": "base64", "content": "AAEC/w==",
                                           "role": "presentation"},
            })
            await self._preview(client, source)
            published = await client.post(f"/api/authoring/drafts/{source['id']}/publish",
                                          json={"revision": source["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            original = published.json()
            example_id = original["example"]["id"]
            login = await client.post("/api/login", auth=("instructor2", "lab12345"))
            self.assertEqual(login.status_code, 200)
            running = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(running.status_code, 200, running.text)
            running_key = running.json()["key"]
            launched = dict(self.launcher.runs[-1])
            login = await client.post("/api/login", auth=("instructor1", "lab12345"))
            self.assertEqual(login.status_code, 200)
            copies_response = await client.get(f"/api/authoring/examples/{example_id}/removal")
            self.assertEqual(copies_response.status_code, 200, copies_response.text)
            copies = copies_response.json()["config_copies"]
            self.assertEqual(copies, ["instructor2"])
            marker = Path(registry.config_copies.copy_dir("instructor2", example_id),
                          "learner-work.txt")
            marker.write_text("preserved")
            assigned = await client.put("/api/admin/assignments/learner1",
                                        json={"example_ids": [example_id]})
            self.assertEqual(assigned.status_code, 200, assigned.text)
            assignments = (await client.get("/api/admin/assignments")).json()
            learner_assignment = assignments["assignments"]["learner1"]
            drafts = []
            for revise in (False, True, True):
                created = await client.post("/api/authoring/drafts", json={
                    "example_id": example_id, "revise": revise})
                self.assertEqual(created.status_code, 201, created.text)
                draft = created.json()
                self.assertNotEqual(draft["id"], source["id"])
                self.assertIsNone(draft["published_revision"])
                self.assertFalse(draft["preview_valid"])
                self.assertEqual(draft["catalog"]["id"], example_id if revise else None)
                for field in ("python", "files", "lesson_files", "guide", "layout"):
                    self.assertEqual(draft[field], original["draft"][field])
                raw = await client.get(f"/api/authoring/drafts/{draft['id']}/file",
                                       params={"path": "presentation/image.png"})
                self.assertEqual(raw.content, b"\x00\x01\x02\xff")
                drafts.append(draft)
            copied, revised, stale = drafts
            await self._preview(client, copied)
            published_copy = await client.post(f"/api/authoring/drafts/{copied['id']}/publish",
                                               json={"revision": copied["revision"]})
            self.assertEqual(published_copy.status_code, 200, published_copy.text)
            self.assertNotEqual(published_copy.json()["example"]["id"], example_id)
            revised = await self._save(client, revised, python=self.SCRIPT + "# revision\n")
            await self._preview(client, revised)
            updated = await client.post(f"/api/authoring/drafts/{revised['id']}/publish", json={
                "revision": revised["revision"], "mode": "update_existing"})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["example"]["id"], example_id)
            self.assertNotEqual(updated.json()["example"]["version"], original["example"]["version"])
            stale = await self._save(client, stale, python=self.SCRIPT + "# stale revision\n")
            await self._preview(client, stale)
            refused = await client.post(f"/api/authoring/drafts/{stale['id']}/publish", json={
                "revision": stale["revision"], "mode": "update_existing"})
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertIn("changed", refused.json()["detail"])
            self.assertEqual((await client.get(
                f"/api/authoring/drafts/{source['id']}")).json(), original["draft"])
            self.assertEqual((await client.get(
                f"/api/authoring/examples/{example_id}/removal")).json()["config_copies"], copies)
            self.assertEqual(marker.read_text(), "preserved")
            self.assertEqual((await client.get("/api/admin/assignments")).json()
                             ["assignments"]["learner1"], learner_assignment)
            self.assertEqual(updated.json()["incompatible_groups"], ["instructor2"])
            self.assertEqual(Path(launched["lab_file"]).read_text(), self.SCRIPT)
            self.assertEqual(Path(launched["cwd"], "helpers/value.py").read_text(), "VALUE = 7\n")
            self.assertEqual(Path(launched["cwd"], "presentation/image.png").read_bytes(),
                             b"\x00\x01\x02\xff")
            live = (await client.get(f"/api/labs/{running_key}")).json()
            self.assertEqual(live["key"], running_key)
            await client.post(f"/api/labs/{running_key}/stop")

        self._as("instructor", exercise)

    def test_learners_cannot_copy_or_revise_authored_python(self) -> None:
        examples = []

        async def publish(client):
            examples.append((await self._published(client))["example"]["id"])

        self._as("instructor", publish)

        async def exercise(client):
            for revise in (False, True):
                denied = await client.post("/api/authoring/drafts", json={
                    "example_id": examples[0], "revise": revise})
                self.assertEqual(denied.status_code, 403, denied.text)

        self._as("learner", exercise)


@pytest.mark.unit
class StartFromExampleAPITests(PythonDraftFixture):
    """Copy starts a new Lab Example; Revise keeps an authored one's Catalog entry."""

    async def _start_from(self, client, example_id, revise=False):
        return await client.post("/api/authoring/drafts",
                                 json={"example_id": example_id, "revise": revise})

    def test_a_shipped_example_cannot_be_revised(self) -> None:
        async def exercise(client):
            refused = await self._start_from(client, "static-lab", revise=True)
            self.assertEqual(refused.status_code, 422)
            self.assertIn("Copy", refused.json()["detail"])

        self._as("instructor", exercise)

    def test_authored_examples_copy_as_new_and_revise_in_their_source_mode(self) -> None:
        async def exercise(client):
            canvas = await self._router_draft(client)
            canvas = await self._save(client, canvas, lesson_files={
                "helpers/value.py": {"encoding": "utf-8", "content": "VALUE = 9\n"}})
            await self._preview(client, canvas)
            canvas_example = (await client.post(f"/api/authoring/drafts/{canvas['id']}/publish",
                                                json={"revision": canvas["revision"]})).json()
            code = await self._python_draft(client)
            await self._preview(client, code)
            python_example = (await client.post(f"/api/authoring/drafts/{code['id']}/publish",
                                                json={"revision": code["revision"]})).json()

            canvas_id = canvas_example["example"]["id"]
            copied = (await self._start_from(client, canvas_id)).json()
            revised = (await self._start_from(client, canvas_id, revise=True)).json()
            self.assertEqual((copied.get("kind"), copied["catalog"]["id"]), (None, None))
            self.assertEqual((revised.get("kind"), revised["catalog"]["id"]), (None, canvas_id))
            self.assertEqual(copied["topology"], revised["topology"])
            self.assertEqual(copied["lesson_files"], canvas["lesson_files"])
            self.assertEqual(revised["lesson_files"], canvas["lesson_files"])

            python_id = python_example["example"]["id"]
            revised = (await self._start_from(client, python_id, revise=True)).json()
            self.assertEqual((revised["kind"], revised["catalog"]["id"]), ("python", python_id))
            self.assertEqual(revised["python"], self.SCRIPT)
            copied_response = await self._start_from(client, python_id)
            self.assertEqual(copied_response.status_code, 201, copied_response.text)
            copied = copied_response.json()
            self.assertEqual(copied["kind"], "python")
            self.assertIsNone(copied["catalog"]["id"])
            self.assertEqual(copied["python"], self.SCRIPT)
            self.assertNotEqual(copied["id"], code["id"])

        self._as("instructor", exercise)


@pytest.mark.unit
class LabScriptFailureTests(unittest.TestCase):
    """A Lab script that fails to build names its own failing line."""

    def test_build_failure_reports_the_scripts_line(self) -> None:
        from mniplab.labprocess import run_lab_process
        with tempfile.TemporaryDirectory() as root:
            script = os.path.join(root, "lab.py")
            with open(script, "w", encoding="utf-8") as target:
                target.write("def build_network():\n    helper = 1\n    return missing_name\n")
            manager, lab = socket.socketpair()
            with manager:
                code = run_lab_process("preview-x", script, lab.detach())
                report = json.loads(manager.makefile().readline())
        self.assertEqual(code, 1)
        self.assertIn("name 'missing_name' is not defined", report["error"])
        self.assertIn("line 3 of lab.py", report["error"])
        self.assertIn("return missing_name", report["error"])


@pytest.mark.unit
class PythonDraftExportAPITests(PythonDraftFixture):
    """A Python draft's own script is what its export shows and downloads."""

    def test_python_draft_exports_its_own_script(self) -> None:
        async def exercise(client):
            draft = await self._python_draft(client)
            shown = await client.get(f"/api/authoring/drafts/{draft['id']}/python")
            self.assertEqual(shown.status_code, 200, shown.text)
            self.assertEqual(shown.json()["source"], self.SCRIPT)
            self.assertEqual(shown.json()["filename"], "lesson.py")
            package = await client.get(f"/api/authoring/drafts/{draft['id']}/python.zip")
            with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
                self.assertEqual(archive.read("lesson/lesson.py").decode(), self.SCRIPT)

        self._as("instructor", exercise)


@pytest.mark.unit
class CanvasPythonContinuationAPITests(AuthoringAPIFixture):
    """Continue in Python preserves a Canvas draft as an independent recipe."""

    def test_blank_canvas_with_unfinished_code_continues_with_diagnostics(self) -> None:
        async def exercise(client):
            source = (await client.post("/api/authoring/drafts", json={})).json()
            source["code_section"] = "def build_network(:\n    pass\n"
            saved = await client.put(
                f"/api/authoring/drafts/{source['id']}",
                json={"revision": source["revision"], "document": source},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            source = saved.json()
            continued = await client.post(
                f"/api/authoring/drafts/{source['id']}/continue-in-python",
                json={"revision": source["revision"]},
            )
            self.assertEqual(continued.status_code, 201, continued.text)
            self.assertEqual(continued.json()["kind"], "python")
            self.assertIsNotNone(continued.json()["python_error"])
            self.assertFalse(continued.json()["preview_valid"])

        self._as("instructor", exercise)

    def test_saved_canvas_continues_without_preview_and_retains_complete_files(self) -> None:
        async def exercise(client):
            source = await self._router_draft(client)
            source["catalog"]["name"] = "Canvas continuation"
            source["code_section"] = "# preserved Code section\nnet.addHost('extra')\n"
            source["guide"] = "# Retained Guide\n"
            source["layout"]["nodes"] = {
                "r": {"x": 100, "y": 300}, "h": {"x": 800, "y": 300}
            }
            source["persistent_config"] = True
            source["lesson_files"] = {
                "presentation/diagram.png": {
                    "encoding": "base64", "content": "AAEC/w==", "role": "presentation"
                }
            }
            saved = await client.put(
                f"/api/authoring/drafts/{source['id']}",
                json={"revision": source["revision"], "document": source},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            original = saved.json()
            original_bytes = (await client.get(
                f"/api/authoring/drafts/{source['id']}/file",
                params={"path": "presentation/diagram.png"},
            )).content

            continued = await client.post(
                f"/api/authoring/drafts/{source['id']}/continue-in-python",
                json={"revision": original["revision"]},
            )
            self.assertEqual(continued.status_code, 201, continued.text)
            python = continued.json()
            self.assertEqual(python["kind"], "python")
            self.assertNotEqual(python["id"], original["id"])
            self.assertIsNone(python["catalog"]["id"])
            self.assertIsNone(python["published_revision"])
            self.assertEqual(python["guide"], original["guide"])
            self.assertEqual(python["layout"]["nodes"], {
                "r1": {"x": 100, "y": 300}, "h1": {"x": 800, "y": 300}
            })
            self.assertIn("preserved Code section", python["python"])
            self.assertEqual(python["lesson_files"], original["lesson_files"])
            seed = python["files"]["frr-config/r1/frr.conf"]
            self.assertIn("router ospf", seed)
            self.assertIn("network 10.0.0.0/30 area 0", seed)
            self.assertFalse(python["preview_valid"])
            source_after = (await client.get(
                f"/api/authoring/drafts/{source['id']}"
            )).json()
            self.assertEqual(source_after, original)
            self.assertEqual((await client.get(
                f"/api/authoring/drafts/{source['id']}/file",
                params={"path": "presentation/diagram.png"},
            )).content, original_bytes)

            python["python"] += "\n# independent edit\n"
            edited = await client.put(
                f"/api/authoring/drafts/{python['id']}",
                json={"revision": python["revision"], "document": python},
            )
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual((await client.get(
                f"/api/authoring/drafts/{source['id']}"
            )).json(), original)

        self._as("instructor", exercise)

    def test_continuation_rejects_stale_revision_without_creating_a_draft(self) -> None:
        async def exercise(client):
            source = await self._buildable_draft(client, "Stale source")
            result = await client.post(
                f"/api/authoring/drafts/{source['id']}/continue-in-python",
                json={"revision": source["revision"] - 1},
            )
            self.assertEqual(result.status_code, 409, result.text)
            drafts = (await client.get("/api/authoring/drafts")).json()["drafts"]
            self.assertEqual([draft["id"] for draft in drafts], [source["id"]])

        self._as("instructor", exercise)

    def test_generation_failure_does_not_create_a_partial_python_draft(self) -> None:
        async def exercise(client):
            source = await self._router_draft(client)
            before = (await client.get("/api/authoring/drafts")).json()["drafts"]
            with patch(
                "mniplab.frrouter.config.FRRConfigBuilder.write_frr_conf",
                side_effect=OSError("Seed volume is unavailable"),
            ):
                failed = await client.post(
                    f"/api/authoring/drafts/{source['id']}/continue-in-python",
                    json={"revision": source["revision"]},
                )
            self.assertEqual(failed.status_code, 422, failed.text)
            self.assertIn("Seed volume is unavailable", failed.json()["detail"])
            self.assertEqual(
                (await client.get("/api/authoring/drafts")).json()["drafts"], before
            )
            self.assertEqual(
                (await client.get(f"/api/authoring/drafts/{source['id']}")).json(),
                source,
            )

        self._as("instructor", exercise)

    def test_learners_cannot_continue_a_canvas_draft_or_read_python_result(self) -> None:
        async def create(client):
            return await self._buildable_draft(client, "Private continuation")

        result: list[dict[str, Any]] = []
        self._as("instructor", lambda client: _collect_result(client, create, result))
        source = result[0]

        async def exercise(client):
            denied = await client.post(
                f"/api/authoring/drafts/{source['id']}/continue-in-python",
                json={"revision": source["revision"]},
            )
            self.assertEqual(denied.status_code, 403, denied.text)
            denied_read = await client.get(f"/api/authoring/drafts/{source['id']}")
            self.assertEqual(denied_read.status_code, 403, denied_read.text)

        self._as("learner", exercise)


async def _collect_result(client, operation, output):
    output.append(await operation(client))


@pytest.mark.unit
class ShippedRecipeCopyTests(AuthoringAPIFixture):
    """Explicit Copy preserves shipped recipes; legacy adaptation stays Canvas."""

    def test_every_shipped_recipe_preserves_source_and_declared_assets(self) -> None:
        root = Path(__file__).resolve().parents[2]
        rows = json.loads((root / 'examples/labs.json').read_text())
        self.assertEqual(len(rows), 14)
        seed_count = 0

        async def exercise(client):
            nonlocal seed_count
            for row in rows:
                with self.subTest(example=row['id']):
                    response = await client.post('/api/authoring/drafts', json={
                        'example_id': row['id'], 'source': 'recipe'})
                    self.assertEqual(response.status_code, 201, response.text)
                    draft = response.json()
                    self.assertEqual(draft['kind'], 'python')
                    self.assertEqual(draft['python'].encode(),
                                     (root / 'examples' / row['file']).read_bytes())
                    expected = {str(p.relative_to(root)): p.read_bytes()
                                for p in (root / 'frr-config').glob('*/**/frr.conf')
                                if p.parts[-3] == {
                                    'bgp-ispixp-lab': 'bgp-ispixp',
                                    'bgp-isp-lab': 'bgp-isp',
                                }.get(row['id'], row['id'])}
                    self.assertEqual({path: text.encode() for path, text in draft['files'].items()}, expected)
                    self.assertEqual(set(row['assets']), set(expected))
                    seed_count += len(expected)
                    guide = root / 'examples/guides' / f"{row['id']}.md"
                    self.assertEqual(draft['guide'].encode(), guide.read_bytes() if guide.exists() else b'')
                    layout = root / 'examples/layouts' / f"{row['id']}.json"
                    self.assertEqual(draft['layout'], json.loads(layout.read_text()) if layout.exists()
                                     else {'version': 1, 'nodes': {}})
                    self.assertIsNone(draft['catalog']['id'])
            self.assertEqual(seed_count, 56)

        self._as('instructor', exercise)

    def test_missing_declared_asset_fails_without_creating_a_draft(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            examples = Path(directory) / 'examples'
            examples.mkdir()
            rows = json.loads((root / 'examples/labs.json').read_text())
            row = next(row for row in rows if row['id'] == 'bgp-dc-lab')
            row['assets'] = ['frr-config/missing/r1/frr.conf']
            (examples / 'labs.json').write_text(json.dumps([row]))
            (examples / row['file']).write_bytes((root / 'examples' / row['file']).read_bytes())

            async def exercise(client):
                declarations = (
                    ({'assets': ['frr-config/missing/r1/frr.conf']}, 'frr-config/missing/r1/frr.conf'),
                    ({'guide': 'examples/guides/missing.md'}, 'examples/guides/missing.md'),
                    ({'layout': 'examples/layouts/missing.json'}, 'examples/layouts/missing.json'),
                    ({'file': 'missing.py'}, 'examples/missing.py'),
                )
                for declaration, missing in declarations:
                    with self.subTest(missing=missing):
                        metadata = {key: value for key, value in row.items()
                                    if key not in ('guide', 'layout', 'assets')}
                        metadata.update(assets=[])
                        metadata.update(declaration)
                        (examples / 'labs.json').write_text(json.dumps([metadata]))
                        response = await client.post('/api/authoring/drafts', json={
                            'example_id': row['id'], 'source': 'recipe'})
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertIn(missing, response.json()['detail'])
                        self.assertEqual((await client.get('/api/authoring/drafts')).json()['drafts'], [])

            with patch('mniplab.webserver.api.resolve_examples_dir', return_value=str(examples)):
                self._as('instructor', exercise)

    def test_legacy_and_explicit_canvas_start_keep_canvas_source(self) -> None:
        async def exercise(client):
            for selector in ({}, {'source': 'canvas'}):
                response = await client.post('/api/authoring/drafts', json={
                    'example_id': 'bgp-dc-lab', **selector})
                self.assertEqual(response.status_code, 201, response.text)
                self.assertNotIn('kind', response.json())
                self.assertIn('topology', response.json())
        self._as('instructor', exercise)

    def test_recipe_selection_is_instructor_only_and_requires_a_shipped_copy(self) -> None:
        async def unauthorized(client):
            response = await client.post('/api/authoring/drafts', json={
                'example_id': 'static-lab', 'source': 'recipe'})
            self.assertEqual(response.status_code, 403)
        self._as('learner', unauthorized)

        async def exercise(client):
            for body in ({'source': 'recipe'},
                         {'example_id': 'static-lab', 'source': 'unknown'},
                         {'example_id': 'static-lab', 'source': 'recipe', 'revise': True}):
                response = await client.post('/api/authoring/drafts', json=body)
                self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual((await client.get('/api/authoring/drafts')).json()['drafts'], [])
        self._as('instructor', exercise)

    def test_declared_helper_and_opaque_asset_keep_bytes_and_exclude_neighbors(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            examples = project / 'examples'
            examples.mkdir()
            row = next(item for item in json.loads((root / 'examples/labs.json').read_text())
                       if item['id'] == 'static-lab')
            row['assets'] = ['helpers/lesson.py', 'data/input.bin']
            row.pop('layout', None)
            row.pop('guide', None)
            (examples / 'labs.json').write_text(json.dumps([row]))
            source = b'# exact line endings\r\nprint("lesson")\r\n'
            (examples / row['file']).write_bytes(source)
            for path, data in [('helpers/lesson.py', b'ANSWER = 42\r\n'),
                               ('data/input.bin', b'\x00\xff\x80\r\n'),
                               ('data/ignored-runtime-output.bin', b'not authored')]:
                target = project / path
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(data)

            async def exercise(client):
                response = await client.post('/api/authoring/drafts', json={
                    'example_id': row['id'], 'source': 'recipe'})
                self.assertEqual(response.status_code, 201, response.text)
                draft = response.json()
                self.assertEqual(draft['python'].encode(), source)
                self.assertEqual(set(draft['lesson_files']), set(row['assets']))
                self.assertEqual(draft['lesson_files']['helpers/lesson.py']['encoding'], 'utf-8')
                self.assertEqual(draft['lesson_files']['data/input.bin']['encoding'], 'base64')
                for path in row['assets']:
                    downloaded = await client.get(
                        f"/api/authoring/drafts/{draft['id']}/file", params={'path': path})
                    self.assertEqual(downloaded.status_code, 200, downloaded.text)
                    self.assertEqual(downloaded.content, (project / path).read_bytes())

            with patch('mniplab.webserver.api.resolve_examples_dir', return_value=str(examples)):
                self._as('instructor', exercise)
