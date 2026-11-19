"""The Lab Authoring canvas by keyboard and pointer: focus, Selection and linking."""

import re

from typing import Any

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase
from mniplab.tests.browser import Page, box_of, expect

pytestmark = pytest.mark.browser_authoring

NEIGHBOR: dict = {"local_as": 65001, "peer_as": 65000, "hold_time": None, "routes": []}


def zone(name: str, **fields: Any) -> dict:
    """A signed primary Zone with no Records of its own."""
    return {"kind": "primary", "name": name, "default_ttl": 300, "records": [], "dnssec": True, **fields}


class AuthoringCanvasBrowserTests(AuthoringPageCase):
    """An Instructor arranges, selects and links Nodes on the canvas."""

    @staticmethod
    def _in_sidebar(page: Page) -> bool:
        return page.evaluate("() => Boolean(document.activeElement.closest('#authoring-sidebar'))")

    def _tabs_from_palette_to_sidebar(self, page: Page) -> int:
        page.get_by_role("button", name="Add Host").focus()
        for presses in range(1, 60):
            page.keyboard.press("Tab")
            if self._in_sidebar(page):
                return presses
        raise AssertionError("Tab never reached the sidebar")

    def test_the_canvas_is_one_tab_stop_whatever_the_topology_size(self) -> None:
        """Two Nodes or twenty with Links: the same Tab presses reach the sidebar."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 300, 100)], [("h1", "h2")])
            small = self._tabs_from_palette_to_sidebar(page)

            grid = [(f"h{index + 1}", 100 + 150 * (index % 5), 100 + 200 * (index // 5)) for index in range(20)]
            self._seed(page, grid, [(f"h{index}", f"h{index + 1}") for index in range(1, 20)])
            large = self._tabs_from_palette_to_sidebar(page)
            self.assertEqual(small, large)

    def test_nodes_come_before_links_in_the_dom(self) -> None:
        """A screen reader's browse order meets every Node before any Link."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 300, 100), ("h3", 500, 100)],
                       [("h1", "h2"), ("h2", "h3")])
            kinds = page.evaluate("""() => [...document.querySelectorAll(
                '#draft-canvas [data-node-id], #draft-canvas [data-link-id]')]
                .map(element => element.hasAttribute('data-node-id') ? 'node' : 'link')""")
            self.assertEqual(kinds, ["node"] * 3 + ["link"] * 2)

    # A centre Router and Hosts placed so each arrow rule decides one move.
    COMPASS = [("r1", 500, 500), ("a1", 700, 520), ("b1", 650, 700), ("c1", 900, 500),
               ("u2", 550, 300), ("u1", 450, 300), ("d1", 300, 800)]

    def test_arrow_keys_move_focus_to_the_nearest_node_in_that_direction(self) -> None:
        """Nearest in the arrow's cone, ties by reading order, the half-plane when the cone is empty."""
        with self.signed_in_page() as page:
            self._seed(page, self.COMPASS)
            self._node(page, "r1").focus()
            # a1 is nearer than c1 straight ahead; b1 lies outside the cone.
            page.keyboard.press("ArrowRight")
            expect(self._node(page, "a1")).to_be_focused()
            page.keyboard.press("ArrowLeft")
            expect(self._node(page, "r1")).to_be_focused()
            # u1 and u2 are mirror images: reading order picks u1, though u2 was placed first.
            page.keyboard.press("ArrowUp")
            expect(self._node(page, "u1")).to_be_focused()
            expect(self._node(page, "u1")).to_have_class("node selected")
            # Nothing lies in c1's downward cone, so the nearest Node below it is taken.
            self._node(page, "c1").focus()
            page.keyboard.press("ArrowDown")
            expect(self._node(page, "a1")).to_be_focused()
            # No Node to the right of c1: focus stays and the status line says so.
            self._node(page, "c1").focus()
            page.keyboard.press("ArrowRight")
            expect(self._node(page, "c1")).to_be_focused()
            expect(page.locator("#status-message")).to_have_text("No Node to the right of c1.")

            # The canvas's one Tab stop is where focus last was.
            page.keyboard.press("Tab")
            page.keyboard.press("Shift+Tab")
            expect(self._node(page, "c1")).to_be_focused()
            self.assertEqual(page.locator("#draft-canvas [tabindex='0']").count(), 1)

    def test_home_end_and_page_keys_reach_every_node_in_reading_order(self) -> None:
        """A linear walk, top to bottom then left to right, that no layout can strand."""
        with self.signed_in_page() as page:
            self._seed(page, self.COMPASS)
            self._node(page, "r1").focus()
            page.keyboard.press("Home")
            walked = []
            for _ in range(len(self.COMPASS)):
                walked.append(page.evaluate(
                    "() => document.activeElement.querySelector('.node-label').textContent"))
                page.keyboard.press("PageDown")
            self.assertEqual(walked, ["u1", "u2", "r1", "c1", "a1", "b1", "d1"])
            expect(page.locator("#status-message")).to_have_text("No Node after d1.")
            page.keyboard.press("PageUp")
            expect(self._node(page, "b1")).to_be_focused()
            page.keyboard.press("End")
            expect(self._node(page, "d1")).to_be_focused()

    def _saved_positions(self, page: Page) -> dict:
        """Each Node's saved position by name, read back once autosave settles."""
        expect(page.locator("#status-draft-state")).to_have_text("Saved")
        return page.evaluate("""async () => {
            const id = document.querySelector('#draft-id').value;
            const draft = await (await fetch(`/api/authoring/drafts/${id}`)).json();
            return Object.fromEntries(draft.topology.nodes.map(node => [node.name, draft.layout.nodes[node.id]]));
        }""")

    def test_shift_arrow_moves_the_focused_node_and_the_move_saves(self) -> None:
        """Shift+Arrow moves 10 units and Ctrl+Shift+Arrow 50; each move is announced."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 500, 500), ("h2", 1995, 20)])
            h1 = self._node(page, "h1")
            h1.focus()
            page.keyboard.press("Shift+ArrowRight")
            expect(page.locator("#status-message")).to_have_text("Moved Host h1 to x 510, y 500.")
            page.keyboard.press("Control+Shift+ArrowDown")
            expect(page.locator("#status-message")).to_have_text("Moved Host h1 to x 510, y 550.")
            expect(h1).to_be_focused()
            expect(h1).to_have_attribute("aria-label", "Host h1 at x 510, y 550")
            self.assertEqual(self._saved_positions(page)["h1"], {"x": 510, "y": 550})

            # Movement is free, as in the running Lab's Topology.
            h2 = self._node(page, "h2")
            h2.focus()
            page.keyboard.press("Control+Shift+ArrowRight")
            expect(page.locator("#status-message")).to_have_text("Moved Host h2 to x 2045, y 20.")
            page.keyboard.press("Shift+ArrowRight")
            expect(page.locator("#status-message")).to_have_text(
                "Moved Host h2 to x 2055, y 20.")
            page.keyboard.press("Control+Shift+ArrowUp")
            self.assertEqual(self._saved_positions(page)["h2"], {"x": 2055, "y": -30})

    def test_delete_removes_the_focused_link_or_node(self) -> None:
        """Delete removes a focused Link, then a focused Node with its Links."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 300, 100), ("h3", 500, 100)],
                       [("h1", "h2"), ("h2", "h3")])
            page.locator("#draft-canvas .link").first.focus()
            page.keyboard.press("Delete")
            expect(page.locator("#status-message")).to_have_text("Removed Link between h1 and h2.")
            expect(self._node(page, "h1")).to_be_focused()

            # A click on a Link's line selects it too.
            line = page.locator("#draft-canvas .link-line")
            box = line.bounding_box()
            assert box is not None
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            expect(page.locator("#draft-canvas .link")).to_be_focused()
            expect(page.get_by_role("region", name="Selection").get_by_role("heading")).to_have_text(
                "Link between h2 and h3")

            self._node(page, "h2").focus()
            page.keyboard.press("Backspace")
            expect(page.locator("#status-message")).to_have_text("Removed Host h2 and its Links.")
            expect(page.locator("#draft-canvas .link")).to_have_count(0)
            self.assertEqual(sorted(self._saved_positions(page)), ["h1", "h3"])

    # A Router with a DHCP Service, one addressed Link and one without addresses.
    ROUTED: list = [("r1", 400, 300, "router", {"protocols": ["OSPF"], "dhcp": {"pools": []}}),
              ("h1", 200, 300), ("s1", 600, 300, "switch", {})]
    ROUTED_LINKS: list = [("r1", "h1", {"ipv4": "10.0.0.1/24", "ipv6": "2001:db8::1/64"}, {"ipv4": "10.0.0.2/24"}),
                    ("r1", "s1")]

    def test_focus_selects_a_node_and_selection_summarises_it(self) -> None:
        """Kind, name, Interfaces with addresses, Services, and the actions; no floating Remove."""
        with self.signed_in_page() as page:
            self._seed(page, self.ROUTED, self.ROUTED_LINKS)
            selection = page.get_by_role("region", name="Selection")
            expect(selection).to_be_hidden()
            self._node(page, "h1").focus()
            page.keyboard.press("ArrowRight")
            expect(self._node(page, "r1")).to_have_class("node selected")
            expect(selection.get_by_role("heading")).to_have_text("Router r1")
            interfaces = selection.get_by_role("list", name="Interfaces").get_by_role("listitem")
            expect(interfaces).to_have_text([
                re.compile(r"^r1-eth0 to h1\s*10\.0\.0\.1/24, 2001:db8::1/64$"),
                re.compile(r"^r1-eth1 to s1\s*no address yet$"),
            ])
            expect(selection).to_contain_text("Services: DHCP")
            for name in ("Edit…", "Connect…", "Remove"):
                expect(selection.get_by_role("button", name=name, exact=True)).to_be_visible()
            canvas = page.get_by_role("region", name="Topology canvas")
            expect(canvas.get_by_role("button", name=re.compile("^Remove"))).to_have_count(0)

            # Edit… opens the Router's dialog and returns focus to itself.
            edit = selection.get_by_role("button", name="Edit…")
            edit.click()
            expect(page.get_by_role("dialog", name="Router r1")).to_be_visible()
            page.keyboard.press("Escape")
            expect(edit).to_be_focused()

            selection.get_by_role("button", name="Remove", exact=True).click()
            expect(page.locator("#status-message")).to_have_text("Removed Router r1 and its Links.")
            self.assertEqual(sorted(self._saved_positions(page)), ["h1", "s1"])

    def test_a_link_is_reached_from_the_selected_nodes_interface_list(self) -> None:
        """The Interface list leads to a Link, whose Selection shows both ends and Edit…."""
        with self.signed_in_page() as page:
            self._seed(page, self.ROUTED, self.ROUTED_LINKS)
            self._node(page, "r1").focus()
            selection = page.get_by_role("region", name="Selection")
            selection.get_by_role("button", name="r1-eth1 to s1").click()
            link = page.locator("#draft-canvas .link[aria-label^='Link between r1 and s1']")
            expect(link).to_be_focused()
            # A Link's description names only what a Link can do.
            expect(link).to_have_accessible_description(re.compile("Enter edits properties"))
            expect(link).not_to_have_accessible_description(re.compile("Shift"))
            expect(self._node(page, "r1")).to_have_accessible_description(re.compile("Shift\\+Arrow moves this Node"))
            expect(selection.get_by_role("heading")).to_have_text("Link between r1 and s1")
            expect(selection.get_by_role("list", name="Ends").get_by_role("listitem")).to_have_text(
                ["r1-eth1: no address yet", "s1-eth1: no address yet"])
            expect(selection.get_by_role("button", name="Connect…")).to_be_hidden()
            expect(selection.get_by_role("button", name="Remove", exact=True)).to_be_hidden()

            # Focus shows on the Link's line apart from its selection.
            focus_colour = page.evaluate("""() => {
                const probe = document.body.appendChild(document.createElement('span'));
                probe.style.color = 'var(--focus-color)';
                const colour = getComputedStyle(probe).color;
                probe.remove();
                return colour;
            }""")
            line = page.locator("#draft-canvas .link-line").nth(1)
            expect(line).to_have_css("stroke", focus_colour)
            edit = selection.get_by_role("button", name="Edit…")
            edit.focus()
            expect(line).not_to_have_css("stroke", focus_colour)
            edit.click()
            expect(page.get_by_role("dialog", name="Link between r1 and s1")).to_be_visible()
            page.keyboard.press("Escape")
            expect(edit).to_be_focused()

            # The addressed Link lists its addresses in place of "no address yet".
            self._node(page, "h1").focus()
            selection.get_by_role("button", name="h1-eth0 to r1").click()
            expect(selection.get_by_role("list", name="Ends").get_by_role("listitem")).to_have_text(
                ["r1-eth0: 10.0.0.1/24, 2001:db8::1/64", "h1-eth0: 10.0.0.2/24"])

    # r4 already uses eth0 and eth2; h3 has no Links yet.
    WIRED: list = [("r4", 400, 300, "router", {}), ("h1", 200, 300), ("h2", 400, 550), ("h3", 650, 320)]
    WIRED_LINKS: list = [("r4", "h1"), ("r4", "h2", {"port": 2}, {})]

    def _saved_links(self, page: Page) -> list:
        """Each saved Link as its two Interface names, once autosave settles."""
        expect(page.locator("#status-draft-state")).to_have_text("Saved")
        return page.evaluate("""async () => {
            const id = document.querySelector('#draft-id').value;
            const draft = await (await fetch(`/api/authoring/drafts/${id}`)).json();
            const names = Object.fromEntries(draft.topology.nodes.map(node => [node.id, node.name]));
            return draft.topology.links.map(link => link.ends.map(end => `${names[end.node_id]}-eth${end.port}`));
        }""")

    def test_connect_links_two_nodes_by_keyboard_without_a_dialog(self) -> None:
        """Connect… asks for a Node; Enter on it adds a Link on each side's next Interface."""
        with self.signed_in_page() as page:
            self._seed(page, self.WIRED, self.WIRED_LINKS)
            self._node(page, "r4").focus()
            selection = page.get_by_role("region", name="Selection")
            selection.get_by_role("button", name="Connect…").focus()
            page.keyboard.press("Enter")

            bar = page.get_by_role("group", name="Connect r4")
            expect(bar).to_contain_text("Connect r4 to… choose a Node · Esc cancels")
            expect(bar.get_by_role("button", name="Cancel")).to_be_visible()
            expect(page.locator("#status-message")).to_have_text(
                "Connect r4 to… choose a Node, or press Escape to cancel.")
            # Focus starts on the nearest other Node, and choosing leaves the Selection on r4.
            expect(self._node(page, "h1")).to_be_focused()
            expect(selection.get_by_role("heading")).to_have_text("Router r4")
            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowRight")
            expect(self._node(page, "h3")).to_be_focused()
            page.keyboard.press("Enter")

            link = page.locator("#draft-canvas .link[aria-label^='Link between r4 and h3']")
            expect(link).to_be_focused()
            expect(link).to_have_class("link selected")
            expect(bar).to_be_hidden()
            expect(page.locator("dialog[open]")).to_have_count(0)
            expect(selection.get_by_role("heading")).to_have_text("Link between r4 and h3")
            expect(selection.get_by_role("list", name="Ends").get_by_role("listitem")).to_have_text(
                ["r4-eth3: no address yet", "h3-eth0: no address yet"])
            self.assertIn(["r4-eth3", "h3-eth0"], self._saved_links(page))

    def test_escape_or_cancel_leaves_connect_mode_on_connect(self) -> None:
        """Escape and Cancel both return focus to Connect…; a click also picks a Node."""
        with self.signed_in_page() as page:
            self._seed(page, self.WIRED, self.WIRED_LINKS)
            self._node(page, "r4").focus()
            connect = page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…")
            bar = page.get_by_role("group", name="Connect r4")
            connect.click()
            expect(bar).to_be_visible()
            page.keyboard.press("Escape")
            expect(bar).to_be_hidden()
            expect(connect).to_be_focused()

            connect.click()
            bar.get_by_role("button", name="Cancel").click()
            expect(bar).to_be_hidden()
            expect(connect).to_be_focused()
            expect(page.locator("#draft-canvas .link")).to_have_count(2)

            # A double click picks once and opens nothing.
            connect.click()
            self._node(page, "h3").locator("circle.node-body").dblclick()
            expect(page.locator("#draft-canvas .link[aria-label^='Link between r4 and h3']")).to_be_focused()
            expect(bar).to_be_hidden()
            expect(page.locator("dialog[open]")).to_have_count(0)

    def test_edit_or_remove_while_connecting_leaves_connect_mode(self) -> None:
        """The Selection's actions end connect mode first, so no Link names a removed Node."""
        with self.signed_in_page() as page:
            self._seed(page, self.WIRED, self.WIRED_LINKS)
            self._node(page, "r4").focus()
            selection = page.get_by_role("region", name="Selection")
            bar = page.get_by_role("group", name="Connect r4")
            selection.get_by_role("button", name="Connect…").click()
            edit = selection.get_by_role("button", name="Edit…")
            edit.click()
            expect(bar).to_be_hidden()
            dialog = page.get_by_role("dialog", name="Router r4")
            expect(dialog).to_be_visible()
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(edit).to_be_focused()

            selection.get_by_role("button", name="Connect…").click()
            selection.get_by_role("button", name="Remove", exact=True).click()
            expect(bar).to_be_hidden()
            page.keyboard.press("Enter")
            self.assertEqual(self._saved_links(page), [])

    def test_dragging_a_nodes_handle_onto_another_links_them(self) -> None:
        """The + shows on a hovered or selected Node; dropping it on a Switch starts at port 1."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 150, 200), ("s1", 550, 200, "switch", {}), ("h2", 150, 500)])
            h1, s1, h2 = (self._node(page, name) for name in ("h1", "s1", "h2"))
            handle = h1.locator(".link-handle")
            expect(handle).to_be_hidden()
            h2.focus()
            expect(h2.locator(".link-handle")).to_be_visible()
            h1.hover()
            expect(handle).to_be_visible()

            box = handle.bounding_box()
            target = s1.locator("circle.node-body").bounding_box()
            assert box is not None and target is not None
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(target["x"] + target["width"] / 2, target["y"] + target["height"] / 2, steps=8)
            page.mouse.up()

            link = page.locator("#draft-canvas .link")
            expect(link).to_be_focused()
            expect(link).to_have_class("link selected")
            expect(page.locator("dialog[open]")).to_have_count(0)
            expect(page.get_by_role("region", name="Selection").get_by_role("list", name="Ends")
                   .get_by_role("listitem")).to_have_text(["h1-eth0: no address yet", "s1-eth1: no address yet"])
            self.assertEqual(self._saved_links(page), [["h1-eth0", "s1-eth1"]])

    # One of each relationship, and a Relay whose server no Node has.
    RELATED: list = [
        ("rly1", 200, 200, "router", {"relay": {"subnets": ["10.0.1.0/24"],
                                                "servers": ["10.0.2.2", "2001:db8:2:0:0::2"]}}),
        ("dhcp1", 450, 200, "host", {"dhcp": {"pools": []}}),
        ("rly2", 200, 450, "router", {"relay": {"subnets": ["10.0.5.0/24"], "servers": ["10.0.9.9"]}}),
        ("sp1", 700, 200, "speaker", {"neighbors": [{"peer": "10.0.3.1", "local_address": "10.0.3.2",
                                                     **NEIGHBOR}]}),
        ("rtr1", 900, 200, "router", {}),
        ("pri1", 450, 700, "host", {"dns": {"zones": [zone("example.com.", ds_delegations=[
            {"node_id": "@chd1", "zone": "lab.example.com."}])]}}),
        ("sec1", 700, 700, "host", {"dns": {"zones": [
            {"kind": "secondary", "name": "example.com.", "primary_node_id": "@pri1"}]}}),
        ("chd1", 200, 700, "host", {"dns": {"zones": [zone("lab.example.com.")]}}),
    ]
    RELATED_LINKS: list = [("rly1", "dhcp1", {}, {"ipv4": "10.0.2.2/24", "ipv6": "2001:db8:2::2/64"}),
                     ("sp1", "rtr1", {"ipv4": "10.0.3.2/24"}, {"ipv4": "10.0.3.1/24"})]

    @staticmethod
    def _related(page: Page) -> list:
        """The Nodes the canvas draws relationship lines to, by name."""
        return page.evaluate("""() => [...document.querySelectorAll('#draft-canvas .relation-line')]
            .map(line => document.querySelector(
                `#draft-canvas [data-node-id="${line.dataset.relatedNodeId}"] .node-label`).textContent)
            .sort()""")

    def test_relationships_show_as_dashed_lines_and_as_text(self) -> None:
        """Each relationship kind, from both ends; an unmatched Relay server is text alone."""
        with self.signed_in_page() as page:
            self._seed(page, self.RELATED, self.RELATED_LINKS)
            relationships = page.get_by_role("region", name="Selection").get_by_role(
                "list", name="Relationships").get_by_role("listitem")
            self.assertEqual(self._related(page), [])
            expected = {
                "rly1": (["dhcp1"], ["DHCP Relay to dhcp1 at 10.0.2.2",
                                     "DHCP Relay to dhcp1 at 2001:db8:2:0:0::2"]),
                "dhcp1": (["rly1"], ["DHCP relayed from rly1"]),
                "rly2": ([], ["DHCP Relay to 10.0.9.9: no Node has this address"]),
                "sp1": (["rtr1"], ["BGP peer rtr1 at 10.0.3.1"]),
                "rtr1": (["sp1"], ["BGP peer sp1, a Speaker"]),
                "sec1": (["pri1"], ["Secondary Zone example.com. from Primary pri1"]),
                "pri1": (["chd1", "sec1"], ["Primary of example.com. for Secondary sec1",
                                            "DS delegation of lab.example.com. to chd1"]),
                "chd1": (["pri1"], ["DS parent Zone example.com. on pri1"]),
            }
            for name, (lines, texts) in expected.items():
                with self.subTest(name):
                    self._node(page, name).focus()
                    expect(relationships).to_have_text(texts)
                    self.assertEqual(self._related(page), lines)
                    dashes = page.locator("#draft-canvas .relation-line").evaluate_all(
                        "lines => lines.map(line => getComputedStyle(line).strokeDasharray)")
                    self.assertTrue(all(dash not in ("", "none") for dash in dashes), dashes)

            # Nothing stays drawn once the selection moves to a Link or to nothing.
            page.locator("#draft-canvas .link").first.focus()
            self.assertEqual(self._related(page), [])
            canvas = page.locator("#draft-canvas").bounding_box()
            assert canvas is not None
            self._node(page, "pri1").focus()
            page.mouse.click(canvas["x"] + canvas["width"] - 20, canvas["y"] + 60)
            self.assertEqual(self._related(page), [])

    def test_escape_right_after_placing_a_node_undoes_it(self) -> None:
        """Escape on a just-placed Node removes it and returns to its palette item."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100)])
            add_router = page.get_by_role("button", name="Add Router")
            add_router.focus()
            page.keyboard.press("Enter")
            expect(self._node(page, "r1")).to_be_focused()
            expect(page.locator("#status-message")).to_have_text(
                re.compile(r"^Added Router r1 at x \d+, y \d+\. Press Escape to undo\.$"))
            page.keyboard.press("Escape")
            expect(self._node(page, "r1")).to_have_count(0)
            expect(page.locator("#status-message")).to_have_text("Cancelled Router r1 placement.")
            expect(add_router).to_be_focused()
            self.assertEqual(sorted(self._saved_positions(page)), ["h1"])

            # Once focus moves on, Escape no longer undoes it.
            page.keyboard.press("Enter")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("Escape")
            self.assertEqual(sorted(self._saved_positions(page)), ["h1", "r1"])

    def test_canvas_nodes_are_drawn_at_the_running_lab_sizes(self) -> None:
        """Radius 30, icon 56, names 18 px and Interface labels 12 px, as Learners see them."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 300, 100)], [("h1", "h2")])
            sizes = page.evaluate("""() => {
                const canvas = document.getElementById('draft-canvas');
                const scale = new DOMMatrix(getComputedStyle(document.getElementById('draft-canvas-g')).transform).a;
                const label = canvas.querySelector('.link-label');
                return {scale,
                    radius: +canvas.querySelector('circle.node-body').getAttribute('r'),
                    icon: +canvas.querySelector('.node-icon').getAttribute('width'),
                    name: getComputedStyle(canvas.querySelector('.node-label')).fontSize,
                    label: getComputedStyle(label).fontSize};
            }""")
            self.assertEqual((sizes["radius"], sizes["icon"]), (30, 56))
            self.assertEqual((sizes["name"], sizes["label"]), ("18px", "12px"))
            # The icon is decorative: the named Node exposes no unnamed image.
            expect(self._node(page, "h1").get_by_role("img")).to_have_count(0)

    def test_a_compact_lab_opens_at_the_same_proportions_in_both_views(self) -> None:
        """Three Nodes close together fit at the shared 1.5 times cap in authoring and running views."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 300, 100), ("h3", 200, 250)], [])
            authoring = box_of(page.locator("#draft-canvas circle.node-body").first)["width"]
            self.assertAlmostEqual(authoring, 60 * 1.5 + 2.5 * 1.5, delta=2)

        with self.signed_in_page() as page:
            nodes: list[dict[str, Any]] = [{"id": name, "type": "host", "label": name, "ip": "", "services": []}
                     for name in ("h1", "h2", "h3")]
            layout = {"nodes": {"h1": {"x": 100, "y": 100}, "h2": {"x": 300, "y": 100},
                                "h3": {"x": 200, "y": 250}}}
            self.render_topology(page, {"nodes": nodes, "links": []}, layout, settle_ms=1200)
            running = box_of(page.locator("#topology .node circle").first)["width"]
            self.assertAlmostEqual(running, 60 * 1.5 + 2 * 1.5, delta=2)

    def test_nodes_move_beyond_the_old_boundary_and_reopen_at_large_coordinates(self) -> None:
        """Keyboard and position fields can save past 1000 without rescaling."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 995, 200), ("h2", 800, 300)], [])
            node = self._node(page, "h1")
            node.focus()
            page.keyboard.press("Shift+ArrowRight")
            self.assertEqual(self._saved_positions(page)["h1"], {"x": 1005, "y": 200})
            page.keyboard.press("Enter")
            page.locator("#node-position-disclosure summary").click()
            page.locator("#host-x").fill("-1800.25")
            page.locator("#host-y").fill("3700.5")
            page.keyboard.press("Escape")
            self.assertEqual(self._saved_positions(page)["h1"], {"x": -1800.25, "y": 3700.5})
            page.reload()
            expect(self._node(page, "h1")).to_have_attribute("aria-label", "Host h1 at x -1800.25, y 3700.5")
            self.assertEqual(self._saved_positions(page)["h2"], {"x": 800, "y": 300})

    def test_dragging_moves_freely_past_the_old_canvas_edges(self) -> None:
        """Pointer movement is unrestricted in either direction, like the running Lab."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1600, "height": 900})
            for start, delta, expected in ((995, 150, 1095), (1995, 150, 2095), (5, -150, -95)):
                with self.subTest(start=start):
                    self._seed(page, [("h1", start, 400), ("h2", start - 95, 400)], [])
                    box = self._node(page, "h1").bounding_box()
                    assert box is not None
                    x = box["x"] + box["width"] / 2
                    y = box["y"] + box["height"] / 2
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + delta, y, steps=8)
                    page.mouse.up()
                    self.assertEqual(self._saved_positions(page)["h1"], {"x": expected, "y": 400})

    def test_large_layout_fits_with_the_same_arrangement_in_both_views(self) -> None:
        """A published-size Layout retains its proportions and fits in either view."""
        positions = {"h1": {"x": 100, "y": 100}, "h2": {"x": 1800, "y": 1600},
                     "h3": {"x": 1800, "y": 100}}
        for authoring in (True, False):
            with self.subTest(authoring=authoring), self.signed_in_page() as page:
                page.set_viewport_size({"width": 1600, "height": 900})
                if authoring:
                    self._seed(page, [(name, point["x"], point["y"]) for name, point in positions.items()], [])
                    svg = page.locator("#draft-canvas")
                    nodes = [self._node(page, name) for name in positions]
                else:
                    nodes_data = [{"id": name, "type": "host", "label": name, "ip": "", "services": []}
                                  for name in positions]
                    self.render_topology(page, {"nodes": nodes_data, "links": []},
                                         {"nodes": positions}, settle_ms=1200)
                    svg = page.locator("#topology")
                    nodes = [svg.locator(".node").filter(
                        has=page.locator(".node-label", has_text=re.compile(f"^{name}$")))
                             for name in positions]
                viewport = svg.bounding_box()
                assert viewport is not None
                centres = []
                for node in nodes:
                    box = node.bounding_box()
                    assert box is not None
                    self.assertGreaterEqual(box["x"], viewport["x"] - 1)
                    self.assertGreaterEqual(box["y"], viewport["y"] - 1)
                    self.assertLessEqual(box["x"] + box["width"], viewport["x"] + viewport["width"] + 1)
                    self.assertLessEqual(box["y"] + box["height"], viewport["y"] + viewport["height"] + 1)
                    centres.append((box["x"] + box["width"] / 2, box["y"] + box["height"] / 2))
                self.assertAlmostEqual((centres[2][0] - centres[0][0]) / (centres[1][1] - centres[2][1]),
                                       1700 / 1500, delta=0.02)

    def test_first_node_is_readable_and_additions_fit_on_a_phone(self) -> None:
        """First placement is readable; later additions keep the whole Topology visible."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 375, "height": 720})
            page.goto(self.base_url + "/authoring")
            page.get_by_role("button", name="New draft", exact=True).click()
            page.get_by_role("button", name="Add Host", exact=True).click()
            label = self._node(page, "h1").locator(".node-label")
            expect(label).to_be_in_viewport()
            box = label.bounding_box()
            assert box is not None
            self.assertGreaterEqual(box["height"], 12)
            for number in range(2, 5):
                page.get_by_role("button", name="Add Host", exact=True).click()
                node = self._node(page, f"h{number}")
                expect(node).to_be_in_viewport()
                for existing in range(1, number + 1):
                    expect(self._node(page, f"h{existing}")).to_be_in_viewport(ratio=1)

    def test_canvas_controls_are_a_labelled_group_of_native_tab_stops(self) -> None:
        """Native buttons have their usual Tab order and keyboard activation."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 300, 100)], [])
            controls = page.get_by_role("group", name="Canvas zoom and arrange controls", exact=True)
            buttons = controls.get_by_role("button")
            expect(buttons).to_have_count(4)
            first = controls.get_by_role("button", name="Zoom in", exact=True)
            first.focus()
            initial = page.locator("#draft-canvas-g").get_attribute("transform")
            page.keyboard.press("Enter")
            page.wait_for_function("before => document.querySelector('#draft-canvas-g')"
                                   ".getAttribute('transform') !== before", arg=initial)
            for name in ("Zoom out", "Fit", "Auto-Arrange Nodes"):
                page.keyboard.press("Tab")
                expect(controls.get_by_role("button", name=name, exact=True)).to_be_focused()
            page.keyboard.press("Shift+Tab")
            fit = controls.get_by_role("button", name="Fit", exact=True)
            expect(fit).to_be_focused()
            page.keyboard.press("Space")
            expect(fit).to_be_focused()
