"""Persistent visual Lab Example drafts and their versioned definition."""

import copy
import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from mniplab.webserver.catalog import AREAS
from mniplab.constants import MAX_INTF_NAME_LEN, PROTO_MAP
from mniplab.dhcp.pool import Pool, Reservation
from mniplab.dhcp.relay import Relay
from mniplab.errors import ConfigurationError
from mniplab.dns import (
    ARecord, AAAARecord, CNAMERecord, MXRecord,
    NSRecord, PTRRecord, SOARecord, SecondaryZone, TXTRecord, Zone,
)
from mniplab.dns.diagnostics import normalize_nameservers
from mniplab.resolver import AccessRule, ResolverService
from mniplab.rpki import HostedCA, RPKICAService, ValidatorService
from mniplab.rpki.diagnostics import multiple_rpki_cas_message, validator_without_rpki_ca_message
from mniplab.rpki.models import ASPA, ROA, as_asn
from mniplab.routernat import PortForward, StaticNAT
from mniplab.switch import ManagedSwitch
from mniplab.logging import get_logger
from mniplab.startup import STAGES, check_timeout
from mniplab.webserver.authoring_files import (
    build_inputs, canonical_path, decode_file, file_role, validate_lesson_files,
)

#: The Protocols an Instructor can choose for a Router: the first name
#: ``PROTO_MAP`` gives each FRR Daemon, so aliases appear once.
ROUTER_PROTOCOLS = tuple(
    name for name, daemon in PROTO_MAP.items()
    if next(first for first, same in PROTO_MAP.items() if same == daemon) == name
)


logger = get_logger(__name__)


class DraftConflictError(Exception):
    """Raised when a stale editor tries to replace a newer draft revision."""


class DraftNotFoundError(Exception):
    """Raised when the requested shared Lab Example Draft does not exist."""


class DraftValidationError(ValueError):
    """Raised when an authored Lab Example Draft fails validation."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "draft",
        item_id: Optional[str] = None,
        control_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.target: Dict[str, Optional[str]] = {
            "kind": kind,
            "id": item_id,
            "controlId": control_id,
            "message": message,
        }



def authored_labs_root() -> str:
    """Return the shared, Git-ignored Instructor authoring directory."""
    configured = os.getenv("MNIPLAB_AUTHORED_LABS_DIR")
    checkout_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.abspath(configured or os.path.join(checkout_root, "authored-labs"))


class AuthoringRepository:
    """Read and atomically write versioned drafts under the authored-labs root."""

    SCHEMA_VERSION = 3
    _max_host_name_length = MAX_INTF_NAME_LEN - len("-eth255")
    _name_pattern = re.compile(
        rf"^[A-Za-z][A-Za-z0-9_-]{{0,{_max_host_name_length - 1}}}$"
    )
    _interface_pattern = re.compile(r"^if-[0-9a-f]{12}$")
    _vrf_pattern = re.compile(rf"^[A-Za-z][A-Za-z0-9_-]{{0,{MAX_INTF_NAME_LEN - 1}}}$")
    _mac_pattern = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
    _node_fields = {
        "host": {"id", "name", "type", "ipv4_gateway", "ipv6_gateway", "startup"},
        "container": {"id", "name", "type", "image", "ipv4_gateway",
                      "ipv6_gateway", "startup"},
        "nat": {"id", "name", "type", "subnet", "ip"},
        "transit": {"id", "name", "type", "subnet", "ip", "downstream_ids"},
        "speaker": {"id", "name", "type", "local_as", "neighbors", "routes", "startup"},
        "router": {"id", "name", "type", "protocols", "frr_config",
                   "fast_timers", "startup"},
        # A plain bridge in the root namespace: no shell of its own to set up.
        "switch": {"id", "name", "type"},
    }
    _python_keys = {
        "schema_version", "kind", "id", "revision", "created_at", "updated_at",
        "published_revision", "catalog", "python", "files", "guide", "layout",
    }
    #: The largest script Upload a script… takes, in bytes.
    UPLOAD_LIMIT = 500_000
    #: Preserve a full Canvas FRR body plus header and 256 dual-stack timer blocks.
    SEED_TEXT_LIMIT = 150_000
    _file_pattern = re.compile(r"^frr-config(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*/frr\.conf$")
    _locks_guard = threading.Lock()
    _root_locks: Dict[str, threading.RLock] = {}

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.drafts_dir = os.path.join(self.root, "drafts")
        with self._locks_guard:
            self._lock = self._root_locks.setdefault(self.root, threading.RLock())

    def create(self, source_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a blank draft, or copy another existing draft."""
        with self._lock:
            if source_id is None:
                document = self._blank_document()
            else:
                source = self.read(source_id)
                document = copy.deepcopy(source)
                document["catalog"] = {
                    **document["catalog"],
                    "id": None,
                    "name": f"Copy of {document['catalog']['name']}"[:120],
                }
                document["published_revision"] = None
                document["revision"] = 0
                document["created_at"] = time.time()
                document["updated_at"] = document["created_at"]
            draft_id = f"draft-{uuid.uuid4().hex}"
            document["id"] = draft_id
            self._write_new(draft_id, document)
            return copy.deepcopy(document)

    def create_from_example(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        """Start an independent draft from a prepared published definition.

        Args:
            definition: The shipped or authored Lab Example definition.

        Returns:
            The new draft, with a publication base for authored examples.
        """
        with self._lock:
            document = self._blank_document()
            if definition.get("kind") == "python":
                for field in ("topology", "address_family", "persistent_config"):
                    del document[field]
                document.update(kind="python", python=definition["python"],
                                files=copy.deepcopy(definition["files"]))
                fields: Tuple[str, ...] = ("catalog", "guide", "layout")
            else:
                fields = ("catalog", "topology", "guide", "layout", "address_family",
                          "persistent_config", "code_section")
            for field in fields:
                if field in definition:
                    document[field] = copy.deepcopy(definition[field])
            if "lesson_files" in definition:
                document["lesson_files"] = copy.deepcopy(definition["lesson_files"])
            draft_id = f"draft-{uuid.uuid4().hex}"
            document["id"] = draft_id
            self.validate(document)
            self._write_new(draft_id, document)
            catalog_id = document["catalog"]["id"]
            if catalog_id:
                entry = self.published_example(catalog_id)
                if entry is None:
                    raise DraftNotFoundError(catalog_id)
                self._atomic_write(self._base_path(draft_id), {
                    "catalog_id": catalog_id, "version": entry.get("version")
                })
            return copy.deepcopy(document)

    def list_drafts(self) -> List[Dict[str, Any]]:
        """Return all drafts, newest update first."""
        if not os.path.isdir(self.drafts_dir):
            return []
        documents = []
        for name in os.listdir(self.drafts_dir):
            if not name.startswith("draft-"):
                continue
            try:
                documents.append(self.read(name))
            except DraftNotFoundError:
                continue
            except ValueError as exc:
                # One unreadable draft must not lock every Instructor out.
                logger.warning(f"Skipping unreadable draft '{name}': {exc}")
                continue
        return sorted(documents, key=lambda doc: (-doc["updated_at"], doc["id"]))

    def fingerprint(self, document: Dict[str, Any]) -> str:
        """Hash only inputs that change the built Lab, excluding Guide/Layout.

        Both source modes include supporting build files by path and bytes.
        """
        payload = ({
            "schema_version": document["schema_version"], "kind": "python",
            "python": document["python"], "files": document["files"],
        } if document.get("kind") == "python" else {
            "schema_version": document["schema_version"],
            "topology": document["topology"],
            "address_family": document["address_family"],
            "persistent_config": document["persistent_config"],
        })
        # Only a Code section that adds code changes the built Lab; leaving
        # an empty one out keeps earlier Preview evidence current.
        if has_code_section(document):
            payload["code_section"] = document["code_section"]
        supporting = build_inputs(document)
        if supporting:
            payload["lesson_files"] = supporting
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def persistent_structure(document: Dict[str, Any]) -> Dict[str, Any]:
        """Describe the identities a Group's Router Config Copy depends on.

        FRR text, addresses, Guide and Layout are deliberately excluded: this
        is structural compatibility, not validation of routing semantics.

        Args:
            document: A validated Lab Example definition.

        Returns:
            The persistent Routers, their VRFs and attached Interface identities.
            A Python-based example's structure cannot be read from its code:
            it is the script's hash and its Seed directories, so any code
            change or moved Seed makes existing copies incompatible, while
            Seed text, Guide and Layout edits do not. An opaque Canvas Code
            section follows the same rule: any code change may change Router
            identities and requires explicit copy discard before reuse. Supporting
            build files follow that conservative rule in either source mode;
            their path, bytes or effective role may change Lab construction.
            Empty collections preserve existing structure records unchanged.
        """
        supporting = build_inputs(document)
        if document.get("kind") == "python":
            python_structure: Dict[str, Any] = {
                "version": 1,
                "python": hashlib.sha256(document["python"].encode("utf-8")).hexdigest(),
                "seeds": sorted({path.rsplit("/", 1)[0] for path in document["files"]}),
            }
            if supporting:
                python_structure["lesson_files"] = supporting
            return python_structure
        routers = []
        for node in document["topology"]["nodes"]:
            if node["type"] != "router" or not document["persistent_config"]:
                continue
            interfaces = []
            for link in document["topology"]["links"]:
                for index, end in enumerate(link["ends"]):
                    if end["node_id"] != node["id"]:
                        continue
                    peer = link["ends"][1 - index]
                    interface = {
                        "interface_id": end["interface_id"], "port": end["port"],
                        "peer_node_id": peer["node_id"],
                        "peer_interface_id": peer["interface_id"],
                        "peer_port": peer["port"],
                    }
                    if end.get("vrf"):
                        interface["vrf"] = end["vrf"]
                    interfaces.append(interface)
            record = {
                "id": node["id"], "name": node["name"], "type": node["type"],
                "seed": f"frr-config/{node['name']}",
                "daemons": sorted({PROTO_MAP[protocol] for protocol in node["protocols"]}),
                "interfaces": sorted(interfaces, key=lambda item: item["interface_id"]),
            }
            if node.get("subinterfaces"):
                record["subinterfaces"] = sorted(({
                    "id": sub["id"], "parent_interface_id": sub["parent_interface_id"],
                    "vlan": sub["vlan"],
                } for sub in node["subinterfaces"]), key=lambda item: item["id"])
            if node.get("vrfs"):
                record["vrfs"] = sorted(({
                    "name": vrf["name"], "table": vrf["table"],
                } for vrf in node["vrfs"]), key=lambda item: item["name"])
            routers.append(record)
        structure: Dict[str, Any] = {
            "version": 1, "routers": sorted(routers, key=lambda item: item["id"])
        }
        if has_code_section(document):
            structure["code_section"] = hashlib.sha256(
                document["code_section"].encode("utf-8")
            ).hexdigest()
        if supporting:
            structure["lesson_files"] = supporting
        return structure

    def layout_for_runtime(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Map stable draft Node IDs to names used by the running Topology.

        The visual definition keys positions by stable IDs so renaming a Host
        does not detach its position. The web Topology feed identifies Hosts
        by their runtime names, so layouts served to that view use those names.
        """
        self.validate(document)
        if document.get("kind") == "python":
            return copy.deepcopy(document["layout"])
        runtime_names = {
            node["id"]: node["name"] for node in document["topology"]["nodes"]
        }
        positions = document["layout"]["nodes"]
        drawn = {name: position for key, position in positions.items()
                 if (name := self._code_section_node(key)) and name not in runtime_names.values()}
        return {
            "version": document["layout"]["version"],
            "nodes": {
                **drawn,
                **{runtime_names[node_id]: position
                   for node_id, position in positions.items() if node_id in runtime_names},
            },
        }

    def preview_status(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Report whether a successful startup still matches this Network recipe.

        Preview evidence records that Mininet-IPLab started the network
        successfully. It does not promise protocol convergence or connectivity.
        The evidence is persisted beside the Draft and remains usable after a
        web-server restart while the network-affecting inputs still match.
        """
        evidence_path = self._preview_path(document["id"])
        try:
            with open(evidence_path, "r", encoding="utf-8") as source:
                evidence = json.load(source)
        except FileNotFoundError:
            return {"current": False, "started_at": None, "node_count": None}
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Preview evidence cannot be read: {exc}") from exc
        if not isinstance(evidence, dict) or not isinstance(evidence.get("fingerprint"), str):
            raise ValueError("Preview evidence has an invalid format")
        return {
            "current": evidence["fingerprint"] == self.fingerprint(document),
            "started_at": evidence.get("started_at"),
            "node_count": evidence.get("node_count"),
        }

    def record_preview(self, document: Dict[str, Any], lab_key: str,
                       node_count: Optional[int] = None) -> None:
        """Persist evidence after the Preview Lab has reported successful startup.

        Args:
            document: The draft revision the Preview was built from.
            lab_key: The Preview Lab's key.
            node_count: How many Nodes the Lab started with; the Catalog's
                count for a Python-based example, whose Nodes only its script
                knows.
        """
        self._atomic_write(
            self._preview_path(document["id"]),
            {
                "fingerprint": self.fingerprint(document),
                "revision": document["revision"],
                "lab_key": lab_key,
                "started_at": time.time(),
                "node_count": node_count,
            },
        )

    def record_snapshot(self, draft_id: str, lab_key: str, configs: Dict[str, Dict[str, str]],
                        features: Optional[Dict[str, Dict[str, Any]]] = None,
                        has_kept_changes: bool = False) -> None:
        """Keep each Router's running config and supported declarative state.

        Args:
            draft_id: The draft the Preview was built from.
            lab_key: The Preview Lab's key; keep reads only that Lab.
            configs: By Node id, the Router's ``name`` and ``config`` body.
            features: Supported settings keyed by stable authored identities.
            has_kept_changes: Whether Keep has saved changes from this Preview.
        """
        self._atomic_write(self._snapshot_path(draft_id), {
            "lab_key": lab_key, "routers": configs, "features": features or {},
            "has_kept_changes": has_kept_changes,
        })

    def read_snapshot(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """The current Preview's reviewed keep baseline, if any."""
        try:
            with open(self._snapshot_path(draft_id), "r", encoding="utf-8") as source:
                snapshot = json.load(source)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Preview snapshot cannot be read: {exc}") from exc
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("routers"), dict):
            raise ValueError("Preview snapshot has an invalid format")
        return snapshot

    def validate_for_build(self, document: Dict[str, Any]) -> None:
        """Refuse a recipe that cannot represent a useful basic Lab."""
        self.validate(document)
        if document.get("kind") == "python":
            error = self.python_error(document)
            if error is not None:
                where = f"line {error['line']}: " if error["line"] else ""
                raise DraftValidationError(
                    f"Python does not compile ({where}{error['message']})",
                    kind="draft",
                    item_id=None,
                    control_id="python-source",
                )
            return
        if not document["topology"]["nodes"]:
            raise DraftValidationError(
                "Add at least one Node before preview or publication",
                kind="draft",
                item_id=None,
                control_id=None,
            )

    def write_builder(self, document: Dict[str, Any], path: str) -> None:
        """Write the runnable script, Seeds and exact supporting lesson bytes.

        The script is :func:`generate_python`'s output, the same text the
        Instructor reads and downloads. Each Router with Persistent Config
        gets its Seed beside the script, ``frr-config/<name>/frr.conf``
        rendered from its native FRR configuration; the Router renders its
        other FRR files from its Protocols when it starts. The script points
        ``frr_dir`` there, so under the manager every start copies the Seed
        and never writes it.
        """
        from mniplab.frrouter.config import FRRConfigBuilder
        from mniplab.webserver.authoring_python import generate_python

        self.validate_for_build(document)
        root = os.path.dirname(os.path.abspath(path))
        os.makedirs(root, exist_ok=True)
        for relative, record in document.get("lesson_files", {}).items():
            destination = os.path.join(root, *relative.split("/"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as supporting:
                supporting.write(decode_file(record))
        if document.get("kind") == "python":
            # The Instructor's own script and Seed files, laid out as they
            # name them: the Lab runs with this folder as its working directory.
            for relative, text in document["files"].items():
                seed_path = os.path.join(root, *relative.split("/"))
                os.makedirs(os.path.dirname(seed_path), exist_ok=True)
                with open(seed_path, "w", encoding="utf-8") as seed:
                    seed.write(text)
            with open(path, "w", encoding="utf-8") as script:
                script.write(document["python"])
            return
        for node in document["topology"]["nodes"]:
            if node["type"] != "router" or not document["persistent_config"]:
                continue
            seed_dir = os.path.join(root, "frr-config", node["name"])
            os.makedirs(seed_dir, exist_ok=True)
            FRRConfigBuilder().write_frr_conf(
                node["name"], seed_dir,
                frr_config=router_frr_config(node, document["topology"]["links"]),
            )
        with open(path, "w", encoding="utf-8") as target:
            target.write(generate_python(document))

    def publish_new(self, draft_id: str, expected_revision: int,
                    mode: str = "save_as_new") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Publish a complete immutable version and select it in the Catalog.

        Args:
            draft_id: The shared draft to publish.
            expected_revision: Revision held by the publishing editor.
            mode: ``save_as_new`` or ``update_existing``.

        Returns:
            The updated draft and the Catalog entry for the new version.

        Raises:
            DraftConflictError: The draft or publication base is stale.
            ValueError: The draft cannot be built or its mode is invalid.
        """
        with self._lock:
            document = self.read(draft_id)
            if document["revision"] != expected_revision:
                raise DraftConflictError(
                    f"Draft changed since it was opened (current revision {document['revision']})."
                )
            self.validate_for_publish(document)
            if not self.preview_status(document)["current"]:
                raise DraftConflictError(
                    "Start a Preview Lab from the current network definition before publishing"
                )
            if document["published_revision"] == document["revision"] - 1:
                raise DraftConflictError(
                    "This Draft revision is already published. Edit it or copy it before publishing again."
                )
            if not document["catalog"]["name"].strip():
                raise DraftValidationError(
                    "Add a Catalog name before publishing",
                    kind="draft",
                    item_id=None,
                    control_id="catalog-name",
                )
            original_document = copy.deepcopy(document)
            published_root = os.path.join(self.root, "published")
            os.makedirs(published_root, exist_ok=True)
            manifest_path = os.path.join(published_root, "catalog.json")
            manifest = self._read_manifest(manifest_path)
            if mode not in ("save_as_new", "update_existing"):
                raise ValueError("Unknown publication mode")
            if mode == "update_existing":
                catalog_id = document["catalog"]["id"]
                existing = next((item for item in manifest if item["id"] == catalog_id), None)
                if not catalog_id or existing is None:
                    raise DraftConflictError("Update existing requires a published authored example")
                if existing.get("kind", "visual") != document.get("kind", "visual"):
                    raise DraftConflictError(
                        "Update existing keeps an example's kind: publish this draft as new")
                base = self._read_base(draft_id)
                if base is None and existing.get("version") is None:
                    # Drafts published before versioned directories had no
                    # base marker. Their original snapshot still names the
                    # draft that published the current Catalog entry.
                    with open(self.published_file(catalog_id, "definition.json"),
                              encoding="utf-8") as source:
                        if json.load(source).get("id") == draft_id:
                            base = {"catalog_id": catalog_id, "version": None}
                if base != {
                    "catalog_id": catalog_id, "version": existing.get("version")
                }:
                    raise DraftConflictError("This published example changed; adapt its latest version before updating")
            else:
                catalog_id = f"authored-{uuid.uuid4().hex}"
            version = f"version-{uuid.uuid4().hex}"
            temporary = tempfile.mkdtemp(prefix=".publish-", dir=published_root)
            version_root = os.path.join(published_root, catalog_id, "versions")
            target = os.path.join(version_root, version)
            updated_draft = None
            previous_base = self._read_base(draft_id)
            base_changed = False
            catalog_committed = False
            try:
                snapshot = copy.deepcopy(document)
                snapshot["catalog"]["id"] = catalog_id
                with open(os.path.join(temporary, "definition.json"), "w", encoding="utf-8") as dest:
                    json.dump(snapshot, dest, ensure_ascii=False, indent=2, allow_nan=False)
                with open(os.path.join(temporary, "guide.md"), "w", encoding="utf-8") as dest:
                    dest.write(snapshot["guide"])
                with open(os.path.join(temporary, "layout.json"), "w", encoding="utf-8") as dest:
                    json.dump(
                        self.layout_for_runtime(snapshot),
                        dest,
                        ensure_ascii=False,
                        indent=2,
                    )
                self.write_builder(snapshot, os.path.join(temporary, "lab.py"))
                os.makedirs(version_root, exist_ok=True)
                os.replace(temporary, target)
                python = snapshot.get("kind") == "python"
                entry = {
                    "id": catalog_id,
                    "version": version,
                    "name": snapshot["catalog"]["name"],
                    "description": snapshot["catalog"]["description"],
                    "protocols": list(snapshot["catalog"]["protocols"]),
                    "area": snapshot["catalog"]["area"],
                    # Only a script knows the Nodes its code adds: count
                    # those its successful Preview started.
                    "node_count": (self.preview_status(document)["node_count"] or 0)
                    if python or has_code_section(snapshot)
                    else len(snapshot["topology"]["nodes"]),
                    "persistent_config": bool(snapshot["files"]) if python else any(
                        node["type"] == "router" and snapshot["persistent_config"]
                        for node in snapshot["topology"]["nodes"]
                    ),
                    "authored": True,
                }
                if python:
                    entry["kind"] = "python"
                document["catalog"]["id"] = catalog_id
                document["published_revision"] = document["revision"]
                document["revision"] += 1
                document["updated_at"] = time.time()
                updated_draft = copy.deepcopy(document)
                self._atomic_write(self._draft_path(draft_id), document)
                self._atomic_write(self._base_path(draft_id), {
                    "catalog_id": catalog_id, "version": version
                })
                base_changed = True
                next_manifest = ([item for item in manifest if item["id"] != catalog_id]
                                 if mode == "update_existing" else list(manifest))
                next_manifest.append(entry)
                self._atomic_write(manifest_path, {"examples": next_manifest})
                catalog_committed = True
                return copy.deepcopy(document), entry
            except Exception:
                if not catalog_committed:
                    if updated_draft is not None:
                        self._atomic_write(
                            self._draft_path(draft_id),
                            original_document,
                        )
                    if base_changed:
                        if previous_base is None:
                            os.unlink(self._base_path(draft_id))
                        else:
                            self._atomic_write(self._base_path(draft_id), previous_base)
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                if os.path.isdir(temporary):
                    shutil.rmtree(temporary)
                raise

    def remove_published(self, catalog_id: str) -> None:
        """Take an authored Lab Example out of the Catalog, with every version of it.

        Drafts that revise it are left as they are; with no Catalog entry
        to update, they publish as a new Lab Example.

        Args:
            catalog_id: The authored Lab Example id.

        Raises:
            DraftNotFoundError: No authored Lab Example has that id.
        """
        with self._lock:
            if self.published_example(catalog_id) is None:
                raise DraftNotFoundError(catalog_id)
            published_root = os.path.join(self.root, "published")
            self._atomic_write(os.path.join(published_root, "catalog.json"), {
                "examples": [item for item in self.list_published() if item["id"] != catalog_id]
            })
            shutil.rmtree(os.path.join(published_root, catalog_id), ignore_errors=True)

    def list_published(self) -> List[Dict[str, Any]]:
        """Return the published authored examples in Catalog order."""
        path = os.path.join(self.root, "published", "catalog.json")
        return self._read_manifest(path)

    def published_file(self, catalog_id: str, filename: str = "lab.py") -> str:
        """Resolve an authored asset beneath a validated published example."""
        if not isinstance(catalog_id, str) or not re.fullmatch(r"authored-[0-9a-f]{32}", catalog_id):
            raise DraftNotFoundError(catalog_id)
        if filename not in {"lab.py", "guide.md", "layout.json", "definition.json"}:
            raise DraftNotFoundError(catalog_id)
        if self.published_example(catalog_id) is None:
            raise DraftNotFoundError(catalog_id)
        entry = self.published_example(catalog_id)
        if entry is None:
            raise DraftNotFoundError(catalog_id)
        version = entry.get("version")
        if version is None:
            return os.path.join(self.root, "published", catalog_id, filename)
        if not isinstance(version, str) or not re.fullmatch(r"version-[0-9a-f]{32}", version):
            raise DraftNotFoundError(catalog_id)
        return os.path.join(self.root, "published", catalog_id, "versions", version, filename)

    def published_example(self, catalog_id: str) -> Optional[Dict[str, Any]]:
        """Return the Catalog metadata for one authored example, if published."""
        return next((entry for entry in self.list_published() if entry.get("id") == catalog_id), None)

    def published_structure(self, catalog_id: str) -> Dict[str, Any]:
        """Read the structure selected for subsequent Catalog starts.

        Args:
            catalog_id: The authored Lab Example id.

        Returns:
            The selected version's persistent Router structure.
        """
        with open(self.published_file(catalog_id, "definition.json"), encoding="utf-8") as source:
            return self.persistent_structure(json.load(source))

    @staticmethod
    def _read_manifest(path: str) -> List[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as source:
                data = json.load(source)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Published Catalog cannot be read: {exc}") from exc
        examples = data.get("examples") if isinstance(data, dict) else None
        if not isinstance(examples, list) or any(not isinstance(item, dict) for item in examples):
            raise ValueError("Published Catalog has an invalid format")
        return examples

    def read(self, draft_id: str) -> Dict[str, Any]:
        """Read one shared draft by its opaque id."""
        path = self._draft_path(draft_id)
        try:
            with open(path, "r", encoding="utf-8") as source:
                data = json.load(source)
        except FileNotFoundError as exc:
            raise DraftNotFoundError(draft_id) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Draft '{draft_id}' cannot be read: {exc}") from exc
        self.validate(data)
        return data

    def save(
        self, draft_id: str, expected_revision: int, document: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Save a valid edit if the caller has the current revision."""
        with self._lock:
            current = self.read(draft_id)
            if current["revision"] != expected_revision:
                raise DraftConflictError(
                    f"Draft changed since it was opened (current revision {current['revision']})."
                )
            if document.get("id") != draft_id:
                raise ValueError("Draft id cannot be changed")
            if document.get("kind") != current.get("kind"):
                raise ValueError("A draft's kind cannot be changed")
            self.validate(document)
            if document["catalog"]["id"] != current["catalog"]["id"]:
                raise ValueError("The draft's published Catalog identity cannot be changed")
            if document.get("published_revision") != current["published_revision"]:
                raise ValueError("The draft's published revision cannot be changed")
            saved = copy.deepcopy(document)
            saved["revision"] = current["revision"] + 1
            saved["created_at"] = current["created_at"]
            saved["updated_at"] = time.time()
            self._atomic_write(self._draft_path(draft_id), saved)
            return copy.deepcopy(saved)

    def delete(self, draft_id: str, expected_revision: int) -> None:
        """Delete a draft and its Preview evidence if the caller has the current revision.

        Published Catalog entries are separate copies and stay visible.
        """
        with self._lock:
            current = self.read(draft_id)
            if current["revision"] != expected_revision:
                raise DraftConflictError(
                    f"Draft changed since it was opened (current revision {current['revision']})."
                )
            shutil.rmtree(os.path.dirname(self._draft_path(draft_id)))

    @classmethod
    def validate(cls, document: Any) -> Any:
        """Validate the complete, versioned visual or Python-based definition."""
        if not isinstance(document, dict):
            raise DraftValidationError("Draft must be an object")
        if document.get("kind") == "python":
            cls._validate_python(document)
            validate_lesson_files(document)
            return document
        expected_document_keys = {
            "schema_version", "id", "revision", "created_at", "updated_at",
            "published_revision", "catalog", "topology", "guide", "layout",
            "address_family", "persistent_config",
        }
        if not expected_document_keys <= set(document) <= expected_document_keys | {
            "code_section", "lesson_files"
        }:
            raise DraftValidationError("Draft fields do not match the Lab Example definition schema")
        code_section = document.get("code_section", "")
        if not isinstance(code_section, str) or len(code_section) > 100_000:
            raise DraftValidationError(
                "The Code section must be text of at most 100,000 characters",
                control_id="code-section-input",
            )
        cls._validate_header(document)
        if not isinstance(document["persistent_config"], bool):
            raise DraftValidationError(
                "Saved Router config setting must be true or false",
                control_id="lab-persistent-config",
            )
        node_ids = cls._validate_topology(document)
        cls._validate_presentation(document, node_ids)
        cls._validate_address_family(document)
        validate_lesson_files(document)
        return document

    @classmethod
    def _validate_header(cls, document: Dict[str, Any]) -> None:
        """Check the identity, revision and Catalog fields every draft has."""
        if document.get("schema_version") != cls.SCHEMA_VERSION:
            raise DraftValidationError("Unsupported Lab Example definition version")
        draft_id = document.get("id")
        if not isinstance(draft_id, str) or not draft_id.startswith("draft-"):
            raise DraftValidationError("Draft id is invalid")
        if (isinstance(document.get("revision"), bool)
                or not isinstance(document.get("revision"), int)
                or document["revision"] < 0):
            raise DraftValidationError("Draft revision is invalid")
        for field_name in ("created_at", "updated_at"):
            timestamp = document.get(field_name)
            if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                    or not math.isfinite(timestamp) or timestamp < 0):
                raise DraftValidationError(f"Draft {field_name} is invalid")
        published_revision = document.get("published_revision")
        if published_revision is not None and (
            isinstance(published_revision, bool)
            or not isinstance(published_revision, int)
            or published_revision < 0
        ):
            raise DraftValidationError("Draft published revision is invalid")
        catalog = document.get("catalog")
        if not isinstance(catalog, dict):
            raise DraftValidationError("Catalog metadata must be an object", control_id="catalog-name")
        if set(catalog) - {"area"} != {"id", "name", "description", "protocols"}:
            raise DraftValidationError(
                "Catalog fields do not match the Lab Example definition schema",
                control_id="catalog-name",
            )
        if "area" in catalog:
            cls._text(catalog["area"], "Catalog Area", 80, control_id="catalog-area")
        catalog_id = catalog.get("id")
        if catalog_id is not None and (
            not isinstance(catalog_id, str)
            or not re.fullmatch(r"authored-[0-9a-f]{32}", catalog_id)
        ):
            raise DraftValidationError("Catalog id is invalid", control_id="catalog-name")
        cls._text(catalog.get("name"), "Catalog name", 120, control_id="catalog-name")
        cls._text(
            catalog.get("description"),
            "Catalog description",
            4000,
            control_id="catalog-description",
        )
        protocols = catalog.get("protocols")
        if not isinstance(protocols, list) or any(
            not isinstance(item, str) or len(item) > 80 for item in protocols
        ):
            raise DraftValidationError(
                "Catalog protocols must be a list of short names",
                control_id="catalog-protocols",
            )

    @classmethod
    def _validate_topology(cls, document: Dict[str, Any]) -> set[str]:
        """Check a visual definition's Topology; return its Node IDs."""
        topology = document.get("topology")
        if not isinstance(topology, dict):
            raise DraftValidationError("Topology must be an object")
        if set(topology) != {"nodes", "links"}:
            raise DraftValidationError("Topology fields do not match the Lab Example definition schema")
        nodes, links = topology.get("nodes"), topology.get("links")
        if not isinstance(nodes, list) or not isinstance(links, list):
            raise DraftValidationError("Topology nodes and links must be lists")
        node_ids: set[str] = set()
        node_names: set[str] = set()
        node_name_by_id: Dict[str, str] = {}
        node_types: Dict[str, str] = {}
        switch_ids: set[str] = set()
        for node in nodes:
            if not isinstance(node, dict):
                raise DraftValidationError("Each Node must be an object")
            node_id = node.get("id") if isinstance(node.get("id"), str) else None
            node_type = node.get("type")
            if not isinstance(node_type, str) or node_type not in cls._node_fields:
                raise DraftValidationError(
                    "Node type is not supported by this definition",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id="host-type" if node_id else None,
                )
            allowed = cls._node_fields[node_type]
            optional = {"subinterfaces", "vrfs", "nat"} if node_type == "router" else set()
            if node_type == "speaker":
                optional.add("route_generator")
            if node_type == "switch":
                optional |= {"stp", "priority"}
            if node_type in ("host", "router"):
                optional |= {"dhcp", "relay", "dns", "resolver", "rpki_ca", "validator"}
            if node_type == "host":
                optional.add("nameservers")
            if not (allowed <= set(node) <= allowed | optional):
                raise DraftValidationError(
                    f"{node_type.title()} fields do not match the Lab Example definition schema",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id="host-name" if node_id else None,
                )
            name = node.get("name")
            if not isinstance(node_id, str) or not node_id or len(node_id) > 80:
                raise DraftValidationError("Node id is invalid", kind="node", item_id=None, control_id="host-name")
            if node_id in node_ids:
                raise DraftValidationError(
                    f"Duplicate Node id '{node_id}'",
                    kind="node",
                    item_id=node_id,
                    control_id="host-name",
                )
            if not isinstance(name, str) or not cls._name_pattern.fullmatch(name):
                raise DraftValidationError(
                    f"Node name '{name}' is invalid",
                    kind="node",
                    item_id=node_id,
                    control_id="host-name",
                )
            if name.lower() in node_names:
                raise DraftValidationError(
                    f"Duplicate Node name '{name}'",
                    kind="node",
                    item_id=node_id,
                    control_id="host-name",
                )
            if node_type in ("host", "container"):
                cls._gateway(node.get("ipv4_gateway"), 4, name, node_id)
                cls._gateway(node.get("ipv6_gateway"), 6, name, node_id)
                if node_type == "container":
                    image = node.get("image")
                    if (not isinstance(image, str) or len(image) > 255
                            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*", image)):
                        raise DraftValidationError(
                            f"Container Host '{name}' image must be a Docker image reference",
                            kind="node",
                            item_id=node_id,
                            control_id="container-image",
                        )
            elif node_type == "router":
                cls._router(node, name, node_id)
            elif node_type in ("nat", "transit"):
                nat_control = "nat-ip"
                try:
                    if not isinstance(node.get("ip"), str):
                        nat_control = "nat-ip"
                        raise ValueError("address and subnet must be text")
                    if not isinstance(node.get("subnet"), str):
                        nat_control = "nat-subnet"
                        raise ValueError("address and subnet must be text")
                    nat_control = "nat-ip"
                    address = ipaddress.IPv4Interface(node.get("ip"))
                    nat_control = "nat-subnet"
                    subnet = ipaddress.IPv4Network(node.get("subnet"))
                except (ValueError, TypeError) as exc:
                    raise DraftValidationError(
                        f"Internet Gateway '{name}' needs an IPv4 address and subnet",
                        kind="node",
                        item_id=node_id,
                        control_id=nat_control,
                    ) from exc
                if address.ip not in subnet:
                    raise DraftValidationError(
                        f"Internet Gateway '{name}' address is outside its subnet",
                        kind="node",
                        item_id=node_id,
                        control_id="nat-ip",
                    )
            elif node_type == "speaker":
                cls._speaker(node, name, node_id)
            if "dhcp" in node:
                cls._dhcp(node["dhcp"], name, node_id)
            if "relay" in node:
                cls._relay(node["relay"], name, node_id)
            if "dns" in node:
                cls._dns(node["dns"], name, node_id)
            if "rpki_ca" in node:
                try:
                    authored_rpki_ca(node["rpki_ca"], name)
                except (TypeError, ValueError) as exc:
                    raise DraftValidationError(
                        f"Node '{name}' RPKI CA: {exc}",
                        kind="node",
                        item_id=node_id,
                        control_id=_rpki_ca_error_control(node["rpki_ca"]),
                    ) from exc
            if "validator" in node:
                try:
                    authored_validator(node["validator"])
                except (TypeError, ValueError) as exc:
                    raise DraftValidationError(
                        f"Node '{name}' Validator: {exc}",
                        kind="node",
                        item_id=node_id,
                        control_id=_validator_error_control(node["validator"]),
                    ) from exc
            if node_type not in ("switch", "nat", "transit"):
                cls._startup(node.get("startup"), name, node_id)
            node_ids.add(node_id)
            node_names.add(name.lower())
            node_name_by_id[node_id] = name
            node_types[node_id] = node_type
            if node_type == "switch":
                try:
                    ManagedSwitch.validate_spanning_tree(
                        node.get("stp", False), node.get("priority"),
                    )
                except ValueError as exc:
                    raise DraftValidationError(
                        str(exc), kind="node", item_id=node_id,
                        control_id="switch-priority" if "priority" in str(exc) else "switch-stp",
                    ) from exc
                switch_ids.add(node_id)
        cls._rpki_lab(nodes)

        router_ids = {node["id"] for node in nodes if node["type"] == "router"}
        primary_hosts: Dict[str, str] = {}
        for node in nodes:
            for zone_index, zone in enumerate(node.get("dns", {}).get("zones", [])):
                if zone["kind"] != "primary":
                    continue
                if zone["name"] in primary_hosts:
                    raise DraftValidationError(
                        f"Zone {zone['name']} has two Primary Nodes",
                        kind="node",
                        item_id=node["id"],
                        control_id=f"dns-zones-{zone_index}-name",
                    )
                primary_hosts[zone["name"]] = node["id"]
        for node in nodes:
            for zone_index, zone in enumerate(node.get("dns", {}).get("zones", [])):
                zbase = f"dns-zones-{zone_index}"
                if zone["kind"] == "secondary":
                    if primary_hosts.get(zone["name"]) != zone["primary_node_id"] or node["id"] == zone["primary_node_id"]:
                        raise DraftValidationError(
                            f"Secondary Zone {zone['name']} must name its Primary Node",
                            kind="node",
                            item_id=node["id"],
                            control_id=f"{zbase}-primary_node_id",
                        )
                else:
                    for target_index, target in enumerate(zone.get("allow_transfer") or []):
                        try:
                            validate_transfer_target(target, node_ids)
                        except ValueError as exc:
                            tfield = "node_id" if isinstance(target, dict) and "node_id" in target else "address"
                            raise DraftValidationError(
                                str(exc),
                                kind="node",
                                item_id=node["id"],
                                control_id=f"{zbase}-allow_transfer-{target_index}-{tfield}",
                            ) from exc
                    for del_index, delegation in enumerate(zone.get("ds_delegations", [])):
                        dbase = f"{zbase}-ds_delegations-{del_index}-child"
                        if not isinstance(delegation, dict) or set(delegation) != {"node_id", "zone"}:
                            raise DraftValidationError(
                                "DS delegation needs a child Node ID and Zone",
                                kind="node",
                                item_id=node["id"],
                                control_id=dbase,
                            )
                        child = delegation["zone"]
                        if (not isinstance(child, str) or not isinstance(delegation["node_id"], str)
                                or primary_hosts.get(child) != delegation["node_id"] or child == zone["name"]):
                            raise DraftValidationError(
                                f"DS delegation for {child!r} must name its signed Primary Node",
                                kind="node",
                                item_id=node["id"],
                                control_id=dbase,
                            )
                        child_zone = next(item for host in nodes if host["id"] == delegation["node_id"]
                                          for item in host.get("dns", {}).get("zones", [])
                                          if item["kind"] == "primary" and item["name"] == child)
                        if not zone.get("dnssec") or not child_zone.get("dnssec"):
                            raise DraftValidationError(
                                "DS delegation requires signed parent and child Zones",
                                kind="node",
                                item_id=node["id"],
                                control_id=dbase,
                            )
                        parent = zone["name"]
                        if parent != "." and not child.endswith("." + parent):
                            raise DraftValidationError(
                                "DS delegation must point to a child Zone",
                                kind="node",
                                item_id=node["id"],
                                control_id=dbase,
                            )
        for node in nodes:
            if node["type"] != "transit":
                continue
            downstream = node["downstream_ids"]
            if (not isinstance(downstream, list) or not downstream
                    or any(not isinstance(item, str) or item not in router_ids for item in downstream)
                    or len(downstream) != len(set(downstream))):
                raise DraftValidationError(
                    f"Transit Internet Gateway '{node['name']}' needs distinct downstream Routers",
                    kind="node",
                    item_id=node["id"],
                    control_id="nat-downstream-fields",
                )

        link_ids: set[str] = set()
        macs: set[str] = set()
        interface_ids: set[str] = set()
        occupied_ports: Dict[str, set[int]] = {node_id: set() for node_id in node_ids}
        router_interfaces: Dict[str, Dict[str, int]] = {node_id: {} for node_id in node_ids}
        vrf_members: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        for link in links:
            if not isinstance(link, dict):
                raise DraftValidationError("Each Link must be an object", kind="link")
            link_id = link.get("id") if isinstance(link.get("id"), str) else None
            if not {"id", "ends"} <= set(link) <= {"id", "ends", "condition"}:
                raise DraftValidationError(
                    "Link fields do not match the Lab Example definition schema",
                    kind="link",
                    item_id=link_id,
                )
            if "condition" in link:
                cls._link_condition(link["condition"], link_id)
            if not isinstance(link_id, str) or not link_id or link_id in link_ids:
                raise DraftValidationError(
                    "Link id is invalid or duplicated",
                    kind="link",
                    item_id=link_id,
                )
            ends = link.get("ends")
            if not isinstance(ends, list) or len(ends) != 2:
                raise DraftValidationError(
                    f"Link '{link_id}' must have two ends",
                    kind="link",
                    item_id=link_id,
                )
            used_nodes = set()
            for end_index, end in enumerate(ends, start=1):
                end_port_control = f"link-end-{end_index - 1}-port"
                end_change_control = f"link-end-{end_index - 1}-change"
                if not isinstance(end, dict):
                    raise DraftValidationError(
                        f"Link '{link_id}' end must be an object",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                required_end = {"node_id", "interface_id", "port", "ipv4", "ipv6"}
                if not (required_end <= set(end) <= required_end | {"vlan", "dhcp4", "dhcp6", "mac", "vrf", "outside"}):
                    raise DraftValidationError(
                        f"Link '{link_id}' Interface fields are incomplete",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                node_id = end.get("node_id")
                interface = end.get("interface_id")
                port = end.get("port")
                if not isinstance(node_id, str) or node_id not in node_ids:
                    raise DraftValidationError(
                        f"Link '{link_id}' refers to a missing or invalid Node",
                        kind="link",
                        item_id=link_id,
                        control_id=end_change_control,
                    )
                if node_id in used_nodes:
                    raise DraftValidationError(
                        f"Link '{link_id}' cannot connect a Node to itself",
                        kind="link",
                        item_id=link_id,
                        control_id=end_change_control,
                    )
                if not isinstance(interface, str) or not cls._interface_pattern.fullmatch(interface):
                    raise DraftValidationError(
                        f"Link '{link_id}' Interface identity is invalid",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                if interface in interface_ids:
                    raise DraftValidationError(
                        f"Interface identity '{interface}' is used more than once",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                if isinstance(port, bool) or not isinstance(port, int) or port < 0 or port > 255:
                    raise DraftValidationError(
                        f"Link '{link_id}' has an invalid Interface port",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                if len(f"{node_name_by_id[node_id]}-eth{port}") > MAX_INTF_NAME_LEN:
                    raise DraftValidationError(
                        f"Link '{link_id}' Interface name exceeds the Linux limit",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                if node_id in switch_ids and port == 0:
                    raise DraftValidationError(
                        f"Link '{link_id}' Switch ports start at 1",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                if "vlan" in end:
                    if node_id not in switch_ids:
                        raise DraftValidationError(
                            f"Link '{link_id}' VLAN settings require a Switch end",
                            kind="link",
                            item_id=link_id,
                            control_id=end_port_control,
                        )
                    cls._vlan(end["vlan"], link_id, node_id, interface)
                if port in occupied_ports[node_id]:
                    raise DraftValidationError(
                        f"Node has two Interfaces assigned to port {port}",
                        kind="link",
                        item_id=link_id,
                        control_id=end_port_control,
                    )
                occupied_ports[node_id].add(port)
                interface_ids.add(interface)
                router_interfaces[node_id][interface] = port
                used_nodes.add(node_id)
                if node_id in switch_ids and (end.get("ipv4") or end.get("ipv6")):
                    addr_field = "ipv4" if end.get("ipv4") else "ipv6"
                    raise DraftValidationError(
                        f"Link '{link_id}': Switch {node_name_by_id[node_id]} is layer 2, so its "
                        "Interface takes no address; put it on the Host or Internet Gateway end",
                        kind="node",
                        item_id=node_id,
                        control_id=f"iface-{interface}-{addr_field}",
                    )
                cls._address(
                    end.get("ipv4"),
                    4,
                    link_id,
                    end_index,
                    node_id=node_id,
                    control_id=f"iface-{interface}-ipv4",
                )
                cls._address(
                    end.get("ipv6"),
                    6,
                    link_id,
                    end_index,
                    node_id=node_id,
                    control_id=f"iface-{interface}-ipv6",
                )
                for flag, field in (("dhcp4", "ipv4"), ("dhcp6", "ipv6")):
                    if flag in end:
                        cls._dhcp_client(
                            end,
                            flag,
                            field,
                            node_types[node_id],
                            link_id,
                            node_id=node_id,
                            interface_id=interface,
                        )
                if "mac" in end:
                    mac = end["mac"]
                    if node_types[node_id] != "host":
                        raise DraftValidationError(
                            f"Link '{link_id}' only a Host Interface can pin its MAC address",
                            kind="node",
                            item_id=node_id,
                            control_id=f"iface-{interface}-mac",
                        )
                    if not isinstance(mac, str) or not cls._mac_pattern.fullmatch(mac):
                        raise DraftValidationError(
                            f"Link '{link_id}' Interface {end_index} MAC address needs six "
                            "colon-separated hex octets, e.g. aa:bb:cc:dd:ee:01",
                            kind="node",
                            item_id=node_id,
                            control_id=f"iface-{interface}-mac",
                        )
                    if mac.lower() in macs:
                        raise DraftValidationError(
                            f"MAC address {mac} is used by two Interfaces",
                            kind="node",
                            item_id=node_id,
                            control_id=f"iface-{interface}-mac",
                        )
                    macs.add(mac.lower())
                if "vrf" in end:
                    vrf = end["vrf"]
                    if node_types[node_id] != "router":
                        raise DraftValidationError(
                            f"Link '{link_id}' only a Router Interface can be placed in a VRF",
                            kind="node",
                            item_id=node_id,
                            control_id=f"iface-{interface}-vrf",
                        )
                    if not isinstance(vrf, str) or not vrf:
                        raise DraftValidationError(
                            f"Link '{link_id}' Interface {end_index} VRF must name one of its Router's VRFs",
                            kind="node",
                            item_id=node_id,
                            control_id=f"iface-{interface}-vrf",
                        )
                    vrf_members.setdefault((node_id, vrf), []).append(
                        (f"{node_name_by_id[node_id]}-eth{port}", interface))
                if "outside" in end:
                    if end["outside"] is not True:
                        raise DraftValidationError(
                            f"Link '{link_id}' Interface {end_index} outside mark is true when set",
                            kind="link",
                            item_id=link_id,
                            control_id=end_port_control,
                        )
                    if node_types[node_id] != "router":
                        raise DraftValidationError(
                            f"Link '{link_id}' only a Router Interface can be outside",
                            kind="node",
                            item_id=node_id,
                            control_id=f"iface-{interface}-outside",
                        )
            link_ids.add(link_id)

        vrf_names = {node["id"]: {item["name"] for item in node.get("vrfs", [])}
                     for node in nodes if node["type"] == "router"}
        for (node_id, vrf), members in vrf_members.items():
            if vrf not in vrf_names[node_id]:
                member_labels = sorted(label for label, _iface in members)
                first_iface = sorted(members, key=lambda item: item[0])[0][1]
                raise DraftValidationError(
                    f"Router '{node_name_by_id[node_id]}' has no VRF '{vrf}', which "
                    f"{', '.join(member_labels)} {'is' if len(member_labels) == 1 else 'are'} in; "
                    "move them to another VRF first",
                    kind="node",
                    item_id=node_id,
                    control_id=f"iface-{first_iface}-vrf",
                )

        # A gateway field leaves by its Host's lowest-numbered Interface, as the
        # generated addHost gateway does at start.
        lowest_ends: Dict[str, Dict[str, Any]] = {}
        for link in links:
            for end in link["ends"]:
                lowest = lowest_ends.get(end["node_id"])
                if lowest is None or end["port"] < lowest["port"]:
                    lowest_ends[end["node_id"]] = end
        for node in nodes:
            if "nameservers" in node:
                try:
                    authored_nameservers(node["nameservers"], topology)
                except ValueError as exc:
                    ns_control = cls._nameservers_error_control(node["nameservers"], topology)
                    raise DraftValidationError(
                        f"Host '{node['name']}' nameservers: {exc}",
                        kind="node",
                        item_id=node["id"],
                        control_id=ns_control,
                    ) from exc
            if "resolver" in node:
                try:
                    authored_resolver(node["resolver"], topology)
                except (TypeError, ValueError) as exc:
                    res_control = cls._resolver_error_control(node["resolver"], topology)
                    raise DraftValidationError(
                        f"Node '{node['name']}' Resolver: {exc}",
                        kind="node",
                        item_id=node["id"],
                        control_id=res_control,
                    ) from exc
        for node in nodes:
            end = lowest_ends.get(node["id"], {})
            for family, source in ((4, "DHCP exchange"), (6, "Router Advertisement")):
                if end.get(f"dhcp{family}") and node.get(f"ipv{family}_gateway"):
                    raise DraftValidationError(
                        f"Host '{node['name']}' IPv{family} default gateway leaves by "
                        f"{node['name']}-eth{end['port']}, a DHCPv{family} client Interface, "
                        f"where the {source} supplies it; clear the gateway or the "
                        f"DHCPv{family} client",
                        kind="node",
                        item_id=node["id"],
                        control_id=f"host-ipv{family}-gateway",
                    )

        pairs: Dict[frozenset[str], List[Dict[str, Any]]] = {}
        for link in links:
            pair = frozenset(end["node_id"] for end in link["ends"])
            pairs.setdefault(pair, []).append(link)
        for group in pairs.values():
            if len(group) > 1:
                conditioned = next((item for item in group if "condition" in item), None)
                if conditioned is not None:
                    raise DraftValidationError(
                        "Initial Link Conditions require one Link between each Node pair",
                        kind="link",
                        item_id=conditioned["id"],
                        control_id="link-condition-status",
                    )

        subinterface_ids: set[str] = set()
        for node in nodes:
            if node["type"] != "router":
                continue
            subinterfaces = node.get("subinterfaces", [])
            if not isinstance(subinterfaces, list):
                raise DraftValidationError(
                    f"Router '{node['name']}' subinterfaces must be a list",
                    kind="node",
                    item_id=node["id"],
                    control_id="router-subinterfaces",
                )
            parents: set[tuple[str, int]] = set()
            for sub_index, sub in enumerate(subinterfaces):
                sbase = f"subinterface-{sub_index}"
                if not isinstance(sub, dict) or set(sub) != {
                    "id", "parent_interface_id", "vlan", "ipv4", "ipv6"
                }:
                    raise DraftValidationError(
                        f"Router '{node['name']}' subinterface fields are incomplete",
                        kind="node",
                        item_id=node["id"],
                        control_id=f"{sbase}-parent",
                    )
                sub_id = sub["id"]
                parent = sub["parent_interface_id"]
                vlan = sub["vlan"]
                if not isinstance(sub_id, str) or not sub_id or len(sub_id) > 80 or sub_id in subinterface_ids:
                    raise DraftValidationError(
                        "Subinterface identity is invalid or duplicated",
                        kind="node",
                        item_id=node["id"],
                        control_id=f"{sbase}-parent",
                    )
                if parent not in router_interfaces[node["id"]]:
                    raise DraftValidationError(
                        f"Router '{node['name']}' subinterface has no attached parent Interface",
                        kind="node",
                        item_id=node["id"],
                        control_id=f"{sbase}-parent",
                    )
                cls._vlan_id(
                    vlan,
                    "Subinterface VLAN",
                    kind="node",
                    item_id=node["id"],
                    control_id=f"{sbase}-vlan",
                )
                if (parent, vlan) in parents:
                    raise DraftValidationError(
                        f"Router '{node['name']}' has duplicate subinterface VLAN {vlan}",
                        kind="node",
                        item_id=node["id"],
                        control_id=f"{sbase}-vlan",
                    )
                port = router_interfaces[node["id"]][parent]
                if len(f"{node['name']}-eth{port}.{vlan}") > MAX_INTF_NAME_LEN:
                    raise DraftValidationError(
                        "Subinterface name exceeds the Linux limit",
                        kind="node",
                        item_id=node["id"],
                        control_id=f"{sbase}-vlan",
                    )
                cls._address(
                    sub["ipv4"],
                    4,
                    sub_id,
                    1,
                    node_id=node["id"],
                    control_id=f"{sbase}-ipv4",
                )
                cls._address(
                    sub["ipv6"],
                    6,
                    sub_id,
                    1,
                    node_id=node["id"],
                    control_id=f"{sbase}-ipv6",
                )
                parents.add((parent, vlan))
                subinterface_ids.add(sub_id)
        cls._router_nat_refusals(nodes, links, node_name_by_id, node_types)
        return node_ids

    @classmethod
    def _router_nat_refusals(
        cls,
        nodes: List[Dict[str, Any]],
        links: List[Dict[str, Any]],
        node_name_by_id: Dict[str, str],
        node_types: Dict[str, str],
    ) -> None:
        """Refuse the Router NAT declarations the Lab would refuse when it is built.

        Mirrors ``mniplab.routernat.validate_network``: an outside Interface may
        not face an Internet Gateway or the Speaker in front of one, and each NAT
        entry needs an outside Interface it can apply to, named when its Router
        has several.

        Raises:
            DraftValidationError: Naming the Router, its NAT entry or Interface,
                with the control of the row or Interface that needs fixing.
        """
        for node in nodes:
            if node["type"] != "router":
                continue
            router = node["name"]
            interfaces: Dict[str, Dict[str, Any]] = {}
            outside: Dict[str, Dict[str, Any]] = {}
            for link in links:
                for index, end in enumerate(link["ends"]):
                    if end["node_id"] != node["id"]:
                        continue
                    name = f"{router}-eth{end['port']}"
                    interfaces[name] = end
                    if end.get("outside"):
                        outside[name] = end
                        cls._outside_far_end_refusal(
                            node, name, end, link["ends"][1 - index]["node_id"],
                            links, node_types, node_name_by_id)
            for index, entry in enumerate(node.get("nat", []), start=1):
                label = f"Router '{router}' NAT entry {index}"
                control = f"router-nat-{index - 1}-interface"
                interface = entry.get("interface")
                if not outside:
                    raise DraftValidationError(
                        f"{label} needs an outside Interface; mark one on its Link end with outside",
                        kind="node", item_id=node["id"], control_id=control)
                if interface is None:
                    if len(outside) > 1:
                        raise DraftValidationError(
                            f"{label} names no Interface, and {router} has {len(outside)} outside "
                            f"Interfaces ({', '.join(outside)}); name one with interface",
                            kind="node", item_id=node["id"], control_id=control)
                    target = next(iter(outside))
                else:
                    if interface not in interfaces:
                        raise DraftValidationError(
                            f"{label} names {interface}, which is not an Interface of {router}",
                            kind="node", item_id=node["id"], control_id=control)
                    if interface not in outside:
                        raise DraftValidationError(
                            f"{label} names {interface}, which is not marked outside; mark it "
                            "with outside on its Link end",
                            kind="node", item_id=node["id"], control_id=control)
                    target = interface
                if entry["kind"] == "port_forward" and not outside[target].get("ipv4"):
                    raise DraftValidationError(
                        f"{label} needs an IPv4 address on its outside Interface {target}",
                        kind="node", item_id=node["id"], control_id=control)

    @staticmethod
    def _outside_far_end_refusal(
        node: Dict[str, Any],
        name: str,
        end: Dict[str, Any],
        far_id: str,
        links: List[Dict[str, Any]],
        node_types: Dict[str, str],
        node_name_by_id: Dict[str, str],
    ) -> None:
        """Refuse an outside Interface whose far end already translates.

        Raises:
            DraftValidationError: If the Interface faces an Internet Gateway, or
                faces the Speaker in front of a Transit one.
        """
        control = f"iface-{end['interface_id']}-outside"
        router = node["name"]
        if node_types[far_id] == "nat":
            raise DraftValidationError(
                f"Router '{router}' Interface {name} is marked outside, but faces the Internet "
                f"Gateway {node_name_by_id[far_id]}, which already translates there",
                kind="node", item_id=node["id"], control_id=control)
        for link in links:
            for index, other in enumerate(link["ends"]):
                if other["node_id"] != far_id:
                    continue
                beyond = link["ends"][1 - index]["node_id"]
                if node_types[beyond] == "transit":
                    raise DraftValidationError(
                        f"Router '{router}' Interface {name} is marked outside, but faces "
                        f"{node_name_by_id[far_id]}, the Speaker in front of the Internet Gateway "
                        f"{node_name_by_id[beyond]}, which already translates there",
                        kind="node", item_id=node["id"], control_id=control)

    @classmethod
    def _validate_presentation(cls, document: Dict[str, Any],
                               node_ids: Optional[set[str]]) -> None:
        """Check the Guide and the Authored Layout.

        Args:
            document: The draft.
            node_ids: The Node IDs Layout positions must name, or None for a
                Python-based draft, whose Layout names runtime Nodes.
        """
        guide = document.get("guide")
        cls._text(guide, "Guide", 200_000, control_id="draft-guide")
        layout = document.get("layout")
        if (not isinstance(layout, dict) or set(layout) != {"version", "nodes"}
                or isinstance(layout.get("version"), bool) or layout.get("version") != 1):
            raise DraftValidationError("Authored Layout version is invalid")
        positions = layout.get("nodes")
        if not isinstance(positions, dict):
            raise DraftValidationError("Authored Layout nodes must be an object")
        # A Node a Code section adds is placed under its name, apart from the
        # canvas's own IDs; only running the script knows whether it exists.
        if node_ids is not None and any(
                key not in node_ids and not cls._code_section_node(key) for key in positions):
            raise DraftValidationError("Authored Layout references a missing Node")
        for node_id, position in positions.items():
            is_node = bool(node_ids and node_id in node_ids)
            kind = "node" if is_node else "draft"
            item_id = node_id if is_node else None
            if not isinstance(position, dict):
                raise DraftValidationError(
                    "Authored Layout position must be an object",
                    kind=kind,
                    item_id=item_id,
                    control_id="host-x" if is_node else None,
                )
            if set(position) != {"x", "y"}:
                raise DraftValidationError(
                    f"Authored Layout position for '{node_id}' is incomplete",
                    kind=kind,
                    item_id=item_id,
                    control_id="host-x" if is_node else None,
                )
            cls._coordinate(
                position.get("x"),
                f"Layout Node '{node_id}' x",
                kind=kind,
                item_id=item_id,
                control_id="host-x" if is_node else None,
            )
            cls._coordinate(
                position.get("y"),
                f"Layout Node '{node_id}' y",
                kind=kind,
                item_id=item_id,
                control_id="host-y" if is_node else None,
            )

    @classmethod
    def _code_section_node(cls, key: str) -> Optional[str]:
        """The name a Layout key gives a Node from the Code section, if it is one."""
        if not isinstance(key, str) or not key.startswith("code:"):
            return None
        name = key[len("code:"):]
        return name if cls._name_pattern.fullmatch(name) else None

    @staticmethod
    def _vlan_id(
        value: Any,
        label: str,
        *,
        kind: str = "node",
        item_id: Optional[str] = None,
        control_id: Optional[str] = None,
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4094:
            raise DraftValidationError(
                f"{label} must be an integer from 1 to 4094",
                kind=kind,
                item_id=item_id,
                control_id=control_id,
            )

    @classmethod
    def _vlan(cls, value: Any, link_id: str, node_id: str, interface_id: str) -> None:
        if not isinstance(value, dict):
            raise DraftValidationError(
                f"Link '{link_id}' VLAN setting must be an object",
                kind="node",
                item_id=node_id,
                control_id=f"iface-{interface_id}-vlan-mode",
            )
        mode = value.get("mode")
        if mode == "access" and set(value) == {"mode", "vlan"}:
            cls._vlan_id(
                value["vlan"],
                "Access VLAN",
                kind="node",
                item_id=node_id,
                control_id=f"iface-{interface_id}-access-vlan",
            )
        elif mode == "trunk" and set(value) == {"mode", "trunks", "native"}:
            trunks = value["trunks"]
            if not isinstance(trunks, list) or not trunks or len(trunks) != len(set(map(str, trunks))):
                raise DraftValidationError(
                    f"Link '{link_id}' trunk VLANs must be a distinct nonempty list",
                    kind="node",
                    item_id=node_id,
                    control_id=f"iface-{interface_id}-trunks",
                )
            for vlan in trunks:
                cls._vlan_id(
                    vlan,
                    "Trunk VLAN",
                    kind="node",
                    item_id=node_id,
                    control_id=f"iface-{interface_id}-trunks",
                )
            if value["native"] is not None:
                cls._vlan_id(
                    value["native"],
                    "Native VLAN",
                    kind="node",
                    item_id=node_id,
                    control_id=f"iface-{interface_id}-native",
                )
        else:
            raise DraftValidationError(
                f"Link '{link_id}' VLAN mode must be access or trunk",
                kind="node",
                item_id=node_id,
                control_id=f"iface-{interface_id}-vlan-mode",
            )

    @staticmethod
    def _text(
        value: Any,
        label: str,
        limit: int,
        *,
        kind: str = "draft",
        item_id: Optional[str] = None,
        control_id: Optional[str] = None,
    ) -> str:
        if not isinstance(value, str) or len(value) > limit:
            raise DraftValidationError(
                f"{label} must be text no longer than {limit} characters",
                kind=kind,
                item_id=item_id,
                control_id=control_id,
            )
        return value

    @staticmethod
    def _coordinate(
        value: Any,
        label: str,
        *,
        kind: str = "draft",
        item_id: Optional[str] = None,
        control_id: Optional[str] = None,
    ) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise DraftValidationError(
                f"{label} must be a finite number",
                kind=kind,
                item_id=item_id,
                control_id=control_id,
            )
        return float(value)

    @staticmethod
    def _gateway(value: Any, family: int, name: str, node_id: str) -> None:
        if value == "":
            return
        label = f"Host '{name}' IPv{family} default gateway"
        control_id = f"host-ipv{family}-gateway"
        if not isinstance(value, str):
            raise DraftValidationError(
                f"{label} must be text",
                kind="node",
                item_id=node_id,
                control_id=control_id,
            )
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise DraftValidationError(
                f"{label} is not an address",
                kind="node",
                item_id=node_id,
                control_id=control_id,
            ) from exc
        if address.version != family:
            raise DraftValidationError(
                f"{label} must be an IPv{family} address",
                kind="node",
                item_id=node_id,
                control_id=control_id,
            )

    @classmethod
    def _router(cls, node: Dict[str, Any], name: str, node_id: str) -> None:
        protocols = node.get("protocols")
        if (not isinstance(protocols, list)
                or any(item not in ROUTER_PROTOCOLS for item in protocols)
                or len(set(protocols)) != len(protocols)):
            raise DraftValidationError(
                f"Router '{name}' Protocols must be distinct known Protocols",
                kind="node",
                item_id=node_id,
                control_id="router-protocols",
            )
        cls._text(
            node.get("frr_config"),
            f"Router '{name}' FRR configuration",
            100_000,
            kind="node",
            item_id=node_id,
            control_id="router-frr-config",
        )
        if not isinstance(node.get("fast_timers"), bool):
            raise DraftValidationError(
                f"Router '{name}' fast timers must be true or false",
                kind="node",
                item_id=node_id,
                control_id="router-fast-timers",
            )
        if "vrfs" in node:
            cls._vrfs(node["vrfs"], name, node_id)
        if "nat" in node:
            cls._router_nat(node["nat"], name, node_id)

    @classmethod
    def _router_nat(cls, entries: Any, name: str, node_id: str) -> None:
        """Check a Router's NAT rows: each a port forward or a static mapping the Lab accepts."""
        if not isinstance(entries, list) or len(entries) > 64:
            raise DraftValidationError(
                f"Router '{name}' NAT must be a list of at most 64 entries",
                kind="node",
                item_id=node_id,
                control_id="router-nat",
            )
        for index, entry in enumerate(entries, start=1):
            try:
                cls._router_nat_entry(entry)
            except ValueError as exc:
                raise DraftValidationError(
                    f"Router '{name}' NAT entry {index}: {exc}",
                    kind="node",
                    item_id=node_id,
                    control_id="router-nat",
                ) from exc

    @staticmethod
    def _router_nat_entry(entry: Any) -> None:
        """Build one NAT row as the Lab would, so the Lab's own checks decide what is valid.

        Args:
            entry: One ``nat`` row of a Router node.

        Raises:
            ValueError: If the row is not a port forward or a static mapping,
                or one of its fields is not valid.
        """
        if not isinstance(entry, dict):
            raise ValueError("an entry must be an object")
        interface = entry.get("interface")
        if interface is not None and not isinstance(interface, str):
            raise ValueError("its Interface must be the name of an Interface, as text")
        kind = entry.get("kind")
        if kind == "port_forward":
            required = {"kind", "protocol", "outside_port", "inside", "inside_port", "hairpin"}
            if not required <= set(entry) <= required | {"interface"}:
                raise ValueError("a port forward names its protocol, ports, inside address and hairpin")
            if not isinstance(entry["hairpin"], bool):
                raise ValueError("hairpin is true or false")
            PortForward(entry["protocol"], entry["outside_port"], entry["inside"],
                        entry["inside_port"], hairpin=entry["hairpin"], interface=interface)
        elif kind == "static":
            required = {"kind", "inside", "outside"}
            if not required <= set(entry) <= required | {"interface"}:
                raise ValueError("a static mapping names its inside and outside addresses")
            StaticNAT(entry["inside"], entry["outside"], interface=interface)
        else:
            raise ValueError("its kind is a port forward or a static mapping")

    @classmethod
    def _vrfs(cls, vrfs: Any, name: str, node_id: str) -> None:
        """Check a Router's VRF rows: a Linux device name and a routing table each."""
        if not isinstance(vrfs, list) or len(vrfs) > 64:
            raise DraftValidationError(
                f"Router '{name}' VRFs must be a list of at most 64 rows",
                kind="node",
                item_id=node_id,
                control_id="router-vrfs",
            )
        names: set[str] = set()
        tables: set[int] = set()
        for vrf_index, vrf in enumerate(vrfs):
            vbase = f"vrf-{vrf_index}"
            if not isinstance(vrf, dict) or set(vrf) != {"name", "table"}:
                raise DraftValidationError(
                    f"Router '{name}' VRF needs a name and a table ID",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{vbase}-name",
                )
            vrf_name, table = vrf["name"], vrf["table"]
            if not isinstance(vrf_name, str) or not cls._vrf_pattern.fullmatch(vrf_name):
                raise DraftValidationError(
                    f"Router '{name}' VRF name {vrf_name!r} must start with a letter and use "
                    f"at most {MAX_INTF_NAME_LEN} letters, digits, '-' or '_'",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{vbase}-name",
                )
            if vrf_name == "default":
                raise DraftValidationError(
                    f"Router '{name}' VRF name 'default' is FRR's own default VRF",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{vbase}-name",
                )
            if vrf_name == "lo" or re.fullmatch(rf"{re.escape(name)}-eth\d+", vrf_name):
                raise DraftValidationError(
                    f"Router '{name}' VRF name '{vrf_name}' is one of its Interfaces",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{vbase}-name",
                )
            if (isinstance(table, bool) or not isinstance(table, int)
                    or not 1 <= table <= 4294967295 or table in (253, 254, 255)):
                raise DraftValidationError(
                    f"Router '{name}' VRF '{vrf_name}' table ID must be from 1 to 4294967295, "
                    "other than the kernel's own tables 253, 254 and 255",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{vbase}-table",
                )
            if vrf_name in names:
                raise DraftValidationError(
                    f"Router '{name}' has two VRFs named '{vrf_name}'",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{vbase}-name",
                )
            if table in tables:
                raise DraftValidationError(
                    f"Router '{name}' has two VRFs using table {table}",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{vbase}-table",
                )
            names.add(vrf_name)
            tables.add(table)

    @staticmethod
    def _asn(
        value: Any,
        label: str,
        *,
        node_id: Optional[str] = None,
        control_id: Optional[str] = None,
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4294967295:
            raise DraftValidationError(
                f"{label} must be an AS number from 1 to 4294967295",
                kind="node" if node_id else "draft",
                item_id=node_id,
                control_id=control_id,
            )

    @classmethod
    def _speaker_routes(
        cls,
        routes: Any,
        family: Optional[int],
        label: str,
        *,
        node_id: Optional[str] = None,
        prefix: str = "speaker-routes",
    ) -> None:
        if not isinstance(routes, list) or len(routes) > 2000:
            raise DraftValidationError(
                f"{label} Routes must be a list of at most 2000",
                kind="node" if node_id else "draft",
                item_id=node_id,
                control_id=prefix,
            )
        for route_index, route in enumerate(routes):
            rbase = f"{prefix}-{route_index}"
            if not isinstance(route, dict) or not {"prefix", "next_hop"} <= set(route) <= {
                "prefix", "next_hop", "community", "as_path", "large_community", "med"
            }:
                raise DraftValidationError(
                    f"{label} Route fields are invalid",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id=f"{rbase}-prefix",
                )
            field_control = f"{rbase}-prefix"
            try:
                if not isinstance(route["prefix"], str):
                    field_control = f"{rbase}-prefix"
                    raise ValueError("Route addresses must be text")
                if not isinstance(route["next_hop"], str):
                    field_control = f"{rbase}-next_hop"
                    raise ValueError("Route addresses must be text")
                field_control = f"{rbase}-prefix"
                route_prefix = ipaddress.ip_network(route["prefix"], strict=True)
                field_control = f"{rbase}-next_hop"
                next_hop = ipaddress.ip_address(route["next_hop"])
            except ValueError as exc:
                raise DraftValidationError(
                    f"{label} Route prefix or next hop is invalid",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id=field_control,
                ) from exc
            if route_prefix.version != next_hop.version or (family and route_prefix.version != family):
                mismatch_control = (
                    f"{rbase}-next_hop"
                    if route_prefix.version != next_hop.version
                    else f"{rbase}-prefix"
                )
                raise DraftValidationError(
                    f"{label} Route address families do not match",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id=mismatch_control,
                )
            for key in ("community", "large_community"):
                values = route.get(key, [])
                if not isinstance(values, list) or any(not isinstance(item, str) or
                                                       not re.fullmatch(r"[0-9]+(?::[0-9]+){1,2}", item)
                                                       for item in values):
                    raise DraftValidationError(
                        f"{label} Route {key} values are invalid",
                        kind="node" if node_id else "draft",
                        item_id=node_id,
                        control_id=f"{rbase}-{key}",
                    )
            path = route.get("as_path", [])
            if not isinstance(path, list):
                raise DraftValidationError(
                    f"{label} Route AS path is invalid",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id=f"{rbase}-as_path",
                )
            for item in path:
                cls._asn(
                    item,
                    f"{label} Route AS path",
                    node_id=node_id,
                    control_id=f"{rbase}-as_path",
                )
            med = route.get("med")
            if med is not None and (isinstance(med, bool) or not isinstance(med, int)
                                    or not 0 <= med <= 4294967295):
                raise DraftValidationError(
                    f"{label} Route MED is invalid",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id=f"{rbase}-med",
                )

    @classmethod
    def _speaker(cls, node: Dict[str, Any], name: str, node_id: str) -> None:
        cls._asn(
            node.get("local_as"),
            f"Speaker '{name}' local AS",
            node_id=node_id,
            control_id="speaker-local-as",
        )
        if "route_generator" in node:
            cls._route_generator(
                node["route_generator"],
                f"Speaker '{name}' Global",
                node_id=node_id,
                prefix="speaker-generator",
            )
        cls._speaker_routes(
            node.get("routes"),
            None,
            f"Speaker '{name}'",
            node_id=node_id,
            prefix="speaker-routes",
        )
        neighbors = node.get("neighbors")
        if not isinstance(neighbors, list) or not 1 <= len(neighbors) <= 32:
            raise DraftValidationError(
                f"Speaker '{name}' needs one to 32 Neighbors",
                kind="node",
                item_id=node_id,
                control_id="speaker-neighbors",
            )
        local_addresses: set[str] = set()
        peers: set[str] = set()
        for neighbor_index, neighbor in enumerate(neighbors):
            nbase = f"speaker-neighbors-{neighbor_index}"
            required = {"peer", "local_address", "local_as", "peer_as", "hold_time", "routes"}
            if not isinstance(neighbor, dict) or not (
                required <= set(neighbor) <= required | {"route_generator"}
            ):
                raise DraftValidationError(
                    f"Speaker '{name}' Neighbor fields are invalid",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{nbase}-peer",
                )
            addr_control = f"{nbase}-peer"
            try:
                if not isinstance(neighbor["peer"], str):
                    addr_control = f"{nbase}-peer"
                    raise ValueError("Neighbor addresses must be text")
                if not isinstance(neighbor["local_address"], str):
                    addr_control = f"{nbase}-local_address"
                    raise ValueError("Neighbor addresses must be text")
                addr_control = f"{nbase}-peer"
                peer = ipaddress.ip_address(neighbor["peer"])
                addr_control = f"{nbase}-local_address"
                local = ipaddress.ip_address(neighbor["local_address"])
            except ValueError as exc:
                raise DraftValidationError(
                    f"Speaker '{name}' Neighbor addresses are invalid",
                    kind="node",
                    item_id=node_id,
                    control_id=addr_control,
                ) from exc
            if peer.version != local.version or peer == local:
                raise DraftValidationError(
                    f"Speaker '{name}' Neighbor address families must match",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{nbase}-local_address",
                )
            if str(local) in local_addresses or str(peer) in peers:
                dup_control = f"{nbase}-peer" if str(peer) in peers else f"{nbase}-local_address"
                raise DraftValidationError(
                    f"Speaker '{name}' Neighbors need distinct peer and local addresses",
                    kind="node",
                    item_id=node_id,
                    control_id=dup_control,
                )
            local_addresses.add(str(local))
            peers.add(str(peer))
            cls._asn(
                neighbor["local_as"],
                f"Speaker '{name}' Neighbor local AS",
                node_id=node_id,
                control_id=f"{nbase}-local_as",
            )
            cls._asn(
                neighbor["peer_as"],
                f"Speaker '{name}' Neighbor peer AS",
                node_id=node_id,
                control_id=f"{nbase}-peer_as",
            )
            hold = neighbor["hold_time"]
            if hold is not None and (isinstance(hold, bool) or not isinstance(hold, int)
                                     or not 3 <= hold <= 65535):
                raise DraftValidationError(
                    f"Speaker '{name}' Neighbor hold time is invalid",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{nbase}-hold_time",
                )
            cls._speaker_routes(
                neighbor["routes"],
                peer.version,
                f"Speaker '{name}' Neighbor",
                node_id=node_id,
                prefix=f"{nbase}-routes",
            )
            if "route_generator" in neighbor:
                cls._route_generator(
                    neighbor["route_generator"],
                    f"Speaker '{name}' Neighbor",
                    node_id=node_id,
                    prefix=f"{nbase}-generator",
                )
            if not neighbor["routes"]:
                cls._speaker_routes(
                    node["routes"],
                    peer.version,
                    f"Speaker '{name}' Global",
                    node_id=node_id,
                    prefix="speaker-routes",
                )

    @classmethod
    def _startup(cls, commands: Any, name: str, node_id: str) -> None:
        if not isinstance(commands, list) or len(commands) > 50:
            raise DraftValidationError(
                f"Node '{name}' Startup commands must be a list of at most 50",
                kind="node",
                item_id=node_id,
                control_id="node-startup",
            )
        for cmd_index, command in enumerate(commands):
            cbase = f"startup-{cmd_index}"
            if not isinstance(command, dict) or set(command) != {
                "stage", "command", "timeout", "may_fail"
            }:
                raise DraftValidationError(
                    f"Node '{name}' Startup command fields are incomplete",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{cbase}-command",
                )
            if command["stage"] not in STAGES:
                raise DraftValidationError(
                    f"Node '{name}' Startup command stage is unknown",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{cbase}-stage",
                )
            text = cls._text(
                command["command"],
                f"Node '{name}' Startup command",
                2000,
                kind="node",
                item_id=node_id,
                control_id=f"{cbase}-command",
            )
            if not text.strip():
                raise DraftValidationError(
                    f"Node '{name}' has an empty Startup command",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{cbase}-command",
                )
            try:
                check_timeout(command["timeout"])
            except ValueError as exc:
                raise DraftValidationError(
                    f"Node '{name}' {exc}",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{cbase}-timeout",
                ) from exc
            if not isinstance(command["may_fail"], bool):
                raise DraftValidationError(
                    f"Node '{name}' Startup command 'may fail' must be true or false",
                    kind="node",
                    item_id=node_id,
                    control_id=f"{cbase}-may_fail",
                )

    @staticmethod
    def _link_condition(value: Any, link_id: Optional[str] = None) -> None:
        """Validate the initial intent accepted by Mininet's TCLink."""
        if not isinstance(value, dict) or set(value) != {
            "status", "delay", "jitter", "loss", "bandwidth", "scheduler"
        }:
            raise DraftValidationError(
                "Link Condition fields are invalid",
                kind="link",
                item_id=link_id,
                control_id="link-condition-status",
            )
        if value["status"] not in ("up", "down"):
            raise DraftValidationError(
                "Link Condition status must be up or down",
                kind="link",
                item_id=link_id,
                control_id="link-condition-status",
            )
        for field in ("delay", "jitter"):
            part = value[field]
            if part is not None and (not isinstance(part, str) or
                                     not re.fullmatch(r"(?:\d+(?:\.\d+)?)(?:us|ms|s)", part)):
                raise DraftValidationError(
                    f"Link Condition {field} needs a duration with units",
                    kind="link",
                    item_id=link_id,
                    control_id=f"link-condition-{field}",
                )
        if value["jitter"] and not value["delay"]:
            raise DraftValidationError(
                "Link Condition jitter requires delay",
                kind="link",
                item_id=link_id,
                control_id="link-condition-jitter",
            )
        loss = value["loss"]
        if loss is not None and (not isinstance(loss, str) or
                                 not re.fullmatch(r"(?:\d+(?:\.\d+)?)%", loss)
                                 or float(loss[:-1]) > 100):
            raise DraftValidationError(
                "Link Condition loss needs a percentage from 0 to 100",
                kind="link",
                item_id=link_id,
                control_id="link-condition-loss",
            )
        bandwidth = value["bandwidth"]
        if bandwidth is not None and (isinstance(bandwidth, bool) or
                                      not isinstance(bandwidth, (int, float)) or
                                      not math.isfinite(bandwidth) or bandwidth <= 0):
            raise DraftValidationError(
                "Link Condition bandwidth needs positive Mbps",
                kind="link",
                item_id=link_id,
                control_id="link-condition-bandwidth",
            )
        if value["scheduler"] not in ("hfsc", "htb", "tbf"):
            raise DraftValidationError(
                "Link Condition scheduler must be hfsc, htb or tbf",
                kind="link",
                item_id=link_id,
                control_id="link-condition-scheduler",
            )
        if bandwidth is None and value["scheduler"] != "hfsc":
            raise DraftValidationError(
                "Link Condition scheduler needs bandwidth",
                kind="link",
                item_id=link_id,
                control_id="link-condition-scheduler",
            )

    @staticmethod
    def _dhcp_client(
        end: Dict[str, Any],
        flag: str,
        field: str,
        node_type: str,
        link_id: str,
        *,
        node_id: Optional[str] = None,
        interface_id: Optional[str] = None,
    ) -> None:
        """A client Interface is left bare for a Learner's DHCP client to address."""
        control_id = f"iface-{interface_id}-{field}" if interface_id else None
        if not isinstance(end[flag], bool):
            raise DraftValidationError(
                f"Link '{link_id}' {flag} must be true or false",
                kind="node" if node_id else "link",
                item_id=node_id or link_id,
                control_id=control_id,
            )
        if end[flag] and node_type != "host":
            raise DraftValidationError(
                f"Link '{link_id}' DHCP clients must be Hosts",
                kind="node" if node_id else "link",
                item_id=node_id or link_id,
                control_id=control_id,
            )
        if end[flag] and end[field]:
            raise DraftValidationError(
                f"Link '{link_id}' DHCP client Interface cannot also have a static "
                f"{field.replace('ip', 'IP')} address",
                kind="node" if node_id else "link",
                item_id=node_id or link_id,
                control_id=control_id,
            )

    @classmethod
    def _dhcp_pool_error_control(cls, pool: Any, pool_index: int, message: str = "") -> str:
        """Identify the dialog control corresponding to a malformed DHCP Pool."""
        base = f"dhcp-pools-{pool_index}"
        if not isinstance(pool, dict) or set(pool) != _POOL_FIELDS:
            return f"{base}-cidr"
        for field in ("cidr", "start", "end", "router", "dns_server"):
            if not isinstance(pool[field], str):
                return f"{base}-{field}"
        if not isinstance(pool["reservations"], list):
            return f"{base}-cidr"
        for res_index, item in enumerate(pool["reservations"]):
            if (not isinstance(item, dict) or set(item) != _RESERVATION_FIELDS
                    or any(not isinstance(item[field], str) for field in _RESERVATION_FIELDS)):
                return f"{base}-reservations-{res_index}-ip"
            try:
                Reservation(**item)
            except ValueError:
                return f"{base}-reservations-{res_index}-identifier"
        try:
            network = ipaddress.ip_network(pool["cidr"], strict=False)
        except ValueError:
            return f"{base}-cidr"
        family = network.version
        if family == 4 and pool["preferred_lifetime"] is not None:
            return f"{base}-preferred_lifetime"
        if pool["preferred_lifetime"] is not None:
            if (isinstance(pool["preferred_lifetime"], bool)
                    or not isinstance(pool["preferred_lifetime"], int)
                    or pool["preferred_lifetime"] <= 0
                    or (isinstance(pool["lease_time"], int)
                        and not isinstance(pool["lease_time"], bool)
                        and pool["preferred_lifetime"] > pool["lease_time"])):
                return f"{base}-preferred_lifetime"
        if pool["lease_time"] is not None:
            if isinstance(pool["lease_time"], bool) or not isinstance(pool["lease_time"], int) or pool["lease_time"] <= 0:
                return f"{base}-lease_time"
        if pool["dns_server"]:
            try:
                addr = ipaddress.ip_address(pool["dns_server"])
                if addr.version != family:
                    return f"{base}-dns_server"
            except ValueError:
                return f"{base}-dns_server"
        if pool["router"]:
            if family == 6:
                return f"{base}-router"
            try:
                addr = ipaddress.ip_address(pool["router"])
                if addr.version != family:
                    return f"{base}-router"
            except ValueError:
                return f"{base}-router"
        if pool["start"] and pool["end"]:
            try:
                start_ip = ipaddress.ip_address(pool["start"])
            except ValueError:
                return f"{base}-start"
            if (start_ip.version != family or start_ip not in network
                    or (family == 4 and network.prefixlen <= 30 and start_ip == network.network_address)
                    or (family == 6 and start_ip == network.network_address)):
                return f"{base}-start"
            try:
                end_ip = ipaddress.ip_address(pool["end"])
            except ValueError:
                return f"{base}-end"
            if (end_ip.version != family or end_ip not in network
                    or int(start_ip) > int(end_ip)
                    or (family == 4 and network.prefixlen <= 30 and end_ip == network.broadcast_address)):
                return f"{base}-end"
        elif pool["start"] or pool["end"]:
            return f"{base}-end" if pool["start"] else f"{base}-start"
        for res_index, item in enumerate(pool["reservations"]):
            if family == 4 and (item["duid"] or not item["mac"] or not cls._mac_pattern.fullmatch(item["mac"])):
                return f"{base}-reservations-{res_index}-identifier"
            if family == 6:
                if bool(item["mac"]) == bool(item["duid"]):
                    return f"{base}-reservations-{res_index}-identifier"
                if item["mac"] and not cls._mac_pattern.fullmatch(item["mac"]):
                    return f"{base}-reservations-{res_index}-identifier"
                if item["duid"] and not re.fullmatch(r"([0-9A-Fa-f]{2}:){3,}[0-9A-Fa-f]{2}", item["duid"]):
                    return f"{base}-reservations-{res_index}-identifier"
            try:
                ip_addr = ipaddress.ip_address(item["ip"])
                if ip_addr.version != family or ip_addr not in network:
                    return f"{base}-reservations-{res_index}-ip"
            except ValueError:
                return f"{base}-reservations-{res_index}-ip"
        # An unfamiliar model refusal must not accuse an unrelated Subnet.
        # A quoted value can still identify the offending field precisely.
        for field in ("cidr", "start", "end", "router", "dns_server"):
            value = pool[field]
            if value and (repr(value) in message or f'"{value}"' in message):
                return f"{base}-{field}"
        return "dhcp-pools"

    @classmethod
    def _dhcp(cls, value: Any, name: str, node_id: str) -> None:
        """Validate a DHCP Service through the Pool and Reservation it builds."""
        if not isinstance(value, dict) or set(value) != {"pools"} or not isinstance(value["pools"], list):
            raise DraftValidationError(
                f"Node '{name}' DHCP Service must hold a list of Pools",
                kind="node",
                item_id=node_id,
                control_id="dhcp-pools",
            )
        cidrs: set[str] = set()
        for pool_index, pool in enumerate(value["pools"]):
            try:
                built = dhcp_pool(pool)
            except (ValueError, TypeError) as exc:
                raise DraftValidationError(
                    f"Node '{name}' DHCP Pool: {exc}",
                    kind="node",
                    item_id=node_id,
                    control_id=cls._dhcp_pool_error_control(pool, pool_index, str(exc)),
                ) from exc
            if built.cidr in cidrs:
                raise DraftValidationError(
                    f"Node '{name}' declares Subnet {built.cidr} in two Pools",
                    kind="node",
                    item_id=node_id,
                    control_id=f"dhcp-pools-{pool_index}-cidr",
                )
            cidrs.add(built.cidr)

    @classmethod
    def _relay(cls, value: Any, name: str, node_id: str) -> None:
        """Validate a Relay through the Relay it builds.

        A destination is not checked for reachability: a Relay forwarding
        into the void is a lesson, not a malformed definition.
        """
        if (not isinstance(value, dict) or set(value) != {"subnets", "servers"}
                or not all(isinstance(value[key], list) and all(isinstance(item, str) for item in value[key])
                           for key in ("subnets", "servers"))):
            raise DraftValidationError(
                f"Node '{name}' DHCP Relay needs lists of Subnets and servers",
                kind="node",
                item_id=node_id,
                control_id="relay-subnets",
            )
        try:
            relay = Relay(*value["subnets"], server=value["servers"])
        except ValueError as exc:
            control_id = "relay-subnets"
            if value["subnets"]:
                subnet_ok = True
                for sub in value["subnets"]:
                    try:
                        ipaddress.ip_network(sub, strict=False)
                    except ValueError:
                        subnet_ok = False
                        break
                if subnet_ok:
                    control_id = "relay-servers"
                    for srv_index, srv in enumerate(value["servers"]):
                        try:
                            ipaddress.ip_address(srv)
                        except ValueError:
                            control_id = f"relay-servers-{srv_index}-address"
                            break
            raise DraftValidationError(
                f"Node '{name}' DHCP Relay: {exc}",
                kind="node",
                item_id=node_id,
                control_id=control_id,
            ) from exc
        if len(relay.servers) != len(value["servers"]):
            raise DraftValidationError(
                f"Node '{name}' DHCP Relay names one server per address family",
                kind="node",
                item_id=node_id,
                control_id="relay-servers-1-address" if len(value["servers"]) > 1 else "relay-servers",
            )
        if len(set(relay.subnets)) != len(relay.subnets):
            raise DraftValidationError(
                f"Node '{name}' DHCP Relay lists a Subnet twice",
                kind="node",
                item_id=node_id,
                control_id="relay-subnets",
            )

    @staticmethod
    def _address(
        value: Any,
        family: int,
        link_id: str,
        end_index: int,
        *,
        node_id: Optional[str] = None,
        control_id: Optional[str] = None,
    ) -> None:
        if value in (None, ""):
            return
        label = f"Link '{link_id}' Interface {end_index}"
        kind = "node" if node_id else "link"
        item_id = node_id if node_id else link_id
        if not isinstance(value, str):
            raise DraftValidationError(
                f"{label} address must be text",
                kind=kind,
                item_id=item_id,
                control_id=control_id,
            )
        try:
            address = ipaddress.ip_interface(value)
        except ValueError as exc:
            raise DraftValidationError(
                f"{label} has an invalid IPv{family} address",
                kind=kind,
                item_id=item_id,
                control_id=control_id,
            ) from exc
        if address.version != family:
            raise DraftValidationError(
                f"{label} address must be IPv{family}",
                kind=kind,
                item_id=item_id,
                control_id=control_id,
            )

    def _blank_document(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "schema_version": self.SCHEMA_VERSION,
            "id": "",
            "revision": 0,
            "created_at": now,
            "updated_at": now,
            "published_revision": None,
            "address_family": "ipv4",
            "persistent_config": False,
            "catalog": {"id": None, "name": "Untitled lab", "description": "", "protocols": []},
            "topology": {"nodes": [], "links": []},
            "guide": "",
            "layout": {"version": 1, "nodes": {}},
        }

    def _draft_path(self, draft_id: str) -> str:
        if not isinstance(draft_id, str) or not re.fullmatch(r"draft-[0-9a-f]{32}", draft_id):
            raise DraftNotFoundError(draft_id)
        return os.path.join(self.drafts_dir, draft_id, "definition.json")

    def _preview_path(self, draft_id: str) -> str:
        if not isinstance(draft_id, str) or not re.fullmatch(r"draft-[0-9a-f]{32}", draft_id):
            raise DraftNotFoundError(draft_id)
        return os.path.join(self.drafts_dir, draft_id, "preview-evidence.json")

    def _base_path(self, draft_id: str) -> str:
        return os.path.join(os.path.dirname(self._draft_path(draft_id)), "publication-base.json")

    def _read_base(self, draft_id: str) -> Optional[Dict[str, Any]]:
        try:
            with open(self._base_path(draft_id), encoding="utf-8") as source:
                base: Dict[str, Any] = json.load(source)
            return base
        except FileNotFoundError:
            return None

    def _snapshot_path(self, draft_id: str) -> str:
        return os.path.join(os.path.dirname(self._preview_path(draft_id)), "preview-snapshot.json")

    def _write_new(self, draft_id: str, document: Dict[str, Any]) -> None:
        path = self._draft_path(draft_id)
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=False)
        try:
            self._atomic_write(path, document)
        except BaseException:
            # This call exclusively created the fresh directory. Failed imports
            # and other new-draft writes must leave no partial draft behind.
            shutil.rmtree(parent)
            raise

    @staticmethod
    def _atomic_write(path: str, document: Dict[str, Any]) -> None:
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=True)
        file_descriptor, temporary = tempfile.mkstemp(prefix=".definition-", dir=parent)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as target:
                json.dump(document, target, ensure_ascii=False, indent=2, allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def _dns_zone_error_control(cls, declaration: Any, zone_index: int) -> str:
        """Identify the dialog control corresponding to a malformed DNS Zone."""
        base = f"dns-zones-{zone_index}"
        if not isinstance(declaration, dict):
            return f"{base}-name"
        if declaration.get("kind") == "secondary":
            if not isinstance(declaration.get("primary_node_id"), str) or not declaration.get("primary_node_id"):
                return f"{base}-primary_node_id"
            return f"{base}-name"
        if not isinstance(declaration.get("name"), str) or not declaration.get("name"):
            return f"{base}-name"
        ttl = declaration.get("default_ttl")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            return f"{base}-default_ttl"
        for field in ("dnssec", "nsec3"):
            if field in declaration and not isinstance(declaration[field], bool):
                return f"{base}-{field}"
        if declaration.get("nsec3") and not declaration.get("dnssec"):
            return f"{base}-nsec3"
        salt = declaration.get("nsec3_salt")
        if salt is not None and (not isinstance(salt, str) or
                                 not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", salt)):
            return f"{base}-nsec3_salt"
        if isinstance(declaration.get("records"), list):
            for rec_index, record in enumerate(declaration["records"]):
                try:
                    dns_record(record)
                except (TypeError, ValueError) as exc:
                    rbase = f"{base}-records-{rec_index}"
                    if not isinstance(record, dict):
                        return f"{rbase}-name"
                    if not isinstance(record.get("name"), str) or not record.get("name"):
                        return f"{rbase}-name"
                    if not isinstance(record.get("type"), str) or record.get("type") not in {
                        "A", "AAAA", "CNAME", "PTR", "TXT", "NS", "MX", "SOA"
                    }:
                        return f"{rbase}-type"
                    rttl = record.get("ttl")
                    if isinstance(rttl, bool) or not isinstance(rttl, int) or rttl <= 0:
                        return f"{rbase}-ttl"
                    if "owner" in str(exc).lower() or "record name" in str(exc).lower():
                        return f"{rbase}-name"
                    return f"{rbase}-rdata"
        return f"{base}-name"

    @classmethod
    def _dns(cls, value: Any, name: str, node_id: str) -> None:
        """Validate authoritative declarations through the DNS models."""
        if not isinstance(value, dict) or set(value) != {"zones"} or not isinstance(value["zones"], list):
            raise DraftValidationError(
                f"Node '{name}' DNS Service needs a list of Zones",
                kind="node",
                item_id=node_id,
                control_id="dns-zones",
            )
        zones: Dict[str, int] = {}
        for zone_index, declaration in enumerate(value["zones"]):
            try:
                zone = authored_zone(declaration)
            except (TypeError, ValueError) as exc:
                raise DraftValidationError(
                    f"Node '{name}' Zone: {exc}",
                    kind="node",
                    item_id=node_id,
                    control_id=cls._dns_zone_error_control(declaration, zone_index),
                ) from exc
            if zone.name in zones:
                # A Secondary's Apex is derived from its Primary. When one
                # collides with a local Primary, target the editable Primary.
                prior_index = zones[zone.name]
                editable_index = (prior_index if declaration["kind"] == "secondary"
                                  and value["zones"][prior_index]["kind"] == "primary"
                                  else zone_index)
                raise DraftValidationError(
                    f"Node '{name}' hosts Zone {zone.name} twice",
                    kind="node",
                    item_id=node_id,
                    control_id=f"dns-zones-{editable_index}-name",
                )
            zones[zone.name] = zone_index

    @staticmethod
    def _rpki_lab(nodes: List[Dict[str, Any]]) -> None:
        """Refuse a Lab whose RPKI Services cannot start together, as the Python API does.

        Raises:
            DraftValidationError: If the Lab declares a second RPKI CA, or a
                Validator with no RPKI CA to take its TAL from.
        """
        authorities = [node for node in nodes if "rpki_ca" in node]
        if len(authorities) > 1:
            raise DraftValidationError(
                multiple_rpki_cas_message(len(authorities), ", ".join(node["name"] for node in authorities)),
                kind="node",
                item_id=authorities[1]["id"],
                control_id="rpki-ca-enabled",
            )
        if not authorities:
            for node in nodes:
                if "validator" in node:
                    raise DraftValidationError(
                        validator_without_rpki_ca_message(node["name"]),
                        kind="node",
                        item_id=node["id"],
                        control_id="validator-enabled",
                    )

    @staticmethod
    def _nameservers_error_control(declarations: Any, topology: Dict[str, Any]) -> str:
        """Identify the dialog control for a Host nameservers validation failure."""
        if not isinstance(declarations, list):
            return "host-nameserver-addresses"
        node_ids = {node["id"] for node in topology["nodes"]}
        for declaration in declarations:
            if isinstance(declaration, dict) and set(declaration) == {"node_id"}:
                nid = declaration["node_id"]
                if not isinstance(nid, str) or nid not in node_ids:
                    return "host-nameserver-choices"
                has_addr = any(
                    end.get(family)
                    for link in topology["links"]
                    for end in link["ends"]
                    if end["node_id"] == nid
                    for family in ("ipv4", "ipv6")
                )
                if not has_addr:
                    return "host-nameserver-choices"
        return "host-nameserver-addresses"

    @classmethod
    def _resolver_error_control(cls, value: Any, topology: Dict[str, Any]) -> str:
        """Identify the dialog control for a Resolver validation failure."""
        if not isinstance(value, dict):
            return "resolver-enabled"
        if not isinstance(value.get("root_hints"), list):
            return "resolver-root-addresses"
        for ref in value["root_hints"]:
            try:
                authored_nameservers([ref], topology)
            except ValueError:
                return (
                    "resolver-root-choices"
                    if isinstance(ref, dict) and "node_id" in ref
                    else "resolver-root-addresses"
                )
        if not isinstance(value.get("forwarders"), list):
            return "resolver-forwarders"
        for ref in value["forwarders"]:
            try:
                authored_nameservers([ref], topology)
            except ValueError:
                return (
                    "resolver-forwarder-choices"
                    if isinstance(ref, dict) and "node_id" in ref
                    else "resolver-forwarders"
                )
        if not isinstance(value.get("access_control"), list):
            return "resolver-access-control"
        for rule_index, item in enumerate(value["access_control"]):
            rbase = f"resolver-access-control-{rule_index}"
            if not isinstance(item, dict) or set(item) != {"subnet", "action"}:
                return f"{rbase}-subnet"
            if not isinstance(item["subnet"], str):
                return f"{rbase}-subnet"
            if not isinstance(item["action"], str):
                return f"{rbase}-action"
            try:
                AccessRule(item["subnet"], item["action"])
            except ValueError as exc:
                return f"{rbase}-action" if "action" in str(exc).lower() else f"{rbase}-subnet"
        for field, ctrl in (("max_ttl", "resolver-max-ttl"), ("max_negative_ttl", "resolver-max-negative-ttl")):
            val = value.get(field)
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                return ctrl
        if "trust_anchors" in value:
            return "resolver-trust-choices"
        return "resolver-enabled"

    def upload(self, filename: str, data: bytes) -> Dict[str, Any]:
        """Create a new Python draft from one uploaded script.

        The draft holds no Seeds and no Catalog ID, and takes its Catalog
        name from the file's name. A script that does not compile is still
        created: :meth:`python_error` marks its line, and building refuses it.

        Args:
            filename: The uploaded file's name.
            data: Its bytes, at most :attr:`UPLOAD_LIMIT` of them.

        Returns:
            The new Python draft.

        Raises:
            ValueError: It is not one ``.py`` script of UTF-8 text within the limit.
        """
        name, python = self._decode_uploaded_script(filename, data)
        return self.create_python(
            {"name": name[:-len(".py")][:120], "description": "", "protocols": []},
            python, files={}, guide="", layout={"version": 1, "nodes": {}},
        )

    def create_python(self, catalog: Dict[str, Any], python: str, files: Dict[str, str],
                      guide: str, layout: Dict[str, Any],
                      lesson_files: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a Python-based draft that is not yet published.

        Args:
            catalog: Catalog name, description and protocols; its id is dropped.
            python: The script.
            files: Seed files by path under ``frr-config/``.
            guide: The Guide.
            layout: The Authored Layout, keyed by runtime Node name.
            lesson_files: Optional supporting file records, by relative path.

        Returns:
            The new draft.
        """
        with self._lock:
            now = time.time()
            draft_id = f"draft-{uuid.uuid4().hex}"
            document = {
                "schema_version": self.SCHEMA_VERSION, "kind": "python", "id": draft_id,
                "revision": 0, "created_at": now, "updated_at": now, "published_revision": None,
                "catalog": {"id": None, "name": catalog["name"],
                            "description": catalog["description"],
                            "protocols": list(catalog["protocols"]),
                            **({"area": catalog["area"]} if "area" in catalog else {})},
                "python": python, "files": dict(sorted(files.items())),
                "guide": guide, "layout": copy.deepcopy(layout),
            }
            if lesson_files is not None:
                document["lesson_files"] = copy.deepcopy(lesson_files)
            self.validate(document)
            self._write_new(draft_id, document)
            return copy.deepcopy(document)

    @classmethod
    def _validate_python(cls, document: Dict[str, Any]) -> None:
        """Check a Python-based draft: its script, Seed files and presentation.

        The script is kept even when it does not compile, so unfinished work
        can be saved; :meth:`python_error` reports that, and building refuses it.
        """
        if not cls._python_keys <= set(document) <= cls._python_keys | {"lesson_files"}:
            raise DraftValidationError("Draft fields do not match the Python Lab Example schema")
        if document.get("schema_version") != cls.SCHEMA_VERSION:
            raise DraftValidationError("Unsupported Lab Example definition version")
        cls._validate_header(document)
        source = document.get("python")
        if not isinstance(source, str) or len(source) > 500_000:
            raise DraftValidationError(
                "Python must be text of at most 500,000 characters",
                control_id="python-source",
            )
        files = document.get("files")
        if not isinstance(files, dict) or len(files) > 200:
            raise DraftValidationError("Seed files must be an object of at most 200 files")
        for path, text in files.items():
            if not isinstance(path, str) or not cls._file_pattern.fullmatch(path) or len(path) > 200:
                raise DraftValidationError(
                    f"Seed file path {path!r} must be an frr.conf under frr-config/;"
                    " the Router's other FRR files come from its Protocols"
                )
            if not isinstance(text, str) or len(text) > cls.SEED_TEXT_LIMIT:
                raise DraftValidationError(
                    f"Seed file {path} must be text of at most {cls.SEED_TEXT_LIMIT:,} characters"
                )
        cls._validate_presentation(document, None)

    @staticmethod
    def python_error(document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Where a Python-based draft's script fails to compile, if it does.

        Args:
            document: A Python-based draft.

        Returns:
            ``line``, ``column`` and ``message``, or None when it compiles.
        """
        try:
            compile(document["python"], "lab.py", "exec")
        except SyntaxError as exc:
            return {"line": exc.lineno, "column": exc.offset, "message": exc.msg}
        except (ValueError, MemoryError, RecursionError) as exc:
            return {"line": None, "column": None,
                    "message": str(exc) or "Python source exceeds compiler diagnostic limits"}
        return None

    @staticmethod
    def _route_generator(
        value: Any,
        label: str,
        *,
        node_id: Optional[str] = None,
        prefix: str = "speaker-generator",
    ) -> None:
        """Bound generated work while retaining reproducible integer seeds."""
        if not isinstance(value, dict) or set(value) != {
            "count", "sub_prefixes", "prefix_seed", "as_path_seed"
        }:
            raise DraftValidationError(
                f"{label} Route generator fields are invalid",
                kind="node" if node_id else "draft",
                item_id=node_id,
                control_id=f"{prefix}-enabled",
            )
        limits = {"count": (1, 2000), "sub_prefixes": (0, 16),
                  "prefix_seed": (0, 4294967295), "as_path_seed": (0, 4294967295)}
        for field, (minimum, maximum) in limits.items():
            number = value[field]
            if isinstance(number, bool) or not isinstance(number, int) or not minimum <= number <= maximum:
                raise DraftValidationError(
                    f"{label} Route generator {field} needs an integer from {minimum} to {maximum}",
                    kind="node" if node_id else "draft",
                    item_id=node_id,
                    control_id=f"{prefix}-{field}",
                )
        if value["count"] * (1 + value["sub_prefixes"]) > 2000:
            raise DraftValidationError(
                f"{label} Route generator may request at most 2000 Routes including sub-prefixes",
                kind="node" if node_id else "draft",
                item_id=node_id,
                control_id=f"{prefix}-count",
            )

    @staticmethod
    def _validate_address_family(document: Dict[str, Any]) -> None:
        family = document["address_family"]
        if family not in ("ipv4", "ipv6", "dual"):
            raise DraftValidationError(
                "Address family must be IPv4, IPv6 or Dual-stack",
                control_id="lab-address-family",
            )
        if family == "dual":
            return
        forbidden = 6 if family == "ipv4" else 4
        conflicts = [
            label
            for label, version in AuthoringRepository._address_fields(
                document["topology"]
            )
            if version == forbidden
        ]
        if conflicts:
            raise DraftValidationError(
                f"{family.upper()} does not use IPv{forbidden}; clear these fields first: "
                + "; ".join(conflicts),
                control_id="lab-address-family",
            )

    @staticmethod
    def _address_fields(topology: Dict[str, Any]) -> List[Tuple[str, int]]:
        """List configured address fields, excluding policy and lesson text."""
        fields: List[Tuple[str, int]] = []

        def visit(value: Any, label: str) -> None:
            if isinstance(value, list):
                for index, item in enumerate(value, 1):
                    visit(item, f"{label}[{index}]")
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key not in {
                        "ipv4",
                        "ipv6",
                        "ipv4_gateway",
                        "ipv6_gateway",
                        "dhcp4",
                        "dhcp6",
                        "ip",
                        "subnet",
                        "cidr",
                        "start",
                        "end",
                        "router",
                        "dns_server",
                        "peer",
                        "local_address",
                        "prefix",
                        "next_hop",
                        "address",
                        "pools",
                        "reservations",
                        "subnets",
                        "servers",
                        "root_hints",
                        "forwarders",
                        "routes",
                        "zones",
                        "allow_transfer",
                    }:
                        continue
                    if key in ("dhcp4", "dhcp6"):
                        if item:
                            fields.append((f"{label}.{key}", int(key[-1])))
                    else:
                        visit(item, f"{label}.{key}")
            elif isinstance(value, str) and value:
                try:
                    address = ipaddress.ip_interface(value)
                except ValueError:
                    return
                fields.append((label, address.version))

        nodes = topology["nodes"]
        by_id = {node["id"]: node for node in nodes}
        for node in nodes:
            for key in (
                "ipv4_gateway",
                "ipv6_gateway",
                "subinterfaces",
                "dhcp",
                "relay",
                "nameservers",
                "resolver",
                "dns",
                "neighbors",
                "routes",
            ):
                if key in node:
                    visit(node[key], f"{node['name']} {key}")
            if node["type"] in ("nat", "transit"):
                for key in ("ip", "subnet"):
                    visit(node[key], f"{node['name']} {key}")
        for link in topology["links"]:
            for end in link["ends"]:
                name = by_id[end["node_id"]]["name"]
                for key in ("ipv4", "ipv6", "dhcp4", "dhcp6"):
                    if key in end:
                        visit({key: end[key]}, f"{name}-eth{end['port']}")
        return fields

    def list_files(self, draft_id: str) -> Dict[str, Any]:
        """List supporting file metadata and its whole-draft revision.

        Args:
            draft_id: The shared draft id.

        Returns:
            Revision and sorted file metadata, without binary content.
        """
        document = self.read(draft_id)
        return {"revision": document["revision"], "files": [
            {"path": path, "encoding": record["encoding"],
             "role": file_role(path, record), "size": len(decode_file(record))}
            for path, record in sorted(document.get("lesson_files", {}).items())
        ]}

    def read_file(self, draft_id: str, path: str) -> bytes:
        """Read supporting file bytes from the saved draft.

        Args:
            draft_id: The shared draft id.
            path: The canonical relative file path.

        Returns:
            Exact file bytes.

        Raises:
            DraftNotFoundError: If the draft or file does not exist.
        """
        document = self.read(draft_id)
        canonical_path(path)
        record = document.get("lesson_files", {}).get(path)
        if record is None:
            raise DraftNotFoundError(path)
        return decode_file(record)

    def put_file(self, draft_id: str, expected_revision: int,
                 path: str, record: Dict[str, Any]) -> Dict[str, Any]:
        """Add or replace one supporting file in a revision-checked Save.

        Args:
            draft_id: The shared draft id.
            expected_revision: The revision the Instructor edited.
            path: The canonical relative file path.
            record: File encoding, content and optional role.

        Returns:
            The updated draft.
        """
        with self._lock:
            document = self.read(draft_id)
            document.setdefault("lesson_files", {})[path] = copy.deepcopy(record)
            return self.save(draft_id, expected_revision, document)

    def delete_file(self, draft_id: str, expected_revision: int,
                    path: str) -> Dict[str, Any]:
        """Delete one supporting file in a revision-checked Save.

        Args:
            draft_id: The shared draft id.
            expected_revision: The revision the Instructor edited.
            path: The canonical relative file path.

        Returns:
            The updated draft.

        Raises:
            DraftNotFoundError: If the draft or file does not exist.
        """
        with self._lock:
            document = self.read(draft_id)
            if document["revision"] != expected_revision:
                raise DraftConflictError(
                    f"Draft changed since it was opened (current revision {document['revision']})."
                )
            canonical_path(path)
            if path not in document.get("lesson_files", {}):
                raise DraftNotFoundError(path)
            del document["lesson_files"][path]
            return self.save(draft_id, expected_revision, document)

    def continue_in_python(self, draft_id: str,
                           expected_revision: int) -> Dict[str, Any]:
        """Create an independent Python draft from a saved Canvas recipe.

        The source revision is checked while holding the repository lock. The
        generated source and FRR Seeds are prepared before a new draft is
        written, so generation errors leave both the source and draft store
        unchanged. Preview evidence is never copied.

        Args:
            draft_id: The saved Canvas draft to continue.
            expected_revision: The revision the Instructor opened.

        Returns:
            The new Python draft, with a fresh identity and no Preview.

        Raises:
            DraftConflictError: The source changed since it was opened.
            DraftNotFoundError: The source draft does not exist.
            ValueError: The source is already Python or cannot be generated.
        """
        with self._lock:
            source = self.read(draft_id)
            if source["revision"] != expected_revision:
                raise DraftConflictError(
                    "Save or reopen this changed draft before continuing in Python"
                )
            if source.get("kind") == "python":
                raise DraftValidationError("This draft is already in Python")

            from mniplab.frrouter.config import FRRConfigBuilder
            from mniplab.webserver.authoring_python import generate_python

            script = generate_python(source)
            seeds: Dict[str, str] = {}
            if source["persistent_config"]:
                with tempfile.TemporaryDirectory(prefix="authoring-convert-") as seed_root:
                    builder = FRRConfigBuilder()
                    for node in source["topology"]["nodes"]:
                        if node["type"] != "router":
                            continue
                        seed_dir = os.path.join(seed_root, node["name"])
                        os.makedirs(seed_dir)
                        try:
                            builder.write_frr_conf(
                                node["name"], seed_dir,
                                frr_config=router_frr_config(
                                    node, source["topology"]["links"]
                                ),
                            )
                            with open(os.path.join(seed_dir, "frr.conf"),
                                      encoding="utf-8") as seed:
                                seeds[f"frr-config/{node['name']}/frr.conf"] = seed.read()
                        except (ConfigurationError, OSError) as exc:
                            raise DraftValidationError(
                                f"Could not generate the Python draft's FRR Seed: {exc}"
                            ) from exc

            name = f"Copy of {source['catalog']['name']}"[:120]
            catalog = {
                "name": name,
                "description": source["catalog"]["description"],
                "protocols": source["catalog"]["protocols"],
                **({"area": source["catalog"]["area"]} if "area" in source["catalog"] else {}),
            }
            return self.create_python(
                catalog,
                script,
                seeds,
                source["guide"],
                self.layout_for_runtime(source),
                source.get("lesson_files"),
            )

    @classmethod
    def _decode_uploaded_script(cls, filename: str, data: bytes) -> Tuple[str, str]:
        """Validate and decode the one-file Python upload contract.

        Args:
            filename: The uploaded filename.
            data: The bounded upload bytes.

        Returns:
            The basename and UTF-8 source, even if the source is unfinished.

        Raises:
            DraftValidationError: The name, size or encoding is not accepted.
        """
        name = os.path.basename(filename or "")
        if not name.endswith(".py") or name == ".py":
            raise DraftValidationError(f"{name or 'This file'} is not a Python script: upload one .py file")
        if len(data) > cls.UPLOAD_LIMIT:
            raise DraftValidationError(f"The script is larger than {cls.UPLOAD_LIMIT // 1000} KB")
        try:
            return name, data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DraftValidationError(f"{name} is not UTF-8 text") from exc

    def replace_script(self, draft_id: str, expected_revision: int,
                       filename: str, data: bytes) -> Dict[str, Any]:
        """Replace only a Python draft's source using its current revision.

        Args:
            draft_id: The existing draft to update.
            expected_revision: The revision the Instructor uploaded against.
            filename: The uploaded ``.py`` basename.
            data: The bounded uploaded UTF-8 bytes.

        Returns:
            The updated draft with its original identity and authored files.

        Raises:
            DraftNotFoundError: No draft has this id.
            DraftConflictError: The draft has advanced since it was read.
            DraftValidationError: The upload is invalid or the draft is not Python.
        """
        _, python = self._decode_uploaded_script(filename, data)
        with self._lock:
            current = self.read(draft_id)
            if current["revision"] != expected_revision:
                raise DraftConflictError(
                    f"Draft changed since it was opened (current revision {current['revision']})."
                )
            if current.get("kind") != "python":
                raise DraftValidationError("Only a Python draft's script can be replaced")
            if current["python"] == python:
                return current
            updated = copy.deepcopy(current)
            updated["python"] = python
            return self.save(draft_id, expected_revision, updated)

    def validate_for_publish(self, document: Dict[str, Any]) -> None:
        """Check that a buildable draft has a chosen Catalog Area.

        Args:
            document: The Lab Example Draft to publish.

        Raises:
            DraftValidationError: Its Area is missing or unknown.
        """
        self.validate_for_build(document)
        if document["catalog"].get("area") not in AREAS:
            raise DraftValidationError(
                "Choose an Area from the Catalog list before publishing",
                control_id="catalog-area",
            )


def shipped_definition(example_id: str, examples_dir: str) -> Dict[str, Any]:
    """Read a shipped Lab Example as its Canvas definition.

    ``examples/definitions/<id>.json`` holds only the Topology and where its
    Seeds live; the Seeds, Catalog row, Guide and Layout stay in their
    shipped files, read here so nothing is maintained twice. Each Router's
    FRR configuration is its Seed's ``frr.conf`` without the header the Lab
    writes itself. An example without Seeds, whose Routers take their text
    from ``add_frr_config``, omits ``seeds`` and carries that text itself.

    Args:
        example_id: The shipped Catalog id.
        examples_dir: The shipped ``examples/`` directory.

    Returns:
        The ``schema_version``, ``catalog``, ``topology``, ``guide`` and
        ``layout`` of a draft, with no Catalog id.

    Raises:
        DraftNotFoundError: The example has no prepared definition.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", example_id):
        raise DraftNotFoundError(example_id)
    try:
        with open(os.path.join(examples_dir, "definitions", f"{example_id}.json"), encoding="utf-8") as source:
            prepared = json.load(source)
        with open(os.path.join(examples_dir, "labs.json"), encoding="utf-8") as source:
            row = next(item for item in json.load(source) if item.get("id") == example_id)
    except (FileNotFoundError, StopIteration) as exc:
        raise DraftNotFoundError(example_id) from exc
    topology = copy.deepcopy(prepared["topology"])
    seeds = (os.path.join(os.path.dirname(os.path.abspath(examples_dir)), prepared["seeds"])
             if "seeds" in prepared else None)
    for node in topology["nodes"]:
        if node["type"] == "router" and seeds is not None:
            try:
                with open(os.path.join(seeds, node["name"], "frr.conf"), encoding="utf-8") as source:
                    seed_text = source.read()
                    node["frr_config"] = frr_config_body(seed_text)
                    # FRR 8's implicit first-AS policy differs from the current
                    # generated Seed header. State the old lesson's policy in
                    # native FRR text when adapting that legacy Seed.
                    if (seed_text.startswith("frr version 8.") and
                            "router bgp " in node["frr_config"] and
                            "bgp enforce-first-as" not in node["frr_config"]):
                        node["frr_config"] = re.sub(
                            r"(?m)^(router bgp [^\n]+)\n",
                            r"\1\n no bgp enforce-first-as\n",
                            node["frr_config"], count=1,
                        )
            except FileNotFoundError:
                node["frr_config"] = ""
    guide_path = os.path.join(examples_dir, "guides", f"{example_id}.md")
    guide = ""
    if os.path.isfile(guide_path):
        with open(guide_path, encoding="utf-8") as source:
            guide = source.read()
    positions: Dict[str, Dict[str, float]] = {}
    layout_path = os.path.join(examples_dir, "layouts", f"{example_id}.json")
    if os.path.isfile(layout_path):
        with open(layout_path, encoding="utf-8") as source:
            shipped = json.load(source)["nodes"]
        ids = {node["name"]: node["id"] for node in topology["nodes"]}
        positions = {
            ids[name]: {
                axis: point[axis]
                for axis in ("x", "y")
            }
            for name, point in shipped.items() if name in ids
        }
    for index, node in enumerate(item for item in topology["nodes"] if item["id"] not in positions):
        positions[node["id"]] = {"x": 120 + (index % 5) * 180,
                                 "y": 120 + (index // 5) * 180}
    return {
        "schema_version": prepared["schema_version"],
        "catalog": {
            "id": None, "name": row["name"], "description": row.get("description", ""),
            "protocols": list(row.get("protocols", [])),
            "area": row.get("area", ""),
        },
        "topology": topology,
        "address_family": prepared["address_family"],
        "persistent_config": prepared["persistent_config"],
        "guide": guide,
        "layout": {"version": 1, "nodes": positions},
    }


def has_code_section(document: Dict[str, Any]) -> bool:
    """Whether a Canvas draft's Code section holds code, not only blank lines.

    Args:
        document: A draft or published definition.

    Returns:
        True when its Code section has anything but whitespace.
    """
    return document.get("kind") != "python" and bool(document.get("code_section", "").strip())


#: Lines FRR's running configuration carries that the Lab generates itself.
_GENERATED_FRR_LINES = re.compile(
    r"^(Building configuration\.\.\.|Current configuration:|frr version .*|frr defaults .*"
    r"|hostname .*|service integrated-vtysh-config|end)$"
)


def frr_config_body(running: str) -> str:
    """Reduce ``show running-config`` to the text a Router's draft holds.

    The header the Lab writes for every Router (version, defaults, hostname,
    integrated config) and the trailing ``end`` are dropped, so keep
    compares only what an Instructor authors.
    """
    lines = [line.rstrip() for line in running.replace("\r", "").splitlines()]
    lines = [line for line in lines if line and not _GENERATED_FRR_LINES.match(line)]
    while lines and lines[0] == "!":
        lines.pop(0)
    return "".join(f"{line}\n" for line in lines)


#: Fast OSPF timers, per Protocol: the Interface commands the default adds.
FAST_TIMERS = {
    "OSPF": (" ip ospf hello-interval 1", " ip ospf dead-interval 3"),
    "OSPF6": (" ipv6 ospf6 hello-interval 1", " ipv6 ospf6 dead-interval 3"),
}


def router_frr_config(node: Dict[str, Any], links: List[Dict[str, Any]]) -> str:
    """The FRR configuration a Router starts with: its text, after any defaults.

    With ``fast_timers`` and OSPF or OSPF6 selected, every Interface of the
    Router first gets hello 1 s / dead 3 s, so a Lab converges in seconds
    rather than FRR's default 40 s dead interval. The defaults come before
    the authored text: Interface timers must precede ``router ospf`` or a
    Designated Router still waits the default 40 s, and a timer the
    Instructor writes for the same Interface is applied after, so it wins.
    """
    commands = [line for protocol in node["protocols"] if node["fast_timers"]
                for line in FAST_TIMERS.get(protocol, ())]
    if not commands:
        return node["frr_config"]
    ports = sorted(end["port"] for link in links for end in link["ends"]
                   if end["node_id"] == node["id"])
    blocks = "".join(
        f"interface {node['name']}-eth{port}\n" + "\n".join(commands) + "\nexit\n!\n"
        for port in ports
    )
    return blocks + node["frr_config"]


def without_fast_timers(node: Dict[str, Any], config: str) -> str:
    """Drop the fast-timer lines the Lab added, so keep does not author them.

    Interface blocks left empty are dropped with them. Timers that differ
    from the defaults stay: those are the Instructor's.
    """
    defaults = {line.strip() for protocol in node["protocols"] if node["fast_timers"]
                for line in FAST_TIMERS.get(protocol, ())}
    if not defaults:
        return config
    kept = [line for line in config.splitlines() if line.strip() not in defaults]
    result: List[str] = []
    for line in kept:
        if line == "exit" and result and result[-1].startswith("interface "):
            result.pop()
            continue
        if line == "!" and result and result[-1] == "!":
            continue
        result.append(line)
    while result and result[0] == "!":
        result.pop(0)
    return "".join(f"{line}\n" for line in result)


_POOL_FIELDS = {"cidr", "start", "end", "lease_time", "preferred_lifetime",
                "router", "dns_server", "reservations"}
_RESERVATION_FIELDS = {"ip", "mac", "duid", "hostname"}


def dns_record(value: Any) -> Any:
    """Construct one authored Resource Record with the DNS library's model.

    Args:
        value: Record declaration with name, type, rdata, and ttl.

    Returns:
        A typed DNS Record.

    Raises:
        ValueError: The declaration is incomplete or invalid.
    """
    if not isinstance(value, dict) or set(value) != {"name", "type", "rdata", "ttl"}:
        raise ValueError("Record needs name, type, rdata and ttl")
    name, kind, data, ttl = (value[key] for key in ("name", "type", "rdata", "ttl"))
    if not all(isinstance(item, str) for item in (name, kind, data)) or not name or not data:
        raise ValueError("Record name, type and rdata must be nonempty text")
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise ValueError("Record TTL must be positive whole seconds")
    direct = {"A": ARecord, "AAAA": AAAARecord, "CNAME": CNAMERecord,
              "PTR": PTRRecord, "TXT": TXTRecord, "NS": NSRecord}
    if kind in direct:
        return direct[kind](name, data, ttl=ttl)
    if kind == "MX":
        preference, exchange = data.split(maxsplit=1)
        return MXRecord(name, exchange, preference=int(preference), ttl=ttl)
    if kind == "SOA":
        mname, rname, *numbers = data.split()
        if len(numbers) != 5:
            raise ValueError("SOA rdata needs serial, refresh, retry, expire and minimum")
        serial, refresh, retry, expire, minimum = (int(item) for item in numbers)
        return SOARecord(mname, rname, serial=serial, refresh=refresh, retry=retry,
                         expire=expire, minimum=minimum, ttl=ttl, name=name)
    if kind == "DS":
        raise ValueError("DS values are generated from signed child Zones; use ds_delegations")
    raise ValueError(f"Record type {kind!r} is unsupported")


def dns_keep_records(
    live: Any, zone: str, authored: List[Dict[str, Any]], node_name: str
) -> List[Dict[str, Any]]:
    """Convert editable live Primary Records, omitting generated DNSSEC data.

    Args:
        live: Flat Records returned by Knot's control API.
        zone: Apex of the Primary Zone being kept.
        authored: Initially authored Records, used to identify explicit
            SOA and apex NS declarations.
        node_name: Serving Node name used by generated SOA and NS defaults.

    Returns:
        Sorted Record declarations accepted by the authoring schema.

    Raises:
        ValueError: A live editable Record is incomplete or invalid.
    """
    if not isinstance(live, list):
        raise ValueError("Live Primary Records must be a list")
    apex = zone.lower()

    def absolute(name: str) -> str:
        name = name.lower()
        if name in ("@", apex):
            return apex
        if name.endswith("."):
            return name
        return f"{name}." if apex == "." else f"{name}.{apex}"

    # Knot answers every owner absolute; an authored Record keeps its own
    # spelling, so ``ns.com.`` in the root Zone is not offered as ``ns.com``.
    spellings = {(item["type"].upper(), absolute(item["name"])): item["name"]
                 for item in authored}
    defaults = Zone(zone)
    defaults.bind_host_node(node_name)
    default_ns = next(item.rdata.lower() for item in defaults.records if item.rtype == "NS")
    default_soa = next(item.rdata.lower().split() for item in defaults.records
                       if item.rtype == "SOA")
    records = []
    for item in live:
        if not isinstance(item, dict) or not {"owner", "ttl", "type", "data"} <= set(item):
            raise ValueError("Live Primary Record is incomplete")
        kind = item["type"].upper()
        if kind not in {"A", "AAAA", "CNAME", "PTR", "TXT", "MX", "NS", "SOA"}:
            continue
        owner = item["owner"].lower()
        name = spellings.get((kind, owner)) or ("@" if owner == apex else (
            owner[:-len(apex) - 1] if apex != "." and owner.endswith("." + apex)
            else owner[:-1] if apex == "." and owner.endswith(".") else owner))
        if (kind, owner) not in spellings and owner == apex:
            if kind == "NS" and str(item["data"]).lower() == default_ns:
                continue
            if kind == "SOA":
                parts = str(item["data"]).lower().split()
                if len(parts) == 7 and parts[:2] == default_soa[:2] and parts[3:] == default_soa[3:]:
                    continue
        record = {"name": name, "type": kind, "rdata": str(item["data"]),
                  "ttl": item["ttl"]}
        dns_record(record)
        records.append(record)
    return sorted(records, key=lambda item: (item["name"], item["type"], item["rdata"], item["ttl"]))


def validate_transfer_target(target: Any, node_ids: set[str]) -> None:
    """Check one transfer ACL reference against authored Node identities.

    Args:
        target: A Node ID reference or literal IP address.
        node_ids: IDs declared in the authored topology.

    Raises:
        ValueError: The reference is malformed or names a missing Node.
    """
    if not isinstance(target, dict):
        raise ValueError("Zone Transfer target needs node_id or address")
    if set(target) == {"node_id"}:
        if not isinstance(target["node_id"], str) or target["node_id"] not in node_ids:
            raise ValueError("Zone Transfer target names a missing Node")
    elif set(target) == {"address"}:
        if not isinstance(target["address"], str):
            raise ValueError("Zone Transfer address must be text")
        ipaddress.ip_address(target["address"])
    else:
        raise ValueError("Zone Transfer target needs node_id or address")


def authored_zone(value: Any) -> Any:
    """Validate one Zone declaration by constructing it with the DNS models.

    Node references stay as their authored IDs; the generated script
    resolves them to Nodes.

    Args:
        value: Primary or Secondary Zone declaration.

    Returns:
        A typed Zone or SecondaryZone.

    Raises:
        ValueError: The declaration or its Records are invalid.
    """
    if not isinstance(value, dict):
        raise ValueError("Zone must be an object")
    if value.get("kind") == "secondary":
        if set(value) != {"kind", "name", "primary_node_id"}:
            raise ValueError("Secondary Zone needs name and primary_node_id, with no Records")
        if not isinstance(value["name"], str) or not isinstance(value["primary_node_id"], str):
            raise ValueError("Secondary Zone name and Primary Node ID must be text")
        return SecondaryZone(value["name"], primary=value["primary_node_id"])
    if not {"kind", "name", "default_ttl", "records"} <= set(value) <= {
        "kind", "name", "default_ttl", "records", "allow_transfer", "dnssec",
        "nsec3", "nsec3_salt", "ds_delegations"
    }:
        raise ValueError("Primary Zone needs kind, name, default_ttl and records")
    if value["kind"] != "primary" or not isinstance(value["records"], list):
        raise ValueError("Zone must be primary with a Record list")
    if not isinstance(value["name"], str):
        raise ValueError("Zone name must be text")
    ttl = value["default_ttl"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise ValueError("Zone default TTL must be positive whole seconds")
    targets = value.get("allow_transfer")
    if targets is not None and not isinstance(targets, list):
        raise ValueError("Zone Transfer allow list must be a list or null")
    resolved = None if targets is None else [
        target["address"] if "address" in target else target for target in targets
    ]
    for field in ("dnssec", "nsec3"):
        if field in value and not isinstance(value[field], bool):
            raise ValueError(f"Zone {field} must be true or false")
    if value.get("nsec3") and not value.get("dnssec"):
        raise ValueError("NSEC3 requires DNSSEC signing")
    salt = value.get("nsec3_salt")
    if salt is not None and (not isinstance(salt, str) or
                             not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", salt)):
        raise ValueError("NSEC3 salt must be hexadecimal bytes")
    delegations = value.get("ds_delegations", [])
    if not isinstance(delegations, list):
        raise ValueError("DS delegations must be a list")
    return Zone(value["name"], [dns_record(record) for record in value["records"]],
                default_ttl=ttl, allow_transfer=resolved, dnssec=value.get("dnssec", False),
                nsec3=value.get("nsec3", False), nsec3_salt=salt)


def authored_nameservers(declarations: Any, topology: Dict[str, Any]) -> List[str]:
    """Resolve stable Node references for a Host's private resolver.

    Args:
        declarations: Node ID references or literal addresses.
        topology: Authored Nodes and Links containing Interface addresses.

    Returns:
        The resolved, normalized nameserver addresses.

    Raises:
        ValueError: A reference is invalid or has no static address.
    """
    if not isinstance(declarations, list):
        raise ValueError("use a list of Node references or IP addresses")
    addresses: List[str] = []
    node_ids = {node["id"] for node in topology["nodes"]}
    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise ValueError("each nameserver needs node_id or address")
        if set(declaration) == {"address"}:
            address = declaration["address"]
            if not isinstance(address, str):
                raise ValueError("nameserver address must be text")
            addresses.append(address)
        elif set(declaration) == {"node_id"}:
            node_id = declaration["node_id"]
            if not isinstance(node_id, str) or node_id not in node_ids:
                raise ValueError(f"Node {node_id!r} is missing")
            found = [str(ipaddress.ip_interface(end[family]).ip)
                     for link in topology["links"] for end in link["ends"]
                     if end["node_id"] == node_id
                     for family in ("ipv4", "ipv6") if end.get(family)]
            if not found:
                raise ValueError(f"Node {node_id!r} has no static address")
            addresses.extend(found)
        else:
            raise ValueError("each nameserver needs node_id or address")
    return normalize_nameservers(addresses)


class HostedCARowError(ValueError):
    """A Hosted CA row that does not validate, naming the row field its error belongs on."""

    def __init__(self, field: str, message: str) -> None:
        """Record the refusal and the field it belongs on.

        Args:
            field: The row's field: asn, prefixes, roas or aspas.
            message: What is wrong, in the Python API's words.
        """
        super().__init__(message)
        self.field = field


def _starting_objects(texts: Any, field: str, parse: Callable[[str], Any]) -> List[Any]:
    """Parse one Hosted CA's starting ROAs or ASPAs, each on its own.

    Args:
        texts: The row's list of object text.
        field: The row field the list belongs to.
        parse: The model's parser for one line of text.

    Returns:
        The parsed models, in order.

    Raises:
        HostedCARowError: If the list or one of its lines does not parse.
    """
    if not isinstance(texts, list):
        raise HostedCARowError(field, f"Hosted CA {field} must be a list")
    parsed: List[Any] = []
    for text in texts:
        if not isinstance(text, str):
            raise HostedCARowError(field, f"Hosted CA {field} must be text")
        try:
            parsed.append(parse(text))
        except ValueError as exc:
            raise HostedCARowError(field, str(exc)) from exc
    return parsed


def authored_hosted_ca(row: Any) -> HostedCA:
    """Validate one Hosted CA row by constructing the Hosted CA it declares.

    Args:
        row: One entry of an RPKI CA's ``hosted_cas``.

    Returns:
        The HostedCA the row declares.

    Raises:
        HostedCARowError: If the row does not validate, naming its field.
    """
    if not isinstance(row, dict) or set(row) != {"asn", "prefixes", "roas", "aspas"}:
        raise HostedCARowError("asn", "Hosted CA fields do not match the definition schema")
    asn = row["asn"]
    if isinstance(asn, bool) or not isinstance(asn, int):
        raise HostedCARowError("asn", "Hosted CA ASN must be a whole number")
    try:
        as_asn(asn)
    except ValueError as exc:
        raise HostedCARowError("asn", str(exc)) from exc
    prefixes = row["prefixes"]
    if not isinstance(prefixes, list) or not prefixes:
        raise HostedCARowError("prefixes", "Hosted CA needs at least one prefix")
    for prefix in prefixes:
        if not isinstance(prefix, str):
            raise HostedCARowError("prefixes", "Hosted CA prefixes must be text")
        try:
            ipaddress.ip_network(prefix, strict=True)
        except ValueError as exc:
            raise HostedCARowError("prefixes", f"'{prefix}' is not a prefix in CIDR notation") from exc
    roas = _starting_objects(row["roas"], "roas", ROA.parse)
    aspas = _starting_objects(row["aspas"], "aspas", ASPA.parse)
    try:
        return HostedCA(asn, prefixes, roas=roas, aspas=aspas)
    except ValueError as exc:
        # The Hosted CA checks each starting object against its resources and customer.
        raise HostedCARowError("aspas" if "ASPA" in str(exc) else "roas", str(exc)) from exc


def authored_rpki_ca(value: Any, node_name: str) -> RPKICAService:
    """Validate an RPKI CA declaration by constructing the Service it describes.

    Args:
        value: The Node's ``rpki_ca``: a hostname or null, and its Hosted CA rows.
        node_name: The Node's name, which Krill answers to when no hostname is declared.

    Returns:
        The RPKICAService the declaration builds.

    Raises:
        ValueError: If the declaration does not validate, including when the
            Node's name is not a DNS name and no hostname is declared.
    """
    if not isinstance(value, dict) or set(value) != {"hostname", "hosted_cas"}:
        raise ValueError("RPKI CA needs a hostname and a list of Hosted CAs")
    hostname = value["hostname"]
    if hostname is not None and not isinstance(hostname, str):
        raise ValueError("RPKI CA hostname must be text, or empty for the Node's name")
    if not isinstance(value["hosted_cas"], list):
        raise ValueError("RPKI CA Hosted CAs must be a list")
    hosted = [authored_hosted_ca(row) for row in value["hosted_cas"]]
    return RPKICAService(hosted, hostname=node_name if hostname is None else hostname)


def _rpki_ca_error_control(value: Any) -> str:
    """Identify the dialog control for an RPKI CA validation failure."""
    if not isinstance(value, dict) or set(value) != {"hostname", "hosted_cas"}:
        return "rpki-ca-enabled"
    if value["hostname"] is not None and not isinstance(value["hostname"], str):
        return "rpki-ca-hostname"
    rows = value["hosted_cas"]
    if not isinstance(rows, list) or not rows:
        return "rpki-ca-hosted-cas"
    seen: set[int] = set()
    for index, row in enumerate(rows):
        try:
            hosted = authored_hosted_ca(row)
        except HostedCARowError as exc:
            return f"rpki-ca-hosted-cas-{index}-{exc.field}"
        if hosted.asn in seen:
            return f"rpki-ca-hosted-cas-{index}-asn"
        seen.add(hosted.asn)
    # Every row is fine, so the hostname, which the Node's name stands in for, is what fails.
    return "rpki-ca-hostname"


def authored_validator(value: Any) -> ValidatorService:
    """Validate a Validator declaration by constructing the Service it describes.

    Args:
        value: The Node's ``validator``: its refresh interval in seconds.

    Returns:
        The ValidatorService the declaration builds.

    Raises:
        ValueError: If the declaration does not validate.
    """
    if not isinstance(value, dict) or set(value) != {"refresh"}:
        raise ValueError("Validator needs a refresh interval in seconds")
    return ValidatorService(refresh=value["refresh"])


def _validator_error_control(value: Any) -> str:
    """Identify the dialog control for a Validator validation failure."""
    if isinstance(value, dict) and set(value) == {"refresh"}:
        return "validator-refresh"
    return "validator-enabled"


def authored_resolver(value: Any, topology: Dict[str, Any]) -> ResolverService:
    """Validate a Resolver declaration by constructing it with its model.

    Node references stay as their authored IDs; the generated script
    resolves them to Nodes.

    Args:
        value: Resolver declaration with hints, forwarders, TTLs, and rules.
        topology: Authored Nodes and Links used to validate references.

    Returns:
        A ResolverService ready to attach to its Node.

    Raises:
        ValueError: A Resolver setting or reference is invalid.
    """
    required = {"root_hints", "forwarders", "max_ttl", "max_negative_ttl", "access_control"}
    if not isinstance(value, dict) or not required <= set(value) <= required | {"trust_anchors"}:
        raise ValueError("Resolver needs root_hints, forwarders, TTL caps and access_control")
    for field in ("root_hints", "forwarders", "access_control"):
        if not isinstance(value[field], list):
            raise ValueError(f"Resolver {field} must be a list")
    hints: List[Any] = []
    for reference in value["root_hints"]:
        addresses = authored_nameservers([reference], topology)
        hints.append(reference["address"] if "address" in reference else addresses[0])
    forwarders: List[str] = []
    for reference in value["forwarders"]:
        forwarders.extend(authored_nameservers([reference], topology))
    rules = []
    for item in value["access_control"]:
        if not isinstance(item, dict) or set(item) != {"subnet", "action"} or not all(
            isinstance(item[key], str) for key in ("subnet", "action")
        ):
            raise ValueError("Resolver Access Rule needs text subnet and action")
        rules.append(AccessRule(item["subnet"], item["action"]))
    for field in ("max_ttl", "max_negative_ttl"):
        if isinstance(value[field], bool) or not isinstance(value[field], int):
            raise ValueError(f"Resolver {field} must be whole seconds")
    anchors = value.get("trust_anchors", [])
    if not isinstance(anchors, list):
        raise ValueError("Resolver trust_anchors must be a list")
    anchor_nodes: List[Any] = []
    primary_by_node = {node["id"]: [zone for zone in node.get("dns", {}).get("zones", [])
                                    if zone["kind"] == "primary" and zone.get("dnssec")]
                       for node in topology["nodes"]}
    for anchor in anchors:
        if (not isinstance(anchor, dict) or set(anchor) != {"node_id"}
                or not isinstance(anchor["node_id"], str)
                or not any(zone["name"] == "." for zone in primary_by_node.get(anchor["node_id"], []))):
            raise ValueError("Resolver trust anchor must name a signed root Primary Node")
        anchor_nodes.append(anchor["node_id"])
    return ResolverService(root_hints=hints, forwarders=forwarders,
                           max_ttl=value["max_ttl"],
                           max_negative_ttl=value["max_negative_ttl"],
                           access_control=rules, trust_anchors=anchor_nodes)


def dhcp_pool(pool: Any) -> Pool:
    """Build the Pool a definition's DHCP Pool declares.

    The Pool and Reservation types own the family rules: an IPv4
    Reservation names a hardware address, an IPv6 one exactly one of a
    hardware address or a DUID, and options must suit the Pool's family.

    Args:
        pool: One Pool object from a Node's ``dhcp.pools``.

    Returns:
        The validated Pool.

    Raises:
        ValueError: The declaration is incomplete or breaks a Pool rule.
    """
    if not isinstance(pool, dict) or set(pool) != _POOL_FIELDS:
        raise ValueError("Pool fields do not match the Lab Example definition schema")
    for field in ("cidr", "start", "end", "router", "dns_server"):
        if not isinstance(pool[field], str):
            raise ValueError(f"Pool {field} must be text")
    if not isinstance(pool["reservations"], list):
        raise ValueError("Pool reservations must be a list")
    reservations = []
    for item in pool["reservations"]:
        if not isinstance(item, dict) or set(item) != _RESERVATION_FIELDS or any(
                not isinstance(item[field], str) for field in _RESERVATION_FIELDS):
            raise ValueError("Reservation fields do not match the Lab Example definition schema")
        reservations.append(Reservation(**item))
    return Pool(
        cidr=pool["cidr"], start=pool["start"], end=pool["end"],
        lease_time=pool["lease_time"], preferred_lifetime=pool["preferred_lifetime"],
        router=pool["router"], dns_server=pool["dns_server"],
        reservations=tuple(reservations),
    )
