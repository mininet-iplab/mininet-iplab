"""Remove from Catalog: Publish's undoing for a Lab Example an Instructor authored."""

import asyncio
import os

import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, SeedingLauncher
from mniplab.webserver import registry


@pytest.mark.unit
class RemoveFromCatalogAPITests(AuthoringAPIFixture):
    """An authored Lab Example leaves the Catalog with every Group's Config Copy of it."""

    def setUp(self) -> None:
        super().setUp()
        self._launch_with("multi", SeedingLauncher())

    async def _published(self, client):
        """Publish a Router lesson and return its draft and Catalog entry."""
        draft = await self._router_draft(client)
        started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                    json={"revision": draft["revision"]})
        self.assertEqual(started.status_code, 200, started.text)
        await client.post(f"/api/labs/{started.json()['key']}/stop")
        published = await client.post(f"/api/authoring/drafts/{draft['id']}/publish",
                                      json={"revision": draft["revision"]})
        self.assertEqual(published.status_code, 200, published.text)
        return published.json()["draft"], published.json()["example"]

    async def _start(self, client, example_id):
        """Start a Lab of the Lab Example as the signed-in Group; return its key."""
        started = await client.post("/api/labs", json={"example_id": example_id})
        self.assertEqual(started.status_code, 200, started.text)
        return started.json()["key"]

    async def _catalog_ids(self, client):
        """The ids of every Lab Example in the Catalog."""
        return {item["id"] for item in (await client.get("/api/examples")).json()["examples"]}

    def test_removing_drops_the_example_its_versions_and_every_config_copy(self) -> None:
        async def exercise(client):
            _, example = await self._published(client)
            await client.post(f"/api/labs/{await self._start(client, example['id'])}/stop")
            self.assertEqual(registry.config_copies.groups_of(example["id"]), ["instructor1"])

            removal = await client.get(f"/api/authoring/examples/{example['id']}/removal")
            self.assertEqual(removal.status_code, 200, removal.text)
            self.assertEqual(removal.json(), {"example": example["id"], "name": "Routed lesson",
                                              "config_copies": ["instructor1"]})
            removed = await client.delete(f"/api/authoring/examples/{example['id']}")
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json(), {"example": example["id"],
                                              "discarded": ["instructor1"]})

            self.assertNotIn(example["id"], await self._catalog_ids(client))
            self.assertEqual(registry.config_copies.groups_of(example["id"]), [])
            self.assertFalse(os.path.exists(
                os.path.join(self.storage.name, "published", example["id"])))
            refused = await client.post("/api/labs", json={"example_id": example["id"]})
            self.assertNotEqual(refused.status_code, 200)
            gone = await client.delete(f"/api/authoring/examples/{example['id']}")
            self.assertEqual(gone.status_code, 404)

        self._as("instructor", exercise)

    def test_removing_is_refused_while_a_lab_of_it_runs_naming_the_group(self) -> None:
        async def exercise(client):
            _, example = await self._published(client)
            key = await self._start(client, example["id"])
            refused = await client.delete(f"/api/authoring/examples/{example['id']}")
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(refused.json()["detail"],
                             f"Routed lesson cannot leave the Catalog: Group 'instructor1' "
                             f"runs a Lab of it ({key}); stop it first")
            self.assertIn(example["id"], await self._catalog_ids(client))
            self.assertEqual(registry.config_copies.groups_of(example["id"]), ["instructor1"])

            await client.post(f"/api/labs/{key}/stop")
            removed = await client.delete(f"/api/authoring/examples/{example['id']}")
            self.assertEqual(removed.status_code, 200, removed.text)

        self._as("instructor", exercise)

    def test_removing_waits_only_for_a_lab_of_it_that_is_starting(self) -> None:
        release = asyncio.Event()
        launcher = SeedingLauncher()

        async def held(example_id, lab_file, config_copy=None, timeout=None, cwd=None):
            if not example_id.startswith("preview-"):
                await release.wait()
            return await SeedingLauncher.__call__(launcher, example_id, lab_file,
                                                  config_copy, timeout, cwd)

        self._launch_with("multi", held)

        async def exercise(client):
            _, example = await self._published(client)
            _, other = await self._published(client)
            starting = asyncio.create_task(
                client.post("/api/labs", json={"example_id": example["id"]}))
            while not registry.lab_service.refuse_removal(example["id"]):
                await asyncio.sleep(0.01)
            refused = await client.delete(f"/api/authoring/examples/{example['id']}")
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(refused.json()["detail"], "Routed lesson cannot leave the Catalog: "
                             "Group 'instructor1' is starting a Lab of it; wait for it")
            removed = await client.delete(f"/api/authoring/examples/{other['id']}")
            self.assertEqual(removed.status_code, 200, removed.text)
            release.set()
            started = await starting
            self.assertEqual(started.status_code, 200, started.text)

        self._as("instructor", exercise)

    def test_a_shipped_example_is_never_removed(self) -> None:
        async def exercise(client):
            for response in (await client.get("/api/authoring/examples/static-lab/removal"),
                             await client.delete("/api/authoring/examples/static-lab")):
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("shipped", response.json()["detail"])
            self.assertIn("static-lab", await self._catalog_ids(client))

        self._as("instructor", exercise)

    def test_a_draft_revising_a_removed_example_publishes_as_new(self) -> None:
        async def exercise(client):
            _, example = await self._published(client)
            revise = (await client.post("/api/authoring/drafts", json={
                "example_id": example["id"], "revise": True})).json()
            self.assertTrue(revise["can_update"])
            await client.delete(f"/api/authoring/examples/{example['id']}")

            reopened = (await client.get(f"/api/authoring/drafts/{revise['id']}")).json()
            self.assertFalse(reopened["can_update"])
            saved = await client.put(f"/api/authoring/drafts/{revise['id']}", json={
                "revision": reopened["revision"], "document": reopened})
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            await client.post(f"/api/labs/{started.json()['key']}/stop")
            update = await client.post(f"/api/authoring/drafts/{draft['id']}/publish", json={
                "revision": draft["revision"], "mode": "update_existing"})
            self.assertEqual(update.status_code, 409, update.text)
            published = await client.post(f"/api/authoring/drafts/{draft['id']}/publish",
                                          json={"revision": draft["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            self.assertNotEqual(published.json()["example"]["id"], example["id"])
            self.assertTrue(published.json()["draft"]["can_update"])

        self._as("instructor", exercise)
