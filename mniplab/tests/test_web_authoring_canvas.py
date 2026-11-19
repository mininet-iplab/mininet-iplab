"""Unbounded authoring Layouts through authenticated HTTP and exported assets."""

import io
import json
from pathlib import Path
import zipfile

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import (
    AuthoringAPIFixture,
    RecordingLauncher,
    host_node,
)
from mniplab.webserver.authoring import shipped_definition


@pytest.mark.unit
class CanvasLayoutAPITests(AuthoringAPIFixture):
    """Save, Preview, publish and download positions anywhere on the canvas."""

    def test_large_layout_survives_preview_publication_and_download(self) -> None:
        """Runtime and downloaded Layouts retain coordinates without rescaling."""
        self._launch_with("multi", RecordingLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "Large canvas"
            draft["catalog"]["area"] = "Static"
            draft["topology"]["nodes"] = [host_node("a", "h1"), host_node("b", "h2")]
            positions = {"a": {"x": -1500.25, "y": -300}, "b": {"x": 4000, "y": 3800.5}}
            draft["layout"]["nodes"] = positions
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": 0, "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            self.assertEqual(
                (await client.get(url)).json()["layout"]["nodes"], positions
            )
            preview = await client.post(
                url + "/preview", json={"revision": draft["revision"]}
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            runtime = {"h1": positions["a"], "h2": positions["b"]}
            preview_layout = await client.get(
                "/api/load_layout", params={"example": f"preview-{draft['id']}"}
            )
            self.assertEqual(preview_layout.json()["layout"]["nodes"], runtime)
            published = await client.post(
                url + "/publish", json={"revision": draft["revision"]}
            )
            self.assertEqual(published.status_code, 200, published.text)
            layout = await client.get(
                "/api/load_layout",
                params={"example": published.json()["example"]["id"]},
            )
            self.assertEqual(layout.json()["layout"]["nodes"], runtime)
            download = await client.get(url + "/python.zip")
            self.assertEqual(download.status_code, 200, download.text)
            with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                filename = next(
                    name for name in archive.namelist() if "/layouts/" in name
                )
                self.assertEqual(json.loads(archive.read(filename))["nodes"], runtime)

        self._as("instructor", exercise)

    def test_invalid_coordinates_are_refused(self) -> None:
        """Only finite numbers can replace a saved Layout's coordinates."""

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["topology"]["nodes"] = [host_node("a", "h1")]
            draft["layout"]["nodes"] = {"a": {"x": 2000, "y": 2000}}
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": 0, "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            for axis in ("x", "y"):
                invalid: object
                for invalid in (True, None, "2001", [], {}, float("nan"), float("inf"), -float("inf")):
                    with self.subTest(axis=axis, value=invalid):
                        changed = json.loads(json.dumps(draft))
                        changed["layout"]["nodes"]["a"][axis] = invalid
                        response = await client.put(
                            url,
                            content=json.dumps({"revision": draft["revision"], "document": changed}),
                            headers={"Content-Type": "application/json"},
                        )
                        self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual((await client.get(url)).json()["layout"], draft["layout"])

        self._as("instructor", exercise)

    def test_copying_a_shipped_layout_preserves_its_coordinates(self) -> None:
        """Copy keeps negative, large and fractional positions from the running Layout."""
        examples = Path(self.storage.name) / "examples"
        (examples / "definitions").mkdir(parents=True)
        (examples / "layouts").mkdir()
        definition = {
            "schema_version": 3,
            "topology": {"nodes": [host_node("a", "h1"), host_node("b", "h2")], "links": []},
            "address_family": "dual",
            "persistent_config": False,
        }
        positions = {"h1": {"x": -125.5, "y": -500}, "h2": {"x": 4200, "y": 3000.25}}
        (examples / "definitions" / "layout-lab.json").write_text(
            json.dumps(definition), encoding="utf-8")
        (examples / "layouts" / "layout-lab.json").write_text(
            json.dumps({"nodes": positions}), encoding="utf-8")
        (examples / "labs.json").write_text(
            json.dumps([{"id": "layout-lab", "name": "Layout Lab"}]), encoding="utf-8")
        copied = shipped_definition("layout-lab", str(examples))
        self.assertEqual(copied["layout"]["nodes"], {"a": positions["h1"], "b": positions["h2"]})


@pytest.mark.unit
class CanvasSwitchAPITests(AuthoringAPIFixture):
    """A Canvas Switch keeps its spanning-tree options in generated Python."""

    def test_spanning_tree_options_reach_the_generated_script(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["topology"]["nodes"] = [
                {"id": "core", "name": "s1", "type": "switch",
                 "stp": "rstp", "priority": 4096},
            ]
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": 0, "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            source = (await client.get(url + "/python")).json()["source"]
            self.assertIn("stp='rstp'", source)
            self.assertIn("priority=4096", source)
            self.assertEqual(saved.json()["topology"], draft["topology"])

        self._as("instructor", exercise)

    def test_switch_refuses_unsupported_protocol_or_priority(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            url = f"/api/authoring/drafts/{draft['id']}"
            for options in ({"stp": "mstp"}, {"stp": 1},
                            {"stp": "rstp", "priority": 1},
                            {"stp": True, "priority": -1},
                            {"stp": True, "priority": True},
                            {"priority": 4096}):
                with self.subTest(options=options):
                    draft["topology"]["nodes"] = [
                        {"id": "core", "name": "s1", "type": "switch", **options},
                    ]
                    saved = await client.put(url, json={"revision": 0, "document": draft})
                    self.assertEqual(saved.status_code, 422, saved.text)
                    self.assertEqual(saved.json()["target"]["kind"], "node")
                    self.assertEqual(saved.json()["target"]["id"], "core")

        self._as("instructor", exercise)
