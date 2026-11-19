"""Router NAT drawn from a script: a Python Lab's rules reach the canvas, and a canvas export records back to the same rules."""

from __future__ import annotations

import asyncio
import copy
import os
import tempfile
import textwrap
import unittest
from typing import Any, Dict, List

import pytest

from mniplab.tests.authoring_fixtures import host_node, router_node
from mniplab.webserver.authoring import AuthoringRepository
from mniplab.webserver.authoring_python import generate_python
from mniplab.webserver.authoring_record import record_script

PORT_FORWARD = {"kind": "port_forward", "protocol": "tcp", "outside_port": 8080,
                "inside": "192.168.1.10", "inside_port": 80, "hairpin": True,
                "interface": "r1-eth3"}
STATIC = {"kind": "static", "inside": "192.168.1.10", "outside": "203.0.113.5"}

SCRIPT = textwrap.dedent('''\
    from mniplab import mnIPLab, PortForward, StaticNAT, run_lab, parse_lab_args


    def build_network():
        net = mnIPLab(topo=None)
        r1 = net.addRouter('r1', nat=[
            PortForward('tcp', 8080, '192.168.1.10', 80, hairpin=True, interface='r1-eth3'),
            StaticNAT('192.168.1.10', '203.0.113.5'),
        ])
        h1 = net.addHost('h1')
        isp = net.addRouter('isp1')
        net.addLink(r1, h1, params1={'ip': '192.168.1.1/24'}, params2={'ip': '192.168.1.10/24'})
        net.addLink(r1, isp, port1=3, params1={'ip': '203.0.113.2/30', 'nat': 'outside'},
                    params2={'ip': '203.0.113.1/30'})
        return net
    ''')


def record(source: str) -> Dict[str, Any]:
    """Record a script's Nodes and Links as the canvas does, from its text."""
    package = tempfile.TemporaryDirectory()
    script = os.path.join(package.name, "lab.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(source)
    try:
        return asyncio.run(record_script(package.name, script))
    finally:
        package.cleanup()


def router_nat_of(drawing: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    """The NAT rows a recorded Router carries, or none."""
    [node] = [item for item in drawing["nodes"] if item["name"] == name]
    return node.get("nat", [])


def outside_ends(drawing: Dict[str, Any]) -> List[tuple]:
    """Each (node, port) a recorded Link end marks outside."""
    return sorted((end["node"], end["port"]) for link in drawing["links"]
                  for end in link["ends"] if end.get("outside"))


@pytest.mark.unit
class ScriptRouterNATRecordTests(unittest.TestCase):
    """A Python Lab's Router NAT is drawn on the canvas as the script declares it."""

    def test_the_recorded_router_carries_its_nat_rows_and_the_outside_end_is_marked(self) -> None:
        drawing = record(SCRIPT)

        self.assertEqual(router_nat_of(drawing, "r1"), [PORT_FORWARD, STATIC])
        self.assertEqual(outside_ends(drawing), [("r1", 3)])

    def test_a_router_without_nat_records_no_nat_rows(self) -> None:
        drawing = record(SCRIPT)

        self.assertEqual(router_nat_of(drawing, "isp1"), [])


@pytest.mark.unit
class CanvasRouterNATRoundTripTests(unittest.TestCase):
    """A canvas Router NAT exports to a script that records back to the same rules."""

    def test_the_export_records_back_to_the_canvas_rules(self) -> None:
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        repository = AuthoringRepository(storage.name)
        base = repository.create()
        base["address_family"] = "dual"
        base["catalog"] = {"id": None, "name": "Lesson", "description": "desc", "protocols": ["OSPF"]}
        r1 = router_node("r1", "r1", protocols=["OSPF"])
        r1["nat"] = copy.deepcopy([PORT_FORWARD, STATIC])
        base["topology"] = {
            "nodes": [r1, host_node("h1", "h1", ipv4_gateway="192.168.1.1"),
                      router_node("isp", "isp1", protocols=["OSPF"])],
            "links": [
                {"id": "link-in", "ends": [
                    {"node_id": "r1", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "192.168.1.1/24", "ipv6": ""},
                    {"node_id": "h1", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "192.168.1.10/24", "ipv6": ""},
                ]},
                {"id": "link-out", "ends": [
                    {"node_id": "r1", "interface_id": "if-000000000003", "port": 3,
                     "ipv4": "203.0.113.2/30", "ipv6": "", "outside": True},
                    {"node_id": "isp", "interface_id": "if-000000000004", "port": 0,
                     "ipv4": "203.0.113.1/30", "ipv6": ""},
                ]},
            ],
        }
        document = repository.validate(base)

        drawing = record(generate_python(document))

        self.assertEqual(router_nat_of(drawing, "r1"), [PORT_FORWARD, STATIC])
        self.assertEqual(outside_ends(drawing), [("r1", 3)])
