"""A Canvas draft's Code section in real Preview Labs."""

import os
import subprocess
from typing import Any, Dict, Tuple

import httpx2

from mniplab.tests.authoring_fixtures import (
    RealLabAuthoringFixture, host_node, root_interfaces, router_node,
)

LOOPED = (
    "from mniplab import mnIPLab\n\n\n"
    "def build_network():\n"
    "    net = mnIPLab(topo=None, controller=None)\n"
    "    s1 = net.addSwitch('s1')\n"
    "    for index in range(1, 4):\n"
    "        router = net.addRouter(f'r{index}')\n"
    "        net.addLink(router, s1, params1={'ip': f'10.0.0.{index}/24'})\n"
    "    net.addHost('h1')\n"
    "    net.addLink('h1', 's1')\n"
    "    return net\n"
)


def host_network() -> Tuple[Any, ...]:
    """What a network on this host leaves behind: interfaces, namespaces, Node shells."""
    namespaces = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True,
                                check=True).stdout
    shells = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as cmdline:
                arguments = cmdline.read().split(b"\0")
        except OSError:
            continue
        shells += [argument for argument in arguments if argument.startswith(b"mininet:")]
    return tuple(root_interfaces()), namespaces, tuple(sorted(shells))


def routed_pair() -> Dict[str, Any]:
    """Router r1 serving Host h1, as drawn on the canvas."""
    return {
        "nodes": [router_node("r", "r1", protocols=()),
                  host_node("h", "h1", ipv4_gateway="10.0.0.1")],
        "links": [{"id": "link-a", "ends": [
            {"node_id": "r", "interface_id": "if-000000000001", "port": 0,
             "ipv4": "10.0.0.1/24", "ipv6": ""},
            {"node_id": "h", "interface_id": "if-000000000002", "port": 0,
             "ipv4": "10.0.0.2/24", "ipv6": ""},
        ]}],
    }


class CodeSectionLabTests(RealLabAuthoringFixture):
    """What a Code section adds starts with the Lab; a stale name fails at its line."""

    async def _with_code(self, client: httpx2.AsyncClient, code: str) -> Dict[str, Any]:
        draft = await self._saved(client, routed_pair())
        draft["code_section"] = code
        saved = await client.put(f"/api/authoring/drafts/{draft['id']}",
                                 json={"revision": draft["revision"], "document": draft})
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    def test_a_router_and_link_the_code_section_adds_join_the_topology(self) -> None:
        self._real_labs("multi")

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._with_code(client, (
                "r2 = net.addRouter('r2')\n"
                "net.addLink(r1, r2, params1={'ip': '10.1.0.1/30'}, params2={'ip': '10.1.0.2/30'})\n"
            ))
            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            lab = f"/api/labs/{preview.json()['key']}"
            topology = (await client.get(f"{lab}/topology")).json()
            self.assertEqual({node["id"] for node in topology["nodes"]}, {"r1", "r2", "h1"})
            self.assertIn({"r1", "r2"}, [{link["source"], link["target"]}
                                         for link in topology["links"]])
            routes = await client.get(f"{lab}/routing/routes/r2")
            self.assertEqual(routes.status_code, 200, routes.text)
            self.assertIn("10.1.0.0/30", routes.text)

        self._run(exercise)


class RecordOnlyDrawingLabTests(RealLabAuthoringFixture):
    """Drawing a script records its Nodes and Links and builds nothing."""

    def test_drawing_a_script_creates_no_network_that_its_preview_does(self) -> None:
        self._real_labs("multi")

        async def exercise(client: httpx2.AsyncClient) -> None:
            uploaded = await client.post("/api/authoring/drafts/upload", files={
                "file": ("looped.py", LOOPED.encode("utf-8"), "text/x-python")})
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            draft = uploaded.json()

            before = host_network()
            drawing = await client.get(f"/api/authoring/drafts/{draft['id']}/drawing")
            self.assertEqual(drawing.status_code, 200, drawing.text)
            self.assertEqual([node["name"] for node in drawing.json()["nodes"]],
                             ["s1", "r1", "r2", "r3", "h1"])
            self.assertEqual(len(drawing.json()["links"]), 4)
            self.assertEqual(host_network(), before)

            # The same script started for real is seen by the same check.
            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            interfaces, _, shells = host_network()
            self.assertNotEqual(interfaces, before[0])
            self.assertGreater(len(shells), len(before[2]))

        self._run(exercise)
