"""The View menu's VLAN Labels option shows and hides the VLAN and trunk badges."""

import pytest

from mniplab.tests.browser import LiveServerCase, expect

pytestmark = pytest.mark.browser_workspace

AUTHORED = {"nodes": {"s1": {"x": 300, "y": 200}, "h1": {"x": 100, "y": 200},
                      "h2": {"x": 500, "y": 200}}}
VLAN = {"mode": "access", "tag": 10, "trunks": [], "native": None}
SWITCH_TOPOLOGY = {
    "nodes": [
        {"id": "s1", "type": "switch", "label": "s1"},
        {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.1"},
        {"id": "h2", "type": "host", "label": "h2", "ip": "10.0.0.2"},
    ],
    "links": [
        {"source": "h1", "target": "s1", "source_intf": "h1-eth0", "target_intf": "s1-eth1",
         "status": "up", "source_vlan": None, "target_vlan": VLAN, "vlan": VLAN},
        {"source": "h2", "target": "s1", "source_intf": "h2-eth0", "target_intf": "s1-eth2",
         "status": "up", "source_vlan": None, "target_vlan": VLAN, "vlan": VLAN},
    ],
    "relays": [], "translators": [],
}
ROUTED_TOPOLOGY = {
    "nodes": [{"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.1"},
              {"id": "h2", "type": "host", "label": "h2", "ip": "10.0.0.2"}],
    "links": [{"source": "h1", "target": "h2", "source_intf": "h1-eth0",
               "target_intf": "h2-eth0", "status": "up"}],
    "relays": [], "translators": [],
}


class VlanLabelsToggleTests(LiveServerCase):
    """The option follows the Lab's Switches and survives a redraw."""

    def open_view_menu(self, page) -> None:
        button = page.locator("#view-options-btn")
        if button.get_attribute("aria-expanded") != "true":
            button.click()

    def test_the_option_hides_and_shows_the_badges_across_a_redraw(self) -> None:
        with self.signed_in_page() as page:
            self.render_topology(page, SWITCH_TOPOLOGY, AUTHORED)
            badges = page.locator("#topology .vlan-badge")
            self.open_view_menu(page)
            option = page.get_by_role("checkbox", name="Toggle VLAN labels")
            expect(option).to_be_checked()
            expect(badges.first).to_be_visible()
            option.uncheck()
            expect(badges.first).to_be_hidden()
            self.render_topology(page, SWITCH_TOPOLOGY, AUTHORED)
            expect(badges.first).to_be_hidden()
            self.open_view_menu(page)
            page.get_by_role("checkbox", name="Toggle VLAN labels").check()
            expect(badges.first).to_be_visible()

    def test_the_option_is_absent_when_the_lab_has_no_switch(self) -> None:
        with self.signed_in_page() as page:
            self.render_topology(page, ROUTED_TOPOLOGY, {"nodes": {"h1": {"x": 100, "y": 200},
                                                              "h2": {"x": 300, "y": 200}}})
            self.open_view_menu(page)
            expect(page.locator("#vlan-labels-option")).to_be_hidden()
            self.render_topology(page, SWITCH_TOPOLOGY, AUTHORED)
            expect(page.locator("#vlan-labels-option")).to_be_visible()
