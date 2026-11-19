"""Lab-wide family and saved Router config controls as an Instructor uses them."""

import re

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, open_details
from mniplab.tests.browser import expect, screenshots

pytestmark = [pytest.mark.browser, pytest.mark.browser_authoring]


class LabSettingsBrowserTests(AuthoringPageCase):
    """The Lesson tab holds settings that apply throughout a Canvas draft."""

    def test_dns_transfer_target_uses_family_filtered_picker_and_validation(
        self,
    ) -> None:
        with self.signed_in_page() as page:
            zone = {
                "kind": "primary",
                "name": "example.com.",
                "default_ttl": 300,
                "records": [],
                "allow_transfer": [{"address": "2001:db8::2"}],
            }
            self._seed(
                page,
                [
                    ("r1", 100, 200, "router", {"dns": {"zones": [zone]}}),
                    ("h2", 500, 200),
                ],
                [("r1", "h2", {"ipv6": "2001:db8::1/64"}, {"ipv6": "2001:db8::2/64"})],
                address_family="ipv6",
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            ).click()
            page.locator("#dns-zones-rows summary").nth(1).click()
            address = page.get_by_role(
                "combobox", name="Zone 1 Transfer target 1 Address", exact=True
            )
            address.fill("192.0.2.2")
            address.press("Tab")
            expect(address).to_have_attribute("aria-invalid", "true")
            address.fill("2001:db8::2")
            address.press("Tab")
            expect(address).not_to_have_attribute("aria-invalid", "true")
            page.get_by_role(
                "button",
                name="Pick a Node Interface for Zone 1 Transfer target 1 Address",
                exact=True,
            ).click()
            expect(page.get_by_role("listbox").get_by_role("option")).to_have_text(
                ["h2 · h2-eth0 · 2001:db8::2"]
            )
            page.keyboard.press("Escape")
            screenshots(
                page, "rework-02", "dns-transfer-family", "#dns-zones-0-allow_transfer-0-address"
            )

    def test_family_and_persistence_are_edited_by_keyboard_and_reopen(self) -> None:
        with self.signed_in_page() as page:
            self._seed(
                page,
                [("h1", 100, 200), ("r1", 500, 200, "router", {})],
                address_family="ipv4",
            )
            family = page.get_by_label("Address family", exact=True)
            expect(family).to_have_value("ipv4")
            family.focus()
            page.keyboard.press("End")
            page.keyboard.press("Tab")
            persistent = page.get_by_role(
                "checkbox",
                name="A Learner's saved Router config carries over between sessions",
            )
            persistent.focus()
            page.keyboard.press("Space")
            saved = self._saved_draft(page)
            self.assertEqual(saved["address_family"], "dual")
            self.assertTrue(saved["persistent_config"])
            page.reload()
            open_details(page)
            expect(family).to_have_value("dual")
            expect(persistent).to_be_checked()
            screenshots(page, "rework-02", "lesson", "#lab-address-family", open_sidebar=True)
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="Routing", exact=True).click()
            screenshots(page, "rework-02", "router", "#router-frr-config")

    def test_gateways_interfaces_and_dhcp_clients_show_only_the_chosen_family(
        self,
    ) -> None:
        with self.signed_in_page() as page:
            for family, visible in (("ipv4", (4,)), ("ipv6", (6,)), ("dual", (4, 6))):
                self._seed(
                    page,
                    [("h1", 100, 200), ("h2", 500, 200, "router", {})],
                    [("h1", "h2")],
                    address_family=family,
                )
                self._node(page, "h1").focus()
                page.keyboard.press("Enter")
                page.get_by_role("tab", name="General", exact=True).click()
                nameservers = page.get_by_label("Additional nameserver IP addresses")
                placeholder = nameservers.get_attribute("placeholder")
                assert placeholder is not None
                self.assertEqual(":" in placeholder, 6 in visible)
                self.assertEqual("192.0.2." in placeholder, 4 in visible)
                page.get_by_role("tab", name="Interfaces", exact=True).click()
                for version in (4, 6):
                    gateway = page.get_by_label(
                        f"IPv{version} default gateway", exact=True
                    )
                    if version in visible:
                        expect(gateway).to_be_visible()
                    else:
                        expect(gateway).to_be_hidden()
                for version in (4, 6):
                    address = page.get_by_label(
                        f"IPv{version} address and prefix", exact=True
                    ).first
                    dhcp = page.get_by_role(
                        "checkbox", name=f"DHCPv{version} client", exact=True
                    ).first
                    if version in visible:
                        expect(address).to_be_visible()
                        expect(address).to_have_value("")
                        expect(dhcp).to_be_visible()
                    else:
                        expect(address).to_be_hidden()
                        expect(dhcp).to_be_hidden()
                if family == "ipv6":
                    screenshots(page, "rework-03", "interfaces", "#host-ipv6-gateway")
                page.keyboard.press("Escape")
                self._node(page, "h2").focus()
                page.keyboard.press("Enter")
                page.get_by_role("tab", name="Routing", exact=True).click()
                page.get_by_role("button", name="Add VLAN subinterface").click()
                for version in (4, 6):
                    address = page.locator(f"#subinterface-0-ipv{version}")
                    if version in visible:
                        expect(address).to_be_visible()
                        expect(address).to_have_value("")
                    else:
                        expect(address).to_be_hidden()
                if family == "ipv6":
                    screenshots(page, "rework-02", "subinterface", "#subinterface-0-ipv6")
                page.keyboard.press("Escape")

    def test_family_switch_refuses_existing_addresses_with_their_field_names(
        self,
    ) -> None:
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    ("h1", 100, 200, "host", {"ipv6_gateway": "2001:db8::1"}),
                    ("h2", 500, 200),
                ],
                [("h1", "h2", {"ipv6": "2001:db8::2/64"}, {})],
            )
            family = page.get_by_label("Address family", exact=True)
            family.select_option("ipv4")
            expect(family).to_have_value("dual")
            error = page.locator("#lab-address-family-error")
            expect(error).to_be_visible()
            expect(error).to_contain_text("h1 ipv6_gateway")
            expect(error).to_contain_text("h1-eth0")
            saved = self._saved_draft(page)
            self.assertEqual(saved["address_family"], "dual")
            self.assertEqual(
                saved["topology"]["links"][0]["ends"][0]["ipv6"], "2001:db8::2/64"
            )

    def test_dhcp_and_speaker_fields_reject_the_unused_family_on_their_rows(
        self,
    ) -> None:
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    ("r1", 100, 200, "router", {}),
                    (
                        "spk1",
                        500,
                        200,
                        "speaker",
                        {
                            "neighbors": [
                                {
                                    "peer": "2001:db8::1",
                                    "local_address": "2001:db8::2",
                                    "local_as": 65001,
                                    "peer_as": 65000,
                                    "hold_time": None,
                                    "routes": [],
                                }
                            ]
                        },
                    ),
                ],
                address_family="ipv6",
            )
            self.open_services(page)
            page.get_by_role("checkbox", name="DHCP", exact=True).check()
            page.get_by_role("button", name="Add Pool", exact=True).click()
            subnet = page.get_by_label("Pool 1 Subnet", exact=True)
            subnet.fill("10.50.0.0/24")
            subnet.press("Tab")
            expect(subnet).to_have_attribute("aria-invalid", "true")
            expect(page.get_by_label("Pool 1 Router", exact=True)).to_be_hidden()
            subnet.fill("2001:db8:1::/64")
            subnet.press("Tab")
            expect(subnet).not_to_have_attribute("aria-invalid", "true")
            screenshots(page, "rework-02", "dhcp", "#dhcp-pools-0-cidr")
            page.get_by_role("checkbox", name="Resolver", exact=True).check()
            expect(page.locator("#resolver-root-addresses")).to_have_attribute(
                "placeholder", "e.g., 2001:db8::53"
            )
            expect(page.locator("#resolver-forwarders")).to_have_attribute(
                "placeholder", "e.g., 2001:db8::53"
            )
            screenshots(page, "rework-02", "resolver", "#resolver-root-addresses")
            page.get_by_role("checkbox", name="Relay", exact=True).check()
            expect(page.locator("#relay-subnets")).to_have_attribute(
                "placeholder", "e.g., 2001:db8::/64"
            )
            screenshots(page, "rework-02", "relay", "#relay-subnets")
            page.keyboard.press("Escape")
            self._node(page, "spk1").focus()
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="Speaker", exact=True).click()
            page.locator("#speaker-neighbors-rows summary").first.click()
            peer = page.get_by_label("Neighbor 1 Peer", exact=True)
            peer.fill("192.0.2.1")
            peer.press("Tab")
            expect(peer).to_have_attribute("aria-invalid", "true")
            screenshots(page, "rework-02", "speaker", "#speaker-neighbors-0-peer")

    def test_connect_and_new_speakers_and_nat_gateways_do_not_assign_addresses(
        self,
    ) -> None:
        with self.signed_in_page() as page:
            self._seed(
                page, [("h1", 100, 200), ("h2", 500, 200)], address_family="ipv4"
            )
            self._node(page, "h1").focus()
            page.get_by_role("region", name="Selection").get_by_role(
                "button", name="Connect…"
            ).click()
            self._node(page, "h2").focus()
            page.keyboard.press("Enter")
            saved = self._saved_draft(page)
            self.assertEqual(
                [
                    (end["ipv4"], end["ipv6"])
                    for end in saved["topology"]["links"][0]["ends"]
                ],
                [("", ""), ("", "")],
            )
            page.get_by_role("button", name="Add Internet Gateway", exact=True).click()
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="NAT", exact=True).click()
            expect(
                page.get_by_label("Gateway IPv4 address", exact=False)
            ).to_have_value("")
            expect(page.get_by_label("Translated subnet", exact=False)).to_have_value(
                ""
            )
            screenshots(page, "rework-02", "nat", "#nat-ip")
            page.keyboard.press("Escape")
            page.get_by_role("button", name="Add BGP Speaker", exact=True).click()
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="Speaker", exact=True).click()
            page.locator("#speaker-neighbors-rows summary").first.click()
            expect(page.get_by_label("Neighbor 1 Peer", exact=True)).to_have_value("")
            expect(
                page.get_by_label("Neighbor 1 Local address", exact=True)
            ).to_have_value("")
