"""Config Copy safety through publication, ordinary start and explicit reset."""

import json
from pathlib import Path

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, SeedingLauncher
from mniplab.webserver import registry


@pytest.mark.unit
class AuthoringCompatibilityTests(AuthoringAPIFixture):
    """Structural changes refuse reuse without discarding Group work silently."""

    def _check_change(self, change: str, *, reset: bool = False) -> None:
        self._launch_with("multi", SeedingLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            router = draft["topology"]["nodes"][0]
            router["vrfs"] = [
                {"name": "blue", "table": 10},
                {"name": "red", "table": 20},
            ]
            draft["topology"]["links"][0]["ends"][0]["vrf"] = "blue"
            if change == "plain":
                router.pop("vrfs")
                draft["topology"]["links"][0]["ends"][0].pop("vrf")
            if change in ("edit_code", "remove_code", "code_only"):
                draft["code_section"] = "net.addRouter('r2', frr_dir='frr-config/r2')"
            if change == "code_only":
                draft["persistent_config"] = False
            path = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(
                path, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            preview = await client.post(
                f"{path}/preview", json={"revision": saved.json()["revision"]}
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            first = await client.post(
                f"{path}/publish", json={"revision": saved.json()["revision"]}
            )
            self.assertEqual(first.status_code, 200, first.text)
            example = first.json()["example"]["id"]
            await registry.stop_lab(preview.json()["key"])
            started = await client.post("/api/labs", json={"example_id": example})
            self.assertEqual(started.status_code, 200, started.text)
            await registry.stop_lab(started.json()["key"])
            directory = Path(registry.config_copies.copy_dir("instructor1", example))
            self.assertTrue((directory / ".structure.json").is_file())
            work = directory / "group-work.txt"
            work.write_text("Group work", encoding="utf-8")

            current = first.json()["draft"]
            router = current["topology"]["nodes"][0]
            if change == "table":
                router["vrfs"][0]["table"] = 11
            elif change == "attachment":
                current["topology"]["links"][0]["ends"][0]["vrf"] = "red"
            elif change == "remove_vrf":
                router["vrfs"].pop()
            elif change in ("add_code", "edit_code", "code_only"):
                current["code_section"] = "net.addRouter('r3', frr_dir='frr-config/r3')"
            elif change == "remove_code":
                current["code_section"] = ""
            elif change == "legacy":
                # Old records omit these fields even when the recipe has VRFs.
                record = json.loads((directory / ".structure.json").read_text())
                for item in record["routers"]:
                    item.pop("vrfs", None)
                    for interface in item["interfaces"]:
                        interface.pop("vrf", None)
                (directory / ".structure.json").write_text(json.dumps(record))
            elif change in ("compatible", "plain"):
                router["vrfs"] = list(reversed(router.get("vrfs", [])))
                router["frr_config"] += "\n! Seed correction\n"
                current["topology"]["links"][0]["ends"][0]["ipv4"] = "10.0.0.9/30"
                current["guide"] = "Revised Guide"
                current["layout"]["nodes"] = {"r": {"x": 150, "y": 250}}
            saved = await client.put(
                path, json={"revision": current["revision"], "document": current}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            preview = await client.post(
                f"{path}/preview", json={"revision": saved.json()["revision"]}
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            updated = await client.post(
                f"{path}/publish",
                json={
                    "revision": saved.json()["revision"],
                    "mode": "update_existing",
                },
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            compatible = change in ("compatible", "plain")
            self.assertEqual(
                updated.json()["incompatible_groups"],
                [] if compatible else ["instructor1"],
            )
            await registry.stop_lab(preview.json()["key"])
            row = next(
                item
                for item in (await client.get("/api/examples")).json()["examples"]
                if item["id"] == example
            )
            self.assertEqual(row["incompatible"], not compatible)
            refused = await client.post("/api/labs", json={"example_id": example})
            if compatible:
                self.assertEqual(refused.status_code, 200, refused.text)
                self.assertEqual(work.read_text(), "Group work")
                await registry.stop_lab(refused.json()["key"])
                return
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(work.read_text(), "Group work")
            if reset:
                discarded = await client.post(
                    "/api/admin/config/reset",
                    json={
                        "example_id": example,
                        "group": "instructor1",
                    },
                )
                self.assertEqual(discarded.status_code, 200, discarded.text)
                self.assertFalse(directory.exists())
            clean = await client.post(
                "/api/labs", json={"example_id": example, "clean": not reset}
            )
            self.assertEqual(clean.status_code, 200, clean.text)
            self.assertFalse(work.exists())
            await registry.stop_lab(clean.json()["key"])
            resumed = await client.post("/api/labs", json={"example_id": example})
            self.assertEqual(resumed.status_code, 200, resumed.text)
            await registry.stop_lab(resumed.json()["key"])

        self._as("instructor", exercise)

    def test_vrf_table_change_requires_start_clean(self) -> None:
        """A table identity change cannot resume the earlier Router config."""
        self._check_change("table")

    def test_interface_vrf_change_requires_config_reset(self) -> None:
        """Changing attachment blocks reuse until explicit Instructor reset."""
        self._check_change("attachment", reset=True)

    def test_removing_a_vrf_requires_start_clean(self) -> None:
        """Removing an unused declared VRF changes the Router structure."""
        self._check_change("remove_vrf")

    def test_adding_code_requires_start_clean(self) -> None:
        """An opaque Code section may add persistent Router identities."""
        self._check_change("add_code")

    def test_editing_code_requires_start_clean(self) -> None:
        """Existing code cannot change without explicit copy discard."""
        self._check_change("edit_code")

    def test_removing_code_requires_start_clean(self) -> None:
        """Removing code may remove a persistent Router."""
        self._check_change("remove_code")

    def test_code_only_router_records_compatibility_and_requires_start_clean(
        self,
    ) -> None:
        """A persistent Router created solely by code also records its contract."""
        self._check_change("code_only")

    def test_legacy_vrf_record_requires_explicit_reset(self) -> None:
        """Missing historical VRF evidence cannot imply compatibility."""
        self._check_change("legacy", reset=True)

    def test_vrf_reordering_and_nonstructural_edits_preserve_group_work(self) -> None:
        """Row order, Seed text, addresses, Guide and Layout do not discard work."""
        self._check_change("compatible")

    def test_plain_legacy_copies_keep_their_existing_contract(self) -> None:
        """Adding empty optional fields must not invalidate unaffected copies."""
        self._check_change("plain")
