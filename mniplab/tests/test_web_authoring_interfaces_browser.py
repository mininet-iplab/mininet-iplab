"""A Node's whole address plan on its Interfaces tab; the Link dialog is connectivity only."""

import re
from typing import Any

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase
from mniplab.tests.browser import Page, expect

pytestmark = pytest.mark.browser_authoring

LINKED: list[tuple[str, int, int, str, dict[str, Any]]] = [
    ("h1", 100, 100, "host", {}), ("r1", 300, 100, "router", {}), ("s1", 500, 100, "switch", {})]
LINKS = [("h1", "r1"), ("r1", "s1")]


class AuthoringInterfacesBrowserTests(AuthoringPageCase):
    """An Instructor edits a Node's Interfaces in one place."""

    def _open_node(self, page: Page, name: str, tab: str = "Interfaces") -> None:
        self._node(page, name).focus()
        page.keyboard.press("Enter")
        expect(page.locator("#host-dialog")).to_be_visible()
        page.locator("#host-dialog").get_by_role("tab", name=tab).click()

    def test_interfaces_tab_lists_each_interface_with_its_peer_and_addresses(self) -> None:
        """A Host's Interface carries addresses, DHCP client, MAC and the gateway on one tab."""
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS, address_family="dual")
            self._open_node(page, "h1")
            dialog = page.locator("#host-dialog")
            interface = dialog.get_by_role("group", name="h1-eth0 to r1")
            expect(interface).to_be_visible()
            interface.get_by_label("IPv4 address and prefix").fill("10.0.0.2/24")
            interface.get_by_label("IPv6 address and prefix").fill("2001:db8::2/64")
            interface.get_by_label("MAC address, optional").fill("aa:bb:cc:dd:ee:01")
            expect(interface.get_by_label("DHCPv4 client")).to_be_visible()
            dialog.get_by_label("IPv4 default gateway").fill("10.0.0.1")
            page.keyboard.press("Escape")
            saved = self._saved_draft(page)
            host = next(node for node in saved["topology"]["nodes"] if node["name"] == "h1")
            end = next(end for link in saved["topology"]["links"] for end in link["ends"]
                       if end["node_id"] == host["id"])
            self.assertEqual((end["ipv4"], end["ipv6"], end["mac"]),
                             ("10.0.0.2/24", "2001:db8::2/64", "aa:bb:cc:dd:ee:01"))
            self.assertEqual(host["ipv4_gateway"], "10.0.0.1")

    def test_interfaces_show_only_the_labs_address_family(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS, address_family="ipv4")
            self._open_node(page, "r1")
            interface = page.locator("#host-dialog").get_by_role("group", name="r1-eth0 to h1")
            expect(interface.get_by_label("IPv4 address and prefix")).to_be_visible()
            expect(interface.get_by_label("IPv6 address and prefix")).to_be_hidden()
            expect(interface.get_by_label("MAC address, optional")).to_have_count(0)

    def test_a_switchs_interfaces_carry_vlan_settings_and_no_addresses(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            self._open_node(page, "s1")
            interface = page.locator("#host-dialog").get_by_role("group", name="s1-eth1 to r1")
            expect(interface.get_by_label("VLAN mode")).to_be_visible()
            expect(interface.get_by_label("IPv4 address and prefix")).to_have_count(0)
            expect(interface.get_by_label("IPv6 address and prefix")).to_have_count(0)
            interface.get_by_label("VLAN mode").select_option("access")
            interface.get_by_label("Access VLAN ID").fill("20")
            page.keyboard.press("Escape")
            saved = self._saved_draft(page)
            vlans = [end.get("vlan") for link in saved["topology"]["links"] for end in link["ends"]
                     if end.get("vlan")]
            self.assertEqual(vlans, [{"mode": "access", "vlan": 20}])

    def test_the_link_dialog_holds_only_connectivity(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            self._open_node(page, "h1")
            page.locator("#host-dialog").get_by_role("button", name="Edit Link h1-eth0").click()
            link = page.locator("#link-dialog")
            expect(link).to_be_visible()
            expect(page.locator("#host-dialog")).to_be_hidden()
            expect(link.get_by_role("combobox", name="Interface").first).to_be_visible()
            for label in ("IPv4 address and prefix", "IPv6 address and prefix", "MAC address, optional",
                          "DHCPv4 client", "VLAN mode"):
                expect(link.get_by_label(label)).to_have_count(0)
            expect(link.locator("#link-condition-enabled")).to_have_count(1)
            page.keyboard.press("Escape")
            expect(link).to_be_hidden()

    def test_an_invalid_address_is_reported_on_the_nodes_interface(self) -> None:
        """A refused address opens the Node's Interfaces tab at that field, not the Link dialog."""
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            self._open_node(page, "h1")
            interface = page.locator("#host-dialog").get_by_role("group", name="h1-eth0 to r1")
            interface.get_by_label("IPv4 address and prefix").fill("not-an-address")
            page.keyboard.press("Escape")
            fix = page.get_by_role("button", name=re.compile("Enter a valid IPv4"))
            expect(fix).to_be_visible(timeout=15000)
            fix.click()
            expect(page.locator("#host-dialog")).to_be_visible()
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_contain_text("Interfaces › IPv4 address and prefix")
            summary.get_by_role("link").click()
            expect(page.locator("#host-dialog").get_by_role("tab", name="Interfaces")).to_have_attribute(
                "aria-selected", "true")
            expect(page.locator("#host-dialog").get_by_label("IPv4 address and prefix")).to_be_focused()
            expect(page.locator("#link-dialog")).to_be_hidden()

            page.locator("#host-dialog").get_by_label("IPv4 address and prefix").fill("10.0.0.2/24")
            mac = page.locator("#host-dialog").get_by_label("MAC address, optional")
            mac.fill("aabbcc")
            page.keyboard.press("Escape")
            page.get_by_role("button", name=re.compile("Enter six colon-separated hex octets")).click()
            summary.get_by_role("link").click()
            expect(mac).to_be_focused()
            expect(page.locator(f"#{mac.get_attribute('id')}-error")).to_have_text(
                "Enter six colon-separated hex octets, such as aa:bb:cc:dd:ee:01, or leave it blank.")

    def _selection(self, page: Page):
        return page.get_by_role("region", name="Selection")

    def test_addresses_popover_edits_a_nodes_interfaces_by_keyboard(self) -> None:
        """Addresses… lists the Node's Interfaces, Apply saves, and focus returns to the Node."""
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS, address_family="dual")
            node = self._node(page, "r1")
            node.focus()
            button = self._selection(page).get_by_role("button", name="Addresses…")
            button.focus()
            page.keyboard.press("Enter")
            popover = page.get_by_role("dialog", name="Addresses of r1")
            expect(popover).to_be_visible()
            first = popover.get_by_role("group", name="r1-eth0 to h1")
            second = popover.get_by_role("group", name="r1-eth1 to s1")
            expect(second).to_be_visible()
            expect(popover.get_by_label("MAC address, optional")).to_have_count(0)
            first.get_by_label("IPv4 address and prefix").fill("10.0.0.1/24")
            second.get_by_label("IPv6 address and prefix").fill("2001:db8:1::1/64")
            popover.get_by_role("button", name="Apply").click()
            expect(popover).to_be_hidden()
            expect(node).to_be_focused()
            saved = self._saved_draft(page)
            router = next(item for item in saved["topology"]["nodes"] if item["name"] == "r1")
            ends = sorted((end for link in saved["topology"]["links"] for end in link["ends"]
                           if end["node_id"] == router["id"]), key=lambda end: end["port"])
            self.assertEqual([end["ipv4"] for end in ends], ["10.0.0.1/24", ""])
            self.assertEqual([end["ipv6"] for end in ends], ["", "2001:db8:1::1/64"])

    def test_escape_discards_the_popovers_edits_and_returns_focus(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            node = self._node(page, "r1")
            node.focus()
            self._selection(page).get_by_role("button", name="Addresses…").click()
            popover = page.get_by_role("dialog", name="Addresses of r1")
            popover.get_by_label("IPv4 address and prefix").first.fill("10.9.9.9/24")
            page.keyboard.press("Escape")
            expect(popover).to_be_hidden()
            expect(node).to_be_focused()
            saved = self._saved_draft(page)
            self.assertEqual({end["ipv4"] for link in saved["topology"]["links"] for end in link["ends"]}, {""})

    def test_a_switch_has_no_addresses_to_edit(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            self._node(page, "s1").focus()
            expect(self._selection(page).get_by_role("button", name="Addresses…")).to_be_hidden()

    def test_a_node_is_renamed_in_place_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            node = self._node(page, "r1")
            node.focus()
            page.keyboard.press("F2")
            field = page.get_by_role("textbox", name="Rename r1")
            expect(field).to_be_focused()
            field.fill("core")
            page.keyboard.press("Enter")
            expect(field).to_have_count(0)
            expect(self._node(page, "core")).to_be_focused()
            names = [item["name"] for item in self._saved_draft(page)["topology"]["nodes"]]
            self.assertIn("core", names)
            self.assertNotIn("r1", names)

    def test_rename_in_place_refuses_what_the_dialog_refuses(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            self._node(page, "r1").focus()
            page.keyboard.press("F2")
            field = page.get_by_role("textbox", name="Rename r1")
            field.fill("h1")
            page.keyboard.press("Enter")
            expect(field).to_have_attribute("aria-invalid", "true")
            expect(page.get_by_role("alert").filter(has_text="Choose a unique")).to_be_visible()
            field.fill("9bad")
            page.keyboard.press("Enter")
            expect(page.get_by_role("alert").filter(has_text="starting with a letter")).to_be_visible()
            page.keyboard.press("Escape")
            expect(field).to_have_count(0)
            expect(self._node(page, "r1")).to_be_focused()

    def test_the_addresses_dialog_opens_on_the_first_address_field(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            self._node(page, "r1").focus()
            self._selection(page).get_by_role("button", name="Addresses…").click()
            popover = page.get_by_role("dialog", name="Addresses of r1")
            expect(popover.get_by_label("IPv4 address and prefix").first).to_be_focused()

    def test_clicking_away_keeps_a_valid_new_name_and_drops_an_invalid_one(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, LINKED, LINKS)
            self._node(page, "r1").focus()
            page.keyboard.press("F2")
            page.get_by_role("textbox", name="Rename r1").fill("core")
            page.get_by_label("Catalog name").click()
            expect(self._node(page, "core")).to_be_visible()
            self._node(page, "core").focus()
            page.keyboard.press("F2")
            page.get_by_role("textbox", name="Rename core").fill("h1")
            page.get_by_label("Catalog name").click()
            expect(self._node(page, "core")).to_be_visible()
