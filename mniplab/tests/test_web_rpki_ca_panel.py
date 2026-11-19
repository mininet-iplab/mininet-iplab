"""Browser tests for the RPKI CA panel in the Web UI.

The panel is opened in a live page and its fetches are routed to a stand-in
for the RPKI CA's Lab API (``page.route``), which keeps the ROAs and ASPAs and
answers the way the Lab does: the listing, each committed change, and a 400
refusal when one is asked for. What is asserted is what a Learner sees and can
do: one Hosted CA at a time, chosen from the picker, with its rows; an add
form that starts filled with the Hosted CA's own prefix and AS; adding,
editing and removing a ROA or an ASPA; and a refusal named inline beside the
form that made it.

Follows the seam in ``test_web_dnssec_panel.py``: the panel module is imported
into the page and its entry point called, rather than clicking a badge on a
Topology that would need a running Lab.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pytest

from mniplab.tests.browser import LiveServerCase, Page, Route, expect

pytestmark = pytest.mark.browser_services

PREFIXES = {65002: "10.1.0.0/24", 65003: "10.3.0.0/24"}


def _asn(text: str) -> int:
    """Read an ASN as the page writes it: ``65002`` or ``AS65002``."""
    return int(text.upper().removeprefix("AS"))


class KrillStandIn:
    """The RPKI CA behind the page's API: each Hosted CA's ROAs and ASPAs.

    Attributes:
        roas: The ROAs signed at each Hosted CA, by ASN.
        aspas: The ASPAs signed at each Hosted CA, by ASN.
        requests: Every change the page asked for, in order, as its method,
            Hosted CA ASN, kind (``roa`` or ``aspa``) and JSON body.
        refusal: The detail the next change is refused with, if one is set;
            it is cleared once used.
    """

    def __init__(self) -> None:
        self.roas: Dict[int, List[Dict[str, Any]]] = {
            65002: [{"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002}],
            65003: [],
        }
        self.aspas: Dict[int, List[Dict[str, Any]]] = {
            65002: [{"customer": 65002, "providers": [65001, 65005]}],
            65003: [],
        }
        self.requests: List[Dict[str, Any]] = []
        self.refusal: Optional[str] = None

    def listing(self) -> Dict[str, Any]:
        """Return the Lab's answer to the listing: both Hosted CAs with their objects."""
        return {"node": "ca", "cas": [
            {
                "asn": asn,
                "handle": f"as{asn}",
                "prefixes": [PREFIXES[asn]],
                "roas": self.roas[asn],
                "aspas": self.aspas[asn],
            }
            for asn in (65002, 65003)
        ]}

    def answer(self, route: Route) -> None:
        """Answer one request the page makes to the RPKI CA's API."""
        request = route.request
        if request.method == "GET":
            self._reply(route, 200, self.listing())
            return

        path = urlparse(request.url).path
        asn = int(path.split("/cas/")[1].split("/")[0])
        kind = "roa" if path.endswith("/roas") else "aspa"
        body = json.loads(request.post_data) if request.post_data else {}
        self.requests.append({"method": request.method, "asn": asn, "kind": kind, "body": body})

        if self.refusal is not None:
            detail, self.refusal = self.refusal, None
            self._reply(route, 400, {"detail": detail})
            return
        if kind == "roa":
            self._change_roa(request.method, asn, body)
        else:
            self._change_aspa(request.method, asn, body)
        self._reply(route, 200, {"node": "ca", "asn": asn})

    def _change_roa(self, method: str, asn: int, body: Dict[str, Any]) -> None:
        """Apply one ROA change: an add, an edit (which carries ``old``), or a removal."""
        if method == "DELETE":
            self._drop_roa(asn, body)
            return
        if "old" in body:
            self._drop_roa(asn, body["old"])
        max_length = body.get("max_length", int(body["prefix"].split("/")[1]))
        self.roas[asn].append({
            "prefix": body["prefix"],
            "max_length": max_length,
            "origin": _asn(str(body["origin"])),
        })

    def _drop_roa(self, asn: int, body: Dict[str, Any]) -> None:
        self.roas[asn] = [
            roa for roa in self.roas[asn]
            if (roa["prefix"], roa["max_length"], roa["origin"])
            != (body["prefix"], body["max_length"], _asn(str(body["origin"])))
        ]

    def _change_aspa(self, method: str, asn: int, body: Dict[str, Any]) -> None:
        """Apply one ASPA change: sign one for the customer, or withdraw it."""
        customer = _asn(str(body["customer"]))
        self.aspas[asn] = [aspa for aspa in self.aspas[asn] if aspa["customer"] != customer]
        if method == "POST":
            providers = [_asn(str(provider)) for provider in body["providers"]]
            self.aspas[asn].append({"customer": customer, "providers": providers})

    @staticmethod
    def _reply(route: Route, status: int, payload: Dict[str, Any]) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


class RPKICAPanelTests(LiveServerCase):
    """Opens the RPKI CA panel in a live page and drives it against a KrillStandIn."""

    def _open(self, page: Page, krill: KrillStandIn) -> None:
        """Route the RPKI CA's API to ``krill``, open the panel on the RPKI CA Node, and wait for its rows."""
        page.route("**/api/labs/fake/rpki/**", krill.answer)
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            appState.labKey = 'fake';
            const panel = await import('/static/js/rpki/ca-panel.js');
            panel.openRpkiCaPanel('ca');
        }""")
        expect(page.locator("#rpki-65002-roa-tbody tr")).to_have_count(1)

    def test_the_picker_shows_one_hosted_ca_at_a_time_with_its_roas_and_aspas(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            expect(page.locator("#rpki-ca-select option")).to_have_text(["AS65002", "AS65003"])
            expect(page.locator("#rpki-ca-select")).to_have_value("65002")
            expect(page.locator("#rpki-cas section[data-asn]:visible")).to_have_count(1)
            expect(page.locator("#rpki-65002-heading")).to_have_text("AS65002")
            expect(page.locator("#rpki-65002-resources")).to_have_text("Resources: 10.1.0.0/24")
            expect(page.locator("#rpki-65002-roa-tbody")).to_contain_text("10.1.0.0/24")
            expect(page.locator("#rpki-65002-roa-tbody")).to_contain_text("AS65002")
            expect(page.locator("#rpki-65002-aspa-tbody")).to_contain_text("AS65001, AS65005")
            expect(page.locator("#rpki-65003-heading")).to_be_hidden()

            page.select_option("#rpki-ca-select", "65003")

            expect(page.locator("#rpki-65002-heading")).to_be_hidden()
            expect(page.locator("#rpki-65003-heading")).to_be_visible()
            expect(page.locator("#rpki-65003-roa-empty")).to_be_visible()
            expect(page.locator("#rpki-65003-roa-table")).to_be_hidden()
            expect(page.locator("#rpki-65003-aspa-empty")).to_be_visible()

    def test_the_roa_form_starts_with_the_held_prefix_and_own_as_and_says_what_an_empty_max_length_means(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            expect(page.locator("#rpki-65002-roa-prefix")).to_have_value("10.1.0.0/24")
            expect(page.locator("#rpki-65002-roa-origin")).to_have_value("AS65002")
            expect(page.locator("#rpki-65002-roa-max")).to_have_value("")
            expect(page.locator("#rpki-65002-roa-max")).to_have_attribute("aria-describedby", "rpki-65002-roa-max-hint")
            expect(page.locator("#rpki-65002-roa-max-hint")).to_contain_text("Leave maxLength empty")

    def test_adding_a_roa_shows_its_row_and_resets_the_form(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            page.fill("#rpki-65002-roa-prefix", "10.1.0.0/24")
            page.fill("#rpki-65002-roa-max", "25")
            page.fill("#rpki-65002-roa-origin", "AS65002")
            page.click("#rpki-65002-roa-submit")

            expect(page.locator("#rpki-65002-roa-tbody tr")).to_have_count(2)
            expect(page.locator("#rpki-65002-roa-tbody")).to_contain_text("25")
            expect(page.locator("#rpki-65002-roa-prefix")).to_have_value("10.1.0.0/24")
            expect(page.locator("#rpki-65002-roa-max")).to_have_value("")
            expect(page.locator("#rpki-65002-roa-prefix")).to_be_focused()
            self.assertEqual(krill.requests[-1]["body"], {
                "prefix": "10.1.0.0/24", "max_length": 25, "origin": "AS65002",
            })

    def test_removing_a_roa_removes_its_row_and_moves_focus_to_the_form(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            page.get_by_role("button", name="Remove ROA 10.1.0.0/24-24 => AS65002").click()

            expect(page.locator("#rpki-65002-roa-tbody tr")).to_have_count(0)
            expect(page.locator("#rpki-65002-roa-empty")).to_be_visible()
            expect(page.locator("#rpki-65002-roa-prefix")).to_be_focused()
            self.assertEqual(krill.requests[-1]["method"], "DELETE")

    def test_editing_a_roa_changes_its_max_length_in_one_change(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            page.get_by_role("button", name="Edit ROA 10.1.0.0/24-24 => AS65002").click()
            expect(page.locator("#rpki-65002-roa-legend")).to_have_text("Edit ROA at AS65002")
            expect(page.locator("#rpki-65002-roa-prefix")).to_have_value("10.1.0.0/24")
            expect(page.locator("#rpki-65002-roa-max")).to_have_value("24")
            page.fill("#rpki-65002-roa-max", "25")
            page.click("#rpki-65002-roa-submit")

            expect(page.locator("#rpki-65002-roa-tbody tr")).to_have_count(1)
            expect(page.locator("#rpki-65002-roa-tbody")).to_contain_text("25")
            self.assertEqual(krill.requests[-1]["body"]["old"], {
                "prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002,
            })

    def test_a_refusal_is_shown_inline_naming_the_prefix(self) -> None:
        krill = KrillStandIn()
        krill.refusal = "Hosted CA AS65002 does not hold 10.9.0.0/24, so it cannot sign a ROA for it"
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            page.fill("#rpki-65002-roa-prefix", "10.9.0.0/24")
            page.fill("#rpki-65002-roa-origin", "AS65002")
            page.click("#rpki-65002-roa-submit")

            alert = page.locator("#rpki-65002-roa-error")
            expect(alert).to_be_visible()
            expect(alert).to_have_attribute("role", "alert")
            expect(alert).to_contain_text("10.9.0.0/24")
            expect(page.locator("#rpki-65002-roa-prefix")).to_have_value("10.9.0.0/24")
            expect(page.locator("#rpki-65002-roa-tbody tr")).to_have_count(1)

    def test_an_origin_that_is_not_an_as_number_is_marked_before_it_is_sent(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            page.fill("#rpki-65002-roa-prefix", "10.1.0.0/24")
            page.fill("#rpki-65002-roa-origin", "Ops")
            page.click("#rpki-65002-roa-submit")

            expect(page.locator("#rpki-65002-roa-error")).to_contain_text("Origin must be an AS number")
            expect(page.locator("#rpki-65002-roa-origin")).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#rpki-65002-roa-origin")).to_be_focused()
            self.assertEqual(krill.requests, [])

    def test_a_max_length_error_keeps_its_hint_described(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            page.fill("#rpki-65002-roa-max", "long")
            page.click("#rpki-65002-roa-submit")

            expect(page.locator("#rpki-65002-roa-max")).to_have_attribute(
                "aria-describedby", "rpki-65002-roa-error rpki-65002-roa-max-hint")
            self.assertEqual(krill.requests, [])

            page.fill("#rpki-65002-roa-max", "")
            page.click("#rpki-65002-roa-submit")

            expect(page.locator("#rpki-65002-roa-max")).to_have_attribute("aria-describedby", "rpki-65002-roa-max-hint")

    def test_an_aspa_can_be_added_and_removed(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)
            page.select_option("#rpki-ca-select", "65003")

            page.fill("#rpki-65003-aspa-providers", "AS65001")
            page.click("#rpki-65003-aspa-submit")
            expect(page.locator("#rpki-65003-aspa-tbody tr")).to_have_count(1)
            expect(page.locator("#rpki-65003-aspa-tbody")).to_contain_text("AS65001")
            self.assertEqual(krill.requests[-1]["body"], {"customer": 65003, "providers": ["AS65001"]})

            page.get_by_role("button", name="Remove ASPA AS65003 => AS65001").click()
            expect(page.locator("#rpki-65003-aspa-tbody tr")).to_have_count(0)
            expect(page.locator("#rpki-65003-aspa-empty")).to_be_visible()

    def test_escape_closes_the_panel(self) -> None:
        krill = KrillStandIn()
        with self.signed_in_page(wait_for_topology=False) as page:
            self._open(page, krill)

            page.keyboard.press("Escape")

            expect(page.locator("#rpki-modal")).to_be_hidden()
