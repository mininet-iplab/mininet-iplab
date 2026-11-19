"""``examples/labs.json`` is the catalogue every Lab Example is listed in.

The Web UI reads it to list what a Learner can start, and each entry names
the script, its protocols and a ``node_count``; a Layout and a Guide may sit
beside the script. One walk over the catalogue checks all of that for every
Example, so a new Lab is covered the moment it is catalogued.

The unprivileged half reads files. The privileged half starts each Example
once, the way a Learner does: a Lab Example is content built from the
Services, which Test Labs prove, so starting is all the suite asks of it.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import pytest

from mniplab.router import FRRouter
from mniplab.tests.labs import EXAMPLES_DIR, built_example, catalogue, load_example
from mniplab.webserver.authoring import shipped_definition
from mniplab.webserver.catalog import AREAS
from mniplab.webserver.repositories import GuideRepository, LayoutRepository
from mniplab.topology import TopologyBuilder

CATALOGUE = catalogue()


def _example_source(entry: Dict[str, Any]) -> str:
    with open(os.path.join(EXAMPLES_DIR, entry["file"]), "r", encoding="utf-8") as f:
        return f.read()


def _per_example(entry: Dict[str, Any]) -> Any:
    """Parametrise on one entry, marked by what building it needs."""
    source = _example_source(entry)
    marks = []
    if "addSwitch(" in source:
        marks.append(pytest.mark.requires_ovs)
    if "addContainer(" in source:
        marks.append(pytest.mark.requires_docker)
    return pytest.param(entry, id=entry["id"], marks=marks)


def _layout(lab_id: str) -> Optional[Dict[str, Any]]:
    """The Example's Authored Layout, validated as the Web UI would; None if it has none."""
    path = os.path.join(EXAMPLES_DIR, "layouts", f"{lab_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        layout = json.load(f)
    LayoutRepository.validate_canonical(layout)
    return layout


def _guide(lab_id: str) -> Optional[str]:
    """The Example's Guide, read as the Web UI would; None if it has none."""
    try:
        return GuideRepository.load(lab_id, EXAMPLES_DIR)
    except FileNotFoundError:
        return None


def test_every_example_script_is_catalogued_once() -> None:
    """Each ``examples/*.py`` has exactly one entry, whose id is the script's stem."""
    scripts = {
        os.path.splitext(name)[0]
        for name in os.listdir(EXAMPLES_DIR)
        if name.endswith(".py") and not name.startswith("_")
    }
    ids = [entry["id"] for entry in CATALOGUE]

    assert sorted(ids) == sorted(set(ids)), "duplicate ids in examples/labs.json"
    assert set(ids) == scripts
    for entry in CATALOGUE:
        assert entry["file"] == f"{entry['id']}.py"


@pytest.mark.parametrize("entry", [pytest.param(e, id=e["id"]) for e in CATALOGUE])
def test_catalogue_entry_names_a_lab_example(entry: Dict[str, Any]) -> None:
    """The entry describes a script that loads, and the assets beside it are well-formed."""
    assert entry["name"].strip()
    assert entry["description"].strip()
    assert entry["protocols"] and all(isinstance(p, str) for p in entry["protocols"])
    assert isinstance(entry["node_count"], int) and entry["node_count"] > 0

    load_example(entry["id"])

    _layout(entry["id"])

    guide = _guide(entry["id"])
    if guide is not None:
        assert guide.startswith(f"## {entry['name']} Guide\n"), guide.splitlines()[0]


@pytest.mark.unit
@pytest.mark.parametrize("entry", [pytest.param(e, id=e["id"]) for e in CATALOGUE])
def test_catalogue_entry_is_complete(entry: Dict[str, Any]) -> None:
    """Every entry has an Area from the fixed list, a Guide, a layout and a Canvas definition."""
    assert entry.get("area") in AREAS, entry.get("area")

    for key, path in (("guide", entry.get("guide")), ("layout", entry.get("layout"))):
        assert path, f"{entry['id']} names no {key}"
        assert os.path.isfile(os.path.join(os.path.dirname(EXAMPLES_DIR), path)), path
    assert _guide(entry["id"]), "the Guide is empty"
    assert _layout(entry["id"]), "the layout is empty"

    definition = shipped_definition(entry["id"], EXAMPLES_DIR)
    assert definition["topology"]["nodes"], "the Canvas definition has no Nodes"


@pytest.mark.integration
@pytest.mark.requires_root
@pytest.mark.parametrize("entry", [_per_example(e) for e in CATALOGUE])
def test_catalogue_entry_starts_as_it_declares(entry: Dict[str, Any]) -> None:
    """The Example starts, its Routers run their Protocols' daemons, no Startup
    command failed, and ``node_count`` and the Layout match the Topology shown."""
    with built_example(entry["id"]) as net:
        net.start()
        shown: List[str] = sorted(node["id"] for node in TopologyBuilder.build(net)["nodes"])
        missing: Dict[str, List[str]] = {}
        for router in (node for node in net.hosts if isinstance(node, FRRouter)):
            running = router.cmd('vtysh -c "show daemons"')
            absent = [daemon for daemon in router.frr_daemons if daemon not in running]
            if absent:
                missing[router.name] = absent
        warnings = list(net.startup_warnings)

    assert missing == {}
    assert warnings == []
    assert entry["node_count"] == len(shown), shown

    layout = _layout(entry["id"])
    if layout is not None:
        assert sorted(layout["nodes"]) == shown
