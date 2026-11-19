"""Router NAT on the canvas: the outside mark, the NAT list, refusals, renames and a Python Lab's rules drawn."""

import re
from typing import Any

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, open_details, upload_script
from mniplab.tests.browser import Page, expect

pytestmark = pytest.mark.browser_authoring

ROUTERS: list[tuple[str, int, int, str, dict[str, Any]]] = [
    ("r1", 300, 100, "router", {}), ("isp", 500, 100, "router", {}),
    ("h1", 100, 100, "host", {})]
LINKS = [("h1", "r1", {"ipv4": "192.168.1.10/24"}, {"ipv4": "192.168.1.1/24"}),
         ("r1", "isp", {"ipv4": "203.0.113.2/30"}, {"ipv4": "203.0.113.1/30"})]
OUTSIDE_LINKS = [("h1", "r1", {"ipv4": "192.168.1.10/24"}, {"ipv4": "192.168.1.1/24"}),
                 ("r1", "isp", {"ipv4": "203.0.113.2/30", "outside": True},
                  {"ipv4": "203.0.113.1/30"})]
WITH_NAT = [("r1", 300, 100, "router", {"nat": [
                {"kind": "static", "inside": "192.168.1.11", "outside": "203.0.113.5",
                 "interface": "r1-eth1"}]}),
            ("isp", 500, 100, "router", {}), ("h1", 100, 100, "host", {})]

DRAWN_NAT = (
    "from mniplab import mnIPLab, PortForward\n\n\n"
    "def build_network():\n"
    "    net = mnIPLab(topo=None, controller=None)\n"
    "    r1 = net.addRouter('r1', nat=[PortForward('tcp', 8080, '192.168.1.10', 80,"
    " hairpin=True, interface='r1-eth1')])\n"
    "    isp = net.addRouter('isp')\n"
    "    h1 = net.addHost('h1')\n"
    "    net.addLink(h1, r1, params2={'ip': '192.168.1.1/24'})\n"
    "    net.addLink(r1, isp, port1=1, params1={'ip': '203.0.113.2/30', 'nat': 'outside'},\n"
    "                params2={'ip': '203.0.113.1/30'})\n"
    "    return net\n"
)


class AuthoringRouterNATBrowserTests(AuthoringPageCase):
    """An Instructor declares a Router's outside Interface and its NAT entries on the canvas."""

    def _open_node(self, page: Page, name: str, tab: str) -> None:
        self._node(page, name).focus()
        page.keyboard.press("Enter")
        expect(page.locator("#host-dialog")).to_be_visible()
        page.locator("#host-dialog").get_by_role("tab", name=tab).click()

    def _router(self, saved: dict, name: str) -> dict:
        return next(node for node in saved["topology"]["nodes"] if node["name"] == name)

    def test_the_outside_mark_and_nat_entries_are_edited_and_survive_a_reload(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTERS, LINKS, address_family="ipv4")
            self._open_node(page, "r1", "Interfaces")
            dialog = page.locator("#host-dialog")
            mark = dialog.get_by_role("group", name="r1-eth1 to isp").get_by_label("Outside (NAT)")
            expect(mark).not_to_be_checked()
            mark.check()
            expect(mark).to_be_checked()

            dialog.get_by_role("tab", name="Routing").click()
            nat = dialog.get_by_role("group", name=re.compile("^Router NAT"))
            nat.get_by_role("button", name="Add NAT entry").click()
            port_forward = dialog.get_by_role("group", name="NAT entry 1")
            expect(port_forward.get_by_label("Kind", exact=True)).to_be_focused()
            port_forward.get_by_label("Outside port", exact=True).fill("8080")
            port_forward.get_by_label("Inside address", exact=True).fill("192.168.1.10")
            port_forward.get_by_label("Inside port", exact=True).fill("80")
            port_forward.get_by_label(re.compile("^Hairpin")).check()
            port_forward.get_by_label("Outside Interface", exact=True).select_option("r1-eth1")

            nat.get_by_role("button", name="Add NAT entry").click()
            static = dialog.get_by_role("group", name="NAT entry 2")
            static.get_by_label("Kind", exact=True).select_option("static")
            static.get_by_label("Inside address", exact=True).fill("192.168.1.11")
            static.get_by_label("Outside address", exact=True).fill("203.0.113.5")
            page.keyboard.press("Escape")

            saved = self._saved_draft(page)
            router = self._router(saved, "r1")
            self.assertEqual(router["nat"], [
                {"kind": "port_forward", "protocol": "tcp", "outside_port": 8080,
                 "inside": "192.168.1.10", "inside_port": 80, "hairpin": True,
                 "interface": "r1-eth1"},
                {"kind": "static", "inside": "192.168.1.11", "outside": "203.0.113.5"},
            ])
            outside = [end for link in saved["topology"]["links"] for end in link["ends"]
                       if end["node_id"] == router["id"] and end.get("outside")]
            self.assertEqual([end["port"] for end in outside], [1])

            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            self._open_node(page, "r1", "Routing")
            dialog = page.locator("#host-dialog")
            reloaded = dialog.get_by_role("group", name="NAT entry 1")
            expect(reloaded.get_by_label("Outside port", exact=True)).to_have_value("8080")
            expect(reloaded.get_by_label("Outside Interface", exact=True)).to_have_value("r1-eth1")
            expect(reloaded.get_by_label(re.compile("^Hairpin"))).to_be_checked()

            dialog.get_by_role("button", name="Remove NAT entry 2").click()
            expect(dialog.get_by_role("group", name="NAT entry 2")).to_have_count(0)
            expect(dialog.get_by_role("button", name="Add NAT entry")).to_be_focused()
            page.keyboard.press("Escape")
            self.assertEqual(len(self._router(self._saved_draft(page), "r1")["nat"]), 1)

    def test_an_entry_the_build_would_refuse_is_reported_on_its_row(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTERS, LINKS, address_family="ipv4")
            self._open_node(page, "r1", "Routing")
            dialog = page.locator("#host-dialog")
            dialog.get_by_role("group", name=re.compile("^Router NAT")).get_by_role(
                "button", name="Add NAT entry").click()
            dialog.get_by_role("group", name="NAT entry 1").get_by_label(
                "Inside address", exact=True).fill("192.168.1.10")
            page.keyboard.press("Escape")

            fix = page.get_by_role("button", name=re.compile("needs an outside Interface"))
            expect(fix).to_be_visible(timeout=15000)
            fix.click()
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_contain_text("Routing › Outside Interface")
            summary.get_by_role("link").click()
            interface = dialog.get_by_label("Outside Interface", exact=True)
            expect(interface).to_be_focused()
            expect(interface).to_have_accessible_description(re.compile("needs an outside Interface"))

    def test_renaming_a_router_keeps_its_entries_on_its_renamed_interfaces(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, WITH_NAT, OUTSIDE_LINKS, address_family="ipv4")
            self._open_node(page, "r1", "General")
            dialog = page.locator("#host-dialog")
            name = dialog.get_by_label("Node name", exact=False)
            name.fill("edge")
            page.keyboard.press("Escape")

            saved = self._saved_draft(page)
            self.assertEqual(self._router(saved, "edge")["nat"][0]["interface"], "edge-eth1")

    def test_a_python_routers_nat_is_drawn_in_the_selection(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, ROUTERS, LINKS, address_family="ipv4")
            upload_script(page, "lab.py", DRAWN_NAT)
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            open_details(page)

            router = page.get_by_role("button", name=re.compile(r"^Router r1, read-only, at x"))
            expect(router).to_be_visible()
            router.focus()
            selection = page.get_by_role("region", name="Selection")
            expect(selection).to_contain_text("Outside Interfaces")
            expect(selection).to_contain_text("r1-eth1")
            expect(selection).to_contain_text(
                "Port forward tcp 8080 to 192.168.1.10:80, hairpin on r1-eth1")
