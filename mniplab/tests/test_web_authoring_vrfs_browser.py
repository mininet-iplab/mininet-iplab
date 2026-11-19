"""A Router's VRFs in its dialog, and each Interface's VRF on its Interfaces tab."""

import re
from typing import Any

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase
from mniplab.tests.browser import Page, expect

pytestmark = pytest.mark.browser_authoring

ROUTERS: list[tuple[str, int, int, str, dict[str, Any]]] = [
    ("r1", 300, 100, "router", {}), ("r2", 500, 100, "router", {}),
    ("h1", 100, 100, "host", {})]
LINKS = [("r1", "r2"), ("h1", "r1")]
IN_VRF = [("r1", 300, 100, "router", {"vrfs": [{"name": "vrf-ext", "table": 10}]}),
          ("r2", 500, 100, "router", {}), ("h1", 100, 100, "host", {})]
IN_VRF_LINKS = [("r1", "r2", {"vrf": "vrf-ext"}, {}), ("h1", "r1", {}, {"vrf": "vrf-ext"})]


class AuthoringVRFBrowserTests(AuthoringPageCase):
    """An Instructor authors a VRF lesson on the canvas."""

    def _open_node(self, page: Page, name: str, tab: str) -> None:
        self._node(page, name).focus()
        page.keyboard.press("Enter")
        expect(page.locator("#host-dialog")).to_be_visible()
        page.locator("#host-dialog").get_by_role("tab", name=tab).click()

    def _router(self, saved: dict, name: str) -> dict:
        return next(node for node in saved["topology"]["nodes"] if node["name"] == name)

    def test_vrf_rows_and_the_interfaces_vrf_are_edited_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTERS, LINKS, address_family="ipv4")
            self._open_node(page, "r1", "Interfaces")
            dialog = page.locator("#host-dialog")
            # With no VRF on the Router there is nothing to choose.
            expect(dialog.get_by_role("combobox", name="VRF")).to_have_count(0)

            dialog.get_by_role("tab", name="Routing").click()
            vrfs = dialog.get_by_role("group", name=re.compile("^VRFs"))
            vrfs.get_by_role("button", name="Add VRF").focus()
            page.keyboard.press("Enter")
            name = vrfs.get_by_label("VRF name")
            expect(name).to_be_focused()
            page.keyboard.type("vrf-ext")
            page.keyboard.press("Tab")
            expect(vrfs.get_by_label("Table ID")).to_be_focused()
            page.keyboard.press("Control+A")
            page.keyboard.type("10")

            dialog.get_by_role("tab", name="Interfaces").click()
            toward_r2 = dialog.get_by_role("group", name="r1-eth0 to r2")
            choice = toward_r2.get_by_role("combobox", name="VRF")
            expect(choice.locator("option")).to_have_text(["Default table", "vrf-ext"])
            expect(choice).to_have_value("")
            choice.focus()
            choice.select_option("vrf-ext")
            # Host Interfaces never take a VRF.
            page.keyboard.press("Escape")
            self._open_node(page, "h1", "Interfaces")
            expect(dialog.get_by_role("combobox", name="VRF")).to_have_count(0)
            page.keyboard.press("Escape")

            saved = self._saved_draft(page)
            router = self._router(saved, "r1")
            self.assertEqual(router["vrfs"], [{"name": "vrf-ext", "table": 10}])
            vrf_by_port = {end["port"]: end.get("vrf") for link in saved["topology"]["links"]
                           for end in link["ends"] if end["node_id"] == router["id"]}
            self.assertEqual(vrf_by_port, {0: "vrf-ext", 1: None})

    def test_removing_a_vrf_in_use_names_its_interfaces_and_renaming_carries_them(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, IN_VRF, IN_VRF_LINKS, address_family="ipv4")
            self._open_node(page, "r1", "Routing")
            dialog = page.locator("#host-dialog")
            vrfs = dialog.get_by_role("group", name=re.compile("^VRFs"))
            vrfs.get_by_role("button", name="Remove VRF vrf-ext").click()
            name = vrfs.get_by_label("VRF name")
            expect(name).to_have_value("vrf-ext")
            expect(name).to_have_accessible_description(
                re.compile("r1-eth0, r1-eth1.*Interfaces tab"))

            # Clearing the name on the way to a new one keeps its Interfaces.
            name.fill("")
            name.fill("ext & co")
            expect(vrfs.get_by_role("button", name="Remove VRF ext & co")).to_be_visible()
            name.fill("ext")
            dialog.get_by_role("tab", name="Interfaces").click()
            for peer in ("r2", "h1"):
                expect(dialog.get_by_role("group", name=re.compile(f"to {peer}$"))
                       .get_by_role("combobox", name="VRF")).to_have_value("ext")

            dialog.get_by_role("group", name="r1-eth0 to r2").get_by_role(
                "combobox", name="VRF").select_option("")
            dialog.get_by_role("group", name="r1-eth1 to h1").get_by_role(
                "combobox", name="VRF").select_option("")
            dialog.get_by_role("tab", name="Routing").click()
            vrfs.get_by_role("button", name="Remove VRF ext").click()
            expect(vrfs.get_by_label("VRF name")).to_have_count(0)
            expect(vrfs.get_by_role("button", name="Add VRF")).to_be_focused()
            page.keyboard.press("Escape")

            saved = self._saved_draft(page)
            self.assertEqual(self._router(saved, "r1")["vrfs"], [])
            self.assertFalse([end for link in saved["topology"]["links"]
                              for end in link["ends"] if "vrf" in end])

    def test_an_invalid_vrf_is_reported_on_its_row(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, IN_VRF, IN_VRF_LINKS, address_family="ipv4")
            self._open_node(page, "r1", "Routing")
            dialog = page.locator("#host-dialog")
            dialog.get_by_role("group", name=re.compile("^VRFs")).get_by_label("Table ID").fill("254")
            page.keyboard.press("Escape")
            fix = page.get_by_role("button", name=re.compile("Enter a table ID"))
            expect(fix).to_be_visible(timeout=15000)
            fix.click()
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_contain_text("Routing › Table ID")
            summary.get_by_role("link").click()
            expect(dialog.get_by_role("tab", name="Routing")).to_have_attribute("aria-selected", "true")
            table = dialog.get_by_label("Table ID")
            expect(table).to_be_focused()
            expect(table).to_have_accessible_description(re.compile("253, 254 and 255"))
