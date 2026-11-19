"""Unit tests for Switch Management Web API endpoints, topology metadata, and WebSockets."""

from __future__ import annotations

import ast
import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

import pytest

from mniplab.tests.fakes import (
    FakeIntf,
    FakeLab,
    FakeLink,
    FakeNode,
    FakeTerminalCloserMixin,
    LoginClientMixin,
)
from mniplab.tests.test_switch import FakeSwitch
from mniplab.webserver import registry
from mniplab.webserver import api as api_mod


def make_switch_lab() -> FakeLab:
    """Build a FakeLab containing a switch, nodes, and configured VLAN links."""
    sw1 = FakeSwitch("s1")
    sw1.set_access("s1-eth1", 10)
    sw1.set_trunk("s1-eth2", [10, 20], native=1)
    sw1.clear_vlan("s1-eth3")

    h1 = FakeNode("h1")
    h2 = FakeNode("h2")
    r1 = FakeNode("r1")

    link1 = FakeLink(FakeIntf("s1-eth1", sw1), FakeIntf("h1-eth0", h1))
    link2 = FakeLink(FakeIntf("s1-eth2", sw1), FakeIntf("r1-eth1", r1))
    link3 = FakeLink(FakeIntf("s1-eth3", sw1), FakeIntf("h2-eth0", h2))

    return FakeLab(h1, h2, r1, switches=[sw1], links=[link1, link2, link3])


@pytest.mark.unit
class SwitchWebAPITests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Tests for switch VLAN management REST endpoints."""

    def setUp(self) -> None:
        super().setUp()
        self.lab = make_switch_lab()
        registry.lab_service.lab = self.lab
        self.addCleanup(setattr, registry.lab_service, "lab", None)
        self.open_session(role="learner")

    def test_get_switch_vlans_returns_structured_port_configs(self) -> None:
        response = self.client.get("/api/labs/fake/switches/s1/vlans")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["switch"], "s1")
        ports = data["ports"]

        self.assertIn("s1-eth1", ports)
        self.assertEqual(ports["s1-eth1"]["mode"], "access")
        self.assertEqual(ports["s1-eth1"]["tag"], 10)

        self.assertIn("s1-eth2", ports)
        self.assertEqual(ports["s1-eth2"]["mode"], "trunk")
        self.assertEqual(ports["s1-eth2"]["trunks"], [10, 20])
        self.assertEqual(ports["s1-eth2"]["native"], 1)

        self.assertIn("s1-eth3", ports)
        self.assertEqual(ports["s1-eth3"]["mode"], "none")

    def test_get_switch_vlans_requires_authentication(self) -> None:
        self.client.cookies.clear()
        response = self.client.get("/api/labs/fake/switches/s1/vlans")
        self.assertEqual(response.status_code, 401)

    def test_get_switch_vlans_switch_not_found(self) -> None:
        response = self.client.get("/api/labs/fake/switches/unknown_switch/vlans")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_get_switch_vlans_no_lab_running(self) -> None:
        registry.lab_service.lab = None
        response = self.client.get("/api/labs/fake/switches/s1/vlans")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no lab running", response.json()["detail"].lower())

    def test_post_set_access_port_vlan(self) -> None:
        with patch.object(registry, "broadcast_topology_update", new_callable=AsyncMock) as mock_broadcast:
            response = self.client.post(
                "/api/labs/fake/switches/s1/ports/s1-eth1/vlan",
                json={"mode": "access", "vlan": 50},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["switch"], "s1")
            self.assertEqual(data["port"], "s1-eth1")
            self.assertEqual(data["config"]["mode"], "access")
            self.assertEqual(data["config"]["tag"], 50)
            mock_broadcast.assert_awaited_once()

        # Verify change on underlying switch
        sw1 = self.lab.switches[0]
        self.assertEqual(sw1.get_vlans()["s1-eth1"]["tag"], 50)

    def test_post_set_trunk_port_vlan(self) -> None:
        with patch.object(registry, "broadcast_topology_update", new_callable=AsyncMock) as mock_broadcast:
            response = self.client.post(
                "/api/labs/fake/switches/s1/ports/s1-eth2/vlan",
                json={"mode": "trunk", "trunks": [100, 200], "native": 10},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["config"]["mode"], "trunk")
            self.assertEqual(data["config"]["trunks"], [100, 200])
            self.assertEqual(data["config"]["native"], 10)
            mock_broadcast.assert_awaited_once()

    def test_post_clear_port_vlan(self) -> None:
        with patch.object(registry, "broadcast_topology_update", new_callable=AsyncMock) as mock_broadcast:
            response = self.client.post(
                "/api/labs/fake/switches/s1/ports/s1-eth1/vlan",
                json={"mode": "none"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["config"]["mode"], "none")
            mock_broadcast.assert_awaited_once()

    def test_post_validation_bad_mode(self) -> None:
        response = self.client.post(
            "/api/labs/fake/switches/s1/ports/s1-eth1/vlan",
            json={"mode": "invalid_mode", "vlan": 10},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid vlan mode", response.json()["detail"].lower())

    def test_post_validation_vlan_out_of_range(self) -> None:
        response = self.client.post(
            "/api/labs/fake/switches/s1/ports/s1-eth1/vlan",
            json={"mode": "access", "vlan": 5000},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("out of valid range", response.json()["detail"])

    def test_post_validation_missing_vlan_for_access(self) -> None:
        response = self.client.post(
            "/api/labs/fake/switches/s1/ports/s1-eth1/vlan",
            json={"mode": "access"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("vlan id is required", response.json()["detail"].lower())

    def test_post_validation_missing_trunks_for_trunk(self) -> None:
        response = self.client.post(
            "/api/labs/fake/switches/s1/ports/s1-eth2/vlan",
            json={"mode": "trunk"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("trunk vlan ids are required", response.json()["detail"].lower())

    def test_post_unknown_port_returns_404(self) -> None:
        response = self.client.post(
            "/api/labs/fake/switches/s1/ports/s1-eth99/vlan",
            json={"mode": "access", "vlan": 10},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_post_unknown_switch_returns_404(self) -> None:
        response = self.client.post(
            "/api/labs/fake/switches/unknown_switch/ports/s1-eth1/vlan",
            json={"mode": "access", "vlan": 10},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_topology_endpoint_contains_vlan_metadata(self) -> None:
        response = self.client.get("/api/labs/fake/topology")
        self.assertEqual(response.status_code, 200)
        links = response.json()["links"]
        self.assertEqual(len(links), 3)

        # Link 1: s1-eth1 (access 10) <-> h1-eth0
        link1 = next(l for l in links if l["source_intf"] == "s1-eth1" or l["target_intf"] == "s1-eth1")
        self.assertIsNotNone(link1["vlan"])
        self.assertEqual(link1["vlan"]["mode"], "access")
        self.assertEqual(link1["vlan"]["tag"], 10)
        self.assertIsNotNone(link1["source_vlan"])
        self.assertEqual(link1["source_vlan"]["mode"], "access")

        # Link 2: s1-eth2 (trunk [10, 20]) <-> r1-eth1
        link2 = next(l for l in links if l["source_intf"] == "s1-eth2" or l["target_intf"] == "s1-eth2")
        self.assertIsNotNone(link2["vlan"])
        self.assertEqual(link2["vlan"]["mode"], "trunk")
        self.assertEqual(link2["vlan"]["trunks"], [10, 20])


    def test_mac_table_returns_learned_addresses_ports_vlans_and_age(self) -> None:
        sw = self.lab.switches[0]
        sw.intfs[1] = FakeIntf("s1-eth1", sw)
        sw.recorder.respond_to("fdb/show s1", "port VLAN MAC Age\n1 10 00:00:00:00:00:01 7\n")
        response = self.client.get("/api/labs/fake/switches/s1/mac-table")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"switch": "s1", "entries": [
            {"mac": "00:00:00:00:00:01", "port": "s1-eth1", "vlan": 10, "age": 7},
        ]})


    def test_flush_empties_the_mac_table_and_new_traffic_relearns(self) -> None:
        sw = self.lab.switches[0]
        sw.intfs[1] = FakeIntf("s1-eth1", sw)
        learned = ["1 10 00:00:00:00:00:01 7"]

        def ovs(command: str) -> str:
            if command.startswith("ovs-appctl fdb/flush "):
                learned.clear()
                return "table successfully flushed\n"
            if command.startswith("ovs-appctl fdb/show "):
                return "port VLAN MAC Age\n" + "\n".join(learned)
            return ""

        with patch.object(sw, "cmd", side_effect=ovs):
            flushed = self.client.post("/api/labs/fake/switches/s1/mac-table/flush")
            self.assertEqual(flushed.status_code, 200, flushed.text)
            self.assertEqual(flushed.json(), {"switch": "s1", "entries": []})
            learned.append("1 10 00:00:00:00:00:01 0")
            refreshed = self.client.get("/api/labs/fake/switches/s1/mac-table")
            self.assertEqual(refreshed.json()["entries"], [
                {"mac": "00:00:00:00:00:01", "port": "s1-eth1", "vlan": 10, "age": 0},
            ])


    def test_spanning_tree_reports_protocol_root_and_port_roles_or_off(self) -> None:
        sw = self.lab.switches[0]
        for protocol in ("rstp", "stp", None):
            with self.subTest(protocol=protocol):
                bridge_id = "1.000.000000000001" if protocol == "rstp" else "1000.000000000001"
                bridge_status = ({"rstp_bridge_id": bridge_id, "rstp_root_id": bridge_id}
                                 if protocol == "rstp" else
                                 {"stp_bridge_id": bridge_id, "stp_designated_root": bridge_id}
                                 if protocol else {})
                port_status = ({"rstp_port_role": "Alternate", "rstp_port_state": "Discarding"}
                               if protocol == "rstp" else
                               {"stp_role": "alternate", "stp_state": "blocking"})

                def ovs(command: str) -> str:
                    if "list Bridge" in command:
                        return json.dumps({
                            "headings": ["stp_enable", "rstp_enable", "status", "rstp_status"],
                            "data": [[protocol == "stp", protocol == "rstp",
                                      ["map", list(bridge_status.items())] if protocol == "stp" else ["map", []],
                                      ["map", list(bridge_status.items())] if protocol == "rstp" else ["map", []]]],
                        })
                    if "list-ports" in command:
                        return "s1-eth1\n"
                    return json.dumps({"headings": ["name", "status", "rstp_status"],
                                       "data": [["s1-eth1",
                                                 ["map", list(port_status.items())] if protocol == "stp" else ["map", []],
                                                 ["map", list(port_status.items())] if protocol == "rstp" else ["map", []]]]})

                with patch.object(sw, "cmd", side_effect=ovs):
                    response = self.client.get("/api/labs/fake/switches/s1/spanning-tree")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {
                    "switch": "s1", "protocol": protocol,
                    "bridge_id": bridge_id if protocol else None,
                    "root_bridge": bridge_id if protocol else None,
                    "priority": 4096 if protocol else None,
                    "mac": "00:00:00:00:00:01" if protocol else None,
                    "root_priority": 4096 if protocol else None,
                    "root_mac": "00:00:00:00:00:01" if protocol else None,
                    "root_name": None, "is_root": bool(protocol),
                    "ports": [{"port": "s1-eth1", "role": "Alternate", "state": "Discarding"}]
                             if protocol else [],
                })


    def test_switch_reads_and_flush_require_a_session_and_a_running_switch(self) -> None:
        operations = (("get", "mac-table"), ("post", "mac-table/flush"),
                      ("get", "spanning-tree"))
        for method, path in operations:
            with self.subTest(path=path):
                missing = getattr(self.client, method)(f"/api/labs/fake/switches/missing/{path}")
                self.assertEqual(missing.status_code, 404, missing.text)
        self.client.cookies.clear()
        for method, path in operations:
            with self.subTest(path=path, state="signed out"):
                response = getattr(self.client, method)(f"/api/labs/fake/switches/s1/{path}")
                self.assertEqual(response.status_code, 401, response.text)

    def test_switch_endpoints_registered_on_lab_router_before_mounting(self) -> None:
        """Switch endpoints must be registered on lab_router before it mounts onto router."""
        source = inspect.getsource(api_mod)
        tree = ast.parse(source)

        include_lineno: int | None = None
        endpoint_linenos: dict[str, int] = {}

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "router"
                and len(node.args) >= 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "lab_router"
            ):
                include_lineno = node.lineno
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in {
                    "get_switch_mac_table",
                    "flush_switch_mac_table",
                    "get_switch_spanning_tree",
                }:
                    endpoint_linenos[node.name] = node.lineno

        self.assertIsNotNone(
            include_lineno,
            "router.include_router(lab_router) call not found in api.py",
        )
        assert include_lineno is not None
        self.assertEqual(
            set(endpoint_linenos.keys()),
            {"get_switch_mac_table", "flush_switch_mac_table", "get_switch_spanning_tree"},
        )
        for name, lineno in endpoint_linenos.items():
            self.assertLess(
                lineno,
                include_lineno,
                f"{name} (line {lineno}) must be declared before router.include_router(lab_router) (line {include_lineno})",
            )


@pytest.mark.unit
class SwitchHttpErrorTests(unittest.TestCase):
    """The one mapping every Switch route uses for the registry's ValueError."""

    def test_not_found_is_404_and_anything_else_is_400(self) -> None:
        from mniplab.webserver.api import _switch_http_error

        missing = _switch_http_error(ValueError("Switch 'x' Not Found"))
        self.assertEqual((missing.status_code, missing.detail), (404, "Switch 'x' Not Found"))
        for message in ("No lab running", "Invalid VLAN mode 'q'"):
            self.assertEqual(_switch_http_error(ValueError(message)).status_code, 400)
