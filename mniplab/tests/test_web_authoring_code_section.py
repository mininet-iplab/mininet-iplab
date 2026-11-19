"""A Canvas draft's Code section through the authoring HTTP API."""

import os
from unittest.mock import patch

import pytest

from mniplab.tests.authoring_fixtures import (
    AuthoringAPIFixture, PackageRecordingLauncher, SourceRecordingLauncher,
)
from mniplab.webserver import authoring_record

CODE = (
    "r1.add_frr_config('router ospf\\n network 10.9.0.0/24 area 0')\n"
    "for index in range(2):\n"
    "    net.addHost(f'extra{index}')\n"
)
INDENTED = (
    "    r1.add_frr_config('router ospf\\n network 10.9.0.0/24 area 0')\n"
    "    for index in range(2):\n"
    "        net.addHost(f'extra{index}')\n"
)


@pytest.mark.unit
class CodeSectionAPITests(AuthoringAPIFixture):
    """The Code section is stored as its own field and kept through regeneration."""

    async def _save(self, client, draft):
        saved = await client.put(f"/api/authoring/drafts/{draft['id']}",
                                 json={"revision": draft["revision"], "document": draft})
        return saved

    async def _source(self, client, draft):
        shown = await client.get(f"/api/authoring/drafts/{draft['id']}/python")
        self.assertEqual(shown.status_code, 200, shown.text)
        return shown.json()

    def test_the_code_section_runs_verbatim_at_the_end_of_the_build_step(self) -> None:
        launcher = SourceRecordingLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            draft = await self._router_draft(client)
            draft["code_section"] = CODE
            saved = await self._save(client, draft)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["code_section"], CODE)
            shown = await self._source(client, saved.json())
            self.assertEqual(shown["filename"], "routed-lesson.py")
            source = shown["source"]
            self.assertIn(
                "    net.addStartupCommand(r1, 'ip addr add 10.9.9.9/32 dev lo', 'before-daemons')\n"
                "\n"
                "    # Code section\n"
                "    r1.add_frr_config('router ospf\\n network 10.9.0.0/24 area 0')\n"
                "    for index in range(2):\n"
                "        net.addHost(f'extra{index}')\n"
                "\n"
                "    return net\n", source)
            started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": saved.json()["revision"]})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(launcher.sources, [source])

        self._as("instructor", exercise)

    def test_canvas_edits_regenerate_the_script_and_leave_the_code_section_untouched(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            draft["code_section"] = CODE
            draft = (await self._save(client, draft)).json()
            draft["topology"]["nodes"][0]["name"] = "core1"
            draft["topology"]["nodes"].append(
                {"id": "h2", "name": "h2", "type": "host", "ipv4_gateway": "",
                 "ipv6_gateway": "", "startup": []})
            draft = (await self._save(client, draft)).json()
            self.assertEqual(draft["code_section"], CODE)
            shown = await self._source(client, draft)
            self.assertIn("core1 = net.addRouter('core1'", shown["source"])
            self.assertIn("h2 = net.addHost('h2')", shown["source"])
            # The stale reference stays as written; the Preview reports it.
            self.assertIn("    r1.add_frr_config(", shown["source"])
            self.assertEqual(
                shown["source"],
                shown["before"] + "    # Code section\n" + INDENTED + "\n" + shown["after"])
            self.assertTrue(shown["after"].startswith("    return net\n"))

        self._as("instructor", exercise)

    def test_a_draft_without_a_code_section_keeps_its_script_and_preview(self) -> None:
        launcher = SourceRecordingLauncher()
        self._launch_with("multi", launcher)

        async def exercise(client):
            draft = await self._router_draft(client)
            self.assertNotIn("code_section", draft)
            before = (await self._source(client, draft))["source"]
            self.assertNotIn("# Code section", before)
            started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(started.status_code, 200, started.text)
            await client.post(f"/api/labs/{started.json()['key']}/stop")
            for blank in ("", "  \n\t\n"):
                draft["code_section"] = blank
                draft = (await self._save(client, draft)).json()
                self.assertEqual((await self._source(client, draft))["source"], before)
                status = await client.get(f"/api/authoring/drafts/{draft['id']}/preview-status")
                self.assertTrue(status.json()["current"], f"{blank!r} changes nothing")
            draft["code_section"] = "net.addHost('h9')\n"
            draft = (await self._save(client, draft)).json()
            status = await client.get(f"/api/authoring/drafts/{draft['id']}/preview-status")
            self.assertFalse(status.json()["current"], "a Code section changes the Lab")

        self._as("instructor", exercise)

    def test_a_code_section_must_be_text(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            for value in (None, 7, ["net.addHost('h9')"], "x" * 100_001):
                draft["code_section"] = value
                refused = await self._save(client, draft)
                self.assertEqual(refused.status_code, 422, value)
                self.assertIn("Code section", refused.json()["detail"])

        self._as("instructor", exercise)

    def test_copy_revise_and_publish_keep_the_code_section(self) -> None:
        self._launch_with("multi", SourceRecordingLauncher())

        async def exercise(client):
            draft = await self._router_draft(client)
            draft["code_section"] = CODE
            draft = (await self._save(client, draft)).json()
            copied = await client.post("/api/authoring/drafts", json={"source_id": draft["id"]})
            self.assertEqual(copied.json()["code_section"], CODE)
            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            await client.post(f"/api/labs/{preview.json()['key']}/stop")
            published = await client.post(f"/api/authoring/drafts/{draft['id']}/publish",
                                          json={"revision": draft["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            example_id = published.json()["example"]["id"]
            for revise in (False, True):
                started = await client.post("/api/authoring/drafts",
                                            json={"example_id": example_id, "revise": revise})
                self.assertEqual(started.status_code, 201, started.text)
                self.assertEqual(started.json()["code_section"], CODE)

        self._as("instructor", exercise)


def _cmdline(pid: str) -> bytes:
    """A process's command line, or nothing once it has gone."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as cmdline:
            return cmdline.read()
    except OSError:
        return b""


LOOPED = (
    "from mininet.node import Host\n"
    "from mniplab import mnIPLab\n"
    "from mniplab.router import FRRouter\n\n\n"
    "class Probe(Host):\n"
    "    pass\n\n\n"
    "def build_network():\n"
    "    net = mnIPLab(topo=None, controller=None)\n"
    "    s1 = net.addSwitch('s1')\n"
    "    for index in range(1, 4):\n"
    "        router = net.addRouter(f'r{index}', proto='OSPF')\n"
    "        net.addLink(router, s1)\n"
    "    net.addNode('core', cls=FRRouter)\n"
    "    net.addHost('probe', cls=Probe)\n"
    "    net.addLink('core', net['r1'], port1=5)\n"
    "    net.addExaBGP('isp1', neighbors=[])\n"
    "    net.addStartupCommand(net.get('r1'), 'true')\n"
    "    for node in net.hosts:\n"
    "        node.cmd('true').strip()\n"
    "    return net\n"
)


@pytest.mark.unit
class DrawingAPITests(AuthoringAPIFixture):
    """A record-only run of the saved script draws its Nodes and Links."""

    async def _python_draft(self, client, python):
        uploaded = await client.post("/api/authoring/drafts/upload", files={
            "file": ("lab.py", python.encode("utf-8"), "text/x-python")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        return uploaded.json()

    async def _drawing(self, client, draft):
        drawn = await client.get(f"/api/authoring/drafts/{draft['id']}/drawing")
        self.assertEqual(drawn.status_code, 200, drawn.text)
        return drawn.json()

    def test_a_python_draft_is_drawn_with_its_loops_and_node_kinds(self) -> None:
        async def exercise(client):
            draft = await self._python_draft(client, LOOPED)
            drawing = await self._drawing(client, draft)
            self.assertEqual(drawing["source"], "script")
            self.assertIsNone(drawing["note"])
            self.assertEqual(
                [(node["name"], node["kind"], node["line"]) for node in drawing["nodes"]],
                [("s1", "switch", 12), ("r1", "router", 14), ("r2", "router", 14),
                 ("r3", "router", 14), ("core", "router", 16), ("probe", "other", 17),
                 ("isp1", "speaker", 19)])
            self.assertEqual(
                [[(end["node"], end["port"]) for end in link["ends"]] for link in drawing["links"]],
                [[("r1", 0), ("s1", 1)], [("r2", 0), ("s1", 2)], [("r3", 0), ("s1", 3)],
                 [("core", 5), ("r1", 1)]])

        self._as("instructor", exercise)

    def test_a_canvas_draft_draws_only_what_its_code_section_adds(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            empty = await self._drawing(client, draft)
            self.assertEqual((empty["nodes"], empty["links"], empty["note"]), ([], [], None))
            draft["code_section"] = (
                "r1.cmd('true')\n"
                "for name in ('r8', 'r9'):\n"
                "    net.addLink(r1, net.addRouter(name))\n"
                "net.addLink(r1, h1)\n"
            )
            draft = (await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})).json()
            drawing = await self._drawing(client, draft)
            self.assertEqual([(node["name"], node["kind"], node["line"])
                              for node in drawing["nodes"]],
                             [("r8", "router", 3), ("r9", "router", 3)])
            self.assertEqual(
                [([(end["node"], end["port"]) for end in link["ends"]], link["line"])
                 for link in drawing["links"]],
                [([("r1", 1), ("r8", 0)], 3), ([("r1", 2), ("r9", 0)], 3),
                 ([("r1", 3), ("h1", 1)], 4)])

        self._as("instructor", exercise)

    def test_a_script_that_cannot_be_recorded_says_why(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            draft["code_section"] = "r1.cmd('true')\nr7.cmd('true')\n"
            draft = (await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})).json()
            drawing = await self._drawing(client, draft)
            self.assertEqual((drawing["source"], drawing["nodes"], drawing["links"]),
                             ("none", [], []))
            self.assertEqual(drawing["note"], (
                "Could not draw the script: name 'r7' is not defined "
                "(Code section line 2: r7.cmd('true')). Start a Preview to draw it."))

            python = await self._python_draft(client, LOOPED.replace(
                "    return net\n", "    while True:\n        pass\n"))
            with patch.object(authoring_record, "RECORD_TIMEOUT", 1.0):
                drawing = await self._drawing(client, python)
            self.assertEqual(drawing["note"], (
                "Could not draw the script: it did not finish within 1 second. "
                "Start a Preview to draw it."))

        self._as("instructor", exercise)

    def test_a_blank_or_broken_code_section_is_drawn_as_it_runs(self) -> None:
        async def exercise(client):
            draft = await self._router_draft(client)
            draft["code_section"] = "  \n\n"
            draft = (await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})).json()
            blank = await self._drawing(client, draft)
            self.assertEqual((blank["nodes"], blank["links"], blank["note"]), ([], [], None))
            draft["code_section"] = "r1.cmd('true')\nfor name in\n"
            draft = (await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})).json()
            note = (await self._drawing(client, draft))["note"]
            self.assertRegex(note, r"^Could not draw the script: .+ \(Code section line 2\)\. "
                                   r"Start a Preview to draw it\.$")

        self._as("instructor", exercise)

    def test_a_script_that_runs_too_long_is_stopped_with_what_it_started(self) -> None:
        async def exercise(client):
            python = await self._python_draft(client, LOOPED.replace(
                "    return net\n",
                "    import subprocess\n    subprocess.Popen(['sleep', '4321'])\n"
                "    while True:\n        pass\n"))
            with patch.object(authoring_record, "RECORD_TIMEOUT", 1.0):
                drawing = await self._drawing(client, python)
            self.assertIn("did not finish within 1 second", drawing["note"])
            left = [pid for pid in os.listdir("/proc") if pid.isdigit()
                    and _cmdline(pid) == b"sleep\x004321\x00"]
            self.assertEqual(left, [])

        self._as("instructor", exercise)

    def test_an_unrecordable_script_falls_back_to_the_running_previews_topology(self) -> None:
        self._launch_with("multi", PackageRecordingLauncher())

        async def exercise(client):
            draft = await self._python_draft(client, LOOPED.replace(
                "    s1 = net.addSwitch('s1')\n",
                "    s1 = net.addSwitch('s1')\n    open('/nonexistent/topology.txt')\n"))
            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            drawing = await self._drawing(client, draft)
            self.assertEqual(drawing["source"], "preview")
            self.assertIn("No such file or directory", drawing["note"])
            self.assertTrue(drawing["note"].endswith("Showing the running Preview's Topology."))
            running = (await client.get(f"/api/labs/{preview.json()['key']}/topology")).json()
            self.assertEqual({node["name"] for node in drawing["nodes"]},
                             {node["id"] for node in running["nodes"]})

        self._as("instructor", exercise)


@pytest.mark.unit
class CodeSectionLayoutAPITests(AuthoringAPIFixture):
    """Nodes a Code section adds are arranged in the Layout by their names."""

    def test_code_section_nodes_keep_positions_that_reach_the_running_view(self) -> None:
        self._launch_with("multi", SourceRecordingLauncher())

        async def exercise(client):
            draft = await self._router_draft(client)
            draft["code_section"] = "net.addRouter('r9')\n"
            for refused in ("code:9bad", "code:", "r9"):
                bad = {**draft, "layout": {"version": 1, "nodes": {
                    refused: {"x": 10, "y": 20}}}}
                answer = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                    "revision": draft["revision"], "document": bad})
                self.assertEqual(answer.status_code, 422, refused)
            draft["layout"]["nodes"]["code:r9"] = {"x": 700, "y": 400}
            saved = await client.put(f"/api/authoring/drafts/{draft['id']}", json={
                "revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()

            preview = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                        json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            layout = await client.get("/api/load_layout",
                                      params={"example": f"preview-{draft['id']}"})
            self.assertEqual(layout.status_code, 200, layout.text)
            self.assertEqual(layout.json()["layout"]["nodes"]["r9"], {"x": 700, "y": 400})

        self._as("instructor", exercise)
