"""The Switch panel reads in plain words: root, unkeyed ports and info hints."""

import json

import pytest

from mniplab.tests.browser import LiveServerCase, expect

pytestmark = pytest.mark.browser_workspace

VLANS = {"switch": "s2", "ports": {"s2-eth1": {
    "port": "s2-eth1", "mode": "access", "tag": 10, "trunks": [], "native": None,
    "peer_node": "h1", "peer_intf": "h1-eth0"}}}
MACS = {"switch": "s2", "entries": [{"mac": "00:00:00:00:00:01", "port": "s2-eth1",
                                      "vlan": 10, "age": 3}]}
TREE = {"switch": "s2", "protocol": "rstp", "bridge_id": "8.000.0000000000bb",
        "root_bridge": "1.000.0000000000aa", "priority": 32768, "mac": "00:00:00:00:00:bb",
        "root_priority": 4096, "root_mac": "00:00:00:00:00:aa", "root_name": "s1",
        "is_root": False,
        "ports": [{"port": "s2-eth1", "role": "Root", "state": "Forwarding"},
                  {"port": "s2-eth2", "role": "Alternate", "state": "Discarding"}]}


def make_handler(body):
    """Answer a route with ``body`` as JSON."""
    return lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(body))


class SwitchPanelReadabilityTests(LiveServerCase):
    """Summary text, plain port names and the Role and State hints."""

    def open_panel(self, page, tree) -> None:
        for suffix, body in (("vlans", VLANS), ("mac-table", MACS), ("spanning-tree", tree)):
            page.route(f"**/switches/s2/{suffix}", make_handler(body))
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            const { openSwitchPanel } = await import('/static/js/switch/switch-panel.js');
            appState.labKey = 'fake';
            openSwitchPanel('s2');
        }""")
        expect(page.locator("#switch-tree-tbody tr")).not_to_have_count(0)

    def test_summary_names_the_root_and_this_switch(self) -> None:
        with self.signed_in_page() as page:
            self.open_panel(page, TREE)
            summary = page.locator("#switch-tree-summary")
            expect(summary).to_have_text("RSTP · Root: s1 (priority 4096)")
            expect(page.locator("#switch-tree-detail")).to_have_text(
                "This Switch: priority 32768 · MAC 00:00:00:00:00:bb")
            expect(page.locator("#switch-tree-tbody")).to_contain_text("s2-eth2")
            expect(page.locator("#switch-modal")).not_to_contain_text("fake-s2")
            page.evaluate("() => document.getElementById('switch-modal-close').click()")
            page.unroute_all()
            self.open_panel(page, {**TREE, "is_root": True, "root_name": "s2"})
            expect(summary).to_have_text("RSTP · This Switch is the root (priority 4096)")
            page.evaluate("() => document.getElementById('switch-modal-close').click()")
            page.unroute_all()
            self.open_panel(page, {**TREE, "root_bridge": None, "root_priority": None,
                                   "root_mac": None, "root_name": None})
            expect(summary).to_have_text("RSTP · Electing a root…")
            page.evaluate("() => document.getElementById('switch-modal-close').click()")
            page.unroute_all()
            self.open_panel(page, {"switch": "s2", "protocol": None, "bridge_id": None,
                                   "root_bridge": None, "priority": None, "mac": None,
                                   "root_priority": None, "root_mac": None, "root_name": None,
                                   "is_root": False, "ports": []})
            expect(summary).to_have_text("Spanning tree is off.")
            expect(page.locator("#switch-tree-detail")).to_have_text("")

    def test_role_hint_shows_on_keyboard_focus_and_escape_dismisses_it_only(self) -> None:
        with self.signed_in_page() as page:
            self.open_panel(page, TREE)
            button = page.get_by_role("button", name="About port roles")
            hint = page.locator("#switch-role-hint")
            expect(hint).to_be_hidden()
            self.assertEqual(button.get_attribute("aria-describedby"), "switch-role-hint")
            page.keyboard.press("Tab")
            for _ in range(40):
                if button.evaluate("el => el === document.activeElement"):
                    break
                page.keyboard.press("Tab")
            expect(hint).to_be_visible()
            expect(hint).to_contain_text("Alternate: blocked backup path to the root.")
            page.keyboard.press("Escape")
            expect(hint).to_be_hidden()
            expect(page.locator("#switch-modal")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator("#switch-modal")).to_be_hidden()

    def test_hint_stays_while_the_pointer_is_over_it(self) -> None:
        with self.signed_in_page() as page:
            self.open_panel(page, TREE)
            page.get_by_role("button", name="About port states").hover()
            hint = page.locator("#switch-state-hint")
            expect(hint).to_be_visible()
            hint.hover()
            expect(hint).to_be_visible()
            expect(hint).to_contain_text("Discarding means this port is not forwarding frames")


class SwitchContextMenuTests(LiveServerCase):
    """A Switch's Node menu offers its panel and no Terminal."""

    def show_menu(self, page, node_type) -> None:
        page.evaluate("""async (type) => {
            const { showNodeContextMenu } = await import('/static/js/topology/interactions.js');
            showNodeContextMenu({ id: 'n1', type, x: 10, y: 10 }, null);
        }""", node_type)

    def test_a_switch_menu_hides_open_terminal_and_a_router_menu_keeps_it(self) -> None:
        with self.signed_in_page() as page:
            terminal = page.get_by_role("menuitem", name="Open node terminal")
            self.show_menu(page, "switch")
            expect(terminal).to_be_hidden()
            expect(page.get_by_role("menuitem", name="Open Switch Management")).to_be_visible()
            self.show_menu(page, "router")
            expect(terminal).to_be_visible()
            expect(page.get_by_role("menuitem", name="Open Switch Management")).to_be_hidden()
