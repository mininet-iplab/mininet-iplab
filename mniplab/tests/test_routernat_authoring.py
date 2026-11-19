"""Router NAT in a canvas definition: the validator accepts it, refuses what it cannot mean, and the export declares it.

A Router node carries ``nat`` (port forwards and static mappings) and a Link
end carries ``outside``. The export is checked by its syntax tree, so the
test reads what the generated script would call and not how it is laid out.
"""

from __future__ import annotations

import ast
import copy
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Tuple

from mniplab.tests.authoring_fixtures import host_node, router_node
from mniplab.webserver.authoring import AuthoringRepository, DraftValidationError
from mniplab.webserver.authoring_python import generate_python

PORT_FORWARD = {"kind": "port_forward", "protocol": "tcp", "outside_port": 8080,
                "inside": "192.168.1.10", "inside_port": 80, "hairpin": True}
STATIC = {"kind": "static", "inside": "192.168.1.10", "outside": "203.0.113.5"}


class CanvasCase(unittest.TestCase):
    """A small definition: r1 with an inside Host and an outside Link to isp1."""

    def setUp(self) -> None:
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.repository = AuthoringRepository(storage.name)

    def document(self, nat: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """A valid definition whose r1 carries ``nat`` when given."""
        base = self.repository.create()
        base["address_family"] = "dual"
        base["catalog"] = {"id": None, "name": "Lesson", "description": "desc",
                           "protocols": ["OSPF"]}
        r1 = router_node("r1", "r1", protocols=["OSPF"])
        if nat is not None:
            r1["nat"] = copy.deepcopy(nat)
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
        return base

    def refusal(self, document: Dict[str, Any]) -> DraftValidationError:
        """Validate ``document``, expecting a refusal; return it."""
        with self.assertRaises(DraftValidationError) as caught:
            self.repository.validate(copy.deepcopy(document))
        return caught.exception


class CanvasRouterNATValidationTests(CanvasCase):
    """The validator takes both entry kinds and the outside mark, and refuses malformed ones."""

    def test_port_forwards_and_static_mappings_on_a_router_validate(self) -> None:
        document = self.document(nat=[PORT_FORWARD, STATIC])

        validated = self.repository.validate(copy.deepcopy(document))

        self.assertEqual(validated["topology"]["nodes"][0]["nat"], [PORT_FORWARD, STATIC])

    def test_an_outside_mark_on_a_host_end_is_refused(self) -> None:
        document = self.document()
        document["topology"]["links"][0]["ends"][1]["outside"] = True

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "h1")
        self.assertIn("only a Router Interface can be outside", str(error))

    def test_an_outside_mark_that_is_not_true_is_refused(self) -> None:
        document = self.document()
        document["topology"]["links"][1]["ends"][0]["outside"] = False

        error = self.refusal(document)

        self.assertIn("outside mark is true when set", str(error))

    def test_a_nat_entry_of_an_unknown_kind_is_refused(self) -> None:
        document = self.document(nat=[{**STATIC, "kind": "masquerade"}])

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "r1")
        self.assertIn("NAT entry 1: its kind is a port forward or a static mapping", str(error))

    def test_a_port_forward_protocol_other_than_tcp_or_udp_is_refused(self) -> None:
        document = self.document(nat=[{**PORT_FORWARD, "protocol": "icmp"}])

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "r1")
        self.assertIn("protocol must be 'tcp' or 'udp'", str(error))

    def test_a_port_outside_one_to_65535_is_refused(self) -> None:
        document = self.document(nat=[{**PORT_FORWARD, "outside_port": 70000}])

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "r1")
        self.assertIn("outside_port must be a port from 1 to 65535", str(error))

    def test_an_address_that_is_not_ipv4_is_refused(self) -> None:
        document = self.document(nat=[{**STATIC, "outside": "2001:db8::5"}])

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "r1")
        self.assertIn("outside must be an IPv4 address", str(error))

    def test_a_port_forward_missing_its_hairpin_setting_is_refused(self) -> None:
        entry = {key: value for key, value in PORT_FORWARD.items() if key != "hairpin"}
        document = self.document(nat=[entry])

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "r1")
        self.assertIn("names its protocol, ports, inside address and hairpin", str(error))

    def test_nat_is_refused_on_a_host(self) -> None:
        document = self.document()
        document["topology"]["nodes"][1]["nat"] = [STATIC]

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "h1")


def _calls(source: str, method: str) -> List[ast.Call]:
    """Every call to ``<something>.<method>(...)`` in a generated script."""
    return [node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == method]


def _entries(source: str) -> Optional[List[Tuple[str, list, dict]]]:
    """The ``nat`` entries of the one ``addRouter`` call, or None when it names no ``nat``.

    Each entry is (class name, positional arguments, keyword arguments).
    """
    [router] = [call for call in _calls(source, "addRouter")
                if ast.literal_eval(call.args[0]) == "r1"]
    nat = [keyword for keyword in router.keywords if keyword.arg == "nat"]
    if not nat:
        return None
    value = nat[0].value
    assert isinstance(value, ast.List)
    entries = []
    for entry in value.elts:
        assert isinstance(entry, ast.Call) and isinstance(entry.func, ast.Name)
        entries.append((entry.func.id, [ast.literal_eval(arg) for arg in entry.args],
                        {keyword.arg: ast.literal_eval(keyword.value) for keyword in entry.keywords}))
    return entries


class CanvasRouterNATExportTests(CanvasCase):
    """The generated script declares the entries on the Router and the mark on its Link end."""

    def test_the_router_declares_its_entries_as_port_forward_and_static_calls(self) -> None:
        source = generate_python(self.repository.validate(self.document(nat=[PORT_FORWARD, STATIC])))

        self.assertEqual(
            _entries(source),
            [("PortForward", ["tcp", 8080, "192.168.1.10", 80], {"hairpin": True}),
             ("StaticNAT", ["192.168.1.10", "203.0.113.5"], {})],
        )

    def test_the_entries_are_imported_from_the_package(self) -> None:
        source = generate_python(self.repository.validate(self.document(nat=[PORT_FORWARD, STATIC])))

        [imported] = [node for node in ast.walk(ast.parse(source))
                      if isinstance(node, ast.ImportFrom) and node.module == "mniplab"]
        self.assertIn("PortForward", [alias.name for alias in imported.names])
        self.assertIn("StaticNAT", [alias.name for alias in imported.names])

    def test_the_outside_end_carries_its_mark_in_the_link_parameters(self) -> None:
        source = generate_python(self.repository.validate(self.document(nat=[PORT_FORWARD])))

        ends = [ast.literal_eval(keyword.value)
                for call in _calls(source, "addLink")
                for keyword in call.keywords if keyword.arg in ("params1", "params2")]
        self.assertIn({"ip": "203.0.113.2/30", "nat": "outside"}, ends)

    def test_a_router_without_nat_declares_none(self) -> None:
        source = generate_python(self.repository.validate(self.document()))

        self.assertIsNone(_entries(source))


class CanvasRouterNATBuildRefusalTests(CanvasCase):
    """The validator refuses what the Lab refuses at build, naming the NAT row or the Interface."""

    def second_outside(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Give r1 a second outside Interface, port 4, toward isp1."""
        document["topology"]["links"].append({"id": "link-out-2", "ends": [
            {"node_id": "r1", "interface_id": "if-000000000005", "port": 4,
             "ipv4": "198.51.100.2/30", "ipv6": "", "outside": True},
            {"node_id": "isp", "interface_id": "if-000000000006", "port": 1,
             "ipv4": "198.51.100.1/30", "ipv6": ""},
        ]})
        return document

    def test_an_entry_on_a_router_with_no_outside_interface_is_refused(self) -> None:
        document = self.document(nat=[PORT_FORWARD])
        del document["topology"]["links"][1]["ends"][0]["outside"]

        error = self.refusal(document)

        self.assertEqual(error.target["id"], "r1")
        self.assertEqual(error.target["controlId"], "router-nat-0-interface")
        self.assertIn("NAT entry 1 needs an outside Interface", str(error))

    def test_an_entry_naming_an_interface_that_is_not_marked_outside_is_refused(self) -> None:
        document = self.document(nat=[{**STATIC, "interface": "r1-eth0"}])

        error = self.refusal(document)

        self.assertEqual(error.target["controlId"], "router-nat-0-interface")
        self.assertIn("names r1-eth0, which is not marked outside", str(error))

    def test_an_entry_naming_an_interface_the_router_does_not_have_is_refused(self) -> None:
        document = self.document(nat=[{**STATIC, "interface": "r9-eth3"}])

        error = self.refusal(document)

        self.assertEqual(error.target["controlId"], "router-nat-0-interface")
        self.assertIn("names r9-eth3, which is not an Interface of r1", str(error))

    def test_an_unnamed_entry_on_a_router_with_two_outside_interfaces_is_refused(self) -> None:
        document = self.second_outside(self.document(nat=[STATIC]))

        error = self.refusal(document)

        self.assertEqual(error.target["controlId"], "router-nat-0-interface")
        self.assertIn("names no Interface, and r1 has 2 outside Interfaces (r1-eth3, r1-eth4)",
                      str(error))

    def test_a_named_entry_on_one_of_two_outside_interfaces_validates(self) -> None:
        document = self.second_outside(self.document(
            nat=[{**STATIC, "interface": "r1-eth4"}]))

        validated = self.repository.validate(copy.deepcopy(document))

        self.assertEqual(validated["topology"]["nodes"][0]["nat"][0]["interface"], "r1-eth4")

    def test_a_port_forward_on_an_outside_interface_without_ipv4_is_refused(self) -> None:
        document = self.document(nat=[PORT_FORWARD])
        document["topology"]["links"][1]["ends"][0]["ipv4"] = ""
        document["topology"]["links"][1]["ends"][0]["ipv6"] = "2001:db8::2/64"

        error = self.refusal(document)

        self.assertEqual(error.target["controlId"], "router-nat-0-interface")
        self.assertIn("needs an IPv4 address on its outside Interface r1-eth3", str(error))

    def test_a_static_mapping_needs_no_ipv4_on_its_outside_interface(self) -> None:
        document = self.document(nat=[STATIC])
        document["topology"]["links"][1]["ends"][0]["ipv4"] = ""

        self.repository.validate(copy.deepcopy(document))

    def test_an_outside_interface_facing_an_internet_gateway_is_refused_without_entries(self) -> None:
        document = self.document()
        document["topology"]["nodes"].append({
            "id": "gw", "name": "nat1", "type": "nat", "ip": "203.0.113.1",
            "subnet": "203.0.113.0/30",
        })
        document["topology"]["links"][1]["ends"][1] = {
            "node_id": "gw", "interface_id": "if-000000000004", "port": 0,
            "ipv4": "203.0.113.1/30", "ipv6": "",
        }

        error = self.refusal(document)

        self.assertEqual(error.target["controlId"], "iface-if-000000000003-outside")
        self.assertIn("r1-eth3 is marked outside, but faces the Internet Gateway nat1", str(error))

    def test_an_outside_interface_facing_the_speaker_in_front_of_a_transit_gateway_is_refused(self) -> None:
        document = self.document()
        document["topology"]["nodes"] += [
            {"id": "sp", "name": "sp1", "type": "speaker", "local_as": 65001, "routes": [],
             "neighbors": [{"peer": "203.0.113.2", "local_address": "203.0.113.1",
                            "local_as": 65001, "peer_as": 65000, "hold_time": None,
                            "routes": []}],
             "startup": []},
            {"id": "tr", "name": "nat2", "type": "transit", "ip": "100.64.50.1/30",
             "subnet": "100.64.50.0/30", "downstream_ids": ["isp"]},
        ]
        document["topology"]["links"][1]["ends"][1] = {
            "node_id": "sp", "interface_id": "if-000000000004", "port": 0,
            "ipv4": "203.0.113.1/30", "ipv6": ""}
        document["topology"]["links"].append({"id": "link-tr", "ends": [
            {"node_id": "sp", "interface_id": "if-000000000007", "port": 1,
             "ipv4": "100.64.50.2/30", "ipv6": ""},
            {"node_id": "tr", "interface_id": "if-000000000008", "port": 0,
             "ipv4": "100.64.50.1/30", "ipv6": ""},
        ]})

        error = self.refusal(document)

        self.assertEqual(error.target["controlId"], "iface-if-000000000003-outside")
        self.assertIn("faces sp1, the Speaker in front of the Internet Gateway nat2", str(error))
