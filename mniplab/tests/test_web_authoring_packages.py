"""Native package export through authenticated HTTP and real saved drafts."""

import base64
import binascii
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import warnings
from typing import Any, Dict
from unittest.mock import patch
import zipfile

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture


def package_members(data: bytes) -> Dict[str, bytes]:
    """Read exported members for deliberate external edits in HTTP tests."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def package_zip(members: Dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """Make an actual compressed archive from externally supplied members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.unit
class NativePackageExportTests(AuthoringAPIFixture):
    """A complete lesson has one authoritative recipe and keeps its bytes."""

    async def _python_draft(self, client: httpx2.AsyncClient) -> Dict[str, Any]:
        source = b"# caf\xc3\xa9\r\ndef build_network():\r\n    return None\r\n"
        response = await client.post("/api/authoring/drafts/upload", files={
            "file": ("lesson.py", source, "text/x-python")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_native_export_preserves_both_modes_and_complete_lesson_bytes(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            for draft in (await self._router_draft(client), await self._python_draft(client)):
                mode = "python" if draft.get("kind") == "python" else "canvas"
                with self.subTest(mode=mode):
                    draft["guide"] = "# café\r\nRead the packaged lesson.\r\n"
                    draft["lesson_files"] = {
                        "helpers/lesson.py": {"encoding": "utf-8", "content": "# café\r\nN = 2\r\n"},
                        "data/input.bin": {"encoding": "base64", "content": "AP9cAA0K"},
                        "presentation/diagram.png": {"encoding": "base64", "content": "iVBORw0KGgr/AA==",
                                                     "role": "presentation"},
                    }
                    if mode == "canvas":
                        draft["code_section"] = "# retained Code section\nnet.author_note = 'lesson'\n"
                        draft["layout"]["nodes"] = {"r": {"x": 100, "y": 200}}
                    else:
                        draft["files"] = {"frr-config/r1/frr.conf": "! café\r\nrouter ospf\r\n"}
                        draft["layout"] = {"version": 1, "nodes": {"r1": {"x": 100, "y": 200}}}
                    saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                        "revision": draft["revision"], "document": draft})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    draft = saved.json()
                    downloaded = await client.get(f"/api/authoring/drafts/{draft['id']}/python.zip")
                    self.assertEqual(downloaded.status_code, 200, downloaded.text)
                    slug = "lesson" if mode == "python" else "routed-lesson"
                    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                        prefix = slug + "/"
                        names = archive.namelist()
                        self.assertTrue(all(name.startswith(prefix) for name in names))
                        manifest = json.loads(archive.read(prefix + "iplab-package.json"))
                        self.assertEqual({key: manifest[key] for key in (
                            "format", "version", "source_mode", "root", "entrypoint", "definition")}, {
                                "format": "mininet-iplab", "version": 1, "source_mode": mode,
                                "root": slug, "entrypoint": slug + ".py", "definition": "definition.json"})
                        self.assertEqual(set(manifest["files"]),
                                         {name[len(prefix):] for name in names} - {"iplab-package.json"})
                        definition = json.loads(archive.read(prefix + "definition.json"))
                        self.assertEqual(definition["catalog"], {
                            key: draft["catalog"][key] for key in ("name", "description", "protocols", "area")
                            if key in draft["catalog"]})
                        self.assertTrue(set(definition).isdisjoint({"id", "revision", "created_at", "updated_at",
                                                                  "published_revision", "python", "files", "guide", "lesson_files"}))
                        self.assertEqual(archive.read(prefix + f"examples/guides/{slug}.md"),
                                         draft["guide"].encode("utf-8"))
                        for path, record in draft["lesson_files"].items():
                            expected = record["content"].encode("utf-8") if record["encoding"] == "utf-8" else base64.b64decode(record["content"])
                            self.assertEqual(archive.read(prefix + path), expected)
                            self.assertEqual(manifest["files"][path], {
                                "role": record.get("role", "build"), "encoding": record["encoding"]})
                        script = archive.read(prefix + slug + ".py")
                        if mode == "python":
                            self.assertEqual(script, draft["python"].encode("utf-8"))
                            self.assertEqual(archive.read(prefix + "frr-config/r1/frr.conf"),
                                             draft["files"]["frr-config/r1/frr.conf"].encode("utf-8"))
                            self.assertNotIn("layout", definition)
                            self.assertEqual(manifest["generated_artifacts"], {})
                        else:
                            for field in ("topology", "layout", "code_section", "address_family", "persistent_config"):
                                self.assertEqual(definition[field], draft[field])
                            self.assertEqual(set(manifest["generated_artifacts"]), {
                                slug + ".py", "frr-config/r1/frr.conf", f"examples/layouts/{slug}.json"})
                            for path, digest in manifest["generated_artifacts"].items():
                                self.assertEqual(digest, hashlib.sha256(archive.read(prefix + path)).hexdigest())
                            self.assertIn(b"    # retained Code section\n    net.author_note = 'lesson'", script)
                        self.assertEqual(manifest["files"][slug + ".py"], {"role": "script", "encoding": "utf-8"})
                        self.assertIn("--enable-web", archive.read(prefix + "README.md").decode())
                    self.assertEqual((await client.get(f"/api/authoring/drafts/{draft['id']}")).json(), draft)

        self._as("instructor", exercise)

    def test_provider_rejects_oversized_archive_and_legacy_seed_aliases(self) -> None:
        from mniplab.webserver.authoring import AuthoringRepository
        from mniplab.webserver.authoring_package import export_package
        from mniplab.webserver.authoring_python import package_archive

        repository = AuthoringRepository(self.storage.name)
        saved = repository.create_python(
            {"name": "Bounded lesson", "description": "", "protocols": []},
            "def build_network():\n    return None\n", {}, "", {"version": 1, "nodes": {}})
        oversized = {**saved, "files": {
            f"frr-config/r{index}/frr.conf": "\U0001f9ea" * 100_000 for index in range(160)}}
        with self.assertRaisesRegex(ValueError, "64,000,000 expanded bytes"):
            export_package(repository, oversized)
        aliases = {**saved, "files": {
            "frr-config/R1/frr.conf": "first\n", "frr-config/r1/frr.conf": "second\n"}}
        with self.assertRaisesRegex(ValueError, "collides"):
            package_archive(repository, aliases)
        self.assertEqual(repository.read(saved["id"]), saved)

    def test_native_download_requires_instructor(self) -> None:
        async def instructor(client: httpx2.AsyncClient) -> None:
            self.draft_id = (await self._python_draft(client))["id"]

        async def learner(client: httpx2.AsyncClient) -> None:
            response = await client.get(f"/api/authoring/drafts/{self.draft_id}/python.zip")
            self.assertEqual(response.status_code, 403, response.text)

        self._as("instructor", instructor)
        self._as("learner", learner)

    def test_native_import_reopens_complete_recipes_without_server_authority(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            for original in (await self._router_draft(client), await self._python_draft(client)):
                with self.subTest(mode=original.get("kind", "canvas")):
                    original["guide"] = "# Import café\r\n"
                    original["lesson_files"] = {
                        "helpers/lesson.py": {"encoding": "utf-8", "content": "# café\r\nN = 2\r\n", "role": "build"},
                        "presentation/diagram.png": {"encoding": "base64", "content": "AP9cAA0K", "role": "presentation"},
                    }
                    if original.get("kind") == "python":
                        original["files"] = {"frr-config/r1/frr.conf": "! Seed\r\n"}
                    else:
                        original["code_section"] = "# authored Code\nnet.note = 'café'\n"
                    saved = await client.put(f"/api/authoring/drafts/{original['id']}", json={
                        "revision": original["revision"], "document": original})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    original = saved.json()
                    exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
                    self.assertEqual(exported.status_code, 200, exported.text)
                    imported = await client.post("/api/authoring/drafts/import", files={
                        "file": ("lesson.zip", exported.content, "application/zip")})
                    self.assertEqual(imported.status_code, 201, imported.text)
                    draft = imported.json()
                    self.assertNotEqual(draft["id"], original["id"])
                    self.assertEqual(draft["revision"], 0)
                    self.assertIsNone(draft["published_revision"])
                    self.assertIsNone(draft["catalog"]["id"])
                    self.assertFalse(draft["preview_valid"])
                    self.assertFalse(draft["can_update"])
                    for field in ("catalog", "guide", "layout", "lesson_files"):
                        self.assertEqual(draft[field], original[field])
                    for field in (("kind", "python", "files") if original.get("kind") == "python"
                                  else ("topology", "code_section", "persistent_config", "address_family")):
                        self.assertEqual(draft[field], original[field])
                    reopened = await client.get(f"/api/authoring/drafts/{draft['id']}")
                    self.assertEqual(reopened.json(), draft)
                    self.assertEqual((await client.get(f"/api/authoring/drafts/{original['id']}")).json(), original)
        self._as("instructor", exercise)

    def test_changed_canvas_artifacts_require_explicit_preserving_python_choice(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._router_draft(client)
            original["lesson_files"] = {"data/input.bin": {"encoding": "base64", "content": "AP8="}}
            saved = await client.put(f"/api/authoring/drafts/{original['id']}", json={
                "revision": original["revision"], "document": original})
            original = saved.json()
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            edits = {
                "routed-lesson.py": b"# external edit\r\ndef build_network(:\r\n",
                "frr-config/r1/frr.conf": b"! external Seed\r\nrouter ospf\r\n",
                "examples/layouts/routed-lesson.json": b'{"version":1,"nodes":{"r1":{"x":900,"y":100}}}',
            }
            for relative, body in edits.items():
                for rewrite_digest in (False, True):
                    with self.subTest(path=relative, rewrite_digest=rewrite_digest):
                        members = package_members(exported.content)
                        members["routed-lesson/" + relative] = body
                        if rewrite_digest:
                            manifest = json.loads(members["routed-lesson/iplab-package.json"])
                            manifest["generated_artifacts"][relative] = hashlib.sha256(body).hexdigest()
                            members["routed-lesson/iplab-package.json"] = json.dumps(manifest).encode()
                        edited = package_zip(members)
                        before = (await client.get("/api/authoring/drafts")).json()
                        response = await client.post("/api/authoring/drafts/import", files={
                            "file": ("edited.zip", edited, "application/zip")})
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertEqual(response.json()["code"], "python_import_choice_required")
                        self.assertEqual(response.json()["choices"], ["python"])
                        self.assertEqual(response.json()["changed_files"], [relative])
                        self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
                        accepted = await client.post("/api/authoring/drafts/import?source=python", files={
                            "file": ("edited.zip", edited, "application/zip")})
                        self.assertEqual(accepted.status_code, 201, accepted.text)
                        draft = accepted.json()
                        self.assertEqual(draft["kind"], "python")
                        self.assertEqual(draft["python"].encode(), members["routed-lesson/routed-lesson.py"])
                        self.assertEqual(draft["files"]["frr-config/r1/frr.conf"].encode(),
                                         members["routed-lesson/frr-config/r1/frr.conf"])
                        self.assertEqual(draft["layout"], json.loads(members[
                            "routed-lesson/examples/layouts/routed-lesson.json"]))
                        self.assertEqual(draft["lesson_files"]["data/input.bin"]["content"], "AP8=")
                        self.assertIsNone(draft["catalog"]["id"])
                        self.assertFalse(draft["preview_valid"])
                        if relative.endswith(".py"):
                            self.assertIsNotNone(draft["python_error"])
            self.assertEqual((await client.get(f"/api/authoring/drafts/{original['id']}")).json(), original)
        self._as("instructor", exercise)

    def test_import_rejects_invalid_native_metadata_before_creating_any_draft(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._router_draft(client)
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            base = package_members(exported.content)
            prefix = "routed-lesson/"
            manifest = json.loads(base[prefix + "iplab-package.json"])
            definition = json.loads(base[prefix + "definition.json"])
            variants = []
            for key, value in (("version", True), ("version", 2), ("format", "other"),
                               ("source_mode", "other"), ("root", "other"),
                               ("entrypoint", "other.py"), ("files", []),
                               ("generated_artifacts", {})):
                variants.append((key, {**base, prefix + "iplab-package.json": json.dumps({**manifest, key: value}).encode()}))
            for field in ("role", "encoding"):
                bad_records = {**manifest["files"], "routed-lesson.py": {
                    **manifest["files"]["routed-lesson.py"], field: []}}
                variants.append(("unhashable-" + field, {**base,
                    prefix + "iplab-package.json": json.dumps({**manifest, "files": bad_records}).encode()}))
            variants += [
                ("schema", {**base, prefix + "definition.json": json.dumps({**definition, "schema_version": 4}).encode()}),
                ("catalog-name", {**base, prefix + "definition.json": json.dumps({**definition, "catalog": {**definition["catalog"], "name": 5}}).encode()}),
                ("runtime-layout", {**base, prefix + "examples/layouts/routed-lesson.json": b'{"version":1,"nodes":[]}'}),
                ("duplicate-manifest-key", {**base, prefix + "iplab-package.json": b'{"version":1,' + base[prefix + "iplab-package.json"][1:]}),
                ("duplicate-definition-key", {**base, prefix + "definition.json": b'{"catalog":{},' + base[prefix + "definition.json"][1:]}),
                ("nonfinite-definition", {**base, prefix + "definition.json": base[prefix + "definition.json"].replace(b'"layout": {', b'"layout": {"bad": NaN,')}),
                ("duplicate-layout-key", {**base, prefix + "examples/layouts/routed-lesson.json": b'{"version":1,"nodes":{},"nodes":{}}'}),
                ("invalid-utf8", {**base, prefix + "examples/guides/routed-lesson.md": b'\xff'}),
                ("unlisted", {**base, prefix + "unlisted.py": b'print("not native")'}),
                ("missing", {key: value for key, value in base.items() if key != prefix + "routed-lesson.py"}),
                ("legacy-zip", {"legacy/lab.py": b'print("not native")'}),
            ]
            bad_node: dict[str, list[Any]]
            for bad_node in ({"type": []}, {"protocols": [[]]}):
                bad_topology = {**definition["topology"], "nodes": [
                    {**definition["topology"]["nodes"][0], **bad_node},
                    *definition["topology"]["nodes"][1:]]}
                variants.append(("unhashable-topology-" + next(iter(bad_node)), {**base,
                    prefix + "definition.json": json.dumps({**definition, "topology": bad_topology}).encode()}))
            bad_seed = {**manifest, "files": {**manifest["files"], "not-a-seed.py": {"role": "seed", "encoding": "utf-8"}},
                        "generated_artifacts": {**manifest["generated_artifacts"], "not-a-seed.py": "0" * 64}}
            variants.append(("false-seed", {**base, prefix + "not-a-seed.py": b"# no\n",
                                           prefix + "iplab-package.json": json.dumps(bad_seed).encode()}))
            before = (await client.get("/api/authoring/drafts")).json()
            for case, members in variants:
                with self.subTest(case=case):
                    response = await client.post("/api/authoring/drafts/import", files={
                        "file": ("invalid.zip", package_zip(members), "application/zip")})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
        self._as("instructor", exercise)

    def test_import_rejects_corrupt_deflate_and_canonical_archive_collisions(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._python_draft(client)
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            base = package_members(exported.content)
            before = (await client.get("/api/authoring/drafts")).json()
            storage_before = {str(path.relative_to(self.storage.name)): path.read_bytes()
                              for path in Path(self.storage.name).rglob("*") if path.is_file()}
            variants = [("not-zip", b"not a zip"), ("truncated", exported.content[:-20])]
            for name in ("lesson/../escape.py", "/absolute.py", "lesson//empty.py",
                         "lesson/./dot.py", "lesson/back\\slash.py", "lesson/C:drive.py",
                         "lesson/hidden/.file", "lesson/percent%2f.py", "other/file.py",
                         "lesson/LESSON.py", "lesson/examples/Guides/other.md",
                         "lesson/lesson.py/child", "lesson/examples", "lesson/nul\x00evil.py"):
                variants.append((name, package_zip({**base, name: b"# invalid"})))
            for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK, stat.S_IFDIR):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for name, body in base.items():
                        archive.writestr(name, body)
                    info = zipfile.ZipInfo("lesson/special")
                    info.create_system = 3
                    info.external_attr = (kind | 0o644) << 16
                    archive.writestr(info, b"target")
                variants.append((f"special-{kind}", buffer.getvalue()))
            for name in ("lesson/lesson.py", "lesson/examples/", "lesson/EXAMPLES/", "lesson/lesson.py/"):
                buffer = io.BytesIO(exported.content)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    with zipfile.ZipFile(buffer, "a") as archive:
                        if name.endswith("/") and name == "lesson/examples/":
                            archive.writestr(name, b"")
                        archive.writestr(name, b"" if name.endswith("/") else b"# duplicate")
                variants.append(("duplicate-or-directory-alias-" + name, buffer.getvalue()))
            corrupted = bytearray(exported.content)
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                member = archive.getinfo("lesson/lesson.py")
                name_size, extra_size = struct.unpack_from("<HH", corrupted, member.header_offset + 26)
                payload_offset = member.header_offset + 30 + name_size + extra_size
            corrupted[payload_offset] = 0x07  # Invalid DEFLATE block type, before CRC validation.
            variants.append(("corrupt-deflate", bytes(corrupted)))
            for case, data in variants:
                with self.subTest(case=case):
                    response = await client.post("/api/authoring/drafts/import", files={
                        "file": ("invalid.zip", data, "application/zip")})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
                    self.assertEqual({str(path.relative_to(self.storage.name)): path.read_bytes()
                                      for path in Path(self.storage.name).rglob("*") if path.is_file()}, storage_before)
        self._as("instructor", exercise)

    def test_import_preserves_maximum_relative_path_without_counting_the_root(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            path = "/".join(("a" * 100, "b" * 100, "c" * 38))
            draft["lesson_files"] = {path: {"encoding": "utf-8", "content": "exact\r\n"}}
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            exported = await client.get(f"/api/authoring/drafts/{draft['id']}/python.zip")
            self.assertEqual(exported.status_code, 200, exported.text)
            imported = await client.post("/api/authoring/drafts/import", files={
                "file": ("paths.zip", exported.content, "application/zip")})
            self.assertEqual(imported.status_code, 201, imported.text)
            self.assertEqual(imported.json()["lesson_files"][path]["content"], "exact\r\n")
        self._as("instructor", exercise)

    def test_import_strips_forged_authority_and_keeps_unfinished_source_without_execution(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._python_draft(client)
            original["lesson_files"] = {"data/input.bin": {"encoding": "base64", "content": "AP8="}}
            saved = await client.put(f"/api/authoring/drafts/{original['id']}", json={
                "revision": original["revision"], "document": original})
            original = saved.json()
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            base = package_members(exported.content)
            marker = Path(self.storage.name, "must-not-execute")
            source = f"from pathlib import Path\r\nPath({str(marker)!r}).write_text('executed')\r\n"
            for unfinished in (False, True):
                with self.subTest(unfinished=unfinished):
                    members = dict(base)
                    text = source + ("def unfinished(:\r\n" if unfinished else "")
                    members["lesson/lesson.py"] = text.encode()
                    forged = {"id": original["id"], "revision": 99, "created_at": 1,
                              "updated_at": 1, "published_revision": 99,
                              "preview_valid": True, "can_update": True,
                              "preview_evidence": {"current": True},
                              "publication_origin": "authored-" + "a" * 32,
                              "assignments": [{"group": "learner1"}], "labs": ["fake"],
                              "config_copies": {"learner1": "injected"}}
                    for filename in ("definition.json", "iplab-package.json"):
                        metadata = json.loads(members["lesson/" + filename])
                        metadata.update(forged)
                        if filename == "definition.json":
                            metadata["catalog"]["id"] = "authored-" + "a" * 32
                        members["lesson/" + filename] = json.dumps(metadata).encode()
                    response = await client.post("/api/authoring/drafts/import", files={
                        "file": ("authority.zip", package_zip(members), "application/zip")})
                    self.assertEqual(response.status_code, 201, response.text)
                    draft = response.json()
                    self.assertEqual(draft["python"].encode(), text.encode())
                    self.assertEqual(draft["revision"], 0)
                    self.assertNotEqual(draft["id"], original["id"])
                    self.assertIsNone(draft["published_revision"])
                    self.assertIsNone(draft["catalog"]["id"])
                    self.assertFalse(draft["preview_valid"])
                    self.assertFalse(draft["can_update"])
                    self.assertEqual(draft["python_error"] is not None, unfinished)
                    self.assertTrue(set(draft).isdisjoint({"assignments", "labs", "config_copies",
                                                          "preview_evidence", "publication_origin"}))
                    self.assertFalse(marker.exists())
                    self.assertEqual((await client.get(f"/api/authoring/drafts/{original['id']}")).json(), original)
            self.assertEqual(list(Path(self.storage.name).rglob("*.py")), [])
            self.package_bytes = exported.content
        self._as("instructor", exercise)

        async def learner(client: httpx2.AsyncClient) -> None:
            before = {str(path): path.read_bytes() for path in Path(self.storage.name).rglob("*") if path.is_file()}
            for query in ("", "?source=python"):
                response = await client.post("/api/authoring/drafts/import" + query, files={
                    "file": ("lesson.zip", self.package_bytes, "application/zip")})
                self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual({str(path): path.read_bytes() for path in Path(self.storage.name).rglob("*") if path.is_file()}, before)
        self._as("learner", learner)

    def test_import_measures_real_archive_limits_before_any_materialization(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._python_draft(client)
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            base = package_members(exported.content)
            # A maximum-sized generated README is allowed; per-file limits do
            # not count ZIP headers or the top-level directory name.
            response = await client.post("/api/authoring/drafts/import", files={
                "file": ("maximum.zip", package_zip({**base, "lesson/README.md": b"x" * 4_000_000}), "application/zip")})
            self.assertEqual(response.status_code, 201, response.text)
            directories = {f"lesson/empty{i}/": b"" for i in range(512 - len(base))}
            response = await client.post("/api/authoring/drafts/import", files={
                "file": ("512.zip", package_zip({**base, **directories}), "application/zip")})
            self.assertEqual(response.status_code, 201, response.text)
            before = (await client.get("/api/authoring/drafts")).json()
            files_before = {str(path): path.read_bytes() for path in Path(self.storage.name).rglob("*") if path.is_file()}
            variants = [
                ("file", package_zip({**base, "lesson/README.md": b"x" * 4_000_001}), "4,000,000"),
                ("entries", package_zip({**base, **directories, "lesson/extra/": b""}), "512"),
                ("expanded", package_zip({**base, **{f"lesson/blob{i}": b"x" * 4_000_000 for i in range(16)}}), "64,000,000"),
                ("compressed", package_zip({**base, **{f"lesson/blob{i}": b"x" * 4_000_000 for i in range(8)}}, zipfile.ZIP_STORED), "32,000,000"),
            ]
            for case, archive, message in variants:
                with self.subTest(case=case):
                    response = await client.post("/api/authoring/drafts/import", files={
                        "file": ("large.zip", archive, "application/zip")})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn(message, response.json()["detail"])
                    self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
                    self.assertEqual({str(path): path.read_bytes() for path in Path(self.storage.name).rglob("*") if path.is_file()}, files_before)
        self._as("instructor", exercise)

    def test_import_rejects_forged_uncompressed_sizes_even_with_matching_prefix_crc(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._python_draft(client)
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            base = package_members(exported.content)
            before = (await client.get("/api/authoring/drafts")).json()
            for body in (b"x" * 4_000_001, b"xyz hidden decoded bytes"):
                with self.subTest(actual_bytes=len(body)):
                    archive_bytes = bytearray(package_zip({**base, "lesson/README.md": body}))
                    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                        local = archive.getinfo("lesson/README.md").header_offset
                    crc = binascii.crc32(body[:1])
                    struct.pack_into("<I", archive_bytes, local + 14, crc)
                    struct.pack_into("<I", archive_bytes, local + 22, 1)
                    central = archive_bytes.index(b"PK\x01\x02")
                    while True:
                        name_size, extra_size, comment_size = struct.unpack_from("<HHH", archive_bytes, central + 28)
                        name = bytes(archive_bytes[central + 46:central + 46 + name_size])
                        if name == b"lesson/README.md":
                            struct.pack_into("<I", archive_bytes, central + 16, crc)
                            struct.pack_into("<I", archive_bytes, central + 24, 1)
                            break
                        central += 46 + name_size + extra_size + comment_size
                    response = await client.post("/api/authoring/drafts/import", files={
                        "file": ("forged.zip", bytes(archive_bytes), "application/zip")})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
        self._as("instructor", exercise)

    def test_failed_import_write_removes_its_new_directory_and_preserves_existing_drafts(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._python_draft(client)
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            before = (await client.get("/api/authoring/drafts")).json()
            paths_before = {str(path.relative_to(self.storage.name)) for path in Path(self.storage.name).rglob("*")}
            # Filesystem replacement is the real external write boundary. A
            # failed commit must remove the directory created for this import.
            with patch("os.replace", side_effect=OSError("simulated full filesystem")):
                response = await client.post("/api/authoring/drafts/import", files={
                    "file": ("lesson.zip", exported.content, "application/zip")})
            self.assertEqual(response.status_code, 500, response.text)
            self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
            self.assertEqual({str(path.relative_to(self.storage.name)) for path in Path(self.storage.name).rglob("*")}, paths_before)
        self._as("instructor", exercise)

    def test_canvas_definition_edits_cannot_authorize_overwriting_archived_source(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._router_draft(client)
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            members = package_members(exported.content)
            definition = json.loads(members["routed-lesson/definition.json"])
            marker = Path(self.storage.name, "must-not-run-canvas")
            definition["code_section"] = f"open({str(marker)!r}, 'w').write('executed')\n"
            members["routed-lesson/definition.json"] = json.dumps(definition).encode()
            before = (await client.get("/api/authoring/drafts")).json()
            response = await client.post("/api/authoring/drafts/import", files={
                "file": ("definition-edit.zip", package_zip(members), "application/zip")})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["changed_files"], ["routed-lesson.py"])
            self.assertFalse(marker.exists())
            self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
            response = await client.post("/api/authoring/drafts/import?source=python", files={
                "file": ("definition-edit.zip", package_zip(members), "application/zip")})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["python"].encode(), members["routed-lesson/routed-lesson.py"])
            self.assertNotIn("code_section", response.json())
            self.assertFalse(marker.exists())
        self._as("instructor", exercise)

    def test_import_bounds_central_entry_count_before_eager_zip_metadata_allocation(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            oversized = package_zip({f"lesson/entry{i}/": b"" for i in range(513)})
            forged = bytearray(oversized)
            footer = forged.rindex(b"PK\x05\x06")
            struct.pack_into("<HH", forged, footer + 8, 1, 1)
            for data in (oversized, bytes(forged)):
                with self.subTest(forged_count=data != oversized):
                    # ZipFile eagerly allocates central records. For an oversized
                    # directory, the preflight must refuse before that boundary.
                    with patch("zipfile.ZipFile", side_effect=AssertionError("eager metadata allocation")):
                        response = await client.post("/api/authoring/drafts/import", files={
                            "file": ("too-many.zip", data, "application/zip")})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn("512", response.json()["detail"])
                    self.assertEqual((await client.get("/api/authoring/drafts")).json(), {"drafts": []})
        self._as("instructor", exercise)

    def test_import_retains_source_when_the_compiler_reaches_a_diagnostic_limit(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            original = await self._python_draft(client)
            exported = await client.get(f"/api/authoring/drafts/{original['id']}/python.zip")
            members = package_members(exported.content)
            source = "x=" + "+" * 10_000 + "1\n"
            members["lesson/lesson.py"] = source.encode()
            response = await client.post("/api/authoring/drafts/import", files={
                "file": ("unfinished.zip", package_zip(members), "application/zip")})
            self.assertEqual(response.status_code, 201, response.text)
            draft = response.json()
            self.assertEqual(draft["python"], source)
            self.assertIsNotNone(draft["python_error"])
            self.assertFalse(draft["preview_valid"])
            self.assertEqual((await client.get(f"/api/authoring/drafts/{draft['id']}")).json(), draft)
        self._as("instructor", exercise)

    def test_unchanged_canvas_import_keeps_complete_generated_maximum_body(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            draft["topology"]["nodes"][0]["frr_config"] = "!" * 100_000
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            exported = await client.get(f"/api/authoring/drafts/{draft['id']}/python.zip")
            self.assertEqual(exported.status_code, 200, exported.text)
            response = await client.post("/api/authoring/drafts/import", files={
                "file": ("canvas.zip", exported.content, "application/zip")})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["topology"], draft["topology"])
            self.assertNotIn("kind", response.json())
        self._as("instructor", exercise)

    def test_maximum_canvas_seed_survives_continuation_native_import_and_python_choice(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client)
            router = draft["topology"]["nodes"][0]
            router.update(name="router01", protocols=["OSPF", "OSPF6"],
                          frr_config="!" * 100_000)
            template = draft["topology"]["links"][0]
            draft["topology"]["links"] = [
                {"id": f"link-{port}", "ends": [
                    {**end, "interface_id": f"if-{2 * port + index:012x}",
                     "port": port, "ipv4": "", "ipv6": ""}
                    for index, end in enumerate(template["ends"])
                ]} for port in range(256)
            ]
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            original = saved.json()
            exported = await client.get(f"/api/authoring/drafts/{draft['id']}/python.zip")
            self.assertEqual(exported.status_code, 200, exported.text)
            members = package_members(exported.content)
            seed_path = "frr-config/router01/frr.conf"
            seed_bytes = members["routed-lesson/" + seed_path]
            self.assertGreater(len(seed_bytes), 100_000)
            self.assertIn(b"interface router01-eth255", seed_bytes)
            self.assertTrue(seed_bytes.endswith(b"!" * 100_000 + b"\n"))

            continued = await client.post(
                f"/api/authoring/drafts/{draft['id']}/continue-in-python",
                json={"revision": original["revision"]})
            self.assertEqual(continued.status_code, 201, continued.text)
            self.assertEqual(continued.json()["files"][seed_path].encode(), seed_bytes)
            native = await client.post("/api/authoring/drafts/import", files={
                "file": ("canvas.zip", exported.content, "application/zip")})
            self.assertEqual(native.status_code, 201, native.text)
            self.assertEqual(native.json()["topology"], original["topology"])
            self.assertNotIn("kind", native.json())
            native_export = await client.get(f"/api/authoring/drafts/{native.json()['id']}/python.zip")
            self.assertEqual(native_export.status_code, 200, native_export.text)
            self.assertEqual(package_members(native_export.content), members)

            members["routed-lesson/routed-lesson.py"] += b"# external edit\r\n"
            changed = package_zip(members)
            choice = await client.post("/api/authoring/drafts/import", files={
                "file": ("edited.zip", changed, "application/zip")})
            self.assertEqual(choice.status_code, 409, choice.text)
            fallback = await client.post("/api/authoring/drafts/import?source=python", files={
                "file": ("edited.zip", changed, "application/zip")})
            self.assertEqual(fallback.status_code, 201, fallback.text)
            self.assertEqual(fallback.json()["files"][seed_path].encode(), seed_bytes)
            self.assertEqual(fallback.json()["python"].encode(), members["routed-lesson/routed-lesson.py"])
            self.assertEqual((await client.get(f"/api/authoring/drafts/{draft['id']}")).json(), original)
        self._as("instructor", exercise)

    def test_python_seed_allowance_is_finite_and_shared_by_save_and_import(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._python_draft(client)
            path = "frr-config/r1/frr.conf"
            draft["files"] = {path: "!" * 150_000}
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            accepted = saved.json()
            exported = await client.get(f"/api/authoring/drafts/{draft['id']}/python.zip")
            self.assertEqual(exported.status_code, 200, exported.text)
            imported = await client.post("/api/authoring/drafts/import", files={
                "file": ("maximum.zip", exported.content, "application/zip")})
            self.assertEqual(imported.status_code, 201, imported.text)
            self.assertEqual(imported.json()["files"], draft["files"])
            rejected = {**accepted, "files": {path: "!" * 150_001}}
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": accepted["revision"], "document": rejected})
            self.assertEqual(saved.status_code, 422, saved.text)
            self.assertIn("150,000", saved.json()["detail"])
            members = package_members(exported.content)
            members["lesson/" + path] += b"!"
            before = (await client.get("/api/authoring/drafts")).json()
            imported = await client.post("/api/authoring/drafts/import", files={
                "file": ("oversized.zip", package_zip(members), "application/zip")})
            self.assertEqual(imported.status_code, 422, imported.text)
            self.assertIn("150,000", imported.json()["detail"])
            self.assertEqual((await client.get("/api/authoring/drafts")).json(), before)
            self.assertEqual((await client.get(f"/api/authoring/drafts/{draft['id']}")).json(), accepted)
        self._as("instructor", exercise)
