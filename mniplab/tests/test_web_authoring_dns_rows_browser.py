"""Primary Zones and Records authored through labelled controls and saved over HTTP."""

import json
import re
from pathlib import Path

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, catalog_row
from mniplab.tests.browser import expect, screenshots

pytestmark = [pytest.mark.browser, pytest.mark.browser_authoring]


class DnsZoneRowsTests(AuthoringPageCase):
    """An Instructor authors DNS on a Router without writing JSON."""

    def test_a_signed_primary_and_record_are_authored_by_keyboard(self) -> None:
        """Add a Zone and Record, sign it, and read the exact declaration over HTTP."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {})])
            self.open_services(page)
            page.get_by_role("checkbox", name="Authoritative DNS", exact=True).focus()
            page.keyboard.press("Space")
            page.get_by_role("button", name="Add Zone", exact=True).focus()
            page.keyboard.press("Enter")
            apex = page.get_by_role("textbox", name="Zone 1 Apex")
            expect(apex).to_be_focused()
            page.keyboard.type("example.com.")
            page.keyboard.press("Tab")
            ttl = page.get_by_role("textbox", name="Zone 1 Default TTL (seconds)")
            expect(ttl).to_be_focused()
            expect(ttl).to_have_value("300")
            page.keyboard.press("Control+a")
            page.keyboard.type("600")
            page.keyboard.press("Tab")
            signing = page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True)
            expect(signing).to_be_focused()
            page.keyboard.press("Space")
            expect(signing).to_be_checked()
            page.get_by_role("button", name="Add Record to Zone 1", exact=True).focus()
            page.keyboard.press("Enter")
            expect(
                page.get_by_role("textbox", name="Zone 1 Record 1 Owner")
            ).to_be_focused()
            page.keyboard.type("web")
            page.keyboard.press("Tab")
            expect(
                page.get_by_role("combobox", name="Zone 1 Record 1 Type")
            ).to_be_focused()
            expect(
                page.get_by_role("combobox", name="Zone 1 Record 1 Type").locator(
                    "option"
                )
            ).to_have_text(["A", "AAAA", "CNAME", "PTR", "TXT", "MX", "NS", "SOA"])
            page.keyboard.press("Tab")
            expect(
                page.get_by_role("textbox", name="Zone 1 Record 1 Data")
            ).to_be_focused()
            page.keyboard.type("10.0.0.2")
            expect(page.get_by_role("table", name="Zone 1 Records")).to_be_visible()
            expect(page.get_by_role("columnheader")).to_contain_text(
                ["Owner", "Type", "Data", "Remove"]
            )
            expect(
                page.get_by_role("textbox", name="Zone 1 Record 1 TTL (seconds)")
            ).to_be_hidden()
            page.keyboard.press("Escape")
            expected = {
                "kind": "primary",
                "name": "example.com.",
                "default_ttl": 600,
                "records": [
                    {"name": "web", "type": "A", "rdata": "10.0.0.2", "ttl": 600}
                ],
                "dnssec": True,
            }
            self.assertEqual(self.saved_node(page)["dns"]["zones"], [expected])
            self.open_services(page)
            summary = page.locator("#dns-zones-rows summary").first
            expect(summary).to_have_accessible_name(
                "Zone 1: example.com. · TTL 600 s · 1 Record · signed"
            )
            expect(apex).to_be_hidden()
            summary.focus()
            page.keyboard.press("Enter")
            expect(apex).to_have_value("example.com.")
            expect(signing).to_be_checked()
            expect(
                page.get_by_role("textbox", name="Zone 1 Record 1 Data")
            ).to_have_value("10.0.0.2")
            expect(page.locator("textarea#dns-zones")).to_have_count(0)
            page.get_by_role(
                "button", name="Remove Zone 1 Record 1", exact=True
            ).focus()
            page.keyboard.press("Enter")
            expect(
                page.get_by_role("button", name="Add Record to Zone 1", exact=True)
            ).to_be_focused()
            page.get_by_role("button", name="Remove Zone 1", exact=True).focus()
            page.keyboard.press("Enter")
            expect(
                page.get_by_role("button", name="Add Zone", exact=True)
            ).to_be_focused()
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page)["dns"]["zones"], [])

    def test_signed_router_zone_screenshots_and_checkbox_target(self) -> None:
        """A signed Router Zone has a usable checkbox target at the reviewed sizes."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        300,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "dnssec": True,
                                        "records": [
                                            {
                                                "name": "web",
                                                "type": "A",
                                                "rdata": "10.0.0.2",
                                                "ttl": 300,
                                            }
                                        ],
                                        "nsec3": True,
                                        "nsec3_salt": "aabb",
                                        "allow_transfer": [],
                                        "ds_delegations": [
                                            {
                                                "node_id": "@r2",
                                                "zone": "lab.example.com.",
                                            }
                                        ],
                                    }
                                ]
                            }
                        },
                    ),
                    (
                        "r2",
                        500,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "lab.example.com.",
                                        "default_ttl": 300,
                                        "dnssec": True,
                                        "records": [],
                                    }
                                ]
                            }
                        },
                    ),
                ],
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            signing = page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True)
            box = signing.bounding_box()
            assert box is not None
            self.assertGreaterEqual(min(box["width"], box["height"]), 24)
            screenshots(
                page,
                "redesign-07",
                "signed-router-zone",
                page.get_by_role("group", name=re.compile(r"^Zones")),
            )
            page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            ).click()
            screenshots(page, "redesign-07", "zone-more", "#dns-zones-0-nsec3")
            page.set_viewport_size({"width": 375, "height": 800})
            dialog_box = page.get_by_role("dialog", name="Router r1").bounding_box()
            assert dialog_box is not None
            for control in (
                page.get_by_role("textbox", name="Zone 1 Apex"),
                page.get_by_role("textbox", name="Zone 1 Default TTL (seconds)"),
                page.get_by_role("button", name="Remove Zone 1", exact=True),
            ):
                control_box = control.bounding_box()
                assert control_box is not None
                self.assertLessEqual(
                    control_box["x"] + control_box["width"],
                    dialog_box["x"] + dialog_box["width"],
                )

    def test_nsec3_and_ds_delegation_use_named_controls_and_signed_children(
        self,
    ) -> None:
        """Signing dependencies retain edits, and DS stores a signed child Node and Zone."""

        def zone(apex: str, signed: bool = False) -> dict:
            return {
                "kind": "primary",
                "name": apex,
                "default_ttl": 300,
                "records": [],
                "dnssec": signed,
            }

        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        200,
                        200,
                        "router",
                        {"dns": {"zones": [zone("."), zone("own.", True)]}},
                    ),
                    (
                        "r2",
                        500,
                        200,
                        "router",
                        {"dns": {"zones": [zone("example.com.")]}},
                    ),
                    ("r3", 500, 400, "router", {"dns": {"zones": [zone("unsigned.")]}}),
                    (
                        "r4",
                        200,
                        400,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "secondary",
                                        "name": "example.com.",
                                        "primary_node_id": "@r2",
                                    }
                                ]
                            }
                        },
                    ),
                ],
            )
            self.open_services(page, "r2")
            page.locator("#dns-zones-rows summary").first.click()
            page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True).focus()
            page.keyboard.press("Space")
            page.keyboard.press("Escape")
            self._saved_draft(page)
            self.open_services(page)
            page.locator(
                "#dns-zones-rows > .row-list > details > summary"
            ).first.click()
            nsec = page.get_by_role("checkbox", name="Zone 1 NSEC3", exact=True)
            expect(nsec).to_be_hidden()
            more = page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            )
            more.focus()
            page.keyboard.press("Enter")
            expect(more).to_have_attribute("aria-expanded", "true")
            nsec.focus()
            page.keyboard.press("Space")
            page.keyboard.press("Tab")
            expect(nsec).to_have_accessible_description(
                "NSEC3 requires DNSSEC signing."
            )
            expect(page.locator("#status-draft-state")).to_contain_text(
                "Can't save yet"
            )
            page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True).focus()
            page.keyboard.press("Space")
            expect(nsec).not_to_have_attribute("aria-invalid", "true")
            salt = page.get_by_role("textbox", name="Zone 1 NSEC3 salt")
            salt.fill("a")
            page.keyboard.press("Tab")
            expect(salt).to_have_accessible_description(
                "Use pairs of hexadecimal digits, or leave the salt empty."
            )
            salt.focus()
            page.keyboard.press("Control+a")
            page.keyboard.type("aabb")
            page.get_by_role(
                "button", name="Add DS delegation to Zone 1", exact=True
            ).focus()
            page.keyboard.press("Enter")
            child = page.get_by_role(
                "combobox", name="Zone 1 DS delegation 1 Child Primary Zone"
            )
            expect(child).to_be_focused()
            expect(child.locator("option")).to_have_text(
                ["Choose a signed Primary Zone…", "r2 · example.com."]
            )
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Tab")
            page.keyboard.press("Escape")
            nodes = self._saved_draft(page)["topology"]["nodes"]
            parent = next(node for node in nodes if node["name"] == "r1")["dns"][
                "zones"
            ][0]
            child_id = next(node for node in nodes if node["name"] == "r2")["id"]
            self.assertEqual(
                parent,
                {
                    **zone(".", True),
                    "nsec3": True,
                    "nsec3_salt": "aabb",
                    "ds_delegations": [{"node_id": child_id, "zone": "example.com."}],
                },
            )
            self.open_services(page)
            page.locator(
                "#dns-zones-rows > .row-list > details > summary"
            ).first.click()
            more.click()
            expect(child).to_have_value(child_id + "|example.com.")
            page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True).uncheck()
            expect(child).to_have_value(child_id + "|example.com.")
            expect(page.locator("#status-draft-state")).to_contain_text(
                "Can't save yet"
            )
            page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True).check()
            page.get_by_role(
                "button", name="Remove Zone 1 DS delegation 1", exact=True
            ).click()
            expect(
                page.get_by_role(
                    "button", name="Add DS delegation to Zone 1", exact=True
                )
            ).to_be_focused()
            page.keyboard.press("Escape")
            self.assertEqual(
                self.saved_node(page)["dns"]["zones"][0]["ds_delegations"], []
            )

    def test_transfer_default_none_and_listed_keep_distinct_saved_values(self) -> None:
        """None refuses transfers; listed Nodes and picked or typed addresses retain their identities."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        200,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "records": [],
                                        "allow_transfer": None,
                                    }
                                ]
                            }
                        },
                    ),
                    ("r2", 500, 300, "router", {}),
                ],
                [("r1", "r2", {"ipv4": "10.0.0.1/24"}, {"ipv4": "10.0.0.2/24"})],
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            ).click()
            permission = page.get_by_role("combobox", name="Zone 1 Transfer permission")
            expect(permission).to_have_value("default")
            expect(permission.locator("option")).to_have_text(
                ["Default (declared Secondaries)", "None (refused)", "Listed"]
            )
            permission.focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Tab")
            page.keyboard.press("Escape")
            self.assertEqual(
                self.saved_node(page)["dns"]["zones"][0]["allow_transfer"], []
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            ).click()
            expect(permission).to_have_value("none")
            permission.focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Tab")
            expect(permission).to_have_accessible_description(
                "Add at least one transfer target, or choose None."
            )
            page.get_by_role(
                "button", name="Add Transfer target to Zone 1", exact=True
            ).focus()
            page.keyboard.press("Enter")
            kind = page.get_by_role(
                "combobox", name="Zone 1 Transfer target 1 Identified by"
            )
            expect(kind).to_be_focused()
            page.keyboard.press("Tab")
            node = page.get_by_role(
                "combobox", name="Zone 1 Transfer target 1 Node", exact=True
            )
            expect(node).to_be_focused()
            node.select_option(label="r2")
            page.get_by_role(
                "button", name="Add Transfer target to Zone 1", exact=True
            ).click()
            page.get_by_role(
                "combobox", name="Zone 1 Transfer target 2 Identified by"
            ).select_option("address")
            address = page.get_by_role(
                "combobox", name="Zone 1 Transfer target 2 Address", exact=True
            )
            address.focus()
            page.keyboard.press("ArrowDown")
            choice = page.get_by_role("option", name="r2 · r2-eth0 · 10.0.0.2")
            expect(choice).to_be_visible()
            choice_box = choice.bounding_box()
            dialog_box = page.get_by_role("dialog", name="Router r1").bounding_box()
            assert choice_box is not None and dialog_box is not None
            self.assertLessEqual(
                choice_box["y"] + choice_box["height"],
                dialog_box["y"] + dialog_box["height"],
            )
            expect(
                page.get_by_role("option", name="r1 · r1-eth0 · 10.0.0.1")
            ).to_have_count(0)
            page.keyboard.press("Enter")
            expect(address).to_have_value("10.0.0.2")
            page.get_by_role(
                "button", name="Add Transfer target to Zone 1", exact=True
            ).click()
            page.get_by_role(
                "combobox", name="Zone 1 Transfer target 3 Identified by"
            ).select_option("address")
            page.get_by_role(
                "combobox", name="Zone 1 Transfer target 3 Address", exact=True
            ).fill("2001:db8::77")
            page.keyboard.press("Tab")
            page.keyboard.press("Escape")
            nodes = self._saved_draft(page)["topology"]["nodes"]
            r2 = next(node for node in nodes if node["name"] == "r2")
            self.assertEqual(
                self.saved_node(page)["dns"]["zones"][0]["allow_transfer"],
                [
                    {"node_id": r2["id"]},
                    {"address": "10.0.0.2"},
                    {"address": "2001:db8::77"},
                ],
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            ).click()
            permission.select_option("default")
            expect(
                page.get_by_role(
                    "button", name="Add Transfer target to Zone 1", exact=True
                )
            ).to_be_hidden()
            page.keyboard.press("Escape")
            self.assertNotIn("allow_transfer", self.saved_node(page)["dns"]["zones"][0])

    def test_server_record_error_reveals_its_zone_and_focuses_data(self) -> None:
        """An IPv4 refusal quotes the offending Record's value and leads back to its Data control."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        300,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "records": [
                                            {
                                                "name": "web",
                                                "type": "A",
                                                "rdata": "10.0.0.2",
                                                "ttl": 300,
                                            },
                                            {
                                                "name": "other",
                                                "type": "A",
                                                "rdata": "10.0.0.3",
                                                "ttl": 300,
                                            },
                                        ],
                                    }
                                ]
                            }
                        },
                    )
                ],
            )
            self.open_services(page)
            summary = page.locator(
                "#dns-zones-rows > .row-list > details > summary"
            ).first
            summary.click()
            data = page.get_by_role("textbox", name="Zone 1 Record 2 Data")
            data.fill("10.0.0.999")
            summary.click()
            page.keyboard.press("Escape")
            fix = page.locator("#status-draft-fix")
            expect(fix).to_contain_text("Invalid IPv4 address")
            fix.focus()
            page.keyboard.press("Enter")
            page.locator("#node-dialog-error-summary").get_by_role(
                "link", name=re.compile("Zone 1 Record 2 Data.*Invalid IPv4 address")
            ).click()
            expect(
                page.get_by_role("tab", name=re.compile("^Services"))
            ).to_have_attribute("aria-selected", "true")
            expect(data).to_be_visible()
            expect(data).to_be_focused()
            expect(data).to_have_accessible_description(
                re.compile("Invalid IPv4 address '10.0.0.999'")
            )
            expect(
                page.get_by_role("textbox", name="Zone 1 Record 1 Data")
            ).not_to_have_attribute("aria-invalid", "true")
            data.fill("10.0.0.4")
            page.keyboard.press("Escape")
            self.assertEqual(
                self.saved_node(page)["dns"]["zones"][0]["records"][1]["rdata"],
                "10.0.0.4",
            )

    def test_shipped_dns_definitions_open_and_resave_byte_identical(self) -> None:
        """Both shipped DNS topologies retain every value and optional key through a real save."""
        examples = Path(__file__).resolve().parents[2] / "examples" / "definitions"
        for example in (
            "dns-lab",
            "dnssec-lab",
        ):
            path = examples / f"{example}.json"
            shipped = path.read_bytes()
            definition = json.loads(shipped)
            with self.subTest(example), self.signed_in_page() as page:
                page.goto(self.base_url + "/authoring")
                catalog_row(page, example, example).get_by_role(
                    "button", name=re.compile("^Start from Canvas version")).click()
                page.wait_for_selector("#draft-form:not([hidden])")
                before = self._saved_draft(page)
                self.assertEqual(before["topology"], definition["topology"])
                for node in definition["topology"]["nodes"]:
                    if not node.get("dns"):
                        continue
                    self.open_services(page, node["name"])
                    for index, zone in enumerate(node["dns"]["zones"], 1):
                        page.locator(
                            "#dns-zones-rows > .row-list > details > summary"
                        ).nth(index - 1).click()
                        if zone["kind"] == "primary":
                            page.get_by_role(
                                "button",
                                name=re.compile(
                                    rf"^More settings for Zone {index}(?:[:]|$)"
                                ),
                            ).click()
                            for summary in page.locator(
                                "#dns-zones-rows details details > summary"
                            ).all():
                                summary.click()
                    page.keyboard.press("Escape")
                primary = next(
                    node
                    for node in definition["topology"]["nodes"]
                    if any(
                        z["kind"] == "primary"
                        for z in node.get("dns", {}).get("zones", [])
                    )
                )
                index = next(
                    i
                    for i, z in enumerate(primary["dns"]["zones"], 1)
                    if z["kind"] == "primary"
                )
                original = primary["dns"]["zones"][index - 1]["default_ttl"]
                self.open_services(page, primary["name"])
                page.locator("#dns-zones-rows > .row-list > details > summary").nth(
                    index - 1
                ).click()
                ttl = page.get_by_role(
                    "textbox", name=f"Zone {index} Default TTL (seconds)"
                )
                ttl.fill(str(original + 1))
                page.keyboard.press("Escape")
                self._saved_draft(page)
                self.open_services(page, primary["name"])
                page.locator("#dns-zones-rows > .row-list > details > summary").nth(
                    index - 1
                ).click()
                ttl.fill(str(original))
                page.keyboard.press("Escape")
                after = self._saved_draft(page)
                for key in ("revision", "updated_at"):
                    before.pop(key)
                    after.pop(key)
                self.assertEqual(
                    json.dumps(after, sort_keys=True).encode(),
                    json.dumps(before, sort_keys=True).encode(),
                )
                self.assertEqual(
                    json.dumps(after["topology"], sort_keys=True).encode(),
                    json.dumps(definition["topology"], sort_keys=True).encode(),
                )
                self.assertEqual(path.read_bytes(), shipped)

    def test_zone_help_explains_signing_delegation_and_transfer_behind_info(
        self,
    ) -> None:
        """Long DNS descriptions appear only when the Instructor opens the Zone information tip."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {"dns": {"zones": []}})])
            self.open_services(page)
            tip = page.get_by_role("button", name="About Zones", exact=True)
            help_text = page.locator("#dns-zones-help")
            expect(help_text).to_be_hidden()
            tip.focus()
            page.keyboard.press("Enter")
            expect(tip).to_have_attribute("aria-expanded", "true")
            expect(help_text).to_contain_text("KASP")
            expect(help_text).to_contain_text("NSEC3")
            expect(help_text).to_contain_text("signed child Primary Zone")
            expect(help_text).to_contain_text("Default permits declared Secondaries")
            expect(help_text).to_contain_text("None refuses transfers")
            page.keyboard.press("Space")
            expect(help_text).to_be_hidden()

    def test_record_ttl_error_reopens_more_and_preserves_other_records(self) -> None:
        """The error link reveals an individual TTL; correcting it leaves the Zone default alone."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        300,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "records": [
                                            {
                                                "name": "web",
                                                "type": "A",
                                                "rdata": "10.0.0.2",
                                                "ttl": 600,
                                            }
                                        ],
                                    }
                                ]
                            }
                        },
                    )
                ],
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            more = page.get_by_role(
                "button", name=re.compile("More settings for Zone 1 Record 1")
            )
            more.click()
            ttl = page.get_by_role("textbox", name="Zone 1 Record 1 TTL (seconds)")
            ttl.fill("0")
            page.keyboard.press("Tab")
            expect(ttl).to_have_accessible_description(
                "Enter whole seconds greater than 0."
            )
            more.click()
            page.keyboard.press("Escape")
            page.locator("#status-draft-fix").click()
            page.locator("#node-dialog-error-summary").get_by_role(
                "link", name=re.compile("TTL.*Enter whole seconds")
            ).click()
            expect(ttl).to_be_visible()
            expect(ttl).to_be_focused()
            expect(more).to_have_attribute("aria-expanded", "true")
            ttl.fill("900")
            page.keyboard.press("Escape")
            zone = self.saved_node(page)["dns"]["zones"][0]
            self.assertEqual(zone["default_ttl"], 300)
            self.assertEqual(
                zone["records"],
                [{"name": "web", "type": "A", "rdata": "10.0.0.2", "ttl": 900}],
            )

    def test_hovered_more_keeps_readable_text_contrast(self) -> None:
        """A pointer over Zone More keeps normal text at the AA contrast minimum."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        300,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "records": [],
                                    }
                                ]
                            }
                        },
                    )
                ],
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            more = page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            )
            page.add_style_tag(
                content="*, *::before, *::after { transition: none !important; }"
            )
            for theme in ("light", "dark"):
                page.evaluate(
                    "theme => document.documentElement.dataset.theme = theme", theme
                )
                more.hover()
                colors = more.evaluate("""element => {
                    const style = getComputedStyle(element);
                    return [style.color, style.backgroundColor];
                }""")
                luminances = []
                for color in colors:
                    channels = [
                        int(value) / 255 for value in re.findall(r"\d+", color)[:3]
                    ]
                    linear = [
                        (
                            channel / 12.92
                            if channel <= 0.04045
                            else ((channel + 0.055) / 1.055) ** 2.4
                        )
                        for channel in channels
                    ]
                    luminances.append(
                        sum(
                            channel * weight
                            for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722))
                        )
                    )
                ratio = (max(luminances) + 0.05) / (min(luminances) + 0.05)
                self.assertGreaterEqual(ratio, 4.5, theme)

    def test_transfer_address_choices_stay_open_at_the_bottom_of_a_phone_dialog(
        self,
    ) -> None:
        """ArrowDown opens a visible Interface option even when the field is near a dialog edge."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        200,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "records": [],
                                        "allow_transfer": [{"address": "10.0.0.2"}],
                                    }
                                ]
                            }
                        },
                    ),
                    ("r2", 500, 300, "router", {}),
                ],
                [("r1", "r2", {"ipv4": "10.0.0.1/24"}, {"ipv4": "10.0.0.2/24"})],
            )
            self.open_services(page)
            page.set_viewport_size({"width": 375, "height": 800})
            page.locator(
                "#dns-zones-rows > .row-list > details > summary"
            ).first.click()
            page.get_by_role(
                "button", name=re.compile(r"^More settings for Zone 1(?:[:]|$)")
            ).click()
            page.locator("#dns-zones-rows details details > summary").first.focus()
            page.keyboard.press("Enter")
            address = page.get_by_role(
                "combobox", name="Zone 1 Transfer target 1 Address", exact=True
            )
            address.focus()
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")
            page.keyboard.press("ArrowDown")
            option = page.get_by_role("option", name="r2 · r2-eth0 · 10.0.0.2")
            expect(option).to_be_visible()
            option_box = option.bounding_box()
            dialog_box = page.get_by_role("dialog", name="Router r1").bounding_box()
            assert option_box is not None and dialog_box is not None
            self.assertGreaterEqual(option_box["y"], dialog_box["y"])
            self.assertLessEqual(
                option_box["y"] + option_box["height"],
                dialog_box["y"] + dialog_box["height"],
            )
            expect(address).to_have_attribute("aria-expanded", "true")
            page.keyboard.press("Enter")
            expect(address).to_have_value("10.0.0.2")
            page.keyboard.press("Escape")
            self.assertEqual(
                self.saved_node(page)["dns"]["zones"][0]["allow_transfer"],
                [{"address": "10.0.0.2"}],
            )

    def test_required_dns_fields_have_visible_and_accessible_indicators(self) -> None:
        """Required Zone and Record fields identify their requirement to sighted and screen-reader users."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        300,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "records": [
                                            {
                                                "name": "web",
                                                "type": "A",
                                                "rdata": "10.0.0.2",
                                                "ttl": 300,
                                            }
                                        ],
                                    }
                                ]
                            }
                        },
                    )
                ],
            )
            self.open_services(page)
            page.locator("#dns-zones-rows summary").first.click()
            for label in (
                "Zone 1 Apex",
                "Zone 1 Default TTL (seconds)",
                "Zone 1 Record 1 Owner",
                "Zone 1 Record 1 Data",
            ):
                expect(page.get_by_role("textbox", name=label)).to_have_attribute(
                    "aria-required", "true"
                )
            expect(
                page.get_by_role("combobox", name="Zone 1 Record 1 Type")
            ).to_have_attribute("aria-required", "true")
            expect(page.locator("label[for='dns-zones-0-name']")).to_have_text("Apex *")
            expect(
                page.get_by_role("columnheader", name="Owner", exact=True)
            ).to_have_text("Owner *")
            expect(page.locator("#dns-zones-hint")).to_contain_text("* Required")
            expect(
                page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True)
            ).not_to_have_attribute("aria-required", "true")
            page.get_by_role(
                "button", name=re.compile("More settings for Zone 1 Record 1")
            ).click()
            expect(
                page.get_by_role("textbox", name="Zone 1 Record 1 TTL (seconds)")
            ).to_have_attribute("aria-required", "true")

    def test_duplicate_primary_and_secondary_error_focuses_editable_apex(self) -> None:
        """A Primary renamed to an existing Secondary's name receives the error on its editable Apex."""
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    (
                        "r1",
                        200,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "own.com.",
                                        "default_ttl": 300,
                                        "records": [],
                                    },
                                    {
                                        "kind": "secondary",
                                        "name": "example.com.",
                                        "primary_node_id": "@r2",
                                    },
                                ]
                            }
                        },
                    ),
                    (
                        "r2",
                        500,
                        300,
                        "router",
                        {
                            "dns": {
                                "zones": [
                                    {
                                        "kind": "primary",
                                        "name": "example.com.",
                                        "default_ttl": 300,
                                        "records": [],
                                    },
                                ]
                            }
                        },
                    ),
                ],
            )
            self.open_services(page)
            page.locator(
                "#dns-zones-rows > .row-list > details > summary"
            ).first.click()
            apex = page.get_by_role("textbox", name="Zone 1 Apex")
            apex.fill("example.com.")
            page.keyboard.press("Escape")
            page.locator("#status-draft-fix").click()
            page.locator("#node-dialog-error-summary").get_by_role(
                "link", name=re.compile("Zone 1 Apex.*hosts Zone example.com. twice")
            ).click()
            expect(apex).to_be_visible()
            expect(apex).to_be_focused()
            expect(apex).to_have_accessible_description(
                re.compile("hosts Zone example.com. twice")
            )
            apex.fill("own.com.")
            page.keyboard.press("Escape")
            self.assertEqual(
                self.saved_node(page)["dns"]["zones"][0]["name"], "own.com."
            )
