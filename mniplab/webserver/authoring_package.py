"""Versioned native lesson packages, independent of server-owned draft authority.

The ZIP contains one portable definition and the runnable files materialized by
AuthoringRepository. Canvas scripts, Seeds and runtime Layouts are generated
views; Python scripts and Seeds remain authoritative bytes. Import validates
the bounded archive and complete recipe before creating a fresh draft.
"""

import base64
import hashlib
import io
import json
import stat
import struct
from pathlib import Path
import tempfile
from typing import Any, Dict
import zipfile
import zlib

from mniplab.webserver.authoring import AuthoringRepository, router_frr_config
from mniplab.webserver.authoring_files import canonical_path, claim_path, file_role

PACKAGE_FORMAT = "mininet-iplab"
PACKAGE_VERSION = 1
MANIFEST_PATH = "iplab-package.json"
DEFINITION_PATH = "definition.json"
MAX_COMPRESSED_BYTES = 32_000_000
MAX_EXPANDED_BYTES = 64_000_000
MAX_FILE_BYTES = 4_000_000
MAX_ENTRIES = 512


def export_package(repository: AuthoringRepository, document: Dict[str, Any]) -> bytes:
    """Export a complete runnable lesson with its original editable source mode.

    Args:
        repository: Repository providing shared materialization and Layouts.
        document: A validated, buildable Canvas or Python definition.

    Returns:
        A ZIP with one script-named root and a versioned native manifest.

    Raises:
        ValueError: If the recipe is invalid or exceeds native package limits.
    """
    from mniplab.webserver.authoring_python import README, script_slug

    repository.validate_for_build(document)
    slug = script_slug(document["catalog"]["name"])
    script = f"{slug}.py"
    python = document.get("kind") == "python"
    guide = f"examples/guides/{slug}.md"
    layout = f"examples/layouts/{slug}.json"
    definition = {
        "schema_version": document["schema_version"],
        "catalog": {field: document["catalog"][field]
                    for field in ("name", "description", "protocols", "area")
                    if field in document["catalog"]},
    }
    if not python:
        for field in ("topology", "address_family", "persistent_config", "layout", "code_section"):
            if field in document:
                definition[field] = document[field]
    records = {
        script: {"role": "script", "encoding": "utf-8"},
        guide: {"role": "guide", "encoding": "utf-8"},
        layout: {"role": "layout", "encoding": "utf-8"},
        DEFINITION_PATH: {"role": "definition", "encoding": "utf-8"},
        "README.md": {"role": "readme", "encoding": "utf-8"},
    }
    for path, record in document.get("lesson_files", {}).items():
        records[path] = {"role": file_role(path, record), "encoding": record["encoding"]}
    if python:
        seeds = document["files"]
    else:
        seeds = {f"frr-config/{node['name']}/frr.conf": None
                 for node in document["topology"]["nodes"]
                 if node["type"] == "router" and document["persistent_config"]}
    for path in seeds:
        records[path] = {"role": "seed", "encoding": "utf-8"}
    # Check portable aliases/prefixes even for legacy Seed maps, whose original
    # schema predates the complete-file collision contract.
    claimed: set[str] = set()
    directories: Dict[str, str] = {}
    for path in [*records, MANIFEST_PATH]:
        claim_path(path, claimed, directories)
    if len(records) + 1 > MAX_ENTRIES:
        raise ValueError("Package exceeds 512 entries")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary, slug)
        repository.write_builder(document, str(root / script))
        for relative, data in (
            (guide, document["guide"].encode("utf-8")),
            (layout, json.dumps(repository.layout_for_runtime(document), indent=2).encode("utf-8")),
            (DEFINITION_PATH, json.dumps(definition, ensure_ascii=False, indent=2).encode("utf-8")),
            ("README.md", README.format(name=document["catalog"]["name"], script=script).encode("utf-8")),
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        generated = {}
        expanded = 0
        for relative, record in sorted(records.items()):
            path = root / relative
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise ValueError("Package file exceeds 4,000,000 bytes")
            expanded += size
            if not python and record["role"] in {"script", "seed", "layout"}:
                generated[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {
            "format": PACKAGE_FORMAT, "version": PACKAGE_VERSION,
            "source_mode": "python" if python else "canvas", "root": slug,
            "entrypoint": script, "definition": DEFINITION_PATH,
            "files": dict(sorted(records.items())), "generated_artifacts": generated,
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        if len(manifest_bytes) > MAX_FILE_BYTES:
            raise ValueError("Package manifest exceeds 4,000,000 bytes")
        if expanded + len(manifest_bytes) > MAX_EXPANDED_BYTES:
            raise ValueError("Package exceeds 64,000,000 expanded bytes")
        (root / MANIFEST_PATH).write_bytes(manifest_bytes)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted([*records, MANIFEST_PATH]):
                archive.write(root / relative, f"{slug}/{relative}")
        result = buffer.getvalue()
        if len(result) > MAX_COMPRESSED_BYTES:
            raise ValueError("Package exceeds 32,000,000 compressed bytes")
        return result


def _json_object(data: bytes) -> Dict[str, Any]:
    """Read strict UTF-8 JSON, rejecting ambiguous keys and non-JSON numbers."""
    def pairs(items: list[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Package JSON contains duplicate keys")
            result[key] = value
        return result

    def constant(_value: str) -> Any:
        raise ValueError("Package JSON contains a non-JSON number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Package metadata must be bounded UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Package metadata must be a JSON object")
    return value


def _read_member(archive: zipfile.ZipFile, data: bytes,
                 entry: zipfile.ZipInfo, limit: int) -> bytes:
    """Measure stored/DEFLATE bytes independently of untrusted declared sizes."""
    # Retain ZipFile's local-name, encryption and overlapping-entry checks. Its
    # reader truncates output to the declared size, so it cannot measure ours.
    with archive.open(entry):
        pass
    header = struct.unpack_from("<4s5H3I2H", data, entry.header_offset)
    if header[2] != entry.flag_bits or header[3] != entry.compress_type:
        raise ValueError("ZIP local and central metadata disagree")
    if not entry.flag_bits & 8 and header[6:9] != (
            entry.CRC, entry.compress_size, entry.file_size):
        raise ValueError("ZIP local and central sizes or CRC disagree")
    offset = entry.header_offset + 30 + header[9] + header[10]
    end = offset + entry.compress_size
    if end > len(data):
        raise ValueError("Truncated ZIP member")
    payload = data[offset:end]
    if entry.compress_type == zipfile.ZIP_STORED:
        body = payload
    else:
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        body = decoder.decompress(payload, limit + 1)
        if len(body) <= limit and (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
            raise ValueError("Invalid or trailing DEFLATE payload")
    if len(body) > limit:
        if limit == MAX_FILE_BYTES:
            raise ValueError("Package file exceeds 4,000,000 bytes")
        raise ValueError("Package exceeds 64,000,000 expanded bytes or has a nonempty directory")
    if len(body) != entry.file_size or zlib.crc32(body) != entry.CRC:
        raise ValueError("ZIP measured size or CRC disagrees with metadata")
    return body


def _check_directory_bound(data: bytes) -> None:
    """Bound central records before ZipFile eagerly constructs their objects."""
    footer = data.rfind(b"PK\x05\x06", max(0, len(data) - 65_557))
    if footer < 0 or footer + 22 > len(data):
        raise ValueError("Invalid native lesson ZIP footer")
    end = struct.unpack_from("<4s4H2IH", data, footer)
    if (end[1] or end[2] or end[3] != end[4]
            or end[4] == 0xFFFF or end[5] == 0xFFFFFFFF or end[6] == 0xFFFFFFFF
            or footer + 22 + end[7] != len(data)):
        raise ValueError("Native packages require a single-disk ZIP without ZIP64 or trailing data")
    if not 1 <= end[4] <= MAX_ENTRIES:
        raise ValueError("Package must have between 1 and 512 entries")
    cursor = end[6]
    boundary = cursor + end[5]
    if boundary != footer:
        raise ValueError("Invalid native ZIP central directory extent")
    count = 0
    while cursor < boundary:
        count += 1
        if count > MAX_ENTRIES:
            raise ValueError("Package exceeds 512 central directory entries")
        if cursor + 46 > boundary or data[cursor:cursor + 4] != b"PK\x01\x02":
            raise ValueError("Invalid native ZIP central directory header")
        lengths = struct.unpack_from("<HHH", data, cursor + 28)
        cursor += 46 + sum(lengths)
    if cursor != boundary or count != end[4]:
        raise ValueError("Native ZIP central directory count or lengths disagree")


def _read_archive(data: bytes) -> tuple[str, Dict[str, bytes]]:
    """Read canonical regular entries in bounded memory without extracting."""
    if len(data) > MAX_COMPRESSED_BYTES:
        raise ValueError("Package exceeds 32,000,000 compressed bytes")
    _check_directory_bound(data)
    contents: Dict[str, bytes] = {}
    files: set[str] = set()
    directories: Dict[str, str] = {}
    explicit_directories: set[str] = set()
    root = ""
    expanded = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ENTRIES:
                raise ValueError("Package must have between 1 and 512 entries")
            for entry in entries:
                name = entry.orig_filename
                directory = entry.is_dir()
                path = name[:-1] if directory else name
                parts = path.split("/")
                canonical_path(parts[0])
                relative = "/".join(parts[1:])
                if len(parts) > 1:
                    canonical_path(relative)
                if entry.filename != name or (root and parts[0] != root):
                    raise ValueError("Package must have one canonical root")
                root = parts[0]
                path = relative
                parts = relative.split("/") if relative else []
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (kind not in (0, stat.S_IFDIR if directory else stat.S_IFREG)
                        or (entry.external_attr & 0x10 and not directory)
                        or entry.flag_bits & 1
                        or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError("Package entries must be regular files or directories")
                if directory:
                    folded = path.casefold()
                    if (folded in explicit_directories or folded in files
                            or directories.get(folded, path) != path):
                        raise ValueError("Package directory entries collide")
                    # Claim each ancestor as a directory too, including empty trees.
                    for index in range(1, len(parts) + 1):
                        parent = "/".join(parts[:index])
                        key = parent.casefold()
                        if key in files or directories.get(key, parent) != parent:
                            raise ValueError("Package directory entries collide")
                        directories[key] = parent
                    explicit_directories.add(folded)
                    if entry.file_size or _read_member(archive, data, entry, 0):
                        raise ValueError("Package directories must be empty")
                    continue
                if not relative:
                    raise ValueError("Package files must be inside its root")
                claim_path(path, files, directories)
                if entry.file_size > MAX_FILE_BYTES:
                    raise ValueError("Package file exceeds 4,000,000 bytes")
                body = _read_member(archive, data, entry,
                                    min(MAX_FILE_BYTES, MAX_EXPANDED_BYTES - expanded))
                if len(body) > MAX_FILE_BYTES:
                    raise ValueError("Package file exceeds 4,000,000 bytes")
                expanded += len(body)
                if expanded > MAX_EXPANDED_BYTES:
                    raise ValueError("Package exceeds 64,000,000 expanded bytes")
                contents[path] = body
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, zlib.error, struct.error) as exc:
        raise ValueError("Invalid native lesson ZIP") from exc
    return root, contents


# Recognized server authority can be present in an untrusted transfer, but is
# never copied to a draft. Unknown recipe fields remain schema errors.
_AUTHORITY_FIELDS = {
    "id", "revision", "created_at", "updated_at", "published_revision",
    "preview_valid", "can_update", "python_error", "preview_evidence",
    "publication_origin", "publication_base", "assignments", "labs", "config_copies",
}


class PythonImportChoiceRequired(ValueError):
    """An externally changed generated view needs explicit Python authority."""

    def __init__(self, changed_files: list[str]) -> None:
        super().__init__("Generated lesson files changed; import in Python to preserve them")
        self.changed_files = changed_files


def _canvas_artifacts(repository: AuthoringRepository,
                      document: Dict[str, Any], root: str) -> Dict[str, bytes]:
    """Render trusted generator views in memory, without executing package code."""
    from mniplab.frrouter.config import FRRConfigBuilder
    from mniplab.webserver.authoring_python import generate_python

    result = {
        root + ".py": generate_python(document).encode("utf-8"),
        f"examples/layouts/{root}.json": json.dumps(
            repository.layout_for_runtime(document), indent=2).encode("utf-8"),
    }
    if document["persistent_config"]:
        builder = FRRConfigBuilder()
        template = str(Path(builder.template_dir, "frr.conf.mako"))
        for node in document["topology"]["nodes"]:
            if node["type"] == "router":
                result[f"frr-config/{node['name']}/frr.conf"] = builder.render_template(
                    template, {"name": node["name"], "daemons": [],
                               "frr_config": router_frr_config(node, document["topology"]["links"])}
                ).encode("utf-8")
    return result


def import_package(repository: AuthoringRepository, data: bytes,
                   source: str | None = None) -> Dict[str, Any]:
    """Validate a native ZIP and create one fresh unpublished source-mode draft.

    Args:
        repository: Repository receiving the new draft after complete validation.
        data: Compressed native ZIP bytes; package Python is never executed.
        source: Explicit ``python`` import, or None to retain the native mode.

    Returns:
        The new draft with fresh server authority and no Preview evidence.

    Raises:
        ValueError: The archive or its complete recipe is malformed or too large.
        PythonImportChoiceRequired: Generated Canvas views changed externally.
    """
    from mniplab.webserver.authoring_python import script_slug

    if source not in (None, "python"):
        raise ValueError("Import source must be python or omitted")
    root, contents = _read_archive(data)
    if MANIFEST_PATH not in contents:
        raise ValueError("Choose a native lesson package with iplab-package.json")
    manifest = _json_object(contents.pop(MANIFEST_PATH))
    required = {"format", "version", "source_mode", "root", "entrypoint",
                "definition", "files", "generated_artifacts"}
    if set(manifest) - _AUTHORITY_FIELDS != required:
        raise ValueError("Invalid native package manifest fields")
    if (manifest["format"] != PACKAGE_FORMAT or type(manifest["version"]) is not int
            or manifest["version"] != PACKAGE_VERSION):
        raise ValueError("Unsupported native package version or format")
    if manifest["source_mode"] not in ("canvas", "python"):
        raise ValueError("Invalid native source mode")
    if (manifest["root"] != root or manifest["entrypoint"] != root + ".py"
            or manifest["definition"] != DEFINITION_PATH):
        raise ValueError("Invalid native package root or entrypoint")
    records = manifest["files"]
    if not isinstance(records, dict) or set(records) != set(contents):
        raise ValueError("Package file inventory must be exhaustive")
    fixed = {
        root + ".py": "script", DEFINITION_PATH: "definition", "README.md": "readme",
        f"examples/guides/{root}.md": "guide", f"examples/layouts/{root}.json": "layout",
    }
    if not set(fixed) <= set(records):
        raise ValueError("Package is missing required lesson files")
    texts: Dict[str, str] = {}
    supporting = {}
    seeds = {}
    for path, record in records.items():
        if (not isinstance(record, dict) or set(record) != {"role", "encoding"}
                or record["encoding"] not in ("utf-8", "base64")):
            raise ValueError("Invalid package file metadata")
        role = record["role"]
        if path in fixed:
            if role != fixed[path] or record["encoding"] != "utf-8":
                raise ValueError("Invalid required file role or encoding")
        elif role == "seed":
            if record["encoding"] != "utf-8":
                raise ValueError("Seed files must be UTF-8")
        elif role not in ("build", "presentation"):
            raise ValueError("Invalid package file role")
        if record["encoding"] == "utf-8":
            try:
                texts[path] = contents[path].decode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Package text file must be UTF-8") from exc
        if role == "seed":
            seeds[path] = texts[path]
        elif role in ("build", "presentation"):
            supporting[path] = {
                "encoding": record["encoding"], "role": role,
                "content": texts[path] if record["encoding"] == "utf-8" else
                           base64.b64encode(contents[path]).decode("ascii"),
            }
    definition = _json_object(contents[DEFINITION_PATH])
    python = manifest["source_mode"] == "python"
    fields = {"schema_version", "catalog"}
    canvas_fields = {"topology", "address_family", "persistent_config", "layout"}
    if not python:
        fields |= canvas_fields
    if (not fields <= set(definition) - _AUTHORITY_FIELDS
            <= fields | ({"code_section"} if not python else set())
            or type(definition["schema_version"]) is not int
            or definition["schema_version"] != repository.SCHEMA_VERSION):
        raise ValueError("Unsupported package definition schema or fields")
    catalog = definition["catalog"]
    if not isinstance(catalog, dict) or set(catalog) - {"id", "area"} != {"name", "description", "protocols"}:
        raise ValueError("Invalid package Catalog metadata")
    document = {
        key: value for key, value in definition.items() if key not in _AUTHORITY_FIELDS
    }
    document.update({
        "id": "draft-import", "revision": 0, "created_at": 0, "updated_at": 0,
        "published_revision": None, "catalog": {**catalog, "id": None},
        "guide": texts[f"examples/guides/{root}.md"], "lesson_files": supporting,
    })
    runtime_layout = _json_object(contents[f"examples/layouts/{root}.json"])
    if python:
        document.update(kind="python", python=texts[root + ".py"], files=seeds,
                        layout=runtime_layout)
    repository.validate(document)
    if not python:
        # Canvas Seeds remain generated views with Canvas's own text limits.
        # Require their exact inventory; validate archived runtime presentation
        # independently before offering the preserving Python choice.
        seed_paths = {f"frr-config/{node['name']}/frr.conf"
                      for node in document["topology"]["nodes"]
                      if node["type"] == "router" and document["persistent_config"]}
        if set(seeds) != seed_paths:
            raise ValueError("Canvas Seed inventory must match its definition")
        portable = {key: value for key, value in document.items()
                    if key not in canvas_fields | {"code_section"}}
        portable.update(kind="python", python="", files={}, layout=runtime_layout)
        repository.validate(portable)
    if script_slug(catalog["name"]) != root:
        raise ValueError("Package root must match its Catalog name")
    generated = manifest["generated_artifacts"]
    expected_paths = {path for path, record in records.items()
                      if record["role"] in ("script", "seed", "layout")} if not python else set()
    if (not isinstance(generated, dict) or set(generated) != expected_paths
            or any(not isinstance(value, str) or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value)
                   for value in generated.values())):
        raise ValueError("Invalid generated artifact inventory or digest")
    if not python:
        expected = _canvas_artifacts(repository, document, root)
        changed = sorted(path for path in set(expected) | set(generated)
                         if path not in expected or path not in contents
                         or contents[path] != expected[path]
                         or generated.get(path) != hashlib.sha256(contents[path]).hexdigest())
        if changed and source != "python":
            raise PythonImportChoiceRequired(changed)
        if source == "python":
            for field in canvas_fields | {"code_section"}:
                document.pop(field, None)
            document.update(kind="python", python=texts[root + ".py"], files=seeds,
                            layout=runtime_layout)
            repository.validate(document)
    return repository.create_from_example(document)
