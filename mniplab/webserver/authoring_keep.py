"""Review and Keep FRR configuration and supported runtime state from a Preview."""

import asyncio
import concurrent.futures
import copy
import difflib
import hashlib
import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from mniplab.rpki.models import ASPA, ROA

_RESERVATION_FIELDS = {"ip", "mac", "duid", "hostname"}


def _sections(config: str) -> Optional[Dict[str, str]]:
    """Retain every line in its section; refuse ambiguous section identities."""
    sections: Dict[str, str] = {}
    heading: Optional[str] = None
    leading = ""
    for line in config.splitlines(keepends=True):
        command = line.strip()
        if not command or command.startswith("!"):
            if heading is None:
                leading += line
            else:
                sections[heading] += line
        elif line[0].isspace() or command in (
            "exit",
            "exit-vrf",
            "exit-address-family",
        ):
            if heading is None:
                return None
            sections[heading] += line
        else:
            # A banner's body need not be indented. Repeated headings have
            # no unique identity, so neither is safe for partial Keep.
            if command.startswith("banner ") or command in sections:
                return None
            heading = command
            sections[heading] = leading + line
            leading = ""
    return None if leading else sections


def config_sections(before: str, after: str) -> List[Dict[str, Any]]:
    """Describe changes as complete FRR sections, or a whole-Router fallback.

    Args:
        before: The last kept configuration, initially the Preview's baseline.
        after: The Router's current authored configuration.

    Returns:
        Changed sections with their ids, top-level names, diff lines and
        added/removed line counts. ``whole`` marks an ambiguous configuration
        that can only be kept as one complete Router.
    """
    if before == after:
        return []
    old, new = _sections(before), _sections(after)
    whole = old is None or new is None
    if old is not None and new is not None:
        # Reordering blocks cannot be represented by independent replacements.
        whole = [name for name in old if name in new] != [
            name for name in new if name in old
        ]
    pairs = [("Whole Router", before, after)] if whole else []
    if not whole:
        for name in dict.fromkeys([*(old or {}), *(new or {})]):
            previous, current = (old or {}).get(name, ""), (new or {}).get(name, "")
            # FRR places a separator after the last command in a group.
            # Adding another route can move that separator without changing
            # the preceding route. Keep those lines for writing, not choosing.
            previous_lines = [
                line for line in previous.splitlines() if line.strip() not in ("", "!")
            ]
            current_lines = [
                line for line in current.splitlines() if line.strip() not in ("", "!")
            ]
            if previous_lines != current_lines:
                pairs.append((name, previous, current))
    changes = []
    for index, (name, previous, current) in enumerate(pairs):
        lines = [
            line[0] + line[2:]
            for line in difflib.ndiff(previous.splitlines(), current.splitlines())
            if not line.startswith("? ")
        ]
        changes.append(
            {
                "id": index,
                "name": name,
                "whole": whole,
                "lines": lines,
                "added": sum(
                    line.startswith("+") and line[1:].strip() not in ("", "!")
                    for line in lines
                ),
                "removed": sum(
                    line.startswith("-") and line[1:].strip() not in ("", "!")
                    for line in lines
                ),
            }
        )
    return changes


def apply_config_sections(before: str, after: str, selected: List[int]) -> str:
    """Keep chosen sections intact while preserving every unselected section.

    Args:
        before: The last kept configuration.
        after: The current authored configuration.
        selected: Ids from the reviewed changed sections.

    Returns:
        The configuration with only those complete sections kept.

    Raises:
        ValueError: A selected section no longer exists.
    """
    changes = config_sections(before, after)
    if any(type(item) is not int or not 0 <= item < len(changes) for item in selected):
        raise ValueError(
            "A selected FRR section no longer exists; review the changes again"
        )
    if not selected:
        return before
    if changes[0]["whole"]:
        return after
    old, new = _sections(before) or {}, _sections(after) or {}
    names = {changes[index]["name"] for index in selected}
    # Place added sections before the next existing one in running-config
    # order. Unselected removals retain their original position.
    order = list(old)
    for index, name in enumerate(new):
        if name in old or name not in names:
            continue
        following = next(
            (item for item in list(new)[index + 1 :] if item in order), None
        )
        order.insert(order.index(following) if following else len(order), name)
    return "".join(
        new.get(name, "") if name in names else old.get(name, "") for name in order
    )


def keep_digest(sections: List[Dict[str, Any]]) -> str:
    """Identify the exact diff an Instructor reviewed."""
    return hashlib.sha256(
        json.dumps(sections, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _condition_value(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Full comparable Link Condition, including unconfigured fields."""
    value = value or {}
    return {
        "status": value.get("status", "up"),
        "delay": value.get("delay"),
        "jitter": value.get("jitter"),
        "loss": value.get("loss"),
        "bandwidth": value.get("bandwidth"),
        "scheduler": value.get("scheduler", "hfsc"),
    }


def pool_value(pool: Dict[str, Any]) -> Dict[str, Any]:
    """A Pool's authored fields, from a definition or a running Service.

    The running Service also reports its resolved Interface and family;
    those are Lab state rather than the recipe, so they are left out.

    Args:
        pool: A definition's Pool, or one Pool as ``get_node_pools`` reports it.

    Returns:
        The Pool in the definition's shape, comparable across both sources.
    """
    return {
        "cidr": pool["cidr"],
        "start": pool["start"],
        "end": pool["end"],
        "lease_time": pool["lease_time"],
        "preferred_lifetime": pool.get("preferred_lifetime"),
        "router": pool["router"],
        "dns_server": pool["dns_server"],
        "reservations": [
            {field: item[field] for field in _RESERVATION_FIELDS}
            for item in pool["reservations"]
        ],
    }


def _rpki_value(roas: List[ROA], aspas: List[ASPA]) -> Dict[str, List[str]]:
    """A Hosted CA's ROAs and ASPAs as sorted declaration lines.

    Args:
        roas: The Hosted CA's ROAs, as models.
        aspas: The Hosted CA's ASPAs, as models.

    Returns:
        Each object as Krill writes it, so spellings that normalise equal compare equal.
    """
    return {
        "roas": sorted(str(roa) for roa in roas),
        "aspas": sorted(str(aspa) for aspa in aspas),
    }


def keep_feature_baseline(document: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Snapshot supported declarative state under stable authored IDs."""
    features: Dict[str, Dict[str, Any]] = {}
    names = {node["id"]: node["name"] for node in document["topology"]["nodes"]}
    types = {node["id"]: node["type"] for node in document["topology"]["nodes"]}
    pair_counts: Dict[frozenset[str], int] = {}
    for link in document["topology"]["links"]:
        pair = frozenset(end["node_id"] for end in link["ends"])
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
    for link in document["topology"]["links"]:
        first, second = link["ends"]
        if pair_counts[frozenset((first["node_id"], second["node_id"]))] == 1:
            feature_id = f"link:{link['id']}"
            features[feature_id] = {
                "kind": "link_condition",
                "link_id": link["id"],
                "node1_name": names[first["node_id"]],
                "node2_name": names[second["node_id"]],
                "name": f"{names[first['node_id']]} ↔ {names[second['node_id']]}",
                "value": _condition_value(link.get("condition")),
            }
        for end in link["ends"]:
            if types[end["node_id"]] != "switch":
                continue
            feature_id = f"vlan:{end['interface_id']}"
            features[feature_id] = {
                "kind": "switch_vlan",
                "link_id": link["id"],
                "interface_id": end["interface_id"],
                "node_id": end["node_id"],
                "node_name": names[end["node_id"]],
                "port": end["port"],
                "name": f"{names[end['node_id']]}-eth{end['port']}",
                "value": end.get("vlan"),
            }
    for node in document["topology"]["nodes"]:
        for pool in node.get("dhcp", {}).get("pools", []):
            features[f"dhcp:{node['id']}:{pool['cidr']}"] = {
                "kind": "dhcp_pool",
                "node_id": node["id"],
                "node_name": node["name"],
                "cidr": pool["cidr"],
                "name": f"{node['name']} · {pool['cidr']}",
                "value": pool_value(pool),
            }
        for zone in node.get("dns", {}).get("zones", []):
            if zone["kind"] == "primary":
                features[f"dns:{node['id']}:{zone['name']}"] = {
                    "kind": "dns_records",
                    "node_id": node["id"],
                    "node_name": node["name"],
                    "zone": zone["name"],
                    "name": f"{node['name']} · {zone['name']}",
                    "value": sorted(
                        zone["records"],
                        key=lambda item: (
                            item["name"],
                            item["type"],
                            item["rdata"],
                            item["ttl"],
                        ),
                    ),
                }
        if "resolver" in node:
            features[f"resolver:{node['id']}"] = {
                "kind": "resolver_acl",
                "node_id": node["id"],
                "node_name": node["name"],
                "name": f"{node['name']} · Access Control",
                "value": node["resolver"]["access_control"],
            }
        if "rpki_ca" in node:
            for row in node["rpki_ca"]["hosted_cas"]:
                features[f"rpki:{node['id']}:{row['asn']}"] = {
                    "kind": "rpki_objects",
                    "node_id": node["id"],
                    "node_name": node["name"],
                    "asn": row["asn"],
                    "name": f"{node['name']} · AS{row['asn']}",
                    "value": _rpki_value(
                        [ROA.parse(text) for text in row["roas"]],
                        [ASPA.parse(text) for text in row["aspas"]],
                    ),
                }
    return features


def _running_configs(handle: Any, names: List[str]) -> Dict[str, str]:
    """Read Routers' running configuration bodies from a running Lab handle."""
    if handle is None:
        raise ValueError("The Preview Lab is no longer running")
    from .authoring import frr_config_body

    return {name: frr_config_body(handle.get_running_config(name)) for name in names}


def _live_keep_features(handle: Any, features: Dict[str, dict]) -> Dict[str, Any]:
    """Read supported runtime intent from a Lab handle, keyed by authored IDs."""
    if handle is None:
        raise ValueError("The Preview Lab is no longer running")
    from .authoring import dns_keep_records

    switch_names = {
        item["node_name"]
        for item in features.values()
        if item["kind"] == "switch_vlan"
    }
    switches = {name: handle.get_switch_vlans(name) for name in switch_names}
    conditions = (
        handle.get_link_conditions()
        if any(item["kind"] == "link_condition" for item in features.values())
        else []
    )
    by_pair = {
        frozenset((item["node1"], item["node2"])): item for item in conditions
    }
    dhcp_names = {
        item["node_name"]
        for item in features.values()
        if item["kind"] == "dhcp_pool"
    }
    pools = {
        name: {
            pool["cidr"]: pool
            for pool in handle.get_node_pools(name)["pools"]
        }
        for name in dhcp_names
    }
    dns_records = {
        feature_id: handle.get_dns_records(item["node_name"], item["zone"])
        for feature_id, item in features.items()
        if item["kind"] == "dns_records"
    }
    resolver_access = {
        name: handle.get_resolver_access(name)
        for name in {
            item["node_name"]
            for item in features.values()
            if item["kind"] == "resolver_acl"
        }
    }
    rpki_cas = {
        name: {row["asn"]: row for row in handle.list_rpki_cas(name)}
        for name in {
            item["node_name"]
            for item in features.values()
            if item["kind"] == "rpki_objects"
        }
    }
    live: Dict[str, Any] = {}
    for feature_id, item in features.items():
        if item["kind"] == "switch_vlan":
            switch = item["node_name"]
            port: Dict[str, Any] = next(
                (
                    value
                    for name, value in switches[switch]["ports"].items()
                    if name == item["name"] or name.endswith(item["name"])
                ),
                {},
            )
            mode = port.get("mode", "none")
            live[feature_id] = (
                {"mode": "access", "vlan": port.get("tag")}
                if mode == "access"
                else (
                    {
                        "mode": "trunk",
                        "trunks": port.get("trunks", []),
                        "native": port.get("native"),
                    }
                    if mode == "trunk"
                    else None
                )
            )
        elif item["kind"] == "link_condition":
            live[feature_id] = _condition_value(
                by_pair.get(frozenset((item["node1_name"], item["node2_name"])))
            )
        elif item["kind"] == "dhcp_pool":
            pool = pools[item["node_name"]].get(item["cidr"])
            if pool is None:
                raise ValueError(f"{item['node_name']} no longer serves {item['cidr']}")
            live[feature_id] = pool_value(pool)
        elif item["kind"] == "dns_records":
            live[feature_id] = dns_keep_records(
                dns_records[feature_id],
                item["zone"],
                item["value"],
                item["node_name"],
            )
        elif item["kind"] == "resolver_acl":
            live[feature_id] = [
                {"subnet": rule["subnet"], "action": rule["action"]}
                for rule in resolver_access[item["node_name"]]
            ]
        elif item["kind"] == "rpki_objects":
            hosted = rpki_cas[item["node_name"]].get(item["asn"])
            if hosted is None:
                raise ValueError(f"{item['node_name']} no longer holds AS{item['asn']}")
            # Only the declaration fields are read; signed objects never reach Keep.
            live[feature_id] = _rpki_value(
                [
                    ROA(prefix=roa["prefix"], max_length=roa["max_length"], origin=roa["origin"])
                    for roa in hosted["roas"]
                ],
                [
                    ASPA(customer=aspa["customer"], providers=aspa["providers"])
                    for aspa in hosted["aspas"]
                ],
            )
    return live


def _python_keep_features(handle: Any, topology: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot a Python Preview's Services, VLANs and unambiguous Links."""
    if handle is None:
        raise ValueError("The Preview Lab is no longer running")
    features: Dict[str, Any] = {}
    types = {node["id"]: node["type"] for node in topology["nodes"]}
    pairs = [
        frozenset((link["source"], link["target"])) for link in topology["links"]
    ]
    for index, link in enumerate(topology["links"]):
        if pairs.count(pairs[index]) == 1:
            features[f"link:{index}"] = {
                "kind": "link_condition",
                "link_id": str(index),
                "node1_name": link["source"],
                "node2_name": link["target"],
                "name": f"{link['source']} ↔ {link['target']}",
                "value": {},
            }
        for side in ("source", "target"):
            if types[link[side]] == "switch":
                name = link[f"{side}_intf"]
                features[f"vlan:{name}"] = {
                    "kind": "switch_vlan",
                    "node_id": link[side],
                    "node_name": link[side],
                    "name": name,
                    "value": None,
                }
    for node in topology["nodes"]:
        name = node["id"]
        if "dhcp" in node.get("services", []):
            served = handle.get_node_pools(name)
            for pool in served["pools"]:
                features[f"dhcp:{name}:{pool['cidr']}"] = {
                    "kind": "dhcp_pool",
                    "node_id": name,
                    "node_name": name,
                    "cidr": pool["cidr"],
                    "name": f"{name} · {pool['cidr']}",
                    "value": pool_value(pool),
                }
        if "resolver" in node.get("services", []):
            features[f"resolver:{name}"] = {
                "kind": "resolver_acl",
                "node_id": name,
                "node_name": name,
                "name": f"{name} · Access Control",
                "value": [],
            }
    if any("dns" in node.get("services", []) for node in topology["nodes"]):
        for server in handle.list_dns_nodes():
            name = server["node"]
            for zone in server["zones"]:
                if server.get("roles", {}).get(zone, {}).get("role") == "primary":
                    features[f"dns:{name}:{zone}"] = {
                        "kind": "dns_records",
                        "node_id": name,
                        "node_name": name,
                        "zone": zone,
                        "name": f"{name} · {zone}",
                        "value": [],
                    }
    values = _live_keep_features(handle, features)
    return {
        feature_id: {**item, "value": values[feature_id]}
        for feature_id, item in features.items()
    }


def _record_keep_destinations(
    repository: Any, draft: Dict[str, Any]
) -> Dict[str, Any]:
    """Record a Python draft's Router Seed destinations via authoring_record."""
    from .authoring_record import RecordError, record_script

    with tempfile.TemporaryDirectory(prefix="keep-") as package:
        script = os.path.join(package, "lab.py")
        repository.write_builder(draft, script)
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                recorded = asyncio.run(record_script(package, script, keep=True))
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    recorded = pool.submit(
                        asyncio.run, record_script(package, script, keep=True)
                    ).result()
            return recorded["keep"]
        except RecordError:
            return {}


def snapshot_preview(
    repository: Any, draft_id: str, lab_key: str, handle: Any
) -> None:
    """Capture baseline Router and Service state from a Preview Lab handle.

    Args:
        repository: The AuthoringRepository storing drafts and snapshots.
        draft_id: The draft the Preview was built from.
        lab_key: The running Preview Lab's key.
        handle: The live Preview Lab handle.
    """
    if handle is None:
        raise ValueError("The Preview Lab is no longer running")
    draft = repository.read(draft_id)
    if draft.get("kind") == "python":
        destinations = _record_keep_destinations(repository, draft)
        topology = handle.topology()
        names = {node["id"] for node in topology["nodes"]}
        python_routers = {
            name: destination
            for name, destination in destinations.items()
            if name in names
        }
        for node in topology["nodes"]:
            if node["type"] == "router" and node["id"] not in python_routers:
                python_routers[node["id"]] = {
                    "seed": None,
                    "not_keepable": (
                        "Seed folder could not be determined; use frr_dir "
                        "under frr-config in your script."
                    ),
                }
        running = _running_configs(handle, list(python_routers))
        repository.record_snapshot(
            draft_id,
            lab_key,
            {
                name: {"name": name, "config": running[name], **destination}
                for name, destination in python_routers.items()
            },
            _python_keep_features(handle, topology),
        )
        return
    routers = [
        node for node in draft["topology"]["nodes"] if node["type"] == "router"
    ]
    running = _running_configs(handle, [node["name"] for node in routers])
    repository.record_snapshot(
        draft_id,
        lab_key,
        {
            node["id"]: {"name": node["name"], "config": running[node["name"]]}
            for node in routers
        },
        keep_feature_baseline(draft),
    )


def review_keep(
    repository: Any, draft_id: str, preview_key: Optional[str], handle: Any
) -> Dict[str, Any]:
    """Diff live Preview state against the draft's Preview baseline snapshot.

    Args:
        repository: The AuthoringRepository storing drafts and snapshots.
        draft_id: The draft being reviewed.
        preview_key: The caller's running Preview Lab key, if any.
        handle: The live Preview Lab handle.

    Returns:
        The Keep review payload with ``key``, ``routers``, ``features``, and
        ``has_kept_changes``.
    """
    from .authoring import DraftConflictError

    draft = repository.read(draft_id)
    python = draft.get("kind") == "python"
    snapshot = repository.read_snapshot(draft_id)
    if (
        snapshot is None
        or not preview_key
        or handle is None
        or preview_key != snapshot.get("lab_key")
    ):
        raise DraftConflictError("No Preview of this draft is running")
    routers = snapshot["routers"]
    live = _running_configs(handle, [item["name"] for item in routers.values()])
    changes = []
    for node_id, item in routers.items():
        sections = config_sections(item["config"], live[item["name"]])
        changes.append(
            {
                "node_id": node_id,
                "name": item["name"],
                "config": live[item["name"]],
                **(
                    {"seed": item.get("seed"), "not_keepable": item.get("not_keepable")}
                    if python
                    else {}
                ),
                "digest": keep_digest(sections),
                "sections": sections,
                "added": sum(section["added"] for section in sections),
                "removed": sum(section["removed"] for section in sections),
            }
        )
    feature_snapshot = snapshot.get("features", {})
    feature_live = _live_keep_features(handle, feature_snapshot)
    feature_changes = [
        {
            "id": feature_id,
            "kind": item["kind"],
            "name": item["name"],
            **({"not_keepable": "change in your script"} if python else {}),
            **{
                field: item[field]
                for field in ("node_id", "node_name", "link_id")
                if field in item
            },
            "before": item["value"],
            "after": feature_live[feature_id],
            "digest": keep_digest(
                [
                    {
                        "id": feature_id,
                        "before": item["value"],
                        "after": feature_live[feature_id],
                    }
                ]
            ),
        }
        for feature_id, item in feature_snapshot.items()
        if feature_live[feature_id] != item["value"]
    ]
    return {
        "key": preview_key,
        "features": feature_changes,
        "routers": changes,
        "has_kept_changes": snapshot.get("has_kept_changes"),
    }


def apply_keep(
    repository: Any,
    draft_id: str,
    expected_revision: int,
    preview_key: Optional[str],
    handle: Any,
    selections: List[Dict[str, Any]],
    feature_selections: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Apply selected, reviewed runtime changes from a Preview to a draft.

    Args:
        repository: The AuthoringRepository storing drafts and snapshots.
        draft_id: The draft to update.
        expected_revision: The revision reviewed by the Instructor.
        preview_key: The running Preview Lab's key.
        handle: The live Preview Lab handle.
        selections: Router section selections (``node_id``, ``digest``, ``sections``).
        feature_selections: Feature selections (``id``, ``digest``).

    Returns:
        The updated, persisted draft document.
    """
    from .authoring import DraftConflictError, without_fast_timers

    with repository._lock:
        document = repository.read(draft_id)
        snapshot = repository.read_snapshot(draft_id)
        if (
            snapshot is None
            or not preview_key
            or handle is None
            or snapshot.get("lab_key") != preview_key
        ):
            raise DraftConflictError("Start a Preview of this draft before keeping")
        if document["revision"] != expected_revision:
            raise DraftConflictError(
                "Save or reopen this changed draft before keeping"
            )
        if document.get("kind") == "python":
            if feature_selections:
                raise ValueError(
                    "Services, VLANs and Link Conditions: change in your script"
                )
            if not any(selection.get("sections") for selection in selections):
                raise ValueError("Select at least one change to Keep")
            routers = snapshot["routers"]
            running = _running_configs(
                handle, [item["name"] for item in routers.values()]
            )
            live = {
                node_id: running[item["name"]]
                for node_id, item in routers.items()
                if item["name"] in running
            }
            for selection in selections:
                node_id = selection.get("node_id")
                taken = routers.get(node_id)
                if taken is None or node_id not in live:
                    raise ValueError("Keep names a Router that is not in this Preview")
                seed = taken.get("seed")
                if (
                    not seed
                    or taken.get("not_keepable")
                    or not repository._file_pattern.fullmatch(seed)
                ):
                    raise ValueError(
                        taken.get("not_keepable") or "Router has no Seed folder"
                    )
                sections = config_sections(taken["config"], live[node_id])
                if selection.get("digest") != keep_digest(sections):
                    raise DraftConflictError(
                        f"Router '{taken['name']}' changed since you reviewed it; review again"
                    )
                ids = selection.get("sections")
                if not isinstance(ids, list):
                    raise ValueError("Keep sections must be a list")
                if not ids:
                    continue
                kept = apply_config_sections(taken["config"], live[node_id], ids)
                document["files"][seed] = kept
                taken["config"] = kept
            repository.validate(document)
            document["revision"] += 1
            document["updated_at"] = time.time()
            repository._atomic_write(repository._draft_path(draft_id), document)
            repository.record_snapshot(
                draft_id,
                preview_key,
                snapshot["routers"],
                snapshot.get("features"),
                has_kept_changes=True,
            )
            return copy.deepcopy(document)
        if (
            not any(selection.get("sections") for selection in selections)
            and not feature_selections
        ):
            raise ValueError("Select at least one change to Keep")
        routers = snapshot["routers"]
        running = (
            _running_configs(handle, [item["name"] for item in routers.values()])
            if selections
            else {}
        )
        live = {
            node_id: running[item["name"]]
            for node_id, item in routers.items()
            if item["name"] in running
        }
        nodes = {node["id"]: node for node in document["topology"]["nodes"]}
        for selection in selections:
            node_id = selection.get("node_id")
            node = nodes.get(node_id)
            taken = routers.get(node_id)
            if (
                node is None
                or node["type"] != "router"
                or taken is None
                or node_id not in live
            ):
                raise ValueError("Keep names a Router that is not in this Preview")
            sections = config_sections(taken["config"], live[node_id])
            if selection.get("digest") != keep_digest(sections):
                raise DraftConflictError(
                    f"Router '{taken['name']}' changed since you reviewed it; review again"
                )
            ids = selection.get("sections")
            if not isinstance(ids, list):
                raise ValueError("Keep sections must be a list")
            if not ids:
                continue
            kept = apply_config_sections(taken["config"], live[node_id], ids)
            node["frr_config"] = without_fast_timers(node, kept)
            taken["config"] = kept
        feature_snapshot = snapshot.get("features", {})
        live_features = (
            _live_keep_features(handle, feature_snapshot)
            if feature_selections
            else None
        )
        links = {link["id"]: link for link in document["topology"]["links"]}
        for selection in feature_selections or []:
            feature_id = selection.get("id")
            taken = feature_snapshot.get(feature_id)
            if (
                taken is None
                or live_features is None
                or feature_id not in live_features
            ):
                raise ValueError("Keep names an unsupported Preview setting")
            current = live_features[feature_id]
            digest = keep_digest(
                [{"id": feature_id, "before": taken["value"], "after": current}]
            )
            if selection.get("digest") != digest:
                raise DraftConflictError(
                    f"{taken['name']} changed since you reviewed it; review again"
                )
            if taken["kind"] == "switch_vlan":
                end = next(
                    end
                    for end in links[taken["link_id"]]["ends"]
                    if end["interface_id"] == taken["interface_id"]
                )
                if current is None:
                    end.pop("vlan", None)
                else:
                    end["vlan"] = current
            elif taken["kind"] == "link_condition":
                links[taken["link_id"]]["condition"] = current
            elif taken["kind"] == "dhcp_pool":
                pools = nodes[taken["node_id"]]["dhcp"]["pools"]
                index = next(
                    index
                    for index, pool in enumerate(pools)
                    if pool["cidr"] == taken["cidr"]
                )
                pools[index] = current
            elif taken["kind"] == "dns_records":
                zone = next(
                    zone
                    for zone in nodes[taken["node_id"]]["dns"]["zones"]
                    if zone["kind"] == "primary" and zone["name"] == taken["zone"]
                )
                zone["records"] = current
            elif taken["kind"] == "resolver_acl":
                nodes[taken["node_id"]]["resolver"]["access_control"] = current
            elif taken["kind"] == "rpki_objects":
                hosted = next(
                    row
                    for row in nodes[taken["node_id"]]["rpki_ca"]["hosted_cas"]
                    if row["asn"] == taken["asn"]
                )
                hosted["roas"] = current["roas"]
                hosted["aspas"] = current["aspas"]
            taken["value"] = current
        saved = repository.save(draft_id, expected_revision, document)
        repository.record_snapshot(
            draft_id,
            preview_key,
            snapshot["routers"],
            feature_snapshot,
            has_kept_changes=True,
        )
        return saved
