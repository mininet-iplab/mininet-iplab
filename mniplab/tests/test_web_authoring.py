"""Authenticated HTTP behavior for Instructor Lab Example authoring."""

import copy
import json
import os
from typing import Any, Callable
from unittest.mock import patch

import httpx2
import pytest

from mniplab.labprocess import LabStartError
from mniplab.tests.authoring_fixtures import (
    AuthoringAPIFixture, RecordingLauncher, dhcp_pool, host_node, router_node,
)
from mniplab.webserver import registry
from mniplab.webserver.config import settings
from mniplab.tests.test_web_multi_lab import FakeLauncher
from mniplab.webserver.authoring import AuthoringRepository


@pytest.mark.unit
class AuthoringDraftAPITests(AuthoringAPIFixture):
    """Drafts persist through the real HTTP API and belong to Instructors."""

    def test_instructor_creates_reads_and_updates_a_shared_draft(self) -> None:
        async def exercise(client):
            created = await client.post("/api/authoring/drafts", json={})
            self.assertEqual(created.status_code, 201)
            draft = created.json()
            self.assertEqual(draft["revision"], 0)
            self.assertEqual(draft["topology"], {"nodes": [], "links": []})
            self.assertEqual((await client.get("/api/authoring/drafts")).json()["drafts"], [draft])

            second_login = await client.post(
                "/api/login", auth=("instructor2", "lab12345")
            )
            self.assertEqual(second_login.status_code, 200)
            shared = (await client.get("/api/authoring/drafts")).json()["drafts"]
            self.assertEqual(shared[0]["id"], draft["id"])
            self.assertEqual(
                (await client.get(f"/api/authoring/drafts/{draft['id']}")).status_code,
                200,
            )
            await client.post("/api/login", auth=("instructor1", "lab12345"))

            changed = {**draft, "catalog": {"name": "Addressing lesson", "description": "Dual stack", "id": None, "protocols": []}}
            saved = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": draft["revision"], "document": changed},
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["revision"], 1)
            reopened = await client.get(f"/api/authoring/drafts/{draft['id']}")
            self.assertEqual(reopened.status_code, 200)
            self.assertEqual(reopened.json()["catalog"]["name"], "Addressing lesson")
        self._as("instructor", exercise)

    def test_stale_draft_save_is_refused_without_losing_the_newer_revision(self) -> None:
        async def exercise(client):
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            updated = {**draft, "catalog": {**draft["catalog"], "name": "Newer"}}
            await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": 0, "document": updated},
            )
            stale = {**draft, "catalog": {**draft["catalog"], "name": "Stale"}}
            refused = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": 0, "document": stale},
            )
            self.assertEqual(refused.status_code, 409)
            reopened = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
            self.assertEqual(reopened["catalog"]["name"], "Newer")
            self.assertEqual(reopened["revision"], 1)
        self._as("instructor", exercise)

    def test_instructor_copies_a_saved_draft_into_a_separate_identity(self) -> None:
        async def exercise(client):
            source = await self._buildable_draft(client, "Source Lesson")
            copied = await client.post(
                "/api/authoring/drafts", json={"source_id": source["id"]}
            )
            self.assertEqual(copied.status_code, 201, copied.text)
            document = copied.json()
            self.assertNotEqual(document["id"], source["id"])
            self.assertEqual(document["revision"], 0)
            self.assertIsNone(document["catalog"]["id"])
            self.assertEqual(document["catalog"]["name"], "Copy of Source Lesson")
            self.assertEqual(document["topology"], source["topology"])
            self.assertEqual(document["layout"], source["layout"])
            self.assertFalse(document["preview_valid"])
        self._as("instructor", exercise)

    def test_instructor_deletes_a_draft_at_its_current_revision(self) -> None:
        async def exercise(client):
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            path = f"/api/authoring/drafts/{draft['id']}"
            deleted = await client.delete(path, params={"revision": 0})
            self.assertEqual(deleted.status_code, 204, deleted.text)
            self.assertEqual((await client.get(path)).status_code, 404)
            listed = (await client.get("/api/authoring/drafts")).json()["drafts"]
            self.assertNotIn(draft["id"], [item["id"] for item in listed])
            self.assertEqual((await client.delete(path, params={"revision": 0})).status_code, 404)
        self._as("instructor", exercise)

    def test_stale_draft_delete_is_refused_and_keeps_the_newer_revision(self) -> None:
        async def exercise(client):
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            path = f"/api/authoring/drafts/{draft['id']}"
            updated = {**draft, "catalog": {**draft["catalog"], "name": "Newer"}}
            await client.put(path, json={"revision": 0, "document": updated})
            refused = await client.delete(path, params={"revision": 0})
            self.assertEqual(refused.status_code, 409)
            self.assertEqual((await client.get(path)).json()["catalog"]["name"], "Newer")
        self._as("instructor", exercise)

    def test_invalid_definition_is_refused_without_changing_the_saved_revision(self) -> None:
        async def exercise(client):
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            invalid = {
                **draft,
                "topology": {
                    "nodes": [
                        {"id": "a", "name": "host1", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
                        {"id": "b", "name": "host1", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
                    ],
                    "links": [],
                },
            }
            response = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": 0, "document": invalid},
            )
            self.assertEqual(response.status_code, 422)
            current = await client.get(f"/api/authoring/drafts/{draft['id']}")
            self.assertEqual(current.json()["revision"], 0)

            buildable = await self._buildable_draft(client, "Malformed endpoint")
            invalid_endpoint = copy.deepcopy(buildable)
            invalid_endpoint["topology"]["links"][0]["ends"][0]["node_id"] = ["a"]
            invalid_endpoint_response = await client.put(
                f"/api/authoring/drafts/{buildable['id']}",
                json={"revision": buildable["revision"], "document": invalid_endpoint},
            )
            self.assertEqual(invalid_endpoint_response.status_code, 422)
        self._as("instructor", exercise)

    def test_preview_is_private_and_current_network_definition_must_be_started_before_publish(self) -> None:
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)

        async def exercise(client):
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            draft["catalog"]["name"] = "Two Host Lesson"
            draft["catalog"]["area"] = "Static"
            draft["guide"] = "Try a connection."
            draft["topology"] = {
                "nodes": [
                    {"id": "a", "name": "h1", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
                    {"id": "b", "name": "h2", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
                ],
                "links": [{"id": "link-a", "ends": [
                    {"node_id": "a", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "192.0.2.1/24", "ipv6": "2001:db8::1/64"},
                    {"node_id": "b", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "192.0.2.2/24", "ipv6": "2001:db8::2/64"},
                ]}],
            }
            draft["layout"]["nodes"] = {"a": {"x": 100, "y": 300}, "b": {"x": 800, "y": 300}}
            saved = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": 0, "document": draft},
            )
            self.assertEqual(saved.status_code, 200)
            current = saved.json()

            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": current["revision"]},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertTrue(preview.json()["preview_valid"])
            key = preview.json()["key"]
            running_preview = registry.lab_service.get(key)
            self.assertTrue(running_preview.preview)
            self.assertTrue(
                running_preview.preview_config_copy.startswith(
                    os.path.join(self.storage.name, "previews", "config")
                )
            )
            self.assertTrue(
                (await client.get(
                    f"/api/authoring/drafts/{draft['id']}/preview-status"
                )).json()["current"]
            )
            preview_layout = await client.get(
                "/api/load_layout", params={"example": f"preview-{draft['id']}"}
            )
            self.assertEqual(
                preview_layout.json()["layout"]["nodes"],
                {"h1": {"x": 100, "y": 300}, "h2": {"x": 800, "y": 300}},
            )
            instructor_status = await client.get("/api/status")
            self.assertTrue(instructor_status.json()["running"])

            learner_login = await client.post(
                "/api/login", auth=("learner1", "lab12345")
            )
            self.assertEqual(learner_login.status_code, 200)
            self.assertFalse((await client.get("/api/status")).json()["running"])
            self.assertEqual((await client.get("/api/labs")).json()["labs"], [])
            self.assertEqual(
                (await client.get(f"/api/labs/{key}/topology")).status_code, 404
            )
            self.assertEqual(
                (await client.post(
                    f"/api/authoring/drafts/{draft['id']}/publish",
                    json={"revision": current["revision"]},
                )).status_code,
                403,
            )

            await client.post("/api/login", auth=("instructor1", "lab12345"))
            changed_guide = {**current, "guide": "Updated walkthrough."}
            changed_guide["layout"] = {
                "version": 1,
                "nodes": {"a": {"x": 140, "y": 300}, "b": {"x": 760, "y": 300}},
            }
            saved_guide = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": current["revision"], "document": changed_guide},
            )
            self.assertTrue(saved_guide.json()["preview_valid"])
            published = await client.post(
                f"/api/authoring/drafts/{draft['id']}/publish",
                json={"revision": saved_guide.json()["revision"]},
            )
            self.assertEqual(published.status_code, 200, published.text)
            published_draft = published.json()["draft"]
            example_id = published.json()["example"]["id"]
            catalog = await client.get("/api/examples")
            authored = next(row for row in catalog.json()["examples"] if row["id"] == example_id)
            self.assertEqual(authored["name"], "Two Host Lesson")
            guide = await client.get("/api/load_guide", params={"example": example_id})
            self.assertEqual(guide.text, "Updated walkthrough.")
            published_layout = await client.get(
                "/api/load_layout", params={"example": example_id}
            )
            self.assertEqual(
                published_layout.json()["layout"]["nodes"],
                {"h1": {"x": 140, "y": 300}, "h2": {"x": 760, "y": 300}},
            )

            changed_topology = copy.deepcopy(published_draft)
            changed_topology["topology"]["links"][0]["ends"][0]["ipv4"] = "192.0.2.10/24"
            network_edit = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": published_draft["revision"], "document": changed_topology},
            )
            self.assertFalse(network_edit.json()["preview_valid"])
            self.assertFalse(
                (await client.get(
                    f"/api/authoring/drafts/{draft['id']}/preview-status"
                )).json()["current"]
            )
            stale_publish = await client.post(
                f"/api/authoring/drafts/{draft['id']}/publish",
                json={"revision": network_edit.json()["revision"]},
            )
            self.assertEqual(stale_publish.status_code, 409)
        self._as("instructor", exercise)

    def test_single_lab_preview_is_blocked_while_the_shared_lab_is_running(self) -> None:
        mode = patch.object(settings, "lab_mode", "single")
        mode.start()
        self.addCleanup(mode.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)

        async def exercise(client):
            draft = await self._buildable_draft(client)
            current = await client.post("/api/labs", json={"example_id": "static-lab"})
            self.assertEqual(current.status_code, 200, current.text)

            blocked = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )

            self.assertEqual(blocked.status_code, 409)
            self.assertIn("Stop the shared Lab", blocked.json()["detail"])
            self.assertEqual(len(launcher.children), 1)
            self.assertFalse(launcher.children[0].stopped)
            status = await client.get("/api/status")
            self.assertEqual(status.json()["example"], "static-lab")
            self.assertFalse(status.json().get("preview", False))
        self._as("instructor", exercise)

    def test_multi_lab_preview_requires_confirmation_to_replace_only_its_own_lab(self) -> None:
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)

        async def exercise(client):
            draft = await self._buildable_draft(client)
            current = await client.post("/api/labs", json={"example_id": "static-lab"})
            self.assertEqual(current.status_code, 200, current.text)

            confirmation = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(confirmation.status_code, 409)
            detail = confirmation.json()["detail"]
            self.assertEqual(detail["code"], "preview_replacement_required")
            self.assertIn("Changes not yet kept will be lost", detail["message"])
            self.assertFalse(launcher.children[0].stopped)

            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"], "replace_key": current.json()["key"]},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertTrue(launcher.children[0].stopped)
            self.assertEqual(len(launcher.children), 2)
            self.assertTrue((await client.get("/api/status")).json()["preview"])
        self._as("instructor", exercise)

    def test_multi_lab_preview_leaves_other_groups_running_within_the_cap(self) -> None:
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        cap = patch.object(settings, "lab_cap", 2)
        cap.start()
        self.addCleanup(cap.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry.groups.assign("learner1", ["static-lab"])

        async def exercise(client):
            other_group = await client.post(
                "/api/labs",
                json={"example_id": "static-lab", "group": "learner1"},
            )
            self.assertEqual(other_group.status_code, 200, other_group.text)
            draft = await self._buildable_draft(client)

            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )

            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertFalse(launcher.children[0].stopped)
            self.assertEqual(
                registry.lab_service.lab_of("learner1").key,
                other_group.json()["key"],
            )
            self.assertEqual(len(registry.lab_service.labs()), 2)
        self._as("instructor", exercise)

    def test_preview_refuses_when_another_groups_lab_uses_the_full_cap(self) -> None:
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        cap = patch.object(settings, "lab_cap", 1)
        cap.start()
        self.addCleanup(cap.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry.groups.assign("learner1", ["static-lab"])

        async def exercise(client):
            other_group = await client.post(
                "/api/labs",
                json={"example_id": "static-lab", "group": "learner1"},
            )
            self.assertEqual(other_group.status_code, 200, other_group.text)
            draft = await self._buildable_draft(client)
            refused = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )

            self.assertEqual(refused.status_code, 409)
            self.assertIn("Lab cap reached", refused.json()["detail"])
            self.assertFalse(launcher.children[0].stopped)
            self.assertEqual(len(registry.lab_service.labs()), 1)
            self.assertEqual(
                registry.lab_service.lab_of("learner1").key,
                other_group.json()["key"],
            )
        self._as("instructor", exercise)

    def test_failed_preview_start_removes_its_fresh_configuration_copy(self) -> None:
        class FailingLauncher:
            """Create the passed isolated path, then fail like an unready Lab."""

            config_copy = None

            async def __call__(self, example_id, lab_file, config_copy=None, timeout=None):
                del example_id, lab_file, timeout
                self.config_copy = config_copy
                os.makedirs(config_copy, exist_ok=True)
                raise LabStartError("simulated Preview startup failure")

        mode = patch.object(settings, "lab_mode", "single")
        mode.start()
        self.addCleanup(mode.stop)
        launcher = FailingLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)

        async def exercise(client):
            draft = await self._buildable_draft(client)
            refused = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )

            self.assertEqual(refused.status_code, 500)
            self.assertIn("simulated Preview startup failure", refused.json()["detail"])
            self.assertIsNotNone(launcher.config_copy)
            self.assertFalse(os.path.exists(launcher.config_copy))
            self.assertEqual(registry.lab_service.labs(), [])
        self._as("instructor", exercise)

    def test_intentionally_misaddressed_but_buildable_topology_can_be_published(self) -> None:
        mode = patch.object(settings, "lab_mode", "single")
        mode.start()
        self.addCleanup(mode.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)

        async def exercise(client):
            draft = await self._buildable_draft(client, "Intentional Address Fault")
            faulty = copy.deepcopy(draft)
            faulty["topology"]["links"][0]["ends"][1]["ipv4"] = "192.0.2.1/24"
            saved = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": draft["revision"], "document": faulty},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": saved.json()["revision"]},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(
                f"/api/authoring/drafts/{draft['id']}/publish",
                json={"revision": saved.json()["revision"]},
            )
            self.assertEqual(published.status_code, 200, published.text)
            self.assertEqual(
                published.json()["example"]["name"], "Intentional Address Fault"
            )
        self._as("instructor", exercise)

    def test_published_authored_example_uses_the_existing_assignment_rules(self) -> None:
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)

        async def exercise(client):
            draft = await self._buildable_draft(client, "Assigned Authored Lab")
            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(
                f"/api/authoring/drafts/{draft['id']}/publish",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(published.status_code, 200, published.text)
            example_id = published.json()["example"]["id"]

            assignment = await client.put(
                "/api/admin/assignments/learner1",
                json={"example_ids": [example_id]},
            )
            self.assertEqual(assignment.status_code, 200, assignment.text)
            await client.post("/api/login", auth=("learner1", "lab12345"))
            learner_catalog = await client.get("/api/examples")
            self.assertEqual(
                [example["id"] for example in learner_catalog.json()["examples"]],
                [example_id],
            )
            started = await client.post(
                "/api/labs", json={"example_id": example_id}
            )
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(started.json()["example"], example_id)
            self.assertEqual(
                (await client.get(f"/api/labs/{preview.json()['key']}/topology")).status_code,
                404,
            )
        self._as("instructor", exercise)

    def test_update_existing_keeps_running_version_and_save_as_new_keeps_original(self) -> None:
        self._launch_with("single", FakeLauncher())

        async def exercise(client):
            draft = await self._buildable_draft(client, "First lesson")
            path = f"/api/authoring/drafts/{draft['id']}"
            preview = await client.post(
                f"{path}/preview", json={"revision": draft["revision"]}
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            first = await client.post(f"{path}/publish", json={"revision": draft["revision"]})
            self.assertEqual(first.status_code, 200, first.text)
            example_id = first.json()["example"]["id"]
            from mniplab.webserver.authoring import AuthoringRepository
            repository = AuthoringRepository(self.storage.name)
            old_file = repository.published_file(example_id)
            old_guide = repository.published_file(example_id, "guide.md")
            self.assertTrue(os.path.isfile(old_file))
            await registry.stop_lab(preview.json()["key"])
            running = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(running.status_code, 200, running.text)

            changed = first.json()["draft"]
            changed["catalog"]["name"] = "Revised lesson"
            changed["guide"] = "Revision two"
            changed["layout"]["nodes"]["a"]["x"] = 200
            saved = await client.put(path, json={"revision": changed["revision"], "document": changed})
            self.assertEqual(saved.status_code, 200, saved.text)
            revised = saved.json()
            update = await client.post(f"{path}/publish", json={
                "revision": revised["revision"], "mode": "update_existing"
            })
            self.assertEqual(update.status_code, 200, update.text)
            self.assertEqual(update.json()["example"]["id"], example_id)
            self.assertEqual((await client.get("/api/load_guide", params={"example": example_id})).text,
                             "Revision two")
            self.assertNotEqual(repository.published_file(example_id), old_file)
            self.assertTrue(os.path.isfile(old_file))
            self.assertTrue(os.path.isfile(old_guide))
            self.assertTrue((await client.get("/api/status")).json()["running"])
            pinned_guide = await client.get("/api/load_guide", params={
                "example": example_id, "key": running.json()["key"]
            })
            self.assertEqual(pinned_guide.text, "")
            self.assertFalse((await client.get("/api/status")).json()["has_guide"])
            examples = (await client.get("/api/examples")).json()["examples"]
            self.assertTrue(next(item for item in examples if item["id"] == example_id)["has_guide"])
            pinned_layout = await client.get("/api/load_layout", params={
                "example": example_id, "key": running.json()["key"]
            })
            self.assertEqual(pinned_layout.json()["layout"]["nodes"]["h1"]["x"], 100)

            latest = update.json()["draft"]
            latest["catalog"]["name"] = "Separate lesson"
            saved = await client.put(path, json={"revision": latest["revision"], "document": latest})
            new = await client.post(f"{path}/publish", json={"revision": saved.json()["revision"]})
            self.assertEqual(new.status_code, 200, new.text)
            self.assertNotEqual(new.json()["example"]["id"], example_id)
            self.assertEqual((await client.get("/api/load_guide", params={"example": example_id})).text,
                             "Revision two")

        self._as("instructor", exercise)

    def test_update_refuses_retargeted_or_stale_draft(self) -> None:
        self._launch_with("single", FakeLauncher())

        async def exercise(client):
            source = await self._buildable_draft(client)
            path = f"/api/authoring/drafts/{source['id']}"
            await client.post(f"{path}/preview", json={"revision": source["revision"]})
            published = await client.post(f"{path}/publish", json={"revision": source["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            example_id = published.json()["example"]["id"]
            second = await client.post("/api/authoring/drafts",
                                       json={"example_id": example_id, "revise": True})
            self.assertEqual(second.status_code, 201, second.text)
            running = registry.lab_service.lab_of("instructor1")
            assert running is not None
            await registry.stop_lab(running.key)
            second_preview = await client.post(
                f"/api/authoring/drafts/{second.json()['id']}/preview",
                json={"revision": second.json()["revision"]},
            )
            self.assertEqual(second_preview.status_code, 200, second_preview.text)

            alien = await self._buildable_draft(client)
            alien["catalog"]["id"] = example_id
            refused = await client.put(f"/api/authoring/drafts/{alien['id']}", json={
                "revision": alien["revision"], "document": alien
            })
            self.assertEqual(refused.status_code, 422)

            current = published.json()["draft"]
            current["guide"] = "One"
            saved = await client.put(path, json={"revision": current["revision"], "document": current})
            updated = await client.post(f"{path}/publish", json={
                "revision": saved.json()["revision"], "mode": "update_existing"
            })
            self.assertEqual(updated.status_code, 200, updated.text)
            stale = second.json()
            stale["guide"] = "Older branch"
            saved_stale = await client.put(f"/api/authoring/drafts/{stale['id']}", json={
                "revision": stale["revision"], "document": stale
            })
            refused = await client.post(f"/api/authoring/drafts/{stale['id']}/publish", json={
                "revision": saved_stale.json()["revision"], "mode": "update_existing"
            })
            self.assertEqual(refused.status_code, 409)
            self.assertEqual((await client.get("/api/load_guide", params={"example": example_id})).text,
                             "One")

        self._as("instructor", exercise)

    def test_compatible_update_resumes_copy_and_renamed_router_requires_clean_start(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client):
            draft = await self._router_draft(client)
            path = f"/api/authoring/drafts/{draft['id']}"
            preview = await client.post(f"{path}/preview", json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(f"{path}/publish", json={"revision": draft["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            example_id = published.json()["example"]["id"]
            first_seed = os.path.join(os.path.dirname(AuthoringRepository(
                self.storage.name).published_file(example_id)), "frr-config", "r1", "frr.conf")
            with open(first_seed, encoding="utf-8") as source:
                first_text = source.read()
            await registry.stop_lab(preview.json()["key"])
            first = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(first.status_code, 200, first.text)
            await registry.stop_lab(first.json()["key"])
            copy_dir = registry.config_copies.copy_dir("instructor1", example_id)
            self.assertTrue(os.path.isfile(os.path.join(copy_dir, ".structure.json")))
            os.makedirs(os.path.join(copy_dir, "r1"), exist_ok=True)
            with open(os.path.join(copy_dir, "r1", "frr.conf"), "w", encoding="utf-8") as target:
                target.write("Group work")

            changed = published.json()["draft"]
            changed["topology"]["nodes"][0]["frr_config"] = "router ospf\n ospf router-id 1.1.1.1\n"
            saved = await client.put(path, json={"revision": changed["revision"], "document": changed})
            preview = await client.post(f"{path}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            compatible = await client.post(f"{path}/publish", json={
                "revision": saved.json()["revision"], "mode": "update_existing"
            })
            self.assertEqual(compatible.status_code, 200, compatible.text)
            self.assertEqual(compatible.json()["incompatible_groups"], [])
            # The update publishes a new version; the first version's Seed is untouched.
            with open(first_seed, encoding="utf-8") as source:
                self.assertEqual(source.read(), first_text)
            await registry.stop_lab(preview.json()["key"])
            resumed = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(resumed.status_code, 200, resumed.text)
            await registry.stop_lab(resumed.json()["key"])
            with open(os.path.join(copy_dir, "r1", "frr.conf"), encoding="utf-8") as source:
                self.assertEqual(source.read(), "Group work")

            renamed = compatible.json()["draft"]
            renamed["topology"]["nodes"][0]["name"] = "r9"
            saved = await client.put(path, json={"revision": renamed["revision"], "document": renamed})
            preview = await client.post(f"{path}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            incompatible = await client.post(f"{path}/publish", json={
                "revision": saved.json()["revision"], "mode": "update_existing"
            })
            self.assertEqual(incompatible.status_code, 200, incompatible.text)
            self.assertEqual(incompatible.json()["incompatible_groups"], ["instructor1"])
            await registry.stop_lab(preview.json()["key"])
            dashboard = await client.get("/api/admin/dashboard")
            instructor = next(group for group in dashboard.json()["groups"]
                              if group["name"] == "instructor1")
            self.assertEqual(instructor["incompatible_config_copies"], [example_id])
            row = next(item for item in (await client.get("/api/examples")).json()["examples"]
                       if item["id"] == example_id)
            self.assertTrue(row["incompatible"])
            self.assertTrue(row["resumes"])
            blocked = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(blocked.status_code, 409)
            self.assertTrue(os.path.isfile(os.path.join(copy_dir, "r1", "frr.conf")))
            clean = await client.post("/api/labs", json={"example_id": example_id, "clean": True})
            self.assertEqual(clean.status_code, 200, clean.text)
            self.assertFalse(os.path.exists(os.path.join(copy_dir, "r1", "frr.conf")))
            self.assertFalse(next(item for item in (await client.get("/api/examples")).json()["examples"]
                                  if item["id"] == example_id)["incompatible"])
            dashboard = await client.get("/api/admin/dashboard")
            instructor = next(group for group in dashboard.json()["groups"]
                              if group["name"] == "instructor1")
            self.assertEqual(instructor["incompatible_config_copies"], [])

            await registry.stop_lab(clean.json()["key"])
            reused = incompatible.json()["draft"]
            reused["topology"]["nodes"][0]["id"] = "r-recreated"
            reused["topology"]["links"][0]["ends"][0]["node_id"] = "r-recreated"
            if "r" in reused["layout"]["nodes"]:
                reused["layout"]["nodes"]["r-recreated"] = reused["layout"]["nodes"].pop("r")
            saved = await client.put(path, json={"revision": reused["revision"], "document": reused})
            self.assertEqual(saved.status_code, 200, saved.text)
            preview = await client.post(f"{path}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            recreated = await client.post(f"{path}/publish", json={
                "revision": saved.json()["revision"], "mode": "update_existing"
            })
            self.assertEqual(recreated.status_code, 200, recreated.text)
            self.assertEqual(recreated.json()["incompatible_groups"], ["instructor1"])
            await registry.stop_lab(preview.json()["key"])
            reset = await client.post("/api/admin/config/reset", json={
                "example_id": example_id, "group": "instructor1"
            })
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertEqual(reset.json()["reset"], ["instructor1"])
            self.assertFalse(os.path.exists(copy_dir))
            reseeded = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(reseeded.status_code, 200, reseeded.text)
            self.assertFalse(next(item for item in (await client.get("/api/examples")).json()["examples"]
                                  if item["id"] == example_id)["incompatible"])

        self._as("instructor", exercise)

    def test_failed_copy_structure_write_stops_launched_lab(self) -> None:
        launcher = FakeLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            draft = await self._router_draft(client)
            path = f"/api/authoring/drafts/{draft['id']}"
            preview = await client.post(f"{path}/preview", json={"revision": draft["revision"]})
            published = await client.post(f"{path}/publish", json={"revision": draft["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            await registry.stop_lab(preview.json()["key"])
            example_id = published.json()["example"]["id"]
            copy_dir = registry.config_copies.copy_dir("instructor1", example_id)
            with patch("mniplab.webserver.services.record_copy_structure", side_effect=OSError("disk full")):
                refused = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(refused.status_code, 500)
            self.assertTrue(launcher.children[-1].stopped)
            self.assertEqual(registry.lab_service.labs(), [])
            self.assertFalse(os.path.exists(copy_dir))

        self._as("instructor", exercise)

    def test_daemon_interface_and_mode_changes_each_refuse_reuse(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client):
            draft = await self._router_draft(client)
            path = f"/api/authoring/drafts/{draft['id']}"
            preview = await client.post(f"{path}/preview", json={"revision": draft["revision"]})
            first = await client.post(f"{path}/publish", json={"revision": draft["revision"]})
            self.assertEqual(first.status_code, 200, first.text)
            example_id = first.json()["example"]["id"]
            await registry.stop_lab(preview.json()["key"])
            started = await client.post("/api/labs", json={"example_id": example_id})
            self.assertEqual(started.status_code, 200, started.text)
            await registry.stop_lab(started.json()["key"])
            current = first.json()["draft"]

            def choose_daemon(document):
                document["topology"]["nodes"][0]["protocols"] = ["BGP"]

            def change_interface(document):
                document["topology"]["links"][0]["ends"][0]["interface_id"] = "if-000000000009"

            def move_port(document):
                document["topology"]["links"][0]["ends"][0]["port"] = 3

            def change_mode(document):
                document["persistent_config"] = False

            for change in (choose_daemon, change_interface, move_port, change_mode):
                change(current)
                saved = await client.put(path, json={
                    "revision": current["revision"], "document": current
                })
                self.assertEqual(saved.status_code, 200, saved.text)
                preview = await client.post(f"{path}/preview", json={"revision": saved.json()["revision"]})
                self.assertEqual(preview.status_code, 200, preview.text)
                updated = await client.post(f"{path}/publish", json={
                    "revision": saved.json()["revision"], "mode": "update_existing"
                })
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["incompatible_groups"], ["instructor1"])
                await registry.stop_lab(preview.json()["key"])
                blocked = await client.post("/api/labs", json={"example_id": example_id})
                self.assertEqual(blocked.status_code, 409)
                clean = await client.post("/api/labs", json={"example_id": example_id, "clean": True})
                self.assertEqual(clean.status_code, 200, clean.text)
                await registry.stop_lab(clean.json()["key"])
                current = updated.json()["draft"]

        self._as("instructor", exercise)

    def test_failed_publication_keeps_the_visible_catalog_and_saved_draft(self) -> None:
        mode = patch.object(settings, "lab_mode", "single")
        mode.start()
        self.addCleanup(mode.stop)
        launcher = FakeLauncher()
        patcher = patch.object(registry.lab_service, "launcher", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)

        async def exercise(client):
            draft = await self._buildable_draft(client, "Stable Catalog Entry")
            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(
                f"/api/authoring/drafts/{draft['id']}/publish",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(published.status_code, 200, published.text)
            baseline = (await client.get("/api/examples")).json()["examples"]
            baseline_ids = {item["id"] for item in baseline}
            baseline_entry = next(
                item for item in baseline if item["name"] == "Stable Catalog Entry"
            )
            published_draft = published.json()["draft"]
            published_draft["catalog"]["name"] = "Attempted replacement"
            saved = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": published_draft["revision"], "document": published_draft},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            catalog_after_draft_edit = (await client.get("/api/examples")).json()["examples"]
            self.assertIn(baseline_entry, catalog_after_draft_edit)

            real_replace = os.replace

            def fail_catalog_replace(source, destination):
                if os.fspath(destination).endswith(os.path.join("published", "catalog.json")):
                    raise OSError("simulated Catalog commit failure")
                real_replace(source, destination)

            with patch("mniplab.webserver.authoring.os.replace", side_effect=fail_catalog_replace):
                failed = await client.post(
                    f"/api/authoring/drafts/{draft['id']}/publish",
                    json={"revision": saved.json()["revision"]},
                )

            self.assertEqual(failed.status_code, 500)
            after = (await client.get("/api/examples")).json()["examples"]
            self.assertEqual({item["id"] for item in after}, baseline_ids)
            self.assertFalse(any(item["name"] == "Attempted replacement" for item in after))
            reopened = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
            self.assertEqual(reopened["revision"], saved.json()["revision"])
            self.assertEqual(reopened["catalog"]["name"], "Attempted replacement")
            with patch("mniplab.webserver.authoring.os.replace", side_effect=fail_catalog_replace):
                failed_update = await client.post(
                    f"/api/authoring/drafts/{draft['id']}/publish",
                    json={"revision": saved.json()["revision"], "mode": "update_existing"},
                )
            self.assertEqual(failed_update.status_code, 500)
            self.assertIn(baseline_entry, (await client.get("/api/examples")).json()["examples"])
            self.assertEqual((await client.get(f"/api/authoring/drafts/{draft['id']}")).json()["revision"],
                             saved.json()["revision"])
        self._as("instructor", exercise)


@pytest.mark.unit
class AuthoringRouterAPITests(AuthoringAPIFixture):
    """Routers, Seeds, gateways and Startup commands through the HTTP API."""

    def test_router_definition_round_trips_and_invalid_fields_are_refused(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            reopened = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
            self.assertEqual(reopened["topology"], draft["topology"])
            self.assertEqual(reopened["schema_version"], 3)

            def broken(change):
                document = copy.deepcopy(reopened)
                change(document["topology"]["nodes"])
                return document

            refusals = {
                "unknown protocol": lambda n: n[0].update(protocols=["OSPFX"]),
                "repeated protocol": lambda n: n[0].update(protocols=["BGP", "BGP"]),
                "config mode": lambda n: n[0].update(config_mode="saved"),
                "stage": lambda n: n[0]["startup"][0].update(stage="later"),
                "timeout": lambda n: n[0]["startup"][0].update(timeout=61),
                "empty command": lambda n: n[0]["startup"][0].update(command="  "),
                "may fail": lambda n: n[0]["startup"][0].update(may_fail="yes"),
                "gateway family": lambda n: n[1].update(ipv4_gateway="2001:db8::1"),
                "host fields": lambda n: n[1].pop("startup"),
            }
            for label, change in refusals.items():
                response = await client.put(
                    f"/api/authoring/drafts/{draft['id']}",
                    json={"revision": reopened["revision"], "document": broken(change)},
                )
                self.assertEqual(response.status_code, 422, label)
            current = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
            self.assertEqual(current["revision"], reopened["revision"])
        self._as("instructor", exercise)

    def test_router_protocol_choices_are_for_instructors_only(self) -> None:
        async def instructor(client):
            response = await client.get("/api/authoring/protocols")
            self.assertEqual(response.status_code, 200)
            self.assertIn("OSPF", response.json()["protocols"])
            self.assertNotIn("OSPF6D", response.json()["protocols"])

        async def learner(client):
            self.assertEqual((await client.get("/api/authoring/protocols")).status_code, 403)
        self._as("instructor", instructor)
        self._as("learner", learner)

    def test_switch_is_a_plain_bridge_with_ports_from_one(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            draft["topology"]["nodes"].append({"id": "s", "name": "s1", "type": "switch"})
            draft["topology"]["links"].append({"id": "link-s", "ends": [
                {"node_id": "s", "interface_id": "if-000000000003", "port": 1,
                 "ipv4": "", "ipv6": ""},
                {"node_id": "r", "interface_id": "if-000000000004", "port": 1,
                 "ipv4": "10.1.0.1/24", "ipv6": ""},
            ]})
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            for label, change in {
                "switch startup": lambda d: d["topology"]["nodes"][2].update(startup=[]),
                "switch port 0": lambda d: d["topology"]["links"][1]["ends"][0].update(port=0),
                # A Switch is layer 2: an address there would land on the host machine.
                "switch IPv4": lambda d: d["topology"]["links"][1]["ends"][0].update(
                    ipv4="10.9.9.1/24"),
                "switch IPv6": lambda d: d["topology"]["links"][1]["ends"][0].update(
                    ipv6="2001:db8:9::1/64"),
            }.items():
                document = copy.deepcopy(current)
                change(document)
                response = await client.put(
                    url, json={"revision": current["revision"], "document": document}
                )
                self.assertEqual(response.status_code, 422, label)
                if label.startswith("switch IPv"):
                    self.assertIn("Switch s1", response.json()["detail"], label)
        self._as("instructor", exercise)

    def test_vlan_and_subinterface_lesson_round_trips_through_publication(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client, frr_config="", persistent_config=True)
            router = draft["topology"]["nodes"][0]
            router["subinterfaces"] = [{
                "id": "subif-10", "parent_interface_id": "if-000000000004",
                "vlan": 10, "ipv4": "192.168.10.1/24", "ipv6": "",
            }]
            draft["topology"]["nodes"].append({"id": "s", "name": "s1", "type": "switch"})
            draft["topology"]["links"].append({"id": "link-s", "ends": [
                {"node_id": "s", "interface_id": "if-000000000003", "port": 1,
                 "ipv4": "", "ipv6": "", "vlan": {"mode": "trunk", "trunks": [10], "native": None}},
                {"node_id": "r", "interface_id": "if-000000000004", "port": 1,
                 "ipv4": "", "ipv6": ""},
            ]})
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            with_subinterfaces = AuthoringRepository.persistent_structure(current)
            older = copy.deepcopy(current)
            del older["topology"]["nodes"][0]["subinterfaces"]
            without_subinterfaces = AuthoringRepository.persistent_structure(older)
            self.assertNotEqual(with_subinterfaces, without_subinterfaces)
            self.assertNotIn("subinterfaces", without_subinterfaces["routers"][0])
            for label, corrupt in {
                "invalid VLAN": lambda doc: doc["topology"]["links"][-1]["ends"][0]["vlan"].update(trunks=[4095]),
                "orphan parent": lambda doc: doc["topology"]["nodes"][0]["subinterfaces"][0].update(parent_interface_id="if-ffffffffffff"),
                "duplicate subinterface": lambda doc: doc["topology"]["nodes"][0]["subinterfaces"].append(
                    {**doc["topology"]["nodes"][0]["subinterfaces"][0], "id": "subif-duplicate"}),
            }.items():
                invalid = copy.deepcopy(current)
                corrupt(invalid)
                rejected = await client.put(url, json={"revision": current["revision"], "document": invalid})
                self.assertEqual(rejected.status_code, 422, label)
            preview = await client.post(f"{url}/preview", json={"revision": current["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(f"{url}/publish", json={"revision": current["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            catalog_id = published.json()["example"]["id"]
            examples = (await client.get("/api/examples")).json()["examples"]
            self.assertIn(catalog_id, {item["id"] for item in examples})

            current = published.json()["draft"]
            changed = copy.deepcopy(current)
            changed["topology"]["links"][-1]["ends"][0]["vlan"]["trunks"] = [10, 20]
            saved_change = await client.put(url, json={"revision": current["revision"],
                                                      "document": changed})
            self.assertEqual(saved_change.status_code, 200, saved_change.text)
            changed = saved_change.json()
            status = (await client.get(f"{url}/preview-status")).json()
            self.assertFalse(status["current"])
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": changed["revision"]})).status_code, 409)

        self._as("instructor", exercise)

    def test_container_host_image_round_trips_through_publication(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._buildable_draft(client, "Container lesson")
            draft["topology"]["nodes"][1] = {
                "id": "b", "name": "d1", "type": "container", "image": "alpine:3.20",
                "ipv4_gateway": "", "ipv6_gateway": "", "startup": [],
            }
            url = f"/api/authoring/drafts/{draft['id']}"
            invalid = copy.deepcopy(draft)
            invalid["topology"]["nodes"][1]["image"] = ""
            refused = await client.put(url, json={"revision": draft["revision"],
                                                 "document": invalid})
            self.assertEqual(refused.status_code, 422, refused.text)
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.get(url)).json()["topology"]["nodes"][1]["image"],
                             "alpine:3.20")
            preview = await client.post(f"{url}/preview", json={"revision": current["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(f"{url}/publish", json={"revision": current["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            self.assertFalse(published.json()["example"]["persistent_config"])

        self._as("instructor", exercise)

    def test_hidden_nat_and_transit_downstream_round_trip(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client, frr_config="", persistent_config=True)
            draft["catalog"]["name"] = "NAT lesson"
            draft["catalog"]["area"] = "Static"
            draft["topology"]["nodes"].extend([
                {"id": "n", "name": "nat1", "type": "nat", "subnet": "100.64.0.0/24",
                 "ip": "100.64.0.1/24"},
                {"id": "t", "name": "nat2", "type": "transit", "subnet": "192.0.2.0/30",
                 "ip": "192.0.2.1/30", "downstream_ids": ["r"]},
            ])
            draft["topology"]["links"].append({"id": "nat-r", "ends": [
                {"node_id": "n", "interface_id": "if-000000000003", "port": 0,
                 "ipv4": "", "ipv6": ""},
                {"node_id": "r", "interface_id": "if-000000000004", "port": 1,
                 "ipv4": "100.64.0.2/24", "ipv6": ""},
            ]})
            url = f"/api/authoring/drafts/{draft['id']}"
            invalid = copy.deepcopy(draft)
            invalid["topology"]["nodes"][-1]["downstream_ids"] = ["h"]
            self.assertEqual((await client.put(url, json={"revision": draft["revision"],
                                                   "document": invalid})).status_code, 422)
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            self.assertEqual((await client.post(f"{url}/preview",
                                                json={"revision": current["revision"]})).status_code, 200)
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": current["revision"]})).status_code, 200)

        self._as("instructor", exercise)

    def test_speaker_neighbors_and_routes_round_trip_through_publication(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client, frr_config="router bgp 65000\n no bgp ebgp-requires-policy\n neighbor 10.0.1.2 remote-as 65001", persistent_config=True)
            draft["catalog"]["name"] = "Speaker lesson"
            draft["catalog"]["area"] = "Inter-domain"
            draft["topology"]["nodes"].append({
                "id": "sp", "name": "sp1", "type": "speaker", "local_as": 65001,
                "routes": [{"prefix": "203.0.113.0/24", "next_hop": "10.0.1.2"}],
                "neighbors": [{"peer": "10.0.1.1", "local_address": "10.0.1.2",
                               "local_as": 65001, "peer_as": 65000, "hold_time": None,
                               "routes": [{"prefix": "198.51.100.0/24", "next_hop": "10.0.1.2"}]}],
                "startup": [],
            })
            draft["topology"]["links"].append({"id": "speaker-r", "ends": [
                {"node_id": "sp", "interface_id": "if-000000000003", "port": 0,
                 "ipv4": "10.0.1.2/24", "ipv6": ""},
                {"node_id": "r", "interface_id": "if-000000000004", "port": 1,
                 "ipv4": "10.0.1.1/24", "ipv6": ""},
            ]})
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            self.assertEqual((await client.post(f"{url}/preview",
                                                json={"revision": current["revision"]})).status_code, 200)
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": current["revision"]})).status_code, 200)
        self._as("instructor", exercise)

    def test_initial_link_condition_round_trips_and_invalidates_preview(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._buildable_draft(client, "Link condition lesson")
            link = draft["topology"]["links"][0]
            link["condition"] = {"status": "down", "delay": "10ms", "jitter": "2ms",
                                 "loss": "5%", "bandwidth": 100, "scheduler": "htb"}
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.post(f"{url}/preview",
                                                json={"revision": current["revision"]})).status_code, 200)
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": current["revision"]})).status_code, 200)
            current = (await client.get(url)).json()
            changed = copy.deepcopy(current)
            changed["topology"]["links"][0]["condition"]["delay"] = "20ms"
            saved = await client.put(url, json={"revision": current["revision"], "document": changed})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertFalse((await client.get(f"{url}/preview-status")).json()["current"])
        self._as("instructor", exercise)

    def test_one_unreadable_draft_does_not_hide_the_others(self) -> None:
        async def exercise(client):
            good = await self._buildable_draft(client, "Readable")
            broken = await self._buildable_draft(client, "Broken")
            path = os.path.join(self.storage.name, "drafts", broken["id"], "definition.json")
            with open(path, "w", encoding="utf-8") as target:
                target.write("{ not json")
            listed = await client.get("/api/authoring/drafts")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual([item["id"] for item in listed.json()["drafts"]], [good["id"]])
            self.assertEqual(
                (await client.get(f"/api/authoring/drafts/{broken['id']}")).status_code, 500
            )
        self._as("instructor", exercise)

    def test_a_draft_of_another_version_is_refused_and_hides_no_other(self) -> None:
        async def exercise(client):
            good = await self._buildable_draft(client, "Current")
            other = await self._buildable_draft(client, "Other version")
            path = os.path.join(self.storage.name, "drafts", other["id"], "definition.json")
            with open(path, encoding="utf-8") as source:
                stored = json.load(source)
            stored["schema_version"] = 2
            with open(path, "w", encoding="utf-8") as target:
                json.dump(stored, target)

            listed = await client.get("/api/authoring/drafts")
            self.assertEqual([item["id"] for item in listed.json()["drafts"]], [good["id"]])
            self.assertEqual(
                (await client.get(f"/api/authoring/drafts/{other['id']}")).status_code, 500
            )
        self._as("instructor", exercise)

    def test_network_edits_to_routers_or_startup_commands_invalidate_preview(self) -> None:
        launcher = RecordingLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            draft = await self._router_draft(client)
            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            edits = [
                lambda n: n[0].update(frr_config="router ospf\n"),
                lambda n: n[0].update(protocols=["OSPF", "BGP"]),
                lambda n: n[0].update(fast_timers=False),
                lambda n: n[0]["startup"][0].update(timeout=6),
                lambda n: n[1].update(ipv4_gateway=""),
            ]
            current = (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()
            self.assertTrue(current["preview_valid"])
            for edit in edits:
                document = copy.deepcopy(draft)
                edit(document["topology"]["nodes"])
                saved = await client.put(
                    f"/api/authoring/drafts/{draft['id']}",
                    json={"revision": current["revision"], "document": {
                        **document, "revision": current["revision"]}},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertFalse(saved.json()["preview_valid"])
                current = saved.json()
        self._as("instructor", exercise)

    def _assert_isolated_preview(self, mode):
        launcher = RecordingLauncher()
        self._launch_with(mode, launcher)

        async def exercise(client):
            draft = await self._router_draft(client, frr_config="! published text\n")
            first = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(first.status_code, 200, first.text)
            published = await client.post(
                f"/api/authoring/drafts/{draft['id']}/publish",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(published.status_code, 200, published.text)
            catalog_id = published.json()["example"]["id"]
            self.assertTrue(published.json()["example"]["persistent_config"])
            from mniplab.webserver.authoring import AuthoringRepository
            published_builder = AuthoringRepository(self.storage.name).published_file(catalog_id)
            seed = os.path.join(os.path.dirname(published_builder), "frr-config", "r1", "frr.conf")
            with open(seed, encoding="utf-8") as source:
                published_seed = source.read()
            self.assertIn("! published text", published_seed)
            await registry.stop_lab(first.json()["key"])

            edited = published.json()["draft"]
            edited["topology"]["nodes"][0]["frr_config"] = "! draft text\n"
            saved = await client.put(
                f"/api/authoring/drafts/{draft['id']}",
                json={"revision": edited["revision"], "document": edited},
            )
            second = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": saved.json()["revision"]},
            )
            self.assertEqual(second.status_code, 200, second.text)

            call = launcher.calls[-1]
            self.assertIn("! draft text", call["seeds"]["r1"]["frr.conf"])
            self.assertEqual(list(call["seeds"]["r1"]), ["frr.conf"])
            self.assertIn("proto='OSPF'", call["builder"])
            previews = os.path.join(self.storage.name, "previews", "config") + os.sep
            self.assertTrue(call["config_copy"].startswith(previews))
            self.assertNotEqual(launcher.calls[0]["config_copy"], call["config_copy"])
            with open(seed, encoding="utf-8") as source:
                self.assertEqual(source.read(), published_seed)
            self.assertFalse(os.path.exists(os.path.join(self.storage.name, "group-config")))
            await registry.stop_lab(second.json()["key"])
        self._as("instructor", exercise)

    def test_preview_uses_a_fresh_copy_and_never_writes_a_published_seed_in_multi_lab_mode(self) -> None:
        self._assert_isolated_preview("multi")

    def test_preview_uses_a_fresh_copy_and_never_writes_a_published_seed_in_single_lab_mode(self) -> None:
        self._assert_isolated_preview("single")

    def test_fast_ospf_timers_are_added_before_the_authored_text_unless_switched_off(self) -> None:
        launcher = RecordingLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            draft = await self._router_draft(client)
            url = f"/api/authoring/drafts/{draft['id']}"
            cases = [
                (["OSPF"], True, " ip ospf hello-interval 1\n ip ospf dead-interval 3\n"),
                (["OSPF6"], True, " ipv6 ospf6 hello-interval 1\n ipv6 ospf6 dead-interval 3\n"),
                (["OSPF"], False, None),
            ]
            for protocols, fast, expected in cases:
                current = (await client.get(url)).json()
                current["topology"]["nodes"][0].update(protocols=protocols, fast_timers=fast)
                saved = await client.put(url, json={"revision": current["revision"], "document": current})
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertNotIn("hello-interval", saved.json()["topology"]["nodes"][0]["frr_config"])
                started = await client.post(
                    f"{url}/preview", json={"revision": saved.json()["revision"]}
                )
                if started.status_code == 409:
                    started = await client.post(f"{url}/preview", json={
                        "revision": saved.json()["revision"],
                        "replace_key": started.json()["detail"]["key"],
                    })
                self.assertEqual(started.status_code, 200, started.text)
                seed = launcher.calls[-1]["seeds"]["r1"]["frr.conf"]
                if expected is None:
                    self.assertNotIn("hello-interval", seed)
                    continue
                self.assertIn(f"interface r1-eth0\n{expected}exit", seed)
                self.assertLess(seed.index("interface r1-eth0"), seed.index("router ospf"))
        self._as("instructor", exercise)

    def test_ephemeral_router_writes_no_seed_and_startup_warnings_are_reported(self) -> None:
        launcher = RecordingLauncher(warnings=["Startup command on h1 (after start) 'x' failed"])
        self._launch_with("multi", launcher)

        async def exercise(client):
            draft = await self._router_draft(client, persistent_config=False)
            preview = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["warnings"], launcher.warnings)
            self.assertEqual(launcher.calls[0]["seeds"], {})
            self.assertTrue(preview.json()["preview_valid"])
        self._as("instructor", exercise)


STARTED = """frr version 10.1
frr defaults traditional
hostname r1
service integrated-vtysh-config
!
router ospf
 network 10.0.0.0/30 area 0
exit
!
ip route 192.0.2.0/24 10.0.0.2
ip route 198.51.100.0/24 10.0.0.2
ip route 203.0.113.0/24 10.0.0.2
!
end
"""


@pytest.mark.unit
class AuthoringDHCPAPITests(AuthoringAPIFixture):
    """DHCP clients, Pools and Relays through save, Preview and publication."""

    async def _refused(self, client, draft, mutate):
        """Assert one malformed variation of ``draft`` is refused unsaved."""
        invalid = copy.deepcopy(draft)
        mutate(invalid)
        url = f"/api/authoring/drafts/{draft['id']}"
        refused = await client.put(url, json={"revision": draft["revision"], "document": invalid})
        self.assertEqual(refused.status_code, 422, refused.text)
        return refused.json()["detail"]

    def test_ipv4_pools_and_clients_round_trip_through_publication(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._dhcp_draft(client)
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            self.assertEqual((await client.post(f"{url}/preview",
                                                json={"revision": current["revision"]})).status_code, 200)
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": current["revision"]})).status_code, 200)

            current = (await client.get(url)).json()
            changed = copy.deepcopy(current)
            changed["topology"]["nodes"][0]["dhcp"]["pools"][0]["end"] = "10.50.0.150"
            saved = await client.put(url, json={"revision": current["revision"], "document": changed})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertFalse((await client.get(f"{url}/preview-status")).json()["current"])

        self._as("instructor", exercise)

    def test_a_host_can_serve_and_two_services_may_share_a_subnet(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            draft["catalog"]["name"] = "Rogue DHCP lesson"
            draft["catalog"]["area"] = "DHCP"
            intended, rogue = host_node("s1", "s1"), host_node("s2", "s2")
            intended["dhcp"] = {"pools": [dhcp_pool("10.60.0.0/24", "10.60.0.100", "10.60.0.150")]}
            rogue["dhcp"] = {"pools": [dhcp_pool("10.60.0.0/24", "10.60.0.151", "10.60.0.200")]}
            draft["topology"] = {
                "nodes": [{"id": "sw", "name": "sw1", "type": "switch"}, intended, rogue,
                          host_node("c", "hc")],
                "links": [
                    {"id": f"l{index}", "ends": [
                        {"node_id": "sw", "interface_id": f"if-00000000000{index}", "port": index,
                         "ipv4": "", "ipv6": ""},
                        {"node_id": node_id, "interface_id": f"if-0000000000{index}0", "port": 0,
                         "ipv4": address, "ipv6": "", **extra},
                    ]}
                    for index, (node_id, address, extra) in enumerate((
                        ("s1", "10.60.0.2/24", {}), ("s2", "10.60.0.3/24", {}),
                        ("c", "", {"dhcp4": True})), start=1)
                ],
            }
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.post(f"{url}/preview",
                                                json={"revision": current["revision"]})).status_code, 200)
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": current["revision"]})).status_code, 200)

        self._as("instructor", exercise)

    def test_malformed_dhcp_declarations_are_refused(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._dhcp_draft(client)

            def pool(document):
                return document["topology"]["nodes"][0]["dhcp"]["pools"][0]

            detail = await self._refused(client, draft, lambda d: pool(d).update(start="10.51.0.100"))
            self.assertIn("not in subnet", detail)
            await self._refused(client, draft, lambda d: pool(d).pop("lease_time"))
            await self._refused(client, draft, lambda d: d["topology"]["nodes"][0]["dhcp"]["pools"]
                                .append(dhcp_pool("10.50.0.0/24", "10.50.0.10", "10.50.0.20")))
            await self._refused(client, draft, lambda d: d["topology"]["links"][0]["ends"][1]
                                .update(ipv4="10.50.0.9/24"))
            await self._refused(client, draft, lambda d: d["topology"]["links"][0]["ends"][0]
                                .update(dhcp4=True))
            await self._refused(client, draft, lambda d: d["topology"]["links"][0]["ends"][1]
                                .update(dhcp4="yes"))
            await self._refused(client, draft, lambda d: d["topology"]["nodes"].append(
                {"id": "sw", "name": "sw1", "type": "switch", "dhcp": {"pools": []}}))

        self._as("instructor", exercise)

    def test_reservations_keep_family_identifier_rules_and_name_a_pinned_mac(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._dhcp_draft(client)
            pools = draft["topology"]["nodes"][0]["dhcp"]["pools"]
            pools[0].update(lease_time=120, router="10.50.0.1", dns_server="8.8.8.8", reservations=[
                {"ip": "10.50.0.50", "mac": "aa:bb:cc:dd:ee:01", "duid": "", "hostname": "h1"}])
            pools.append(dhcp_pool("2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200",
                                   lease_time=600, preferred_lifetime=120,
                                   dns_server="2001:db8:1::1", reservations=[
                {"ip": "2001:db8:1::50", "mac": "", "duid": "00:01:00:01:aa:bb:cc:dd",
                 "hostname": ""}]))
            draft["topology"]["links"][0]["ends"][0]["ipv6"] = "2001:db8:1::1/64"
            draft["topology"]["links"][0]["ends"][1]["mac"] = "aa:bb:cc:dd:ee:01"
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])

            def v4(document):
                return document["topology"]["nodes"][0]["dhcp"]["pools"][0]

            def v6(document):
                return document["topology"]["nodes"][0]["dhcp"]["pools"][1]

            detail = await self._refused(client, current, lambda d: v4(d)["reservations"][0].update(
                mac="", duid="00:01:00:01:aa:bb:cc:dd"))
            self.assertIn("DUID is an IPv6 client identifier", detail)
            detail = await self._refused(client, current, lambda d: v6(d)["reservations"][0].update(
                mac="aa:bb:cc:dd:ee:01"))
            self.assertIn("exactly one client identifier", detail)
            await self._refused(client, current, lambda d: v6(d)["reservations"][0].update(duid=""))
            await self._refused(client, current, lambda d: v6(d).update(router="2001:db8:1::1"))
            await self._refused(client, current, lambda d: v4(d).update(preferred_lifetime=60))
            await self._refused(client, current, lambda d: v4(d)["reservations"][0].update(
                ip="10.51.0.50"))
            ends = lambda d: d["topology"]["links"]  # noqa: E731
            await self._refused(client, current, lambda d: ends(d)[1]["ends"][1].update(
                mac="aa:bb:cc:dd:ee:01"))
            await self._refused(client, current, lambda d: ends(d)[0]["ends"][1].update(mac="aabbcc"))
            await self._refused(client, current, lambda d: ends(d)[0]["ends"][0].update(
                mac="aa:bb:cc:dd:ee:09"))

            v6_reserved = copy.deepcopy(current)
            v6(v6_reserved)["reservations"][0].update(mac="aa:bb:cc:dd:ee:01", duid="")
            saved = await client.put(url, json={"revision": current["revision"],
                                                "document": v6_reserved})
            self.assertEqual(saved.status_code, 200, saved.text)

        self._as("instructor", exercise)

    def test_dhcpv6_clients_and_pools_keep_router_advertisements_in_frr_text(self) -> None:
        self._launch_with("multi", FakeLauncher())
        advertisement = ("interface r1-eth0\n ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
                         " ipv6 nd managed-config-flag\n no ipv6 nd suppress-ra\n")

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._dhcp_draft(client)
            router = draft["topology"]["nodes"][0]
            router["frr_config"] = advertisement
            router["dhcp"]["pools"].append(dhcp_pool(
                "2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200",
                lease_time=600, preferred_lifetime=120))
            served = draft["topology"]["links"][0]["ends"]
            served[0]["ipv6"] = "2001:db8:1::1/64"
            served[1]["dhcp6"] = True
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            stored = (await client.get(url)).json()
            self.assertEqual(stored["topology"], draft["topology"])
            self.assertEqual(stored["topology"]["nodes"][0]["frr_config"], advertisement)
            self.assertEqual((await client.post(f"{url}/preview",
                                                json={"revision": current["revision"]})).status_code, 200)
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": current["revision"]})).status_code, 200)

            current = (await client.get(url)).json()
            client_end = lambda d: d["topology"]["links"][0]["ends"][1]  # noqa: E731
            detail = await self._refused(client, current, lambda d: client_end(d).update(
                ipv6="2001:db8:1::9/64"))
            self.assertIn("static IPv6 address", detail)
            detail = await self._refused(client, current, lambda d: d["topology"]["nodes"][1].update(
                ipv6_gateway="2001:db8:1::1"))
            self.assertIn("Router Advertisement", detail)
            await self._refused(client, current, lambda d: d["topology"]["links"][0]["ends"][0]
                                .update(dhcp6=True))

        self._as("instructor", exercise)

    def test_a_host_gateway_is_refused_only_beside_its_lowest_ports_dhcp_client(self) -> None:
        """A gateway field leaves by the Host's lowest-numbered Interface, as in Python."""
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._dhcp_draft(client)
            draft["topology"]["links"].append({"id": "static", "ends": [
                {"node_id": "r", "interface_id": "if-000000000005", "port": 2,
                 "ipv4": "10.53.0.1/24", "ipv6": "2001:db8:53::1/64"},
                {"node_id": "h", "interface_id": "if-000000000006", "port": 1,
                 "ipv4": "10.53.0.2/24", "ipv6": "2001:db8:53::2/64"},
            ]})
            served, static = (link["ends"][1] for link in draft["topology"]["links"][::2])
            served["dhcp6"] = True
            host = lambda d: d["topology"]["nodes"][1]  # noqa: E731

            detail = await self._refused(client, draft, lambda d: host(d).update(
                ipv4_gateway="10.53.0.1"))
            self.assertIn("Host 'h1' IPv4 default gateway leaves by h1-eth0", detail)
            self.assertIn("DHCPv4 client", detail)
            detail = await self._refused(client, draft, lambda d: host(d).update(
                ipv6_gateway="2001:db8:53::1"))
            self.assertIn("Host 'h1' IPv6 default gateway leaves by h1-eth0", detail)
            self.assertIn("Router Advertisement", detail)

            served["port"], static["port"] = 1, 0
            host(draft).update(ipv4_gateway="10.53.0.1", ipv6_gateway="2001:db8:53::1")
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)

        self._as("instructor", exercise)

    def test_relays_and_an_unreachable_relay_destination_publish(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._dhcp_draft(client)
            nodes, links = draft["topology"]["nodes"], draft["topology"]["links"]
            nodes[0]["dhcp"]["pools"].append(dhcp_pool("10.20.0.0/24", "10.20.0.100", "10.20.0.200"))
            relay = router_node("r2", "r2", protocols=())
            relay["relay"] = {"subnets": ["10.20.0.0/24", "2001:db8:20::/64"],
                              "servers": ["10.0.0.1", "2001:db8::1"]}
            broken = router_node("r3", "r3", protocols=())
            broken["relay"] = {"subnets": ["10.30.0.0/24"], "servers": ["10.99.0.1"]}
            nodes.extend([relay, broken, host_node("h2", "h2"), host_node("h3b", "h4")])
            link_specs: tuple[tuple[str, str, str, dict[str, str | bool]], ...] = (
                    ("r", "r2", "10.0.0.1/30", {"ipv4": "10.0.0.2/30"}),
                    ("r2", "h2", "10.20.0.1/24", {"ipv4": "", "dhcp4": True}),
                    ("r3", "h3b", "10.30.0.1/24", {"ipv4": "", "dhcp4": True}))
            for index, (a, b, address_a, extra_b) in enumerate(link_specs, start=5):
                links.append({"id": f"relay-{index}", "ends": [
                    {"node_id": a, "interface_id": f"if-0000000000{index}a", "port": 2 if a == "r" else 1,
                     "ipv4": address_a, "ipv6": ""},
                    {"node_id": b, "interface_id": f"if-0000000000{index}b", "port": 0,
                     "ipv6": "", **extra_b},
                ]})
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            self.assertEqual((await client.post(f"{url}/preview",
                                                json={"revision": current["revision"]})).status_code, 200)
            self.assertEqual((await client.post(f"{url}/publish",
                                                json={"revision": current["revision"]})).status_code, 200)

            current = (await client.get(url)).json()

            def relay_of(document):
                return document["topology"]["nodes"][3]["relay"]

            await self._refused(client, current, lambda d: relay_of(d).update(subnets=[]))
            await self._refused(client, current, lambda d: relay_of(d).update(servers=[]))
            detail = await self._refused(client, current, lambda d: relay_of(d).update(
                subnets=["10.20.0.0/33"]))
            self.assertIn("Invalid Subnet CIDR", detail)
            await self._refused(client, current, lambda d: relay_of(d).update(servers=["10.0.0.x"]))
            await self._refused(client, current, lambda d: relay_of(d).update(
                servers=["10.0.0.1", "10.0.0.5"]))
            await self._refused(client, current, lambda d: relay_of(d).update(interface="r2-eth0"))
            await self._refused(client, current, lambda d: d["topology"]["nodes"].append(
                {"id": "sw", "name": "sw1", "type": "switch",
                 "relay": {"subnets": ["10.20.0.0/24"], "servers": ["10.0.0.1"]}}))

        self._as("instructor", exercise)


@pytest.mark.unit
class AuthoringKeepAPITests(AuthoringAPIFixture):
    """Selective keep of running FRR configuration from a Preview."""

    async def _previewed(self, client):
        draft = await self._router_draft(client, persistent_config=False)
        self.running_configs["r1"] = STARTED
        preview = await client.post(
            f"/api/authoring/drafts/{draft['id']}/preview",
            json={"revision": draft["revision"]},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        return draft, preview.json()

    def test_link_condition_keep_uses_reviewed_digest_and_invalidates_preview(self) -> None:
        self._launch_with("multi", FakeLauncher())
        conditions = [{"node1": "h1", "node2": "h2", "status": "up",
                       "delay": None, "jitter": None, "loss": None,
                       "bandwidth": None, "scheduler": "hfsc"}]
        getter = patch("mniplab.labhandle.LocalLabHandle._op_get_link_conditions",
                       lambda _handle: conditions)
        getter.start()
        self.addCleanup(getter.stop)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._buildable_draft(client, "Condition keep")
            url = f"/api/authoring/drafts/{draft['id']}"
            preview = await client.post(f"{url}/preview", json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            review = await client.get(f"{url}/keep")
            self.assertEqual(review.status_code, 200, review.text)
            self.assertEqual(review.json()["features"], [])
            conditions[0]["delay"] = "10ms"
            review = (await client.get(f"{url}/keep")).json()["features"]
            self.assertEqual(len(review), 1)
            self.assertEqual(review[0]["id"], "link:link-a")
            stale = await client.post(f"{url}/keep", json={"revision": draft["revision"],
                "selections": [], "features": [{"id": review[0]["id"], "digest": "0" * 64}]})
            self.assertEqual(stale.status_code, 409)
            kept = await client.post(f"{url}/keep", json={"revision": draft["revision"],
                "selections": [], "features": [{"id": review[0]["id"],
                                                "digest": review[0]["digest"]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertEqual(kept.json()["topology"]["links"][0]["condition"]["delay"], "10ms")
            self.assertFalse(kept.json()["preview_valid"])
            self.assertEqual((await client.get(f"{url}/keep")).json()["features"], [])
        self._as("instructor", exercise)

    def test_vlan_keep_preserves_identity_and_ignores_speaker_announcements(self) -> None:
        self._launch_with("multi", FakeLauncher())
        vlan = {"switch": "s1", "ports": {"zz99s1-eth1": {
            "mode": "access", "tag": 20, "trunks": [], "native": None}}}
        announcements = [{"prefix": "198.51.100.0/24", "next_hop": "10.0.1.2"}]
        patches: list[Any] = [
            patch("mniplab.labhandle.LocalLabHandle._op_get_switch_vlans",
                  lambda _handle, _switch: vlan),
            patch("mniplab.labhandle.LocalLabHandle._op_get_speaker",
                  lambda _handle, _speaker: {"neighbors": [{"address": "10.0.1.1",
                      "announced": announcements,
                      "received": [{"prefix": "192.0.2.0/24", "next_hop": "10.0.1.1"}]}]}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client, persistent_config=False)
            draft["topology"]["nodes"].extend([
                {"id": "s", "name": "s1", "type": "switch"},
                {"id": "sp", "name": "sp1", "type": "speaker", "local_as": 65001,
                 "neighbors": [{"peer": "10.0.1.1", "local_address": "10.0.1.2",
                                "local_as": 65001, "peer_as": 65000, "hold_time": None,
                                "routes": []}],
                 "routes": [{"prefix": "203.0.113.0/24", "next_hop": "10.0.1.2"}],
                 "startup": []},
            ])
            draft["topology"]["links"].extend([
                {"id": "s-r", "ends": [
                    {"node_id": "s", "interface_id": "if-000000000003", "port": 1,
                     "ipv4": "", "ipv6": ""},
                    {"node_id": "r", "interface_id": "if-000000000004", "port": 1,
                     "ipv4": "", "ipv6": ""}]},
                {"id": "sp-r", "ends": [
                    {"node_id": "sp", "interface_id": "if-000000000005", "port": 0,
                     "ipv4": "10.0.1.2/24", "ipv6": ""},
                    {"node_id": "r", "interface_id": "if-000000000006", "port": 2,
                     "ipv4": "10.0.1.1/24", "ipv6": ""}]},
            ])
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            preview = await client.post(f"{url}/preview", json={"revision": current["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            features = (await client.get(f"{url}/keep")).json()["features"]
            self.assertEqual({item["id"] for item in features},
                             {"vlan:if-000000000003"})
            response = await client.post(f"{url}/keep", json={"revision": current["revision"],
                "selections": [], "features": [{"id": item["id"], "digest": item["digest"]}
                                             for item in features]})
            self.assertEqual(response.status_code, 200, response.text)
            topology = response.json()["topology"]
            self.assertEqual(topology["links"][1]["ends"][0]["interface_id"], "if-000000000003")
            self.assertEqual(topology["links"][1]["ends"][0]["vlan"]["vlan"], 20)
            speaker = topology["nodes"][-1]
            self.assertEqual(speaker["neighbors"][0]["peer"], "10.0.1.1")
            self.assertEqual([item["prefix"] for item in speaker["neighbors"][0]["routes"]],
                             [])
            self.assertEqual(speaker["routes"][0]["prefix"], "203.0.113.0/24")
            self.assertNotIn("198.51.100.0/24", json.dumps(speaker))
            self.assertNotIn("192.0.2.0/24", json.dumps(speaker))
        self._as("instructor", exercise)

    def test_dhcp_pool_keep_takes_reviewed_pool_fields_and_no_leases(self) -> None:
        self._launch_with("multi", FakeLauncher())
        running: dict[str, Any] = {"success": True, "pools": [
            {"family": 4, "cidr": "10.50.0.0/24", "start": "10.50.0.100", "end": "10.50.0.200",
             "interface": "r1-eth0", "lease_time": None, "router": "", "dns_server": "",
             "reservations": []},
            {"family": 6, "cidr": "2001:db8:1::/64", "start": "2001:db8:1::100",
             "end": "2001:db8:1::200", "interface": "r1-eth0", "lease_time": 600,
             "preferred_lifetime": 120, "router": "", "dns_server": "", "reservations": []},
        ]}
        reads = []

        def pools(_handle, node):
            reads.append(node)
            return copy.deepcopy(running)

        for item in (patch("mniplab.labhandle.LocalLabHandle._op_get_node_pools", pools),
                     patch("mniplab.labhandle.LocalLabHandle._op_get_node_leases",
                           side_effect=AssertionError("Leases are not authored content"))):
            item.start()
            self.addCleanup(item.stop)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._dhcp_draft(client)
            draft["topology"]["nodes"][0]["dhcp"]["pools"].append(dhcp_pool(
                "2001:db8:1::/64", "2001:db8:1::100", "2001:db8:1::200",
                lease_time=600, preferred_lifetime=120))
            draft["topology"]["links"][0]["ends"][0]["ipv6"] = "2001:db8:1::1/64"
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            preview = await client.post(f"{url}/preview", json={"revision": current["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual((await client.get(f"{url}/keep")).json()["features"], [])
            self.assertEqual(reads, ["r1"])

            running["pools"][0].update(end="10.50.0.150", lease_time=300, reservations=[
                {"mac": "aa:bb:cc:dd:ee:01", "duid": "", "ip": "10.50.0.50", "hostname": "h1"}])
            features = (await client.get(f"{url}/keep")).json()["features"]
            self.assertEqual([item["id"] for item in features], ["dhcp:r:10.50.0.0/24"])
            stale = await client.post(f"{url}/keep", json={"revision": current["revision"],
                "selections": [], "features": [{"id": features[0]["id"], "digest": "0" * 64}]})
            self.assertEqual(stale.status_code, 409)
            kept = await client.post(f"{url}/keep", json={"revision": current["revision"],
                "selections": [], "features": [{"id": features[0]["id"],
                                                "digest": features[0]["digest"]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            pools_after = kept.json()["topology"]["nodes"][0]["dhcp"]["pools"]
            self.assertEqual(pools_after[0], dhcp_pool(
                "10.50.0.0/24", "10.50.0.100", "10.50.0.150", lease_time=300, reservations=[
                    {"ip": "10.50.0.50", "mac": "aa:bb:cc:dd:ee:01", "duid": "", "hostname": "h1"}]))
            self.assertEqual(pools_after[1]["preferred_lifetime"], 120)
            self.assertFalse(kept.json()["preview_valid"])
            self.assertEqual((await client.get(f"{url}/keep")).json()["features"], [])
        self._as("instructor", exercise)

    def test_kept_config_does_not_author_the_default_fast_timers(self) -> None:
        self._launch_with("multi", RecordingLauncher())
        timers = ("interface r1-eth0\n ip ospf dead-interval 3\n ip ospf hello-interval 1\n"
                  "exit\n!\n")

        async def exercise(client):
            draft = await self._router_draft(client, persistent_config=False)
            self.running_configs["r1"] = "hostname r1\n!\n" + timers + "router ospf\nexit\n!\nend\n"
            started = await client.post(
                f"/api/authoring/drafts/{draft['id']}/preview",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(started.status_code, 200, started.text)
            self.running_configs["r1"] = ("hostname r1\n!\n" + timers
                                          + "router ospf\n ospf router-id 1.1.1.1\nexit\n!\nend\n")
            url = f"/api/authoring/drafts/{draft['id']}/keep"
            review = (await client.get(url)).json()["routers"][0]
            kept = await client.post(url, json={"revision": draft["revision"], "selections": [
                {"node_id": "r", "digest": review["digest"], "sections": [0]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertEqual(
                kept.json()["topology"]["nodes"][0]["frr_config"],
                "router ospf\n ospf router-id 1.1.1.1\nexit\n!\n",
            )
            self.assertEqual((await client.get(url)).json()["routers"][0]["sections"], [])
        self._as("instructor", exercise)

    def test_keep_needs_this_drafts_running_preview_and_an_instructor(self) -> None:
        self._launch_with("multi", RecordingLauncher())

        async def instructor(client):
            draft = await self._router_draft(client)
            url = f"/api/authoring/drafts/{draft['id']}/keep"
            self.assertEqual((await client.get(url)).status_code, 409)
            previewed, started = await self._previewed(client)
            await registry.stop_lab(started["key"])
            self.assertEqual(
                (await client.get(f"/api/authoring/drafts/{previewed['id']}/keep")).status_code, 409
            )

        async def learner(client):
            response = await client.get("/api/authoring/drafts/draft-" + "0" * 32 + "/keep")
            self.assertEqual(response.status_code, 403)
        self._as("instructor", instructor)
        self._as("learner", learner)

    def test_single_lab_rebuild_replaces_only_this_drafts_preview_with_a_fresh_copy(self) -> None:
        launcher = RecordingLauncher()
        self._launch_with("single", launcher)

        async def exercise(client):
            draft, first = await self._previewed(client)
            url = f"/api/authoring/drafts/{draft['id']}/preview"
            asked = await client.post(url, json={"revision": draft["revision"]})
            self.assertEqual(asked.status_code, 409)
            self.assertEqual(asked.json()["detail"]["code"], "preview_replacement_required")
            rebuilt = await client.post(
                url, json={"revision": draft["revision"], "replace_key": first["key"]}
            )
            self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
            self.assertTrue(launcher.children[0].stopped)
            self.assertNotEqual(launcher.calls[0]["config_copy"], launcher.calls[1]["config_copy"])

            other = await self._router_draft(client)
            blocked = await client.post(
                f"/api/authoring/drafts/{other['id']}/preview", json={"revision": other["revision"]}
            )
            self.assertEqual(blocked.status_code, 409)
            self.assertIn("Single-Lab mode cannot replace", blocked.json()["detail"])
        self._as("instructor", exercise)

    def test_snapshot_and_review_keep_diff_live_router_and_service_state_for_canvas_draft(self) -> None:
        from mniplab.webserver.authoring_keep import review_keep, snapshot_preview

        repository = AuthoringRepository(self.storage.name)
        draft = repository.create()
        draft["address_family"] = "dual"
        router = router_node("r", "r1", frr_config="router ospf\n network 10.50.0.0/24 area 0\nexit\n!\n")
        router["dhcp"] = {"pools": [dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")]}
        draft["topology"] = {
            "nodes": [router, host_node("h", "h1")],
            "links": [{
                "id": "link-a",
                "ends": [
                    {"node_id": "r", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "10.50.0.1/24", "ipv6": ""},
                    {"node_id": "h", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "", "ipv6": "", "dhcp4": True},
                ],
            }],
        }
        draft = repository.save(draft["id"], draft["revision"], draft)
        pools = [{"family": 4, "cidr": "10.50.0.0/24", "start": "10.50.0.100",
                  "end": "10.50.0.200", "interface": "r1-eth0", "lease_time": None,
                  "preferred_lifetime": None, "router": "", "dns_server": "", "reservations": []}]
        conditions = [{"node1": "r1", "node2": "h1", "status": "up", "delay": None,
                       "jitter": None, "loss": None, "bandwidth": None, "scheduler": "hfsc"}]

        class Handle:
            configs: dict[str, str]

            def get_running_config(self, name: str) -> str:
                return self.configs[name]

            def get_node_pools(self, _name: str) -> dict:
                return {"pools": copy.deepcopy(pools)}

            def get_link_conditions(self) -> list:
                return copy.deepcopy(conditions)

        handle = Handle()
        handle.configs = {"r1": "frr version 10\nhostname r1\n!\nrouter ospf\n network 10.50.0.0/24 area 0\nexit\n!\nend\n"}
        snapshot_preview(repository, draft["id"], "k1", handle)
        initial = review_keep(repository, draft["id"], "k1", handle)
        self.assertEqual(initial["key"], "k1")
        self.assertEqual(initial["routers"][0]["sections"], [])
        self.assertEqual(initial["features"], [])
        self.assertFalse(initial["has_kept_changes"])

        handle.configs["r1"] = (
            "frr version 10\nhostname r1\n!\n"
            "router ospf\n ospf router-id 1.1.1.1\n network 10.50.0.0/24 area 0\nexit\n!\nend\n"
        )
        pools[0]["end"] = "10.50.0.150"
        conditions[0]["delay"] = "15ms"

        reviewed = review_keep(repository, draft["id"], "k1", handle)
        self.assertEqual([s["name"] for s in reviewed["routers"][0]["sections"]], ["router ospf"])
        self.assertEqual({f["id"] for f in reviewed["features"]}, {"dhcp:r:10.50.0.0/24", "link:link-a"})

    def test_apply_keep_updates_canvas_draft_and_refreshes_snapshot_baseline(self) -> None:
        from mniplab.webserver.authoring import DraftConflictError
        from mniplab.webserver.authoring_keep import apply_keep, review_keep, snapshot_preview

        repository = AuthoringRepository(self.storage.name)
        draft = repository.create()
        draft["address_family"] = "dual"
        router = router_node("r", "r1", frr_config="router ospf\n network 10.50.0.0/24 area 0\nexit\n!\n")
        router["dhcp"] = {"pools": [dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")]}
        draft["topology"] = {
            "nodes": [router, host_node("h", "h1")],
            "links": [{
                "id": "link-a",
                "ends": [
                    {"node_id": "r", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "10.50.0.1/24", "ipv6": ""},
                    {"node_id": "h", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "", "ipv6": "", "dhcp4": True},
                ],
            }],
        }
        draft = repository.save(draft["id"], draft["revision"], draft)
        pools = [{"family": 4, "cidr": "10.50.0.0/24", "start": "10.50.0.100",
                  "end": "10.50.0.200", "interface": "r1-eth0", "lease_time": None,
                  "preferred_lifetime": None, "router": "", "dns_server": "", "reservations": []}]
        conditions = [{"node1": "r1", "node2": "h1", "status": "up", "delay": None,
                       "jitter": None, "loss": None, "bandwidth": None, "scheduler": "hfsc"}]
        timers = "interface r1-eth0\n ip ospf dead-interval 3\n ip ospf hello-interval 1\nexit\n!\n"

        class Handle:
            configs: dict[str, str]

            def get_running_config(self, name: str) -> str:
                return self.configs[name]

            def get_node_pools(self, _name: str) -> dict:
                return {"pools": copy.deepcopy(pools)}

            def get_link_conditions(self) -> list:
                return copy.deepcopy(conditions)

        handle = Handle()
        handle.configs = {"r1": "frr version 10\nhostname r1\n!\n" + timers + router["frr_config"] + "end\n"}
        snapshot_preview(repository, draft["id"], "k1", handle)

        handle.configs["r1"] = (
            "frr version 10\nhostname r1\n!\n" + timers
            + "router ospf\n ospf router-id 1.1.1.1\n network 10.50.0.0/24 area 0\nexit\n!\nend\n"
        )
        pools[0]["end"] = "10.50.0.150"
        conditions[0]["delay"] = "15ms"

        reviewed = review_keep(repository, draft["id"], "k1", handle)
        router_review = reviewed["routers"][0]
        dhcp_feature = next(f for f in reviewed["features"] if f["id"] == "dhcp:r:10.50.0.0/24")

        with self.assertRaises(DraftConflictError):
            apply_keep(
                repository, draft["id"], draft["revision"], "k1", handle,
                [{"node_id": "r", "digest": "0" * 64, "sections": [0]}],
            )
        with self.assertRaises(DraftConflictError):
            apply_keep(
                repository, draft["id"], draft["revision"] + 9, "k1", handle,
                [{"node_id": "r", "digest": router_review["digest"], "sections": [0]}],
            )

        saved = apply_keep(
            repository,
            draft["id"],
            draft["revision"],
            "k1",
            handle,
            [{"node_id": "r", "digest": router_review["digest"], "sections": [0]}],
            [{"id": dhcp_feature["id"], "digest": dhcp_feature["digest"]}],
        )
        self.assertEqual(saved["revision"], draft["revision"] + 1)
        self.assertEqual(
            saved["topology"]["nodes"][0]["frr_config"],
            "router ospf\n ospf router-id 1.1.1.1\n network 10.50.0.0/24 area 0\nexit\n!\n",
        )
        self.assertEqual(saved["topology"]["nodes"][0]["dhcp"]["pools"][0]["end"], "10.50.0.150")

        after = review_keep(repository, draft["id"], "k1", handle)
        self.assertTrue(after["has_kept_changes"])
        self.assertEqual(after["routers"][0]["sections"], [])
        self.assertEqual([f["id"] for f in after["features"]], ["link:link-a"])

    def test_snapshot_review_and_apply_keep_for_python_draft(self) -> None:
        from mniplab.webserver.authoring_keep import apply_keep, review_keep, snapshot_preview

        repository = AuthoringRepository(self.storage.name)
        source = (
            "from mniplab import mnIPLab\n"
            "def build_network():\n"
            "    net = mnIPLab(topo=None, controller=None)\n"
            "    net.addRouter('r1', frr_dir='./frr-config/lesson/%(name)s')\n"
            "    return net\n"
        )
        draft = repository.create_python(
            {"name": "Python lesson", "description": "", "protocols": []},
            source,
            {},
            "",
            {"version": 1, "nodes": {}},
        )
        pools = [{"family": 4, "cidr": "10.50.0.0/24", "start": "10.50.0.100",
                  "end": "10.50.0.200", "interface": "r1-eth0", "lease_time": None,
                  "preferred_lifetime": None, "router": "", "dns_server": "", "reservations": []}]

        class Handle:
            configs: dict[str, str]

            def topology(self) -> dict:
                return {
                    "nodes": [{"id": "r1", "type": "router", "services": ["dhcp"], "service_details": []}],
                    "links": [],
                }

            def get_running_config(self, name: str) -> str:
                return self.configs[name]

            def get_node_pools(self, _name: str) -> dict:
                return {"pools": copy.deepcopy(pools)}

        handle = Handle()
        handle.configs = {"r1": "frr version 10\nhostname r1\n!\nend\n"}
        snapshot_preview(repository, draft["id"], "k1", handle)

        handle.configs["r1"] = "interface lo\n ip address 192.0.2.9/32\nexit\n"
        pools[0]["lease_time"] = 300
        reviewed = review_keep(repository, draft["id"], "k1", handle)
        router_row = reviewed["routers"][0]
        self.assertEqual(router_row["seed"], "frr-config/lesson/r1/frr.conf")
        feature_row = reviewed["features"][0]
        self.assertEqual(feature_row["not_keepable"], "change in your script")

        with self.assertRaisesRegex(ValueError, "change in your script"):
            apply_keep(
                repository,
                draft["id"],
                draft["revision"],
                "k1",
                handle,
                [],
                [{"id": feature_row["id"], "digest": feature_row["digest"]}],
            )

        saved = apply_keep(
            repository,
            draft["id"],
            draft["revision"],
            "k1",
            handle,
            [{"node_id": "r1", "digest": router_row["digest"], "sections": [0]}],
        )
        self.assertEqual(saved["revision"], draft["revision"] + 1)
        self.assertEqual(
            saved["files"],
            {"frr-config/lesson/r1/frr.conf": "interface lo\n ip address 192.0.2.9/32\nexit\n"},
        )
        after = review_keep(repository, draft["id"], "k1", handle)
        self.assertTrue(after["has_kept_changes"])
        self.assertEqual(after["routers"][0]["sections"], [])

    def test_authoring_repository_retains_only_snapshot_persistence_for_keep(self) -> None:
        self.assertTrue(hasattr(AuthoringRepository, "record_snapshot"))
        self.assertTrue(hasattr(AuthoringRepository, "read_snapshot"))
        for removed in (
            "keep",
            "keep_python",
            "keep_feature_baseline",
            "pool_value",
            "_condition_value",
            "keep_digest",
        ):
            self.assertFalse(hasattr(AuthoringRepository, removed), removed)


@pytest.mark.unit
class AuthoringStructuredValidationTests(AuthoringAPIFixture):
    """Structured DraftValidationError targets across repository.validate and HTTP 422."""

    def test_validate_raises_draft_validation_error_with_structured_targets(self) -> None:
        from mniplab.webserver.authoring import DraftValidationError

        repository = AuthoringRepository(self.storage.name)
        base = repository.create()
        base["address_family"] = "dual"
        base["catalog"] = {"id": None, "name": "Lesson", "description": "desc", "protocols": ["OSPF"]}
        router = router_node("r", "r1", protocols=["OSPF"])
        router["vrfs"] = [{"name": "blue", "table": 100}]
        router["subinterfaces"] = [{
            "id": "subif-1",
            "parent_interface_id": "if-000000000001",
            "vlan": 10,
            "ipv4": "192.168.10.1/24",
            "ipv6": "",
        }]
        router["dhcp"] = {
            "pools": [dhcp_pool(
                "10.50.0.0/24", "10.50.0.100", "10.50.0.200",
                reservations=[{"ip": "10.50.0.50", "mac": "aa:bb:cc:dd:ee:01", "duid": "", "hostname": "h1"}],
            )]
        }
        router["dns"] = {
            "zones": [{
                "name": "example.test.",
                "kind": "primary",
                "default_ttl": 300,
                "dnssec": False,
                "nsec3": False,
                "nsec3_salt": "",
                "allow_transfer": [],
                "ds_delegations": [],
                "records": [{"name": "@", "type": "A", "rdata": "10.50.0.1", "ttl": 300}],
            }]
        }
        host = host_node("h", "h1")
        speaker = {
            "id": "sp",
            "name": "sp1",
            "type": "speaker",
            "local_as": 65001,
            "routes": [{"prefix": "203.0.113.0/24", "next_hop": "10.0.1.2"}],
            "neighbors": [{
                "peer": "10.0.1.1",
                "local_address": "10.0.1.2",
                "local_as": 65001,
                "peer_as": 65000,
                "hold_time": None,
                "routes": [{"prefix": "198.51.100.0/24", "next_hop": "10.0.1.2"}],
            }],
            "startup": [],
        }
        switch = {"id": "s", "name": "s1", "type": "switch"}
        base["topology"] = {
            "nodes": [router, host, speaker, switch],
            "links": [
                {
                    "id": "link-a",
                    "ends": [
                        {"node_id": "r", "interface_id": "if-000000000001", "port": 0,
                         "ipv4": "10.50.0.1/24", "ipv6": ""},
                        {"node_id": "h", "interface_id": "if-000000000002", "port": 0,
                         "ipv4": "10.50.0.2/24", "ipv6": ""},
                    ],
                },
                {
                    "id": "link-sp",
                    "ends": [
                        {"node_id": "r", "interface_id": "if-000000000003", "port": 1,
                         "ipv4": "10.0.1.1/24", "ipv6": ""},
                        {"node_id": "sp", "interface_id": "if-000000000004", "port": 0,
                         "ipv4": "10.0.1.2/24", "ipv6": ""},
                    ],
                },
                {
                    "id": "link-sw",
                    "ends": [
                        {"node_id": "s", "interface_id": "if-000000000005", "port": 1,
                         "ipv4": "", "ipv6": "",
                         "vlan": {"mode": "trunk", "trunks": [10], "native": None}},
                        {"node_id": "r", "interface_id": "if-000000000006", "port": 2,
                         "ipv4": "", "ipv6": ""},
                    ],
                },
            ],
        }
        validated = repository.validate(copy.deepcopy(base))
        self.assertEqual(validated["catalog"]["name"], "Lesson")

        cases: list[tuple[str, Callable[[Any], object], dict[str, str | None]]] = [
            (
                "catalog name",
                lambda d: d["catalog"].update(name="x" * 121),
                {"kind": "draft", "id": None, "controlId": "catalog-name"},
            ),
            (
                "node duplicate name",
                lambda d: d["topology"]["nodes"][1].update(name="r1"),
                {"kind": "node", "id": "h", "controlId": "host-name"},
            ),
            (
                "host gateway",
                lambda d: d["topology"]["nodes"][1].update(ipv4_gateway="not-an-ip"),
                {"kind": "node", "id": "h", "controlId": "host-ipv4-gateway"},
            ),
            (
                "router vrf table",
                lambda d: d["topology"]["nodes"][0]["vrfs"][0].update(table=254),
                {"kind": "node", "id": "r", "controlId": "vrf-0-table"},
            ),
            (
                "router subinterface vlan",
                lambda d: d["topology"]["nodes"][0]["subinterfaces"][0].update(vlan=5000),
                {"kind": "node", "id": "r", "controlId": "subinterface-0-vlan"},
            ),
            (
                "startup command",
                lambda d: d["topology"]["nodes"][0]["startup"].append(
                    {"stage": "before-daemons", "command": "", "timeout": 5, "may_fail": False}
                ),
                {"kind": "node", "id": "r", "controlId": "startup-0-command"},
            ),
            (
                "dhcp pool start",
                lambda d: d["topology"]["nodes"][0]["dhcp"]["pools"][0].update(start="10.99.0.1"),
                {"kind": "node", "id": "r", "controlId": "dhcp-pools-0-start"},
            ),
            (
                "dhcp reservation ip",
                lambda d: d["topology"]["nodes"][0]["dhcp"]["pools"][0]["reservations"][0].update(
                    ip="10.99.0.5"
                ),
                {"kind": "node", "id": "r", "controlId": "dhcp-pools-0-reservations-0-ip"},
            ),
            (
                "dhcp reservation mac",
                lambda d: d["topology"]["nodes"][0]["dhcp"]["pools"][0]["reservations"][0].update(
                    mac="bad-mac"
                ),
                {"kind": "node", "id": "r", "controlId": "dhcp-pools-0-reservations-0-identifier"},
            ),
            (
                "dns record rdata",
                lambda d: d["topology"]["nodes"][0]["dns"]["zones"][0]["records"][0].update(
                    rdata="not-an-ipv4"
                ),
                {"kind": "node", "id": "r", "controlId": "dns-zones-0-records-0-rdata"},
            ),
            (
                "speaker local_as",
                lambda d: d["topology"]["nodes"][2].update(local_as=0),
                {"kind": "node", "id": "sp", "controlId": "speaker-local-as"},
            ),
            (
                "speaker neighbor peer",
                lambda d: d["topology"]["nodes"][2]["neighbors"][0].update(peer="bad-ip"),
                {"kind": "node", "id": "sp", "controlId": "speaker-neighbors-0-peer"},
            ),
            (
                "interface ipv4",
                lambda d: d["topology"]["links"][0]["ends"][0].update(ipv4="10.50.0.1/99"),
                {"kind": "node", "id": "r", "controlId": "iface-if-000000000001-ipv4"},
            ),
            (
                "switch vlan trunks",
                lambda d: d["topology"]["links"][2]["ends"][0]["vlan"].update(trunks=[4095]),
                {"kind": "node", "id": "s", "controlId": "iface-if-000000000005-trunks"},
            ),
            (
                "link condition delay",
                lambda d: d["topology"]["links"][0].update(
                    condition={"status": "up", "delay": "fast", "jitter": None, "loss": None,
                               "bandwidth": None, "scheduler": "hfsc"}
                ),
                {"kind": "link", "id": "link-a", "controlId": "link-condition-delay"},
            ),
            (
                "link duplicate port",
                lambda d: d["topology"]["links"][1]["ends"][0].update(port=0),
                {"kind": "link", "id": "link-sp", "controlId": "link-end-0-port"},
            ),
        ]
        for label, mutate, expected in cases:
            broken = copy.deepcopy(base)
            mutate(broken)
            with self.subTest(label=label):
                with self.assertRaises(DraftValidationError) as ctx:
                    repository.validate(broken)
                self.assertIsInstance(ctx.exception, ValueError)
                target = ctx.exception.target
                self.assertEqual(target["kind"], expected["kind"], label)
                self.assertEqual(target["id"], expected["id"], label)
                self.assertEqual(target["controlId"], expected["controlId"], label)
                self.assertEqual(target["message"], str(ctx.exception), label)

    def test_put_draft_returns_422_with_structured_target(self) -> None:
        async def exercise(client):
            draft = await self._dhcp_draft(client)
            url = f"/api/authoring/drafts/{draft['id']}"
            broken = copy.deepcopy(draft)
            broken["topology"]["nodes"][0]["dhcp"]["pools"][0]["reservations"] = [
                {"ip": "10.99.0.99", "mac": "aa:bb:cc:dd:ee:01", "duid": "", "hostname": "h1"}
            ]
            response = await client.put(url, json={"revision": draft["revision"], "document": broken})
            self.assertEqual(response.status_code, 422, response.text)
            payload = response.json()
            self.assertIn("detail", payload)
            self.assertIn("target", payload)
            self.assertEqual(payload["target"]["kind"], "node")
            self.assertEqual(payload["target"]["id"], "r")
            self.assertEqual(
                payload["target"]["controlId"],
                "dhcp-pools-0-reservations-0-ip",
            )
            self.assertEqual(payload["target"]["message"], payload["detail"])

        self._as("instructor", exercise)

