"""A script uploaded over HTTP keeps unsaved FRR work into a fresh Preview."""

import os
import subprocess

import httpx2
import pytest

from mniplab.labprocess import LabChild
from mniplab.tests.labs import REPO_ROOT
from mniplab.tests.authoring_fixtures import AuthoringAPIFixture
from mniplab.webserver import registry


@pytest.mark.integration
class PythonKeepLabTests(AuthoringAPIFixture):
    """Keep fills an empty script-named Seed using the running Lab's FRR text."""

    fake_running_configs = False

    def test_upload_empty_seed_vtysh_keep_and_fresh_preview(self) -> None:
        self._launch_with("multi", LabChild.launch)
        source = (
            "from mniplab import mnIPLab\n"
            "def build_network():\n"
            "    net = mnIPLab(topo=None, controller=None)\n"
            "    net.addRouter('r1', proto='OSPF', frr_dir='./frr-config/lesson/%(name)s')\n"
            "    return net\n"
        )

        async def exercise(client: httpx2.AsyncClient) -> None:
            uploaded = await client.post(
                "/api/authoring/drafts/upload",
                files={"file": ("lesson.py", source, "text/x-python")},
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            draft = uploaded.json()
            self.assertEqual(draft["files"], {})
            key = None
            try:
                started = await client.post(
                    f"/api/authoring/drafts/{draft['id']}/preview",
                    json={"revision": draft["revision"]},
                )
                self.assertEqual(started.status_code, 200, started.text)
                key = started.json()["key"]
                lab = registry.lab_service.get(key)
                before = lab.handle.get_running_config("r1")
                self.assertNotIn("router ospf", before)
                attachment = lab.handle.node_attachment("r1")
                edited = subprocess.run(
                    [
                        "mnexec",
                        "-a",
                        str(attachment.pid),
                        "vtysh",
                        "-c",
                        "configure terminal",
                        "-c",
                        "router ospf",
                        "-c",
                        "ospf router-id 192.0.2.9",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
                review = await client.get(f"/api/authoring/drafts/{draft['id']}/keep")
                self.assertEqual(review.status_code, 200, review.text)
                row = review.json()["routers"][0]
                self.assertIn(
                    "router ospf", [section["name"] for section in row["sections"]]
                )
                kept = await client.post(
                    f"/api/authoring/drafts/{draft['id']}/keep",
                    json={
                        "revision": draft["revision"],
                        "selections": [
                            {
                                "node_id": row["node_id"],
                                "digest": row["digest"],
                                "sections": [
                                    section["id"] for section in row["sections"]
                                ],
                            }
                        ],
                    },
                )
                self.assertEqual(kept.status_code, 200, kept.text)
                saved = kept.json()
                self.assertEqual(saved["python"], source)
                self.assertIn(
                    "ospf router-id 192.0.2.9",
                    saved["files"]["frr-config/lesson/r1/frr.conf"],
                )
                self.assertFalse(saved["preview_valid"])
                stopped = await client.post(f"/api/labs/{key}/stop")
                self.assertEqual(stopped.status_code, 200, stopped.text)
                key = None
                fresh = await client.post(
                    f"/api/authoring/drafts/{draft['id']}/preview",
                    json={"revision": saved["revision"]},
                )
                self.assertEqual(fresh.status_code, 200, fresh.text)
                key = fresh.json()["key"]
                self.assertTrue(fresh.json()["preview_valid"])
                running = registry.lab_service.get(key).handle.get_running_config("r1")
                self.assertIn("ospf router-id 192.0.2.9", running)
                # The kept Seed lives in the draft's package, never in the checkout.
                self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "frr-config", "lesson")))
                unchanged = await client.get(
                    f"/api/authoring/drafts/{draft['id']}/keep"
                )
                self.assertEqual(unchanged.json()["routers"][0]["sections"], [])
            finally:
                if key:
                    await client.post(f"/api/labs/{key}/stop")

        self._as("instructor", exercise)

    def test_exact_copy_empty_start_seed_revision_and_explicit_group_reset(self) -> None:
        """A new exercise preserves student work until an explicit stopped-Lab reset."""
        self._launch_with("multi", LabChild.launch)

        async def exercise(client: httpx2.AsyncClient) -> None:
            async def call(method: str, path: str, body: dict | None = None) -> dict:
                response = await client.request(method, path, json=body)
                self.assertLess(response.status_code, 300, response.text)
                return response.json()

            def configure(key: str, *commands: str) -> str:
                attachment = registry.lab_service.get(key).handle.node_attachment("r1")
                args = ["mnexec", "-a", str(attachment.pid), "vtysh"]
                for command in commands:
                    args.extend(["-c", command])
                result = subprocess.run(args, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            draft = await call("POST", "/api/authoring/drafts", {"example_id": "static-lab", "source": "recipe"})
            source = draft["python"]
            with open(os.path.join(REPO_ROOT, "examples/static-lab.py"), encoding="utf-8") as shipped:
                self.assertEqual(source, shipped.read())
            self.assertIn("ip route 192.168.2.0/24", draft["files"]["frr-config/static-lab/r1/frr.conf"])
            draft["catalog"]["name"] = "Empty routing exercise"
            # Clear only the lesson routing statements, preserving startup settings.
            draft["files"] = {path: "\n".join(line for line in text.splitlines()
                                if not line.startswith("ip route ")) + "\n"
                              for path, text in draft["files"].items()}
            root = f"/api/authoring/drafts/{draft['id']}"
            draft = await call("PUT", root, {"revision": draft["revision"], "document": draft})
            keys: set[str] = set()
            try:
                preview = await call("POST", root + "/preview", {"revision": draft["revision"]})
                keys.add(preview["key"])
                self.assertNotIn("ip route 192.168.2.0/24", configure(preview["key"], "show running-config"))
                published = await call("POST", root + "/publish", {"revision": draft["revision"]})
                draft, example = published["draft"], published["example"]["id"]
                self.assertNotEqual(example, "static-lab")
                self.assertEqual(draft["python"], source)
                await call("POST", f"/api/labs/{preview['key']}/stop")
                keys.remove(preview["key"])
                await call("PUT", "/api/admin/assignments/learner1", {"example_ids": [example]})

                async def start(group: str) -> str:
                    started = await call("POST", "/api/labs", {"example_id": example, "group": group})
                    keys.add(started["key"])
                    return started["key"]

                async def stop(key: str) -> None:
                    await call("POST", f"/api/labs/{key}/stop")
                    keys.remove(key)

                first = await start("learner1")
                topology = await call("GET", f"/api/labs/{first}/topology")
                self.assertEqual({node["id"] for node in topology["nodes"]}, {"r1", "r2", "h1", "h2"})
                routes = await call("GET", f"/api/labs/{first}/routing/routes/r1")
                self.assertIn("192.168.1.0/24", routes["output"])
                self.assertIn("10.10.1.0/30", routes["output"])
                self.assertNotIn("192.168.2.0/24", routes["output"])
                # Responsive vtysh demonstrates the required FRR daemons survived clearing.
                configure(first, "configure terminal", "ip route 198.51.100.0/24 Null0", "end", "write memory")
                self.assertIn("ip route 198.51.100.0/24 Null0", configure(first, "show running-config"))
                await stop(first)

                revised = await call("POST", "/api/authoring/drafts", {"example_id": example, "revise": True})
                root = f"/api/authoring/drafts/{revised['id']}"
                revised["files"]["frr-config/static-lab/r1/frr.conf"] += "ip route 203.0.113.0/24 Null0\n"
                revised = await call("PUT", root, {"revision": revised["revision"], "document": revised})
                preview = await call("POST", root + "/preview", {"revision": revised["revision"]})
                keys.add(preview["key"])
                publication = await call("POST", root + "/publish", {"revision": revised["revision"], "mode": "update_existing"})
                self.assertEqual(publication["incompatible_groups"], [])
                await stop(preview["key"])
                old = await start("learner1")
                old_config = configure(old, "show running-config")
                self.assertIn("198.51.100.0/24", old_config)
                self.assertNotIn("203.0.113.0/24", old_config)
                await stop(old)
                second = await start("instructor2")
                self.assertIn("203.0.113.0/24", configure(second, "show running-config"))
                configure(second, "configure terminal", "ip route 198.18.0.0/24 Null0", "end", "write memory")
                # An idle first Group must not be reset before refusal on the live second Group.
                refused = await client.post("/api/admin/config/reset", json={"example_id": example})
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertIn(second, refused.text)
                await stop(second)
                old = await start("learner1")
                self.assertIn("198.51.100.0/24", configure(old, "show running-config"))
                self.assertNotIn("203.0.113.0/24", configure(old, "show running-config"))
                await stop(old)
                second = await start("instructor2")
                self.assertIn("198.18.0.0/24", configure(second, "show running-config"))
                await stop(second)
                reset = await call("POST", "/api/admin/config/reset", {"example_id": example, "group": "learner1"})
                self.assertEqual(reset["reset"], ["learner1"])
                old = await start("learner1")
                fresh = configure(old, "show running-config")
                self.assertIn("203.0.113.0/24", fresh)
                self.assertNotIn("198.51.100.0/24", fresh)
                await stop(old)

                # Opaque inputs and Python edits conservatively require reset, preserving work.
                for field in ("lesson_files", "python"):
                    revision = await call("POST", "/api/authoring/drafts", {"example_id": example, "revise": True})
                    root = f"/api/authoring/drafts/{revision['id']}"
                    if field == "lesson_files":
                        revision[field] = {"helper.py": {"encoding": "utf-8", "content": "VALUE = 1\n"}}
                    else:
                        revision[field] += "\n# Revised teaching source\n"
                    revision = await call("PUT", root, {"revision": revision["revision"], "document": revision})
                    preview = await call("POST", root + "/preview", {"revision": revision["revision"]})
                    keys.add(preview["key"])
                    publication = await call("POST", root + "/publish", {"revision": revision["revision"], "mode": "update_existing"})
                    self.assertEqual(set(publication["incompatible_groups"]), {"learner1", "instructor2"})
                    await stop(preview["key"])
                    refused = await client.post("/api/labs", json={"example_id": example, "group": "instructor2"})
                    self.assertEqual(refused.status_code, 409, refused.text)
                # Restoring the compatible recipe makes the untouched Copy runnable.
                restored = await call("POST", "/api/authoring/drafts", {"example_id": example, "revise": True})
                root = f"/api/authoring/drafts/{restored['id']}"
                restored["python"], restored["lesson_files"] = source, {}
                restored = await call("PUT", root, {"revision": restored["revision"], "document": restored})
                preview = await call("POST", root + "/preview", {"revision": restored["revision"]})
                keys.add(preview["key"])
                publication = await call("POST", root + "/publish", {"revision": restored["revision"], "mode": "update_existing"})
                self.assertEqual(publication["incompatible_groups"], [])
                await stop(preview["key"])
                second = await start("instructor2")
                self.assertIn("198.18.0.0/24", configure(second, "show running-config"))
                await stop(second)
                assignment = await call("GET", "/api/admin/assignments")
                self.assertEqual(assignment["assignments"]["learner1"], [example])
            finally:
                for key in keys:
                    await client.post(f"/api/labs/{key}/stop")

        self._as("instructor", exercise)
