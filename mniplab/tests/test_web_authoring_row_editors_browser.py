"""Row editors in the Node dialog: lists are labelled rows, never JSON."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from mniplab.tests.authoring_editors import RESOLVER, AuthoringPageCase
from mniplab.tests.browser import Page, expect, screenshots
from mniplab.webserver import authoring

pytestmark = [pytest.mark.browser, pytest.mark.browser_authoring]

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "definitions"


class ResolverAccessControlRowsTests(AuthoringPageCase):
    """Resolver Access Control is rows of Subnet and action."""

    def _open(self, page: Page) -> None:
        self._seed(page, [("r1", 300, 300, "router", {"resolver": RESOLVER})])
        self.open_services(page)

    def test_existing_rules_are_labelled_rows_and_no_json_remains(self) -> None:
        """Each rule shows its Subnet and action, and no textarea is left."""
        with self.signed_in_page() as page:
            self._open(page)
            expect(page.get_by_label("Access rule 1 Subnet")).to_have_value("0.0.0.0/0")
            expect(page.get_by_label("Access rule 2 Subnet")).to_have_value("::/0")
            expect(page.get_by_label("Access rule 2 Action")).to_have_value("allow")
            self.assertEqual(page.locator("#host-dialog textarea#resolver-access-control").count(), 0)
            screenshots(page, "redesign-06", "access-control", "#resolver-access-control")

    def test_rules_authored_by_keyboard_only_save_the_same_values(self) -> None:
        """Add, type, choose an action and remove: the definition matches the JSON form's."""
        with self.signed_in_page() as page:
            self._open(page)
            page.get_by_role("button", name="Add Access rule").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Access rule 3 Subnet")).to_be_focused()
            page.keyboard.type("10.0.0.0/8")
            page.keyboard.press("Tab")
            expect(page.get_by_label("Access rule 3 Action")).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(page.get_by_label("Access rule 3 Action")).to_have_value("refuse")
            expect(page.locator("#status-message")).to_contain_text("Access rule 3 added")
            page.get_by_role("button", name="Remove Access rule 1").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Access rule 1 Subnet")).to_be_focused()
            expect(page.get_by_label("Access rule 1 Subnet")).to_have_value("::/0")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["resolver"]["access_control"], [
                {"subnet": "::/0", "action": "allow"},
                {"subnet": "10.0.0.0/8", "action": "refuse"}])

    def test_a_bare_address_is_a_host_subnet_as_the_server_reads_it(self) -> None:
        """10.0.0.5 alone means 10.0.0.5/32: accepted, and saved as typed."""
        with self.signed_in_page() as page:
            self._open(page)
            subnet = page.get_by_label("Access rule 2 Subnet")
            subnet.fill("10.0.0.5")
            page.keyboard.press("Tab")
            expect(subnet).not_to_have_attribute("aria-invalid", "true")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["resolver"]["access_control"][1]["subnet"], "10.0.0.5")

    def test_a_bad_subnet_is_named_on_its_own_row(self) -> None:
        """The error sits on the offending row's field, and saving is refused until fixed."""
        with self.signed_in_page() as page:
            self._open(page)
            subnet = page.get_by_label("Access rule 2 Subnet")
            subnet.fill("not-a-subnet")
            page.keyboard.press("Tab")
            expect(subnet).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#resolver-access-control-1-subnet-error")).to_have_text(
                "Enter a Subnet CIDR, such as 10.0.0.0/8.")
            expect(subnet).to_have_accessible_description("Enter a Subnet CIDR, such as 10.0.0.0/8.")
            expect(page.get_by_label("Access rule 1 Subnet")).not_to_have_attribute("aria-invalid", "true")
            expect(page.locator("#status-draft-state")).to_contain_text("Can't save yet")
            subnet.fill("2001:db8::/32")
            expect(subnet).not_to_have_attribute("aria-invalid", "true")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["resolver"]["access_control"][1]["subnet"],
                             "2001:db8::/32")


class InfoTipTests(AuthoringPageCase):
    """A list's description sits behind an (i) button, closed until asked for."""

    def test_description_is_a_keyboard_disclosure_behind_an_info_button(self) -> None:
        """The (i) button names its list, toggles the description, and keeps it as the group's description."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {"resolver": RESOLVER})])
            self.open_services(page)
            tip = page.get_by_role("button", name="About Access Control rules")
            help_text = page.locator("#resolver-access-control-help")
            expect(tip).to_have_attribute("aria-expanded", "false")
            expect(help_text).to_be_hidden()
            box = tip.bounding_box()
            assert box is not None
            self.assertGreaterEqual(min(box["width"], box["height"]), 24)
            tip.focus()
            page.keyboard.press("Enter")
            expect(tip).to_have_attribute("aria-expanded", "true")
            expect(help_text).to_be_visible()
            expect(help_text).to_contain_text("most specific matching Subnet")
            page.keyboard.press("Space")
            expect(tip).to_have_attribute("aria-expanded", "false")
            expect(help_text).to_be_hidden()
            expect(page.locator("#resolver-access-control")).to_have_accessible_description(
                re.compile("most specific matching Subnet"))

    def test_relay_descriptions_sit_behind_their_own_info_buttons(self) -> None:
        """The Relay's intro and each field's note open from an (i) named for what it explains."""
        relay = {"subnets": ["10.20.0.0/24"], "servers": ["10.0.0.1"]}
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {"relay": relay})])
            self.open_services(page)
            for topic, note, phrase in (
                ("Relay", "relay-enabled-help", "Forwards DHCP requests"),
                ("Relayed Subnets", "relay-subnets-help", "Subnet CIDRs, separated by commas"),
                ("DHCP servers", "relay-servers-help", "one IPv4, one IPv6"),
            ):
                tip = page.get_by_role("button", name=f"About {topic}", exact=True)
                expect(tip).to_have_attribute("aria-expanded", "false")
                expect(page.locator(f"#{note}")).to_be_hidden()
                tip.focus()
                page.keyboard.press("Enter")
                expect(page.locator(f"#{note}")).to_contain_text(phrase)
                expect(page.locator(f"#{note}")).to_be_visible()
                page.keyboard.press("Enter")
                expect(page.locator(f"#{note}")).to_be_hidden()
            page.get_by_role("button", name="About DHCP servers").click()
            expect(page.locator("#relay-servers")).to_have_accessible_description(
                re.compile("one IPv4, one IPv6"))
            page.get_by_role("button", name="About DHCP servers").click()
            screenshots(page, "redesign-06", "relay-info", "#relay-enabled")

    def test_every_description_in_the_node_dialog_starts_closed_behind_an_info_button(self) -> None:
        """No description shows until asked for, and each has an (i) that opens exactly it."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {"relay": {"subnets": ["10.20.0.0/24"], "servers": ["10.0.0.1"]},
                                                            "resolver": RESOLVER, "dhcp": {"pools": []}, "dns": {"zones": []}})])
            self.open_services(page)
            for tab in ("general", "routing", "services", "startup"):
                page.locator(f"#node-tab-{tab}").click()
                for note in page.locator(f"#node-panel-{tab} .authoring-note[id]").all():
                    note_id = note.get_attribute("id")
                    in_hidden_group = note.evaluate(
                        "n => { for (let e = n.parentElement; e; e = e.parentElement) if (e.hidden) return true; return false; }")
                    if in_hidden_group:
                        continue
                    expect(note, note_id).to_be_hidden()
                    tip = page.locator(f'.info-tip[data-info-for="{note_id}"]')
                    self.assertEqual(tip.count(), 1, note_id)
                    tip.click()
                    expect(note, note_id).to_be_visible()
                    tip.click()

    def test_reopening_the_dialog_shows_every_description_closed(self) -> None:
        """A description left open for one Node is closed when the dialog opens again."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {}), ("h1", 500, 300, "host", {})])
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-routing").click()
            tip = page.get_by_role("button", name="About Protocols")
            tip.click()
            expect(page.locator("#router-protocols-help")).to_be_visible()
            screenshots(page, "redesign-06", "routing-info", "#router-protocols")
            page.keyboard.press("Escape")
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-routing").click()
            expect(tip).to_have_attribute("aria-expanded", "false")
            expect(page.locator("#router-protocols-help")).to_be_hidden()


class DhcpPoolRowsTests(AuthoringPageCase):
    """DHCP Pools are collapsible rows with a Reservations table inside each."""

    POOLS = [{"cidr": "10.50.0.0/24", "start": "10.50.0.100", "end": "10.50.0.200", "lease_time": 120,
              "preferred_lifetime": None, "router": "10.50.0.1", "dns_server": "8.8.8.8",
              "reservations": [{"ip": "10.50.0.50", "mac": "00:00:00:00:00:02", "duid": "", "hostname": "h1"}]},
             {"cidr": "10.51.0.0/24", "start": "10.51.0.100", "end": "10.51.0.200", "lease_time": None,
              "preferred_lifetime": None, "router": "", "dns_server": "", "reservations": []}]

    def _open(self, page: Page, pools: list | None = None) -> None:
        pools = self.POOLS if pools is None else pools
        self._seed(page, [("r1", 300, 300, "router", {"dhcp": {"pools": pools}})])
        self.open_services(page)

    def test_collapsed_pools_summarise_and_no_json_remains(self) -> None:
        """Each Pool is a closed row reading Subnet, range and Reservation count."""
        with self.signed_in_page() as page:
            self._open(page)
            expect(page.get_by_role("group", name="Pools")).to_be_visible()
            first = page.locator("#dhcp-pools-rows summary").first
            expect(first).to_contain_text("10.50.0.0/24 · 100–200 · 1 Reservation")
            expect(first).to_have_accessible_name(re.compile(r"^Pool 1: 10\.50\.0\.0/24 · 100–200 · 1 Reservation$"))
            expect(page.locator("#dhcp-pools-rows summary").nth(1)).to_contain_text(
                "10.51.0.0/24 · 100–200 · 0 Reservations")
            expect(page.get_by_label("Pool 1 Range start")).to_be_hidden()
            first.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Pool 1 Range start")).to_have_value("10.50.0.100")
            expect(page.get_by_label("Pool 1 Lease time (seconds)")).to_have_value("120")
            expect(page.get_by_label("Pool 1 Reservation 1 Address")).to_have_value("10.50.0.50")
            expect(page.get_by_label("Pool 1 Reservation 1 MAC")).to_have_value("00:00:00:00:00:02")
            self.assertEqual(page.locator("#host-dialog textarea#dhcp-pools").count(), 0)
            screenshots(page, "redesign-06", "dhcp-pools", "#dhcp-pools")

    def test_a_pool_and_a_nested_reservation_authored_by_keyboard_only(self) -> None:
        """Add, type and Tab through a Pool and its Reservation: the saved values are the JSON form's."""
        with self.signed_in_page() as page:
            self._open(page, [])
            page.get_by_role("button", name="Add Pool", exact=True).focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Pool 1 Subnet")).to_be_focused()
            for text in ("10.60.0.0/24", "10.60.0.100", "10.60.0.200", "600", "10.60.0.1", "8.8.8.8"):
                page.keyboard.type(text)
                page.keyboard.press("Tab")
            page.get_by_role("button", name="Add Reservation to Pool 1").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Pool 1 Reservation 1 Address")).to_be_focused()
            for text in ("10.60.0.50", "00:00:00:00:00:09", "h9"):
                page.keyboard.type(text)
                page.keyboard.press("Tab")
            expect(page.locator("#status-message")).to_contain_text("Pool 1 Reservation 1 added")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["dhcp"]["pools"], [{
                "cidr": "10.60.0.0/24", "start": "10.60.0.100", "end": "10.60.0.200", "lease_time": 600,
                "preferred_lifetime": None, "router": "10.60.0.1", "dns_server": "8.8.8.8",
                "reservations": [{"ip": "10.60.0.50", "mac": "00:00:00:00:00:09", "duid": "",
                                  "hostname": "h9"}]}])

    def test_family_rules_show_in_the_controls(self) -> None:
        """Router is IPv4-only, preferred lifetime IPv6-only, and an IPv6 Reservation names one identifier."""
        with self.signed_in_page() as page:
            self._open(page, [])
            page.get_by_role("button", name="Add Pool", exact=True).click()
            subnet = page.get_by_label("Pool 1 Subnet")
            subnet.fill("10.60.0.0/24")
            expect(page.get_by_label("Pool 1 Router")).to_be_visible()
            expect(page.get_by_label("Pool 1 Preferred lifetime (seconds)")).to_be_hidden()
            page.get_by_role("button", name="Add Reservation to Pool 1").click()
            expect(page.get_by_label("Pool 1 Reservation 1 Identified by")).to_be_hidden()
            page.get_by_label("Pool 1 Router").fill("10.60.0.1")
            subnet.fill("2001:db8:60::/64")
            expect(page.get_by_label("Pool 1 Router")).to_be_hidden()
            expect(page.get_by_label("Pool 1 Preferred lifetime (seconds)")).to_be_visible()
            identified = page.get_by_label("Pool 1 Reservation 1 Identified by")
            expect(identified).to_be_visible()
            expect(identified).to_have_value("mac")
            page.get_by_label("Pool 1 Lease time (seconds)").fill("600")
            page.get_by_label("Pool 1 Preferred lifetime (seconds)").fill("300")
            page.get_by_label("Pool 1 Reservation 1 Address").fill("2001:db8:60::50")
            identified.select_option("duid")
            duid = page.get_by_label("Pool 1 Reservation 1 DUID")
            duid.fill("00:01:00:01:aa:bb:cc:dd")
            page.keyboard.press("Escape")
            pool = self.saved_node(page)["dhcp"]["pools"][0]
            self.assertEqual(pool["router"], "")
            self.assertEqual(pool["preferred_lifetime"], 300)
            self.assertEqual(pool["reservations"], [{"ip": "2001:db8:60::50", "mac": "",
                                                     "duid": "00:01:00:01:aa:bb:cc:dd", "hostname": ""}])

    def test_errors_land_on_the_offending_row_and_open_it(self) -> None:
        """A bad range end and a bad Reservation are named on their own fields, inside collapsed rows."""
        with self.signed_in_page() as page:
            self._open(page)
            page.locator("#dhcp-pools-rows summary").nth(1).click()
            end = page.get_by_label("Pool 2 Range end")
            end.fill("10.99.0.200")
            page.keyboard.press("Tab")
            expect(end).to_have_attribute("aria-invalid", "true")
            expect(end).to_have_accessible_description("Outside 10.51.0.0/24.")
            expect(page.get_by_label("Pool 2 Range start")).not_to_have_attribute("aria-invalid", "true")
            expect(page.locator("#dhcp-pools-rows summary").nth(1).locator(".row-card-error")).to_be_visible()
            page.locator("#dhcp-pools-rows summary").nth(1).click()
            expect(end).to_be_hidden()
            page.keyboard.press("Escape")
            fix = page.locator("#status-draft-fix")
            expect(fix).to_contain_text("Outside 10.51.0.0/24.")
            fix.focus()
            page.keyboard.press("Enter")
            page.locator("#node-dialog-error-summary").get_by_role("link", name=re.compile("Outside")).click()
            expect(end).to_be_visible()
            expect(end).to_be_focused()
            end.fill("10.51.0.200")
            expect(end).not_to_have_attribute("aria-invalid", "true")
            page.locator("#dhcp-pools-rows summary").first.click()
            reservation = page.get_by_label("Pool 1 Reservation 1 Address")
            reservation.fill("10.60.0.5")
            page.keyboard.press("Tab")
            expect(reservation).to_have_accessible_description("Outside 10.50.0.0/24.")

    def test_shipped_dhcp_examples_reopen_and_resave_unchanged(self) -> None:
        """Editing and undoing in the editors leaves the shipped Pools exactly as they were."""
        for example in ("dhcp-lab",):
            pools = next(node["dhcp"]["pools"] for node in
                         json.loads((EXAMPLES / f"{example}.json").read_text())["topology"]["nodes"]
                         if node.get("dhcp"))
            with self.subTest(example), self.signed_in_page() as page:
                self._open(page, pools)
                page.locator("#dhcp-pools-rows summary").first.click()
                dns = page.get_by_label("Pool 1 DNS server")
                dns.press("End")
                page.keyboard.type("x")
                page.keyboard.press("Backspace")
                page.keyboard.press("Escape")
                self.assertEqual(self.saved_node(page)["dhcp"]["pools"], pools)


class RelayServerPickerTests(AuthoringPageCase):
    """Relay servers are rows, each a combobox of Node, Interface and address that still takes free typing."""

    NODES = [
        ("r1", 300, 300, "router", {"relay": {"subnets": ["10.20.0.0/24"], "servers": ["10.0.0.9"]}}),
        ("r2", 500, 300, "router", {}),
        ("h1", 300, 500, "host", {}),
    ]
    LINKS = [("r1", "r2", {"ipv4": "10.0.0.1/24"}, {"ipv4": "10.0.0.2/24", "ipv6": "2001:db8::2/64"}),
             ("r1", "h1", {"ipv4": "10.9.0.1/24"}, {"ipv4": "10.9.0.5/24"})]

    def _open(self, page: Page) -> None:
        self._seed(page, self.NODES, self.LINKS)
        self.open_services(page)

    def test_a_server_is_picked_by_keyboard_from_other_nodes_interfaces(self) -> None:
        """Down opens the list, arrows move, Enter fills the bare address; own addresses are absent."""
        with self.signed_in_page() as page:
            self._open(page)
            server = page.get_by_role("combobox", name="DHCP server 1 Address")
            expect(server).to_have_value("10.0.0.9")
            expect(server).to_have_attribute("aria-expanded", "false")
            opener = page.get_by_role("button", name="Pick a Node Interface for DHCP server 1 Address")
            box = opener.bounding_box()
            assert box is not None
            self.assertGreaterEqual(min(box["width"], box["height"]), 24)
            server.focus()
            page.keyboard.press("ArrowDown")
            expect(server).to_have_attribute("aria-expanded", "true")
            options = page.get_by_role("listbox").get_by_role("option")
            self.assertEqual(options.all_inner_texts(), [
                "r2 · r2-eth0 · 10.0.0.2", "r2 · r2-eth0 · 2001:db8::2", "h1 · h1-eth0 · 10.9.0.5"])
            self.assertEqual(page.get_by_role("listbox").get_by_role("option", name="10.0.0.1").count(), 0)
            screenshots(page, "redesign-06", "relay-picker-open")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            active = page.get_by_role("listbox").get_by_role("option", name="h1 · h1-eth0 · 10.9.0.5")
            expect(active).to_have_attribute("aria-selected", "true")
            expect(server).to_have_attribute("aria-activedescendant", active.get_attribute("id"))
            page.keyboard.press("ArrowUp")
            page.keyboard.press("ArrowUp")
            page.keyboard.press("Enter")
            expect(server).to_have_value("10.0.0.2")
            expect(server).to_have_attribute("aria-expanded", "false")
            expect(server).to_be_focused()
            screenshots(page, "redesign-06", "relay-picker", "#relay-servers")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["relay"]["servers"], ["10.0.0.2"])

    def test_typing_filters_the_choices_and_a_free_typed_server_is_kept(self) -> None:
        """A typed address that matches nothing closes the list and is stored as typed."""
        with self.signed_in_page() as page:
            self._open(page)
            server = page.get_by_role("combobox", name="DHCP server 1 Address")
            server.fill("")
            server.press_sequentially("h1")
            self.assertEqual(page.get_by_role("listbox").get_by_role("option").all_inner_texts(), ["h1 · h1-eth0 · 10.9.0.5"])
            expect(page.locator("#relay-servers-rows [role=status]")).to_have_text("1 matching Interface")
            server.fill("192.0.2.99")
            expect(server).to_have_attribute("aria-expanded", "false")
            page.keyboard.press("Tab")
            expect(server).to_have_value("192.0.2.99")
            expect(server).not_to_have_attribute("aria-invalid", "true")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["relay"]["servers"], ["192.0.2.99"])

    def test_escape_closes_the_list_before_the_dialog(self) -> None:
        """The first Escape closes the open list and keeps the dialog; the second closes the dialog."""
        with self.signed_in_page() as page:
            self._open(page)
            server = page.get_by_role("combobox", name="DHCP server 1 Address")
            server.focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Escape")
            expect(server).to_have_attribute("aria-expanded", "false")
            expect(page.locator("#host-dialog")).to_be_visible()
            expect(server).to_be_focused()
            page.keyboard.press("Escape")
            expect(page.locator("#host-dialog")).to_be_hidden()

    def test_a_choice_can_be_clicked_and_two_servers_are_the_most(self) -> None:
        """Clicking picks; a second server per family is refused, and Add stops at two."""
        with self.signed_in_page() as page:
            self._open(page)
            page.get_by_role("button", name="Add DHCP server", exact=True).click()
            second = page.get_by_role("combobox", name="DHCP server 2 Address")
            expect(second).to_be_focused()
            page.get_by_role("button", name="Pick a Node Interface for DHCP server 2 Address").click()
            page.get_by_role("listbox").get_by_role("option", name="r2 · r2-eth0 · 2001:db8::2").click()
            expect(second).to_have_value("2001:db8::2")
            expect(page.get_by_role("button", name="Add DHCP server", exact=True)).to_be_disabled()
            second.fill("10.0.0.3")
            page.keyboard.press("Tab")
            expect(second).to_have_accessible_description("Only one server per address family.")
            second.fill("2001:db8::2")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["relay"]["servers"], ["10.0.0.9", "2001:db8::2"])

            # Removing every server is refused, and the refusal leads back to the list.
            page.keyboard.press("Escape")
            expect(page.locator("#host-dialog")).to_be_hidden()
            self.open_services(page)
            page.get_by_role("button", name="Remove DHCP server 2").click()
            page.get_by_role("button", name="Remove DHCP server 1").click()
            page.keyboard.press("Escape")
            fix = page.locator("#status-draft-fix")
            expect(fix).to_contain_text("Add at least one DHCP server")
            fix.focus()
            page.keyboard.press("Enter")
            page.locator("#node-dialog-error-summary").locator('a[href="#relay-servers"]').click()
            expect(page.locator("#relay-servers-error")).to_have_text("Add at least one DHCP server.")
            expect(page.locator("#relay-servers")).to_be_focused()

    def test_shipped_relay_example_reopens_and_resaves_unchanged(self) -> None:
        """Editing and undoing leaves the shipped Relays exactly as they were."""
        nodes = [node for node in json.loads((EXAMPLES / "dhcp-lab.json").read_text())["topology"]["nodes"]
                 if node.get("relay")]
        for node in nodes:
            with self.subTest(node["name"]), self.signed_in_page() as page:
                self._seed(page, [("r1", 300, 300, "router", {"relay": node["relay"]})])
                self.open_services(page)
                first = page.get_by_role("combobox", name="DHCP server 1 Address")
                first.press("End")
                page.keyboard.type("9")
                page.keyboard.press("Backspace")
                page.keyboard.press("Escape")
                page.keyboard.press("Escape")
                self.assertEqual(self.saved_node(page)["relay"], node["relay"])


def route(prefix: str, next_hop: str, **more: object) -> dict:
    """A Route as the definition stores it, with every optional field present."""
    return {"prefix": prefix, "next_hop": next_hop, "community": [], "as_path": [],
            "large_community": [], "med": None, **more}


def neighbor(peer: str, local: str, routes: list | None = None, **fields: object) -> dict:
    """A Neighbor as the definition stores it."""
    return {"peer": peer, "local_address": local, "local_as": 65001, "peer_as": 65000,
            "hold_time": None, "routes": routes or [], **fields}


class SpeakerRowsTests(AuthoringPageCase):
    """Speaker Neighbors, their Per-Neighbor Routes and the Global Routes are rows."""

    LINKS = [("r1", "s1", {"ipv4": "10.0.0.1/24"}, {"ipv4": "10.0.0.2/24"})]

    def _open(self, page: Page, neighbors: list, routes: list | None = None) -> None:
        speaker = {"local_as": 65001, "neighbors": neighbors, "routes": routes or []}
        self._seed(page, [("r1", 300, 300, "router", {}), ("s1", 500, 300, "speaker", speaker)], self.LINKS)
        self._node(page, "s1").focus()
        page.keyboard.press("Enter")
        page.locator("#node-tab-speaker").click()
        page.locator("#speaker-routes-more summary").click()

    def saved_speaker(self, page: Page) -> dict:
        return self.saved_node(page, "s1")

    def test_neighbors_are_cards_with_their_routes_and_no_json_remains(self) -> None:
        """A Neighbor summarises peer, ASes and Route count; its Routes hide optional fields behind More."""
        neighbors = [neighbor("10.0.0.1", "10.0.0.2", [route("203.0.113.0/24", "10.0.0.2", as_path=[65001, 64512])])]
        with self.signed_in_page() as page:
            self._open(page, neighbors)
            card = page.locator("#speaker-neighbors-rows summary").first
            expect(card).to_contain_text("10.0.0.1 · AS 65001 → 65000 · 1 hand-written Route")
            card.click()
            page.get_by_role("button", name="More settings for Neighbor 1: Route set", exact=True).click()
            expect(page.get_by_role("combobox", name="Neighbor 1 Peer")).to_have_value("10.0.0.1")
            expect(page.get_by_label("Neighbor 1 Local address")).to_have_value("10.0.0.2")
            expect(page.get_by_label("Neighbor 1 Hold time (seconds)")).to_have_value("")
            expect(page.get_by_label("Neighbor 1 Route 1 Prefix")).to_have_value("203.0.113.0/24")
            expect(page.get_by_label("Neighbor 1 Route 1 Next hop")).to_have_value("10.0.0.2")
            more = page.get_by_role("button", name="More settings for Neighbor 1 Route 1: AS path set")
            expect(more).to_contain_text("More · AS path")
            expect(more).to_have_attribute("aria-expanded", "false")
            expect(page.get_by_label("Neighbor 1 Route 1 AS path")).to_be_hidden()
            more.click()
            expect(page.get_by_label("Neighbor 1 Route 1 AS path")).to_have_value("65001 64512")
            self.assertEqual(page.locator("#host-dialog textarea#speaker-neighbors").count(), 0)
            self.assertEqual(page.locator("#host-dialog textarea#speaker-routes").count(), 0)
            screenshots(page, "redesign-06", "speaker", "#speaker-neighbors")

    def test_a_route_without_its_optional_fields_opens_validates_and_saves_unchanged(self) -> None:
        """The Lab lets a Route leave out communities and MED, as the Secure BGP Lab does: none of that breaks."""
        sparse = {"prefix": "203.0.113.0/24", "next_hop": "10.0.0.2", "as_path": [65003, 65002]}
        neighbors = [neighbor("10.0.0.1", "10.0.0.2", [sparse])]
        errors: list = []
        with self.signed_in_page() as page:
            page.on("pageerror", lambda error: errors.append(str(error)))
            self._open(page, neighbors)
            page.locator("#speaker-neighbors-rows summary").first.click()
            page.get_by_role("button", name="More settings for Neighbor 1: Route set", exact=True).click()
            page.get_by_role("button", name="More settings for Neighbor 1 Route 1: AS path set").click()
            expect(page.get_by_label("Neighbor 1 Route 1 Communities")).to_have_value("")
            expect(page.get_by_label("Neighbor 1 Route 1 MED")).to_have_value("")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_speaker(page)["neighbors"][0]["routes"], [sparse])
        self.assertEqual(errors, [])

    def test_a_neighbor_with_routes_authored_by_keyboard_only(self) -> None:
        """Pick the peer, Tab through the fields, add Routes, open More: the saved values are the JSON form's."""
        with self.signed_in_page() as page:
            self._open(page, [neighbor("10.0.0.1", "10.0.0.2")])
            page.locator("#speaker-neighbors-rows summary").first.focus()
            page.keyboard.press("Enter")
            page.get_by_role("button", name="Remove Neighbor 1", exact=True).focus()
            page.keyboard.press("Enter")
            page.get_by_role("button", name="Add Neighbor", exact=True).focus()
            page.keyboard.press("Enter")
            peer = page.get_by_role("combobox", name="Neighbor 1 Peer")
            expect(peer).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(page.get_by_role("listbox").get_by_role("option")).to_have_text(["r1 · r1-eth0 · 10.0.0.1"])
            page.keyboard.press("Enter")
            expect(peer).to_have_value("10.0.0.1")
            page.keyboard.press("Tab")
            for text in ("10.0.0.2", "65001", "65000", "90"):
                page.keyboard.type(text)
                page.keyboard.press("Tab")
            page.get_by_role("checkbox", name="Neighbor 1 Generate Routes", exact=True).uncheck()
            page.get_by_role("button", name="More settings for Neighbor 1", exact=True).click()
            page.get_by_role("button", name="Add Route to Neighbor 1").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Neighbor 1 Route 1 Prefix")).to_be_focused()
            page.keyboard.type("203.0.113.0/24")
            page.keyboard.press("Tab")
            page.keyboard.type("10.0.0.2")
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            page.keyboard.press("Enter")
            expect(page.get_by_label("Neighbor 1 Route 1 Communities")).to_be_visible()
            for text in ("65000:100, 65000:200", "65000:1:2", "65001 64512", "50"):
                page.keyboard.press("Tab")
                page.keyboard.type(text)
            page.get_by_role("button", name="Add Route to Neighbor 1").focus()
            page.keyboard.press("Enter")
            page.keyboard.type("198.51.100.0/24")
            page.keyboard.press("Tab")
            page.keyboard.type("10.0.0.2")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_speaker(page)["neighbors"], [neighbor(
                "10.0.0.1", "10.0.0.2", hold_time=90, routes=[
                    route("203.0.113.0/24", "10.0.0.2", community=["65000:100", "65000:200"],
                          large_community=["65000:1:2"], as_path=[65001, 64512], med=50),
                    route("198.51.100.0/24", "10.0.0.2")])])

    def test_a_peer_is_picked_by_mouse_or_typed_freely(self) -> None:
        """The Speaker's own Interface is not offered; a peer no Node has is kept as typed."""
        with self.signed_in_page() as page:
            self._open(page, [neighbor("10.0.0.1", "10.0.0.2")])
            page.locator("#speaker-neighbors-rows summary").first.click()
            peer = page.get_by_role("combobox", name="Neighbor 1 Peer")
            page.get_by_role("button", name="Pick a Node Interface for Neighbor 1 Peer").click()
            expect(page.get_by_role("listbox").get_by_role("option")).to_have_text(["r1 · r1-eth0 · 10.0.0.1"])
            page.get_by_role("listbox").get_by_role("option", name="r1 · r1-eth0 · 10.0.0.1").click()
            expect(peer).to_have_value("10.0.0.1")
            peer.fill("10.0.0.77")
            expect(peer).to_have_attribute("aria-expanded", "false")
            page.keyboard.press("Tab")
            expect(peer).not_to_have_attribute("aria-invalid", "true")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_speaker(page)["neighbors"][0]["peer"], "10.0.0.77")

    def test_global_routes_are_rows_with_the_same_more(self) -> None:
        """Global Routes show prefix and next hop, keep the rest behind More, and save as the JSON form did."""
        with self.signed_in_page() as page:
            self._open(page, [neighbor("10.0.0.1", "10.0.0.2")])
            page.get_by_role("button", name="Add Global Route", exact=True).focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Global Route 1 Prefix")).to_be_focused()
            page.keyboard.type("203.0.113.0/24")
            page.keyboard.press("Tab")
            page.keyboard.type("10.0.0.2")
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            page.keyboard.press("Space")
            page.get_by_label("Global Route 1 MED").fill("7")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_speaker(page)["routes"], [route("203.0.113.0/24", "10.0.0.2", med=7)])

    def test_errors_land_on_the_row_and_reveal_what_hides_them(self) -> None:
        """A bad prefix, AS path in More and repeated local address are named on their own fields."""
        neighbors = [neighbor("10.0.0.1", "10.0.0.2", [route("203.0.113.0/24", "10.0.0.2")]),
                     neighbor("10.0.1.1", "10.0.1.2")]
        with self.signed_in_page() as page:
            self._open(page, neighbors)
            page.locator("#speaker-neighbors-rows summary").first.click()
            page.get_by_role("button", name="More settings for Neighbor 1: Route set", exact=True).click()
            prefix = page.get_by_label("Neighbor 1 Route 1 Prefix")
            prefix.fill("203.0.113.5/24")
            page.keyboard.press("Tab")
            expect(prefix).to_have_accessible_description("Enter a network such as 203.0.113.0/24, with no host bits set.")
            prefix.fill("203.0.113.0/24")
            expect(prefix).not_to_have_attribute("aria-invalid", "true")
            page.get_by_role("button", name="More settings for Neighbor 1 Route 1").click()
            path = page.get_by_label("Neighbor 1 Route 1 AS path")
            path.fill("65001 abc")
            page.keyboard.press("Tab")
            expect(path).to_have_attribute("aria-invalid", "true")
            page.get_by_role("button", name=re.compile("More settings for Neighbor 1 Route 1")).click()
            expect(path).to_be_hidden()
            page.keyboard.press("Escape")
            fix = page.locator("#status-draft-fix")
            expect(fix).to_contain_text("AS numbers")
            fix.focus()
            page.keyboard.press("Enter")
            page.locator("#node-dialog-error-summary").get_by_role("link", name=re.compile("AS numbers")).click()
            expect(path).to_be_visible()
            expect(path).to_be_focused()
            path.fill("65001")
            page.locator("#speaker-neighbors-rows summary").nth(1).click()
            local = page.get_by_label("Neighbor 2 Local address")
            local.fill("10.0.0.2")
            page.keyboard.press("Tab")
            expect(local).to_have_accessible_description("Each Neighbor needs its own local address.")

    def test_a_global_route_of_the_wrong_family_is_refused_on_its_row(self) -> None:
        """Global Routes apply to Neighbors without Routes, so an IPv6 one with an IPv4 Neighbor is wrong."""
        with self.signed_in_page() as page:
            self._open(page, [neighbor("10.0.0.1", "10.0.0.2")])
            page.get_by_role("button", name="Add Global Route", exact=True).click()
            prefix = page.get_by_label("Global Route 1 Prefix")
            prefix.fill("2001:db8::/32")
            page.get_by_label("Global Route 1 Next hop").fill("2001:db8::1")
            page.keyboard.press("Tab")
            prefix.focus()
            page.keyboard.press("Tab")
            expect(prefix).to_have_accessible_description(
                "An IPv6 Route cannot apply to Neighbor 10.0.0.1, which has no Routes of its own.")

    def test_shipped_speaker_examples_reopen_and_resave_unchanged(self) -> None:
        """Editing and undoing leaves each shipped Speaker's Neighbors and Routes exactly as they were."""
        cases = (("bgp-isp-lab", "routes"),
                 ("bgp-isp-lab", "routes-ipv6"),
                 ("bgp-ispixp-lab", "neighbors"),
                 ("bgp-isp-lab", "neighbors"))
        for example, where in cases:
            speaker = next(node for node in json.loads((EXAMPLES / f"{example}.json").read_text())["topology"]["nodes"]
                           if node["type"] == "speaker")
            if where.startswith("routes"):
                # Exercise Global Routes on a surviving Speaker definition.
                speaker["route_generator"] = speaker["neighbors"][0].pop("route_generator")
                if where == "routes-ipv6":
                    speaker["neighbors"][0]["peer"] = "2001:db8:1::1"
                    speaker["neighbors"][0]["local_address"] = "2001:db8:1::2"
            fields = {key: speaker[key] for key in ("local_as", "neighbors", "routes")}
            if "route_generator" in speaker:
                fields["route_generator"] = speaker["route_generator"]
            with self.subTest(example=example, field=where), self.signed_in_page() as page:
                self._seed(page, [("r1", 300, 300, "router", {}), ("s1", 500, 300, "speaker", fields)])
                self._node(page, "s1").focus()
                page.keyboard.press("Enter")
                page.locator("#node-tab-speaker").click()
                if where == "neighbors":
                    page.locator("#speaker-neighbors-rows summary").first.click()
                    page.get_by_label("Neighbor 1 Prefix seed", exact=True).press("End")
                else:
                    page.get_by_label("Global Prefix seed", exact=True).press("End")
                page.keyboard.type("9")
                page.keyboard.press("Backspace")
                page.keyboard.press("Escape")
                saved = self.saved_speaker(page)
                self.assertEqual({key: saved[key] for key in fields}, fields)


class ServerErrorRowTests(AuthoringPageCase):
    """A server refusal lands on the row it names, or on the list when it names none."""

    POOLS = DhcpPoolRowsTests.POOLS

    def _refuse(self, message: str, when_start: str) -> None:
        """Make the server refuse any Pool starting at an address the client accepts."""
        real = authoring.dhcp_pool

        def refuse(pool: object) -> object:
            if isinstance(pool, dict) and pool.get("start") == when_start:
                raise ValueError(message)
            return real(pool)

        patcher = patch.object(authoring, "dhcp_pool", refuse)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _follow_to_field(self, page: Page, phrase: str) -> None:
        page.keyboard.press("Escape")
        fix = page.locator("#status-draft-fix")
        expect(fix).to_contain_text(phrase)
        fix.focus()
        page.keyboard.press("Enter")
        page.locator("#node-dialog-error-summary").get_by_role("link", name=re.compile(phrase)).click()

    def test_a_refusal_quoting_a_value_is_put_on_the_row_holding_it(self) -> None:
        """"Invalid start address '10.51.0.150'" goes to Pool 2's Range start, not to Pool 1."""
        self._refuse("Invalid start address '10.51.0.150': reserved for the gateway", "10.51.0.150")
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {"dhcp": {"pools": self.POOLS}})])
            self.open_services(page)
            page.locator("#dhcp-pools-rows summary").nth(1).click()
            page.get_by_label("Pool 2 Range start").fill("10.51.0.150")
            page.locator("#dhcp-pools-rows summary").nth(1).click()
            self._follow_to_field(page, "Invalid start address")
            start = page.get_by_label("Pool 2 Range start")
            expect(start).to_be_visible()
            expect(start).to_be_focused()
            expect(start).to_have_accessible_description(
                "Node 'r1' DHCP Pool: Invalid start address '10.51.0.150': reserved for the gateway")
            expect(page.locator("#dhcp-pools")).not_to_have_attribute("aria-invalid", "true")

    def test_a_refusal_naming_no_row_is_put_on_the_list(self) -> None:
        """A message with no value to find stays on the Pools group, in words."""
        self._refuse("Too many Reservations for this Subnet", "10.51.0.150")
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {"dhcp": {"pools": self.POOLS}})])
            self.open_services(page)
            page.locator("#dhcp-pools-rows summary").nth(1).click()
            page.get_by_label("Pool 2 Range start").fill("10.51.0.150")
            self._follow_to_field(page, "Too many Reservations")
            group = page.locator("#dhcp-pools")
            expect(group).to_be_focused()
            expect(group).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#dhcp-pools-error")).to_have_text(
                "Node 'r1' DHCP Pool: Too many Reservations for this Subnet")
