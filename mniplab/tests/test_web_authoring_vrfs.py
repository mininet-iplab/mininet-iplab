"""VRFs on Routers and their Interfaces through the authenticated authoring API."""

import ast
import copy

import pytest
import httpx2

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, host_node, router_node


def end(node_id, interface, port, ipv4="", **fields):
    """One Link end in the definition shape."""
    return {"node_id": node_id, "interface_id": interface, "port": port,
            "ipv4": ipv4, "ipv6": "", **fields}


def vrf_topology():
    """r1 holds vrf-ext; its Interfaces toward r2 and r3 are in it, h1's is not."""
    r1 = router_node("r1", "r1", protocols=("BGP",), fast_timers=False)
    r1["vrfs"] = [{"name": "vrf-ext", "table": 10}, {"name": "blue", "table": 20}]
    return {
        "nodes": [r1, router_node("r2", "r2", protocols=("BGP",), fast_timers=False),
                  router_node("r3", "r3", protocols=("BGP",), fast_timers=False),
                  host_node("h1", "h1")],
        "links": [
            {"id": "r1-r2", "ends": [end("r1", "if-000000000001", 0, "10.0.1.1/30", vrf="vrf-ext"),
                                     end("r2", "if-000000000002", 0, "10.0.1.2/30")]},
            {"id": "r3-r1", "ends": [end("r3", "if-000000000003", 0, "10.0.2.2/30"),
                                     end("r1", "if-000000000004", 1, "10.0.2.1/30", vrf="vrf-ext")]},
            {"id": "h1-r1", "ends": [end("h1", "if-000000000005", 0, "192.168.1.2/24"),
                                     end("r1", "if-000000000006", 2, "192.168.1.1/24")]},
        ],
    }


def calls(source, method):
    """Each ``net.<method>(...)`` call in a script, as its keyword values."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == method):
            found.append({
                "args": [ast.literal_eval(arg) for arg in node.args
                         if isinstance(arg, ast.Constant)],
                **{keyword.arg: ast.unparse(keyword.value) for keyword in node.keywords},
            })
    return found


@pytest.mark.unit
class VRFAuthoringAPITests(AuthoringAPIFixture):
    """An Instructor declares a Router's VRFs and places its Interfaces in them."""

    async def _save(self, client, draft, topology):
        draft = copy.deepcopy(draft)
        draft["topology"] = topology
        return await client.put(f"/api/authoring/drafts/{draft['id']}",
                                json={"revision": draft["revision"], "document": draft})

    def test_generated_script_declares_router_vrfs_and_each_interfaces_vrf(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            saved = await self._save(client, draft, vrf_topology())
            self.assertEqual(saved.status_code, 200, saved.text)
            source = (await client.get(
                f"/api/authoring/drafts/{draft['id']}/python")).json()["source"]

            routers = {call["args"][0]: call for call in calls(source, "addRouter")}
            self.assertEqual(ast.literal_eval(routers["r1"]["vrfs"]),
                             {"vrf-ext": 10, "blue": 20})
            self.assertNotIn("vrfs", routers["r2"])
            links = calls(source, "addLink")
            self.assertEqual(ast.literal_eval(links[0]["params1"]),
                             {"ip": "10.0.1.1/30", "vrf": "vrf-ext"})
            self.assertEqual(ast.literal_eval(links[1]["params2"]),
                             {"ip": "10.0.2.1/30", "vrf": "vrf-ext"})
            self.assertEqual(ast.literal_eval(links[2]["params2"]), {"ip": "192.168.1.1/24"})

        self._as("instructor", exercise)

    def test_invalid_or_duplicate_vrfs_are_refused(self) -> None:
        cases = {
            "not a list": "vrf-ext",
            "extra field": [{"name": "vrf-ext", "table": 10, "vni": 10}],
            "blank name": [{"name": "", "table": 10}],
            "name too long for Linux": [{"name": "v" * 16, "table": 10}],
            "name with a space": [{"name": "vrf ext", "table": 10}],
            "FRR's default VRF": [{"name": "default", "table": 10}],
            "the loopback's name": [{"name": "lo", "table": 10}],
            "an Interface's name": [{"name": "r1-eth0", "table": 10}],
            "table zero": [{"name": "vrf-ext", "table": 0}],
            "local table": [{"name": "vrf-ext", "table": 255}],
            "main table": [{"name": "vrf-ext", "table": 254}],
            "default table": [{"name": "vrf-ext", "table": 253}],
            "table beyond 32 bits": [{"name": "vrf-ext", "table": 2 ** 32}],
            "table as text": [{"name": "vrf-ext", "table": "10"}],
            "table as boolean": [{"name": "vrf-ext", "table": True}],
            "duplicate name": [{"name": "vrf-ext", "table": 10}, {"name": "vrf-ext", "table": 20}],
            "duplicate table": [{"name": "vrf-ext", "table": 10}, {"name": "blue", "table": 10}],
        }

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            for label, vrfs in cases.items():
                with self.subTest(label):
                    topology = vrf_topology()
                    for link in topology["links"]:
                        for item in link["ends"]:
                            item.pop("vrf", None)
                    topology["nodes"][0]["vrfs"] = vrfs
                    refused = await self._save(client, draft, topology)
                    self.assertEqual(refused.status_code, 422, refused.text)
                    self.assertIn("r1", refused.json()["detail"])

        self._as("instructor", exercise)

    def test_removing_a_vrf_in_use_is_refused_naming_its_interfaces(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            topology = vrf_topology()
            topology["nodes"][0]["vrfs"] = [{"name": "blue", "table": 20}]
            refused = await self._save(client, draft, topology)
            self.assertEqual(refused.status_code, 422, refused.text)
            detail = refused.json()["detail"]
            for named in ("vrf-ext", "r1-eth0", "r1-eth1"):
                self.assertIn(named, detail)
            self.assertNotIn("r1-eth2", detail)

        self._as("instructor", exercise)

    def test_only_a_router_interface_names_a_vrf_and_only_by_name(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            for label, link_index, end_index, value in (
                ("Host Interface", 2, 0, "vrf-ext"),
                ("blank VRF", 2, 1, ""),
                ("VRF as a number", 2, 1, 10),
            ):
                with self.subTest(label):
                    topology = vrf_topology()
                    topology["links"][link_index]["ends"][end_index]["vrf"] = value
                    refused = await self._save(client, draft, topology)
                    self.assertEqual(refused.status_code, 422, refused.text)

        self._as("instructor", exercise)
