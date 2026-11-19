"""Browser tests for the Validator panel in the Web UI.

The panel is opened in a live page as a Learner, and its fetches are routed to
a stand-in for the Validator's Lab API (``page.route``), which answers with the
VRPs, ASPAs, refresh times and connected Routers the Validator serves, or with a
503 when Routinator is not answering. What is asserted is what a Learner sees
and can do: the VRP and ASPA tables, filtering a long VRP list by prefix and
by origin ASN, when the Validator fetched and fetches next, the connected
Routers, and that nothing on the panel writes to the Lab.

Follows the seam in ``test_web_rpki_ca_panel.py``: the panel module is imported
into the page and its entry point called, rather than clicking a badge on a
Topology that would need a running Lab.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from mniplab.tests.browser import LiveServerCase, Page, Route, expect

pytestmark = pytest.mark.browser_services

VRPS = [
    {"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002},
    {"prefix": "10.3.0.0/24", "max_length": 25, "origin": 65003},
    {"prefix": "2001:db8:1::/48", "max_length": 48, "origin": 65003},
]
# Enough VRPs that the panel offers its filter.
MANY_VRPS = VRPS + [
    {"prefix": f"198.51.{i}.0/24", "max_length": 24, "origin": 65010} for i in range(9)
]
ASPAS = [{"customer": 65002, "providers": [65001, 65005]}]


class RoutinatorStandIn:
    """The Validator behind the page's API: what Routinator serves, and whether it answers.

    Attributes:
        vrps: The VRPs Routinator serves.
        routers: The connected Routers' names, as the Lab reports them.
        refusal: The detail every read is refused with while it is set, as a
            503 for a Validator that is not answering; None when it answers.
        requests: How many reads the page has made, so a test can wait on a poll.
    """

    def __init__(self) -> None:
        self.vrps: List[Dict[str, Any]] = VRPS
        self.routers: List[str] = ["r1"]
        self.refusal: Optional[str] = None
        self.requests = 0

    def payload(self) -> Dict[str, Any]:
        """Return the Lab's answer to a read of the Validator: it fetched 6 s ago, and fetches again in 4 s."""
        now = datetime.now(timezone.utc)
        return {
            "node": "rv",
            "vrps": self.vrps,
            "aspas": ASPAS,
            "refresh": 10,
            "last_refresh": (now - timedelta(seconds=6)).isoformat(),
            "next_refresh": (now + timedelta(seconds=4)).isoformat(),
            "routers": self.routers,
        }

    def answer(self, route: Route) -> None:
        """Answer one request the page makes to the Validator's API."""
        if route.request.method != "GET":
            route.fulfill(status=405, content_type="application/json", body=json.dumps({"detail": "Method Not Allowed"}))
            return
        self.requests += 1
        if self.refusal is not None:
            route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": self.refusal}))
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.payload()))


class ValidatorPanelTests(LiveServerCase):
    """Opens the Validator panel in a live page as a Learner and drives it against a RoutinatorStandIn."""

    def _open(self, page: Page, validator: RoutinatorStandIn) -> None:
        """Route the Validator's API to ``validator``, open its panel, and wait for its rows."""
        page.route("**/api/labs/fake/rpki/**", validator.answer)
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            appState.labKey = 'fake';
            const panel = await import('/static/js/rpki/validator-panel.js');
            panel.openValidatorPanel('rv');
        }""")
        expect(page.locator("#validator-vrp-tbody tr")).to_have_count(len(validator.vrps))

    def test_the_vrp_table_lists_each_vrp_and_the_aspa_table_each_aspa(self) -> None:
        validator = RoutinatorStandIn()
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            expect(page.locator("#validator-vrp-tbody")).to_contain_text("10.1.0.0/24")
            expect(page.locator("#validator-vrp-tbody")).to_contain_text("AS65002")
            expect(page.locator("#validator-vrp-tbody")).to_contain_text("2001:db8:1::/48")
            expect(page.locator("#validator-aspa-tbody tr")).to_have_count(1)
            expect(page.locator("#validator-aspa-tbody")).to_contain_text("AS65002")
            expect(page.locator("#validator-aspa-tbody")).to_contain_text("AS65001, AS65005")
            expect(page.locator("#validator-vrp-count")).to_have_text("3 VRPs")

    def test_the_filter_shows_only_for_a_long_list(self) -> None:
        validator = RoutinatorStandIn()
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            expect(page.locator("#validator-filter")).to_be_hidden()

    def test_no_vrps_says_where_they_come_from_and_hides_the_table(self) -> None:
        validator = RoutinatorStandIn()
        validator.vrps = []
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            expect(page.locator("#validator-vrp-empty")).to_have_text(
                "No VRPs yet. Add a ROA at the RPKI CA, and it shows here after the next fetch.")
            expect(page.locator("#validator-vrp-table")).to_be_hidden()
            expect(page.locator("#validator-vrp-count")).to_have_text("")

    def test_filtering_by_prefix_narrows_the_rows_and_updates_the_count(self) -> None:
        validator = RoutinatorStandIn()
        validator.vrps = MANY_VRPS
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            page.fill("#validator-filter", "10.1.0")

            expect(page.locator("#validator-vrp-tbody tr")).to_have_count(1)
            expect(page.locator("#validator-vrp-tbody")).to_contain_text("10.1.0.0/24")
            expect(page.locator("#validator-vrp-count")).to_have_text("Showing 1 of 12 VRPs")

            page.fill("#validator-filter", "")

            expect(page.locator("#validator-vrp-tbody tr")).to_have_count(12)
            expect(page.locator("#validator-vrp-count")).to_have_text("12 VRPs")

    def test_filtering_by_origin_asn_matches_with_or_without_the_as_prefix(self) -> None:
        validator = RoutinatorStandIn()
        validator.vrps = MANY_VRPS
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            page.fill("#validator-filter", "AS65003")
            expect(page.locator("#validator-vrp-tbody tr")).to_have_count(2)
            expect(page.locator("#validator-vrp-count")).to_have_text("Showing 2 of 12 VRPs")

            page.fill("#validator-filter", "65002")
            expect(page.locator("#validator-vrp-tbody tr")).to_have_count(1)
            expect(page.locator("#validator-vrp-tbody")).to_contain_text("10.1.0.0/24")

    def test_a_filter_that_matches_nothing_says_so(self) -> None:
        validator = RoutinatorStandIn()
        validator.vrps = MANY_VRPS
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            page.fill("#validator-filter", "203.0.113")

            expect(page.locator("#validator-vrp-tbody tr")).to_have_count(0)
            expect(page.locator("#validator-vrp-empty")).to_be_visible()
            expect(page.locator("#validator-vrp-empty")).to_have_text("No VRP matches this filter.")
            expect(page.locator("#validator-vrp-count")).to_have_text("Showing 0 of 12 VRPs")

    def test_the_status_shows_the_fetch_times_and_the_connected_routers(self) -> None:
        validator = RoutinatorStandIn()
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            expect(page.locator("#validator-last-refresh")).to_have_text(re.compile(r"^\d+ s ago$"))
            expect(page.locator("#validator-last-refresh")).to_have_attribute("title", re.compile(r"\d"))
            expect(page.locator("#validator-next-refresh")).to_have_text(re.compile(r"^(in \d+ s|due now)$"))
            expect(page.locator("#validator-routers li")).to_have_text(["r1"])
            expect(page.locator("#validator-routers-empty")).to_be_hidden()

    def test_no_connected_router_reads_none(self) -> None:
        validator = RoutinatorStandIn()
        validator.routers = []
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            expect(page.locator("#validator-routers li")).to_have_count(0)
            expect(page.locator("#validator-routers-empty")).to_be_visible()
            expect(page.locator("#validator-routers-empty")).to_have_text("none")

    def test_the_panel_offers_no_write_control(self) -> None:
        validator = RoutinatorStandIn()
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            expect(page.locator("#validator-modal form")).to_have_count(0)
            expect(page.locator("#validator-modal button")).to_have_count(1)
            expect(page.locator("#validator-modal button")).to_have_attribute("id", "validator-modal-close")
            expect(page.locator("#validator-modal input:not([type='checkbox'])")).to_have_count(1)
            expect(page.locator("#validator-modal input:not([type='checkbox'])")).to_have_attribute("id", "validator-filter")

    def test_a_validator_that_is_not_answering_is_named_in_the_status(self) -> None:
        validator = RoutinatorStandIn()
        validator.refusal = "The Validator is not answering: no JSON for /api/v1/status"
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            page.route("**/api/labs/fake/rpki/**", validator.answer)
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.labKey = 'fake';
                const panel = await import('/static/js/rpki/validator-panel.js');
                panel.openValidatorPanel('rv');
            }""")

            status = page.locator("#validator-status")
            expect(status).to_contain_text("The Validator is not answering")
            expect(status).to_have_class(re.compile(r"validator-status-error"))
            expect(page.locator("#validator-vrp-tbody tr")).to_have_count(0)

    def test_opening_focuses_close_not_the_filter(self) -> None:
        validator = RoutinatorStandIn()
        validator.vrps = MANY_VRPS
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            expect(page.locator("#validator-modal-close")).to_be_focused()

    def test_escape_closes_the_panel(self) -> None:
        validator = RoutinatorStandIn()
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            self._open(page, validator)

            page.keyboard.press("Escape")

            expect(page.locator("#validator-modal")).to_be_hidden()
