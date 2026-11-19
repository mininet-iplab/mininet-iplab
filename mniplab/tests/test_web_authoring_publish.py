"""Publish checks startup itself through the authenticated authoring API."""

import copy
from typing import Any
from unittest.mock import patch

import httpx2
import pytest

from mniplab.labprocess import LabStartError
from mniplab.tests.authoring_fixtures import AuthoringAPIFixture
from mniplab.tests.test_web_multi_lab import FakeLabChild, FakeLauncher
from mniplab.webserver import registry
from mniplab.webserver.config import settings


@pytest.mark.unit
class PublishStartCheckTests(AuthoringAPIFixture):
    """Publication needs startup evidence, and creates it when missing."""

    def test_publish_starts_a_check_and_publishes_without_a_separate_preview_request(
        self,
    ) -> None:
        launcher = FakeLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            url = f"/api/authoring/drafts/{draft['id']}"
            published = await client.post(
                f"{url}/publish", json={"revision": draft["revision"]}
            )
            self.assertEqual(published.status_code, 200, published.text)
            example = published.json()["example"]
            self.assertEqual(example["name"], draft["catalog"]["name"])
            labs = (await client.get("/api/labs")).json()["labs"]
            self.assertEqual(len(labs), 1)
            self.assertEqual(labs[0]["example"], f"preview-{draft['id']}")
            self.assertTrue(
                (await client.get(f"{url}/preview-status")).json()["current"]
            )
            self.assertIn(
                example["id"],
                [
                    item["id"]
                    for item in (await client.get("/api/examples")).json()["examples"]
                ],
            )

        self._as("instructor", exercise)

    def test_publish_requires_a_known_area_and_returns_the_choice(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            url = f"/api/authoring/drafts/{draft['id']}"
            for area in (None, "Other", "Typo"):
                draft["catalog"].pop("area", None)
                if area is not None:
                    draft["catalog"]["area"] = area
                saved = await client.put(url, json={
                    "revision": draft["revision"], "document": draft,
                })
                self.assertEqual(saved.status_code, 200, saved.text)
                draft = saved.json()
                refused = await client.post(f"{url}/publish", json={
                    "revision": draft["revision"],
                })
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertIn("Area", refused.text)
            draft["catalog"]["area"] = "Intra-domain"
            draft = (await client.put(url, json={
                "revision": draft["revision"], "document": draft,
            })).json()
            published = await client.post(f"{url}/publish", json={
                "revision": draft["revision"],
            })
            self.assertEqual(published.status_code, 200, published.text)
            self.assertEqual(published.json()["example"]["area"], "Intra-domain")
            examples = (await client.get("/api/examples")).json()["examples"]
            entry = next(row for row in examples if row["id"] == published.json()["example"]["id"])
            self.assertEqual(entry["area"], "Intra-domain")

        self._as("instructor", exercise)

    def test_start_failure_is_shown_and_nothing_is_published(self) -> None:
        class FailingLauncher(FakeLauncher):
            """Refuse startup at the Lab process boundary."""

            async def __call__(self, *args: Any, **kwargs: Any) -> FakeLabChild:
                raise LabStartError("Router r1 failed to start")

        self._launch_with("multi", FailingLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            failed = await client.post(
                f"/api/authoring/drafts/{draft['id']}/publish",
                json={"revision": draft["revision"]},
            )
            self.assertEqual(failed.status_code, 500, failed.text)
            self.assertIn("Router r1 failed to start", failed.json()["detail"])
            self.assertEqual((await client.get("/api/labs")).json()["labs"], [])
            self.assertFalse(
                (
                    await client.get(
                        f"/api/authoring/drafts/{draft['id']}/preview-status"
                    )
                ).json()["current"]
            )
            self.assertIsNone(
                (await client.get(f"/api/authoring/drafts/{draft['id']}")).json()[
                    "catalog"
                ]["id"]
            )
            self.assertFalse(
                any(
                    item.get("authored")
                    for item in (await client.get("/api/examples")).json()["examples"]
                )
            )

        self._as("instructor", exercise)

    def test_publish_check_obeys_the_cap_and_single_lab_limits(self) -> None:
        launcher = FakeLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client: httpx2.AsyncClient) -> None:
            registry.groups.assign("learner1", ["static-lab"])
            other = await client.post(
                "/api/labs", json={"example_id": "static-lab", "group": "learner1"}
            )
            self.assertEqual(other.status_code, 200, other.text)
            draft = await self._router_draft(client)
            with patch.object(settings, "lab_cap", 1):
                blocked = await client.post(
                    f"/api/authoring/drafts/{draft['id']}/publish",
                    json={"revision": draft["revision"]},
                )
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertIn("Lab cap reached", blocked.json()["detail"])
            self.assertFalse(launcher.children[0].stopped)
            await client.post(f"/api/labs/{other.json()['key']}/stop")
            with patch.object(settings, "lab_mode", "single"):
                shared = await client.post(
                    "/api/labs", json={"example_id": "static-lab"}
                )
                self.assertEqual(shared.status_code, 200, shared.text)
                blocked = await client.post(
                    f"/api/authoring/drafts/{draft['id']}/publish",
                    json={"revision": draft["revision"]},
                )
                self.assertEqual(blocked.status_code, 409, blocked.text)
                self.assertIn("Stop the shared Lab", blocked.json()["detail"])
                self.assertIn("Single-Lab mode", blocked.json()["detail"])
            self.assertFalse(launcher.children[-1].stopped)
            self.assertFalse(
                any(
                    item.get("authored")
                    for item in (await client.get("/api/examples")).json()["examples"]
                )
            )

        self._as("instructor", exercise)

    def test_replacing_a_lab_requires_its_exact_key_and_rechecks_the_changed_network(
        self,
    ) -> None:
        launcher = FakeLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            url = f"/api/authoring/drafts/{draft['id']}"
            first = await client.post(
                f"{url}/preview", json={"revision": draft["revision"]}
            )
            key = first.json()["key"]
            edited = copy.deepcopy(draft)
            edited["topology"]["nodes"][0][
                "frr_config"
            ] += "ip route 192.0.2.0/24 Null0\n"
            saved = (
                await client.put(
                    url, json={"revision": draft["revision"], "document": edited}
                )
            ).json()
            refused = await client.post(
                f"{url}/publish", json={"revision": saved["revision"]}
            )
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(
                refused.json()["detail"]["code"], "preview_replacement_required"
            )
            self.assertFalse(launcher.children[0].stopped)
            wrong = await client.post(
                f"{url}/publish",
                json={"revision": saved["revision"], "replace_key": "stale"},
            )
            self.assertEqual(wrong.status_code, 409, wrong.text)
            self.assertFalse(launcher.children[0].stopped)
            published = await client.post(
                f"{url}/publish",
                json={"revision": saved["revision"], "replace_key": key},
            )
            self.assertEqual(published.status_code, 200, published.text)
            self.assertTrue(launcher.children[0].stopped)
            self.assertEqual(len((await client.get("/api/labs")).json()["labs"]), 1)
            self.assertTrue(
                (await client.get(f"{url}/preview-status")).json()["current"]
            )

        self._as("instructor", exercise)
