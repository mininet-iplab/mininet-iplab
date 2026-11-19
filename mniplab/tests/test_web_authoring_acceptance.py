"""Classroom acceptance of Lab Authoring at the authenticated HTTP boundary.

Each scenario crosses features an earlier authoring test proved one at a
time: an Instructor revising a published lesson while a Group keeps its
work, Preview admission against the Lab cap, the Learner boundary over
every authoring route, and the shipped Lab Examples' reconciliation.
Storage is real and temporary; only the Lab process is fake.
"""

import os
from collections import defaultdict
from typing import Any, Dict
from unittest.mock import patch

import httpx2
import pytest
from starlette.testclient import TestClient

from mniplab.tests.authoring_fixtures import (
    PREPARED, AuthoringAPIFixture, RecordingLauncher, draft_from_shipped,
)
from mniplab.webserver import create_app, registry
from mniplab.webserver.authoring import AuthoringRepository
from mniplab.webserver.authoring_python import generate_python
from mniplab.webserver.config import settings

LESSON = "hostname r1\n!\nrouter ospf\n network 10.0.0.0/30 area 0\nexit\n!\nend\n"
GROUP_WORK = "! the Group's saved work\n"


@pytest.mark.unit
class ClassroomRevisionTests(AuthoringAPIFixture):
    """An Instructor revises a published lesson; the Group's work stays its own."""

    def _revise_while_a_group_keeps_its_work(self, mode: str) -> None:
        launcher = RecordingLauncher()
        # The Group's Config Copy is made by a Multi-Lab start; a server
        # later run in Single-Lab Mode still holds it on disk.
        self._launch_with("multi", launcher)
        self.running_configs["r1"] = LESSON

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            draft["guide"] = "Lesson one"
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = (await client.put(url, json={"revision": draft["revision"],
                                                 "document": draft})).json()
            preview = await client.post(f"{url}/preview", json={"revision": saved["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            first = await client.post(f"{url}/publish", json={"revision": saved["revision"]})
            self.assertEqual(first.status_code, 200, first.text)
            example_id = first.json()["example"]["id"]
            await registry.stop_lab(preview.json()["key"])

            copy_dir = registry.config_copies.copy_dir("learner1", example_id)
            assigned = await client.put("/api/admin/assignments/learner1",
                                        json={"example_ids": [example_id]})
            self.assertEqual(assigned.status_code, 200, assigned.text)
            started = await client.post("/api/labs", json={"example_id": example_id,
                                                           "group": "learner1"})
            self.assertEqual(started.status_code, 200, started.text)
            group_lab = started.json()["key"]
            self.assertEqual(launcher.calls[-1]["config_copy"], copy_dir)
            # The Group's saved work, as `write memory` in its Lab leaves it.
            os.makedirs(os.path.join(copy_dir, "r1"), exist_ok=True)
            with open(os.path.join(copy_dir, "r1", "frr.conf"), "w", encoding="utf-8") as target:
                target.write(GROUP_WORK)
            if mode == "single":
                await registry.stop_lab(group_lab)
                self._launch_with("single", launcher)

            # Revise: a draft of the published recipe, keeping its Catalog entry.
            revise = await client.post("/api/authoring/drafts",
                                       json={"example_id": example_id, "revise": True})
            self.assertEqual(revise.status_code, 201, revise.text)
            revision = revise.json()
            url = f"/api/authoring/drafts/{revision['id']}"
            preview = await client.post(f"{url}/preview", json={"revision": revision["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            seeds = launcher.calls[-1]["seeds"]["r1"]["frr.conf"]
            self.assertIn("network 10.0.0.0/30 area 0", seeds)
            self.assertNotIn(GROUP_WORK, seeds)
            previews = os.path.join(self.storage.name, "previews", "config") + os.sep
            self.assertTrue(launcher.calls[-1]["config_copy"].startswith(previews))

            # An unsaved vtysh change in the Preview, kept selectively.
            self.running_configs["r1"] = LESSON.replace(
                "exit\n!\n", "exit\n!\nip route 0.0.0.0/0 10.0.0.2\n!\n")
            review = (await client.get(f"{url}/keep")).json()["routers"]
            self.assertEqual(len(review[0]["sections"]), 1)
            kept = await client.post(f"{url}/keep", json={
                "revision": revision["revision"],
                "selections": [{"node_id": "r", "digest": review[0]["digest"], "sections": [0]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertFalse(kept.json()["preview_valid"])
            revised = kept.json()
            revised["guide"] = "Lesson two"
            saved = (await client.put(url, json={"revision": revised["revision"],
                                                 "document": revised})).json()
            self.running_configs["r1"] = LESSON
            rebuilt = await client.post(f"{url}/preview", json={
                "revision": saved["revision"], "replace_key": preview.json()["key"]})
            self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
            self.assertIn("ip route 0.0.0.0/0 10.0.0.2", launcher.calls[-1]["seeds"]["r1"]["frr.conf"])

            updated = await client.post(f"{url}/publish", json={
                "revision": saved["revision"], "mode": "update_existing"})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["example"]["id"], example_id)
            self.assertEqual(updated.json()["incompatible_groups"], [])
            await registry.stop_lab(rebuilt.json()["key"])

            published = AuthoringRepository(self.storage.name).published_file(example_id)
            with open(os.path.join(os.path.dirname(published), "frr-config", "r1", "frr.conf"),
                      encoding="utf-8") as source:
                self.assertIn("ip route 0.0.0.0/0 10.0.0.2", source.read())
            self.assertEqual((await client.get("/api/load_guide",
                                               params={"example": example_id})).text, "Lesson two")
            with open(os.path.join(copy_dir, "r1", "frr.conf"), encoding="utf-8") as source:
                self.assertEqual(source.read(), GROUP_WORK)

            if mode == "multi":
                running = registry.lab_service.lab_of("learner1")
                assert running is not None
                self.assertEqual(running.key, group_lab)
                self.assertTrue(running.child.alive)
                pinned = await client.get("/api/load_guide",
                                          params={"example": example_id, "key": group_lab})
                self.assertEqual(pinned.text, "Lesson one")
                await registry.stop_lab(group_lab)
                resumed = await client.post("/api/labs", json={"example_id": example_id,
                                                               "group": "learner1"})
                self.assertEqual(resumed.status_code, 200, resumed.text)
                self.assertEqual(launcher.calls[-1]["config_copy"], copy_dir)
        self._as("instructor", exercise)

    def test_multi_lab_revision_leaves_a_running_groups_lab_and_config_copy_alone(self) -> None:
        self._revise_while_a_group_keeps_its_work("multi")

    def test_single_lab_revision_previews_keeps_and_updates_past_a_saved_group_copy(self) -> None:
        self._revise_while_a_group_keeps_its_work("single")


@pytest.mark.unit
class PreviewAdmissionTests(AuthoringAPIFixture):
    """A Preview Lab is a Lab: it holds a slot under the Lab cap until it stops."""

    def test_a_running_preview_holds_a_cap_slot_until_it_stops(self) -> None:
        launcher = RecordingLauncher()
        self._launch_with("multi", launcher)
        cap = patch.object(settings, "lab_cap", 1)
        cap.start()
        self.addCleanup(cap.stop)
        registry.groups.assign("learner1", ["static-lab"])
        preview_key = ""

        async def instructor(client: httpx2.AsyncClient) -> None:
            nonlocal preview_key
            draft = await self._buildable_draft(client)
            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            preview_key = preview.json()["key"]

        async def learner(client: httpx2.AsyncClient) -> None:
            refused = await client.post("/api/labs", json={"example_id": "static-lab"})
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertIn("Lab cap reached", refused.json()["detail"])
            self.assertEqual([lab.key for lab in registry.lab_service.labs()], [preview_key])
            await registry.stop_lab(preview_key)
            admitted = await client.post("/api/labs", json={"example_id": "static-lab"})
            self.assertEqual(admitted.status_code, 200, admitted.text)

        self._as("instructor", instructor)
        self._as("learner", learner)


def stored_files(root: str) -> Dict[str, bytes]:
    """Every file under ``root`` with its bytes, keyed by relative path."""
    files = {}
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as source:
                files[os.path.relpath(path, root)] = source.read()
    return files


@pytest.mark.unit
class LearnerBoundaryTests(AuthoringAPIFixture):
    """The server, not the page, keeps Learners out of authoring and Previews."""

    def _sweep(self, mode: str) -> None:
        self._launch_with(mode, RecordingLauncher())
        registry.groups.assign("learner1", ["static-lab"])
        routes = [(method.upper(), path) for path, operations in
                  create_app().openapi()["paths"].items() for method in operations]
        authoring = [(method, path) for method, path in routes
                     if path.startswith("/api/authoring/")]
        preview_lab = [(method, path) for method, path in routes
                       if path.startswith("/api/labs/{key}")]
        # Guard against a vacuous sweep: 13 authoring routes and 42 Lab
        # routes exist today, and a route added later joins the sweep itself.
        self.assertGreaterEqual(len(authoring), 13)
        self.assertGreaterEqual(len(preview_lab), 42)
        started: Dict[str, Any] = {}

        async def instructor(client: httpx2.AsyncClient) -> None:
            draft = await self._buildable_draft(client)
            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            started.update(draft=draft, key=preview.json()["key"])

        async def learner(client: httpx2.AsyncClient) -> None:
            draft = started["draft"]
            body = {"revision": draft["revision"], "document": draft, "selections": [],
                    "example_id": "static-lab"}
            values = defaultdict(lambda: "h1", draft_id=draft["id"], key=started["key"])
            before = stored_files(self.storage.name)
            for method, path in authoring:
                response = await client.request(method, path.format_map(values), json=body)
                with self.subTest(method=method, path=path):
                    self.assertEqual(response.status_code, 403, response.text)
            for method, path in preview_lab:
                response = await client.request(method, path.format_map(values), json=body)
                with self.subTest(method=method, path=path):
                    self.assertEqual(response.status_code, 404, response.text)
            for asset in ("/api/load_layout", "/api/load_guide"):
                response = await client.get(asset, params={"example": f"preview-{draft['id']}"})
                with self.subTest(path=asset):
                    self.assertEqual(response.status_code, 404, response.text)
            with self.subTest(path="/authoring"):
                self.assertEqual((await client.get("/authoring")).status_code, 403)
            with self.subTest(path="/api/status"):
                self.assertFalse((await client.get("/api/status")).json()["running"])
            with self.subTest(path="/api/labs"):
                self.assertEqual((await client.get("/api/labs")).json()["labs"], [])
            self.assertEqual(stored_files(self.storage.name), before)
            running = registry.lab_service.get(started["key"])
            assert running is not None
            self.assertTrue(running.child.alive)

        self._as("instructor", instructor)
        self._as("learner", learner)
        sockets = TestClient(create_app())
        self.assertEqual(sockets.post("/api/login", auth=("learner1", "lab12345")).status_code, 200)
        hidden = f"No Lab with key '{started['key']}' is running"
        # The Lab Prompt refuses every Learner before it looks at the key.
        for path, message in (("topology", hidden), ("terminal?node=h1", hidden),
                              ("capture?node=h1&interface=h1-eth0", hidden),
                              ("prompt", "The Lab Prompt is the Instructor's; use a Node Terminal")):
            with self.subTest(websocket=path), sockets.websocket_connect(
                    f"/ws/labs/{started['key']}/{path}") as socket:
                self.assertEqual(socket.receive_json(), {"type": "error", "message": message})

    def test_a_learner_is_refused_authoring_and_the_preview_in_multi_lab_mode(self) -> None:
        self._sweep("multi")

    def test_a_learner_is_refused_authoring_and_the_shared_preview_in_single_lab_mode(self) -> None:
        # Every login sees the shared Lab; only the Preview rule hides this one.
        self._sweep("single")


@pytest.mark.unit
class ShippedExampleAdaptTests(AuthoringAPIFixture):
    """Every prepared shipped lesson becomes an editable copy of its definition."""

    def test_every_prepared_example_adapts_saves_and_shows_its_definitions_python(self) -> None:
        proven = AuthoringRepository(os.path.join(self.storage.name, "proven"))

        families = {"dhcp-lab": "dual", "static-lab": "dual", "dns-lab": "dual", "dnssec-lab": "dual",
                    "nat-lab": "dual"}

        async def exercise(client: httpx2.AsyncClient) -> None:
            catalog = (await client.get("/api/examples")).json()["examples"]
            entries = {entry["id"]: entry for entry in catalog}
            for example_id in PREPARED:
                with self.subTest(example_id):
                    adapted = await client.post("/api/authoring/drafts",
                                                json={"example_id": example_id})
                    self.assertEqual(adapted.status_code, 201, adapted.text)
                    draft = adapted.json()
                    # A Copy is a new, unpublished Canvas draft with the Lab's settings.
                    self.assertNotIn("kind", draft)
                    self.assertIsNone(draft["catalog"]["id"])
                    self.assertEqual(draft["catalog"]["name"], entries[example_id]["name"])
                    self.assertFalse(draft["preview_valid"])
                    self.assertEqual(draft["schema_version"], 3)
                    self.assertEqual(draft["address_family"], families.get(example_id, "ipv4"))
                    self.assertEqual(draft["persistent_config"],
                                     entries[example_id].get("persistent_config", False))
                    url = f"/api/authoring/drafts/{draft['id']}"
                    saved = await client.put(url, json={"revision": draft["revision"],
                                                        "document": draft})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    reopened = (await client.get(url)).json()
                    python = await client.get(f"{url}/python")
                    self.assertEqual(python.status_code, 200, python.text)
                    shipped = draft_from_shipped(proven, example_id)
                    self.assertEqual(python.json()["source"], generate_python(shipped))
                    for field in ("guide", "layout"):
                        self.assertEqual(reopened[field], shipped[field], field)
            self.assertEqual((await client.get("/api/examples")).json()["examples"], catalog)
        self._as("instructor", exercise)
