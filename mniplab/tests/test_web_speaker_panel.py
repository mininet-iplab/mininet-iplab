"""Browser tests for the Speaker panel in the Web UI.

Follows the pattern from ``test_web_dhcp_relay_panel.py``: an ephemeral
uvicorn server, a page signed in as a *learner*, a synthetic Topology on the
canvas, and ``GET /api/speakers/{node}`` faked with ``page.route`` (a real
Speaker needs root, which these tests deliberately do not stand up).

The Speaker panel is read-only:
- A Speaker's node context menu offers a "Speaker" entry; a Host, Router or
  Container Host's does not, and the entry is ``aria-hidden`` there.
- Clicking the Speaker itself still opens a Terminal.
- Summary first, then details: each Neighbor's header carries state, uptime
  and route counts; the announced and received tables open from
  "Announced: N prefixes" / "Received: N prefixes"; each route row shows
  prefix, next hop and AS-PATH with the remaining attributes behind a
  per-row toggle.
- A Neighbor whose session is down reads as down, with the routes announced
  to it shown beside that state; the next poll picks up a change.
- A failed read shows the service's reason, never a blank panel.
- The poll starts on open and stops on close.
- The panel is a labelled dialog with labelled regions, a polite live region
  for the session summary, a status region for routes landing, and focus that
  returns to the Node on close.

And ``04-announce-and-withdraw.md``:
- The announce form's controls are labelled; the Neighbor selector offers
  every Neighbor and "all"; the next-hop pre-fills with the chosen
  Neighbor's local address and re-fills when the selection changes.
- An announce posts the CLI's field set, reports its result in the live
  region and re-reads the Speaker; a refusal or rejection is shown with its
  reason.
- Every announced row carries a withdraw control; "withdraw all" asks for a
  confirmation that mentions the Lab Example's routes, and only a confirmed
  one posts.

And ``05-generate-routes.md``:
- The generate form's controls are labelled; a burst posts the CLI's
  options, shows the prefixes it announced and says so in the live region;
  a refused burst is shown with its reason.
"""

import contextlib
import json
import re
import unittest
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from mniplab.tests.browser import LiveServerCase, Page, Route

INDEX_HTML = Path(__file__).resolve().parents[1] / "webserver" / "static" / "index.html"

AUTHORED_LAYOUT = {
    "nodes": {
        "sp1": {"x": 200, "y": 200},
        "r1": {"x": 400, "y": 200},
        "h1": {"x": 600, "y": 200},
        "c1": {"x": 800, "y": 200},
    }
}

TOPOLOGY = {
    "nodes": [
        {"id": "sp1", "type": "bgp-speaker", "label": "sp1", "ip": "10.0.1.2", "services": [], "relays": []},
        {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.1.1", "services": [], "relays": []},
        {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.2.1", "services": [], "relays": []},
        {"id": "c1", "type": "container", "label": "c1", "ip": "10.0.3.1", "services": [], "relays": []},
    ],
    "links": [
        {"source": "sp1", "target": "r1", "source_intf": "sp1-eth0", "target_intf": "r1-eth0", "status": "up"},
    ],
}

V4_NEIGHBOR = {
    "address": "10.0.1.1",
    "peer_as": 65000,
    "local_address": "10.0.1.2",
    "local_as": 65001,
    "family": "ipv4",
    "state": "established",
    "up": True,
    "uptime": 95,
    "announced": [
        {
            "prefix": "203.0.113.0/24",
            "next_hop": "10.0.1.2",
            "community": ["65001:100"],
            "as_path": [65001],
            "large_community": ["65001:1:2"],
            "med": 50,
        },
    ],
    "received": [
        {
            "prefix": "198.51.100.0/24",
            "next_hop": "10.0.1.1",
            "community": [],
            "as_path": [65000],
            "large_community": [],
            "med": 0,
        },
    ],
}

V6_NEIGHBOR_DOWN = {
    "address": "2001:db8:1::1",
    "peer_as": 65000,
    "local_address": "2001:db8:1::2",
    "local_as": 65001,
    "family": "ipv6",
    "state": "idle",
    "up": False,
    "uptime": None,
    "announced": [
        {
            "prefix": "2001:db8:100::/48",
            "next_hop": "2001:db8:1::2",
            "community": [],
            "as_path": [65001],
            "large_community": [],
            "med": None,
        },
    ],
    "received": [],
}


def speaker_payload(*neighbors: dict[str, Any]) -> str:
    """The body ``GET /api/speakers/sp1`` answers with the given Neighbors."""
    return json.dumps({"node": "sp1", "local_as": 65001, "neighbors": list(neighbors)})


def test_the_announce_form_ships_with_every_control_labelled() -> None:
    """The static markup labels every announce control by ``for``/``id``,
    so the form is operable before any script runs."""
    html = INDEX_HTML.read_text()
    form = re.search(r'<form id="speaker-announce-form"(.*?)</form>', html, re.S)
    assert form, "index.html has no <form id=\"speaker-announce-form\">"
    controls = re.findall(r'<(?:input|select)[^>]*\bid="([^"]+)"', form.group(1))
    assert {"speaker-announce-neighbor", "speaker-announce-prefix", "speaker-announce-next-hop",
            "speaker-announce-as-path", "speaker-announce-community",
            "speaker-announce-large-community", "speaker-announce-med"} <= set(controls)
    for control in controls:
        assert re.search(rf'<label for="{control}"', form.group(1)), f"{control} has no label"
    assert re.search(r'<button[^>]*type="submit"', form.group(1))
    # A refused write is announced through an alert region beside the form.
    assert re.search(r'id="speaker-write-error"[^>]*role="alert"', html)
    # The required fields are marked for assistive technology (`required`)
    # and for the eye (an asterisk the screen reader skips), with one note
    # explaining the mark — WCAG 3.3.2, never colour alone.
    assert_required_marked(form.group(1), ["speaker-announce-prefix", "speaker-announce-next-hop"])
    assert "Fields marked * are required" in form.group(1)


def assert_required_marked(form_html: str, ids: list[str]) -> None:
    """Assert that exactly ``ids`` are the form's required controls, each
    carrying ``required`` and an ``aria-hidden`` asterisk in its label."""
    required = re.findall(r'<(?:input|select)[^>]*\bid="([^"]+)"[^>]*\brequired\b', form_html)
    assert sorted(required) == sorted(ids), required
    for control in ids:
        label = re.search(rf'<label for="{control}">(.*?)</label>', form_html, re.S)
        assert label and re.search(r'<span[^>]*aria-hidden="true"[^>]*>\*</span>', label.group(1)), control


def test_the_generate_form_ships_with_every_control_labelled() -> None:
    html = INDEX_HTML.read_text()
    form = re.search(r'<form id="speaker-generate-form"(.*?)</form>', html, re.S)
    assert form, "index.html has no <form id=\"speaker-generate-form\">"
    controls = re.findall(r'<(?:input|select)[^>]*\bid="([^"]+)"', form.group(1))
    assert {"speaker-generate-neighbor", "speaker-generate-count", "speaker-generate-sub-prefixes",
            "speaker-generate-prefix-seed", "speaker-generate-as-path-seed"} <= set(controls)
    for control in controls:
        assert re.search(rf'<label for="{control}"', form.group(1)), f"{control} has no label"
    assert re.search(r'<button[^>]*type="submit"', form.group(1))
    # The prefixes a burst announced are listed in a labelled region.
    assert re.search(r'id="speaker-generate-result"[^>]*aria-labelledby="speaker-generate-result-heading"', html)
    assert_required_marked(form.group(1), ["speaker-generate-count"])
    assert "Fields marked * are required" in form.group(1)


def test_the_context_menu_ships_its_speaker_entry_hidden() -> None:
    """The static markup carries the entry hidden, so a Node that is not a
    Speaker never shows it before the menu's own logic runs."""
    html = INDEX_HTML.read_text()
    match = re.search(r'<button id="node-speaker-btn"([^>]*)>(.*?)</button>', html, re.S)
    assert match, "index.html has no <button id=\"node-speaker-btn\">"
    attributes, visible = match.group(1), " ".join(match.group(2).split())
    assert 'role="menuitem"' in attributes
    assert 'aria-hidden="true"' in attributes
    assert re.search(r'class="[^"]*\bhidden\b', attributes)
    assert "Speaker" in visible
    # WCAG 2.5.3: the accessible name must contain the visible label.
    aria_label = re.search(r'aria-label="([^"]*)"', attributes)
    assert aria_label and visible in aria_label.group(1)


class SpeakerPanelCase(LiveServerCase):
    """A signed-in learner's page with the Topology rendered, and the panel's levers."""

    pytestmark = [pytest.mark.browser_services]

    @contextlib.contextmanager
    def _panel_page(self) -> Iterator[Page]:
        """Yield a page signed in as a learner with ``TOPOLOGY`` rendered."""
        with self.signed_in_page(role="learner") as page:
            self.render_topology(page, TOPOLOGY, AUTHORED_LAYOUT)
            yield page

    def _route_speaker(self, page: Page, handler: Callable[[Route], None]) -> None:
        page.route("**/api/labs/*/speakers/*", handler)

    def _answer(self, page: Page, body: str, status: int = 200) -> None:
        self._route_speaker(page, lambda route: route.fulfill(
            status=status, content_type="application/json", body=body,
        ))

    def _open_menu(self, page: Page, node: str) -> None:
        page.locator(f".node[aria-label*='{node}']").click(button="right")
        page.wait_for_selector("#node-context-menu:not(.hidden)")

    def _open_panel(self, page: Page) -> None:
        self._open_menu(page, "sp1")
        page.locator("#node-speaker-btn").click()
        page.wait_for_selector("#speaker-modal", state="visible")


class SpeakerPanelTests(SpeakerPanelCase):
    """Drives the Speaker panel opened from a Node's context menu."""

    # ── Entry point ──────────────────────────────────────────────────────────

    def test_context_menu_offers_the_speaker_entry_only_for_a_speaker(self) -> None:
        """A Speaker's menu shows the entry; a Host, Router and Container Host hide it."""
        with self._panel_page() as page:
            self._open_menu(page, "sp1")
            button = page.locator("#node-speaker-btn")
            self.assertTrue(button.is_visible())
            self.assertEqual(button.get_attribute("aria-hidden"), "false")
            self.assertEqual(button.get_attribute("role"), "menuitem")
            page.keyboard.press("Escape")

            for other in ("r1", "h1", "c1"):
                self._open_menu(page, other)
                self.assertFalse(button.is_visible(), f"{other} offered the Speaker entry")
                self.assertEqual(button.get_attribute("aria-hidden"), "true")
                page.keyboard.press("Escape")

    def test_clicking_the_speaker_still_opens_a_terminal(self) -> None:
        """The panel is reached from the menu; the Node itself keeps its Terminal."""
        with self._panel_page() as page:
            page.evaluate(
                """async () => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    window.__openedTerminals = [];
                    appState.tabManager.openTerminal = (name) => {
                        window.__openedTerminals.push(name);
                    };
                }"""
            )
            # dispatch_event, not click(): Playwright's click() moves the
            # mouse in steps and d3.drag() swallows the gesture.
            page.locator(".node[aria-label*='sp1']").dispatch_event("click")
            page.wait_for_function("() => window.__openedTerminals.length === 1")
            self.assertEqual(page.evaluate("() => window.__openedTerminals"), ["sp1"])
            self.assertFalse(page.locator("#speaker-modal").is_visible())

    # ── Rendering ────────────────────────────────────────────────────────────

    def test_panel_renders_every_neighbor_with_both_route_tables(self) -> None:
        """Each Neighbor gets a section: header with address, AS, state and
        uptime; announced rows with prefix, next hop and AS-PATH and the other
        attributes behind a per-row disclosure; the received side collapsed
        behind its count."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR, V6_NEIGHBOR_DOWN))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-neighbors .speaker-neighbor').length === 2"
            )

            self.assertEqual(page.locator("#speaker-modal-title").inner_text(), "sp1 — BGP Speaker")
            self.assertIn("AS 65001", page.locator("#speaker-summary").inner_text())

            sections = page.locator("#speaker-neighbors .speaker-neighbor")
            v4 = sections.nth(0)
            heading = v4.locator("h3").inner_text()
            self.assertIn("10.0.1.1", heading)
            self.assertIn("AS 65000", heading)
            meta = v4.locator(".speaker-neighbor-meta").inner_text()
            self.assertIn("Established", meta)
            self.assertIn("1 min 35 s", meta)
            self.assertIn("IPv4", meta)

            # Summary first: the tables open from their counts.
            announced = v4.locator("details.speaker-announced")
            self.assertEqual(announced.locator("summary").inner_text(), "Announced: 1 prefix")
            self.assertFalse(announced.locator("table").is_visible())
            announced.locator("summary").click()
            self.assertTrue(announced.locator("table").is_visible())
            self.assertEqual(
                announced.locator("thead th").all_inner_texts(),
                ["Prefix", "Next hop", "AS-PATH", "Attributes", "Withdraw"],
            )
            row = announced.locator("tbody tr.speaker-route").nth(0)
            self.assertEqual(
                row.locator("td").all_inner_texts()[:3],
                ["203.0.113.0/24", "10.0.1.2", "65001"],
            )

            # The rest of the route sits behind a per-row disclosure.
            toggle = row.locator("button.speaker-route-toggle")
            self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
            details_id = toggle.get_attribute("aria-controls")
            details = announced.locator(f"#{details_id}")
            self.assertFalse(details.is_visible())
            toggle.click()
            self.assertEqual(toggle.get_attribute("aria-expanded"), "true")
            self.assertTrue(details.is_visible())
            self.assertEqual(
                details.locator("dt").all_inner_texts(),
                ["Communities", "Large communities", "MED"],
            )
            self.assertEqual(
                details.locator("dd").all_inner_texts(),
                ["65001:100", "65001:1:2", "50"],
            )

            received = v4.locator("details.speaker-received")
            self.assertEqual(received.locator("summary").inner_text(), "Received: 1 prefix")
            self.assertFalse(received.locator("table").is_visible())
            received.locator("summary").click()
            self.assertTrue(received.locator("table").is_visible())
            self.assertEqual(
                received.locator("thead th").all_inner_texts(),
                ["Prefix", "Next hop", "AS-PATH", "Attributes"],
            )
            self.assertEqual(
                received.locator("tbody tr.speaker-route").nth(0).locator("td").all_inner_texts()[:3],
                ["198.51.100.0/24", "10.0.1.1", "65000"],
            )
            # A route with nothing beyond its AS-PATH says so instead of a toggle.
            self.assertEqual(
                received.locator("tbody tr.speaker-route").nth(0).locator("td").nth(3).inner_text(),
                "MED 0",
            )

            # The live region says what arrived, once.
            self.assertEqual(
                page.locator("#speaker-status").inner_text(),
                "2 Neighbors: 1 Established, 1 down",
            )

    def test_disclosures_survive_a_poll(self) -> None:
        """An opened row or received table stays open across an unchanged poll."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-neighbors .speaker-neighbor').length === 1"
            )
            section = page.locator("#speaker-neighbors .speaker-neighbor").nth(0)
            section.locator("details.speaker-received summary").click()
            section.locator("details.speaker-announced summary").click()
            toggle = section.locator("details.speaker-announced button.speaker-route-toggle").nth(0)
            toggle.click()
            # Wait out at least one poll.
            page.wait_for_timeout(4000)
            self.assertTrue(section.locator("details.speaker-received table").is_visible())
            self.assertEqual(toggle.get_attribute("aria-expanded"), "true")

    def test_a_down_neighbor_reads_as_down_beside_its_queued_routes(self) -> None:
        """A configured Neighbor that never connected is listed as down, and
        the route announced to it sits visibly beside that state."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V6_NEIGHBOR_DOWN))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-neighbors .speaker-neighbor').length === 1"
            )
            section = page.locator("#speaker-neighbors .speaker-neighbor").nth(0)
            meta = section.locator(".speaker-neighbor-meta").inner_text()
            self.assertIn("Down", meta)
            self.assertIn("idle", meta)
            self.assertNotIn("Established", meta)

            # The queued note sits in the summary, beside the down state, so
            # it is read before the table is opened.
            queued = section.locator(".speaker-queued-note")
            self.assertTrue(queued.is_visible())
            self.assertIn("queued", queued.inner_text().lower())
            announced = section.locator("details.speaker-announced")
            self.assertEqual(announced.locator("summary").inner_text(), "Announced: 1 prefix")
            announced.locator("summary").click()
            rows = announced.locator("tbody tr.speaker-route")
            self.assertEqual(rows.count(), 1)
            self.assertEqual(rows.nth(0).locator("td").nth(0).inner_text(), "2001:db8:100::/48")
            received = section.locator("details.speaker-received")
            self.assertEqual(received.locator("summary").inner_text(), "Received: nothing yet")

    def test_the_next_poll_shows_a_neighbor_coming_up(self) -> None:
        """A session that comes up between polls flips to Established without a reload."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V6_NEIGHBOR_DOWN))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelector('#speaker-neighbors .speaker-neighbor-meta')?.textContent.includes('Down')"
            )

            # Playwright matches the most recently registered route first.
            came_up = {**V6_NEIGHBOR_DOWN, "state": "established", "up": True, "uptime": 4}
            self._answer(page, speaker_payload(came_up))
            page.wait_for_function(
                "() => document.querySelector('#speaker-neighbors .speaker-neighbor-meta')?.textContent.includes('Established')",
                timeout=10000,
            )
            section = page.locator("#speaker-neighbors .speaker-neighbor").nth(0)
            self.assertFalse(section.locator(".speaker-queued-note").is_visible())
            self.assertEqual(page.locator("#speaker-status").inner_text(), "1 Neighbor: 1 Established")

    def test_a_route_landing_on_an_established_neighbor_is_announced(self) -> None:
        """A poll that changes a Neighbor's route counts is told to assistive
        technology through the status region, even though the session summary
        did not change."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-neighbors .speaker-neighbor').length === 1"
            )
            live = page.locator("#speaker-live")
            self.assertEqual(live.get_attribute("role"), "status")
            self.assertEqual(live.inner_text(), "")

            landed = {
                **V4_NEIGHBOR,
                "received": V4_NEIGHBOR["received"] + [{  # type: ignore[operator]
                    "prefix": "203.0.113.0/24", "next_hop": "10.0.1.1", "community": [],
                    "as_path": [65000, 65001], "large_community": [], "med": None,
                }],
            }
            self._answer(page, speaker_payload(landed))
            page.wait_for_function(
                "() => document.getElementById('speaker-live').textContent.length > 0",
                timeout=10000,
            )
            self.assertEqual(live.inner_text(), "10.0.1.1: 1 announced, 2 received.")
            self.assertEqual(
                page.locator("details.speaker-received summary").inner_text(),
                "Received: 2 prefixes",
            )
            # The session summary is unchanged, so it stays quiet.
            self.assertEqual(page.locator("#speaker-status").inner_text(), "1 Neighbor: 1 Established")

    # ── Errors ───────────────────────────────────────────────────────────────

    def test_a_failed_read_shows_the_services_reason(self) -> None:
        """A silent daemon is named as such, in the live region, not a blank panel."""
        detail = "no answer from sp1 to 'show neighbor json' within 5 s: is the ExaBGP daemon running?"
        with self._panel_page() as page:
            self._answer(page, json.dumps({"detail": detail, "command": "show neighbor json"}), status=502)
            self._open_panel(page)
            page.wait_for_function(
                "() => document.getElementById('speaker-status').textContent.includes('within 5 s')"
            )
            status = page.locator("#speaker-status")
            self.assertEqual(status.inner_text(), detail)
            self.assertTrue(status.evaluate("el => el.classList.contains('speaker-status-error')"))
            self.assertEqual(status.get_attribute("aria-live"), "polite")
            self.assertEqual(page.locator("#speaker-neighbors .speaker-neighbor").count(), 0)

    def test_a_rejected_command_is_reported_and_clears_stale_sections(self) -> None:
        """A read that worked and then fails leaves the reason, not the old picture."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-neighbors .speaker-neighbor').length === 1"
            )
            detail = "ExaBGP on sp1 rejected 'show adj-rib out extensive' (the reason is in the daemon's log)"
            self._answer(page, json.dumps({"detail": detail, "command": "show adj-rib out extensive"}), status=502)
            page.wait_for_function(
                "() => document.getElementById('speaker-status').textContent.includes('rejected')",
                timeout=10000,
            )
            self.assertEqual(page.locator("#speaker-status").inner_text(), detail)
            self.assertEqual(page.locator("#speaker-neighbors .speaker-neighbor").count(), 0)

    # ── Polling ──────────────────────────────────────────────────────────────

    def test_polling_starts_on_open_and_stops_on_close(self) -> None:
        """Requests flow while the panel is open and none arrive after close."""
        with self._panel_page() as page:
            page.evaluate("() => { window.__speakerReads = 0; }")

            def count(route: Route) -> None:
                page.evaluate("() => { window.__speakerReads += 1; }")
                route.fulfill(status=200, content_type="application/json",
                              body=speaker_payload(V4_NEIGHBOR))

            self._route_speaker(page, count)
            self._open_panel(page)
            page.wait_for_function("() => window.__speakerReads >= 2", timeout=10000)

            page.locator("#speaker-modal-close").click()
            page.wait_for_selector("#speaker-modal", state="hidden")
            reads_at_close = page.evaluate("() => window.__speakerReads")
            page.wait_for_timeout(4000)
            self.assertEqual(page.evaluate("() => window.__speakerReads"), reads_at_close)

    # ── Accessibility ────────────────────────────────────────────────────────

    def test_panel_is_a_labelled_dialog_with_labelled_regions(self) -> None:
        """WCAG structure: dialog role and name, labelled sections and tables,
        scoped headers, a polite live region, focus moved inside on open."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR, V6_NEIGHBOR_DOWN))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-neighbors .speaker-neighbor').length === 2"
            )

            modal = page.locator("#speaker-modal")
            self.assertEqual(modal.get_attribute("role"), "dialog")
            self.assertEqual(modal.get_attribute("aria-modal"), "true")
            self.assertEqual(modal.get_attribute("aria-labelledby"), "speaker-modal-title")
            self.assertEqual(page.locator("#speaker-status").get_attribute("aria-live"), "polite")
            self.assertTrue(page.evaluate(
                "() => document.getElementById('speaker-modal').contains(document.activeElement)"
            ))

            for section in page.locator("#speaker-neighbors .speaker-neighbor").all():
                labelled_by = section.get_attribute("aria-labelledby")
                assert labelled_by is not None
                self.assertEqual(section.locator(f"#{labelled_by}").count(), 1)
                for table in section.locator("table").all():
                    label = table.get_attribute("aria-label")
                    assert label is not None
                    self.assertIn(section.locator("h3").inner_text().split(" ")[1], label)
                    for th in table.locator("thead th").all():
                        self.assertEqual(th.get_attribute("scope"), "col")

    def test_escape_closes_the_panel_and_returns_focus_to_the_node(self) -> None:
        """Escape closes the dialog and focus returns to the Node whose menu
        opened it — the menu entry itself is gone by then."""
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR))
            node = page.locator(".node[aria-label*='sp1']")
            node.focus()
            page.keyboard.press("Shift+F10")
            page.wait_for_selector("#node-context-menu:not(.hidden)")
            page.locator("#node-speaker-btn").click()
            page.wait_for_selector("#speaker-modal", state="visible")

            page.keyboard.press("Escape")
            page.wait_for_selector("#speaker-modal", state="hidden")
            self.assertEqual(
                page.evaluate("() => document.activeElement.getAttribute('aria-label')"),
                node.get_attribute("aria-label"),
            )


class SpeakerPanelWriteTests(SpeakerPanelCase):
    """Drives the announce form, per-row withdraw and withdraw-all."""

    def _record_posts(self, page: Page, status: int = 200, body: str = "{}") -> None:
        """Answer every POST under the Speaker with ``body`` and record each one."""
        page.evaluate("() => { window.__speakerPosts = []; }")

        def record(route: Route) -> None:
            request = route.request
            page.evaluate(
                "([url, body]) => { window.__speakerPosts.push({ url, body: JSON.parse(body) }); }",
                [request.url, request.post_data or "null"],
            )
            route.fulfill(status=status, content_type="application/json", body=body)

        page.route("**/api/labs/*/speakers/sp1/announce", record)
        page.route("**/api/labs/*/speakers/sp1/withdraw", record)

    def _open_with(self, page: Page, *neighbors: dict[str, Any]) -> None:
        self._answer(page, speaker_payload(*neighbors))
        self._open_panel(page)
        page.wait_for_function(
            f"() => document.querySelectorAll('#speaker-neighbors .speaker-neighbor').length === {len(neighbors)}"
        )

    # ── The announce form ────────────────────────────────────────────────────

    def test_the_neighbor_selector_prefills_and_refills_the_next_hop(self) -> None:
        """The first Neighbor is chosen by default with its local address as
        the next hop; choosing another re-fills it; "all" leaves it to the
        Learner."""
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR, V6_NEIGHBOR_DOWN)
            select = page.locator("#speaker-announce-neighbor")
            self.assertEqual(
                select.locator("option").all_inner_texts(),
                ["10.0.1.1 (AS 65000, IPv4)", "2001:db8:1::1 (AS 65000, IPv6)", "All Neighbors"],
            )
            self.assertEqual(select.input_value(), "10.0.1.1")
            self.assertEqual(page.locator("#speaker-announce-next-hop").input_value(), "10.0.1.2")

            select.select_option("2001:db8:1::1")
            self.assertEqual(page.locator("#speaker-announce-next-hop").input_value(), "2001:db8:1::2")

            select.select_option("")
            self.assertEqual(page.locator("#speaker-announce-next-hop").input_value(), "")

            # A poll that changes nothing leaves the Learner's choice alone.
            select.select_option("2001:db8:1::1")
            page.wait_for_timeout(3500)
            self.assertEqual(select.input_value(), "2001:db8:1::1")

    def test_an_announce_posts_the_cli_field_set_and_reports_the_result(self) -> None:
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR)
            self._record_posts(page, body=json.dumps({
                "node": "sp1", "neighbors": ["10.0.1.1"],
                "route": {"prefix": "198.18.0.0/24", "next_hop": "10.0.1.2", "as_path": [65001, 65002],
                          "community": ["65001:100", "no-export"], "large_community": ["65001:1:2"],
                          "med": 50},
            }))
            page.locator("#speaker-announce-prefix").fill("198.18.0.0/24")
            page.locator("#speaker-announce-as-path").fill("65001 65002")
            page.locator("#speaker-announce-community").fill("65001:100 no-export")
            page.locator("#speaker-announce-large-community").fill("65001:1:2")
            page.locator("#speaker-announce-med").fill("50")
            page.locator("#speaker-announce-submit").click()

            page.wait_for_function("() => window.__speakerPosts.length === 1")
            post = page.evaluate("() => window.__speakerPosts[0]")
            self.assertTrue(post["url"].endswith("/api/labs/fake/speakers/sp1/announce"))
            self.assertEqual(post["body"], {
                "neighbor": "10.0.1.1", "prefix": "198.18.0.0/24", "next_hop": "10.0.1.2",
                "as_path": [65001, 65002], "community": ["65001:100", "no-export"],
                "large_community": ["65001:1:2"], "med": 50,
            })
            page.wait_for_function(
                "() => document.getElementById('speaker-live').textContent.includes('Announced')"
            )
            self.assertEqual(page.locator("#speaker-live").inner_text(),
                             "Announced 198.18.0.0/24 to 10.0.1.1.")
            self.assertFalse(page.locator("#speaker-write-error").is_visible())
            # The prefix is cleared for the next one; the next hop stays.
            self.assertEqual(page.locator("#speaker-announce-prefix").input_value(), "")
            self.assertEqual(page.locator("#speaker-announce-next-hop").input_value(), "10.0.1.2")

    def test_an_announce_to_all_omits_the_neighbor_and_sends_the_typed_next_hop(self) -> None:
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR, V6_NEIGHBOR_DOWN)
            self._record_posts(page, body=json.dumps(
                {"node": "sp1", "neighbors": ["10.0.1.1"], "route": {"prefix": "198.18.0.0/24"}}))
            page.locator("#speaker-announce-neighbor").select_option("")
            page.locator("#speaker-announce-prefix").fill("198.18.0.0/24")
            page.locator("#speaker-announce-next-hop").fill("10.0.1.2")
            page.locator("#speaker-announce-submit").click()
            page.wait_for_function("() => window.__speakerPosts.length === 1")
            body = page.evaluate("() => window.__speakerPosts[0].body")
            self.assertNotIn("neighbor", body)
            self.assertEqual(body["next_hop"], "10.0.1.2")
            self.assertNotIn("med", body)

    def test_a_refused_announce_shows_the_reason_beside_the_form(self) -> None:
        detail = ("next-hop 10.0.1.2 is IPv4 but prefix 2001:db8:200::/48 is IPv6; "
                  "a route's next-hop must be the prefix's family")
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR)
            self._record_posts(page, status=400, body=json.dumps({"detail": detail}))
            page.locator("#speaker-announce-prefix").fill("2001:db8:200::/48")
            page.locator("#speaker-announce-submit").click()
            page.wait_for_selector("#speaker-write-error", state="visible")
            error = page.locator("#speaker-write-error")
            self.assertEqual(error.inner_text(), detail)
            self.assertEqual(error.get_attribute("role"), "alert")
            self.assertTrue(page.evaluate(
                "() => document.activeElement === document.getElementById('speaker-write-error')"
            ))
            self.assertEqual(page.locator("#speaker-live").inner_text(), "")

    def test_a_rejected_announce_names_the_command_that_failed(self) -> None:
        detail = ("ExaBGP on sp1 rejected 'neighbor 10.0.1.1 announce route 198.18.0.0/24 "
                  "next-hop 10.0.1.2' (the reason is in the daemon's log)")
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR)
            self._record_posts(page, status=502, body=json.dumps(
                {"detail": detail, "command": "neighbor 10.0.1.1 announce route 198.18.0.0/24 next-hop 10.0.1.2"}))
            page.locator("#speaker-announce-prefix").fill("198.18.0.0/24")
            page.locator("#speaker-announce-submit").click()
            page.wait_for_selector("#speaker-write-error", state="visible")
            self.assertEqual(page.locator("#speaker-write-error").inner_text(), detail)

    def test_a_successful_announce_clears_an_earlier_error(self) -> None:
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR)
            self._record_posts(page, status=400, body=json.dumps({"detail": "prefix '198.18.0.0' needs a length"}))
            page.locator("#speaker-announce-prefix").fill("198.18.0.0")
            page.locator("#speaker-announce-submit").click()
            page.wait_for_selector("#speaker-write-error", state="visible")
            self._record_posts(page, body=json.dumps({"node": "sp1", "neighbors": ["10.0.1.1"],
                                                      "route": {"prefix": "198.18.0.0/24"}}))
            page.locator("#speaker-announce-prefix").fill("198.18.0.0/24")
            page.locator("#speaker-announce-submit").click()
            page.wait_for_selector("#speaker-write-error", state="hidden")

    # ── Withdraw ─────────────────────────────────────────────────────────────

    def test_each_announced_row_offers_a_withdraw(self) -> None:
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR)
            self._record_posts(page, body=json.dumps(
                {"node": "sp1", "prefix": "203.0.113.0/24", "neighbors": ["10.0.1.1"]}))
            section = page.locator("#speaker-neighbors .speaker-neighbor").nth(0)
            section.locator("details.speaker-announced summary").click()
            # The received table has no such control: those are the Router's.
            self.assertEqual(section.locator("details.speaker-received button.speaker-withdraw-btn").count(), 0)
            button = section.locator("details.speaker-announced tbody tr.speaker-route button.speaker-withdraw-btn")
            self.assertEqual(button.count(), 1)
            self.assertEqual(button.get_attribute("aria-label"), "Withdraw 203.0.113.0/24 from 10.0.1.1")
            button.click()
            page.wait_for_function("() => window.__speakerPosts.length === 1")
            post = page.evaluate("() => window.__speakerPosts[0]")
            self.assertTrue(post["url"].endswith("/api/labs/fake/speakers/sp1/withdraw"))
            self.assertEqual(post["body"], {"neighbor": "10.0.1.1", "prefix": "203.0.113.0/24"})
            page.wait_for_function(
                "() => document.getElementById('speaker-live').textContent.includes('Withdrew')"
            )
            self.assertEqual(page.locator("#speaker-live").inner_text(),
                             "Withdrew 203.0.113.0/24 from 10.0.1.1.")

    def test_withdraw_all_asks_first_and_names_the_lab_examples_routes(self) -> None:
        with self._panel_page() as page:
            self._open_with(page, V4_NEIGHBOR)
            self._record_posts(page, body=json.dumps(
                {"node": "sp1", "all": True, "neighbors": ["10.0.1.1"]}))
            button = page.locator("#speaker-neighbors .speaker-neighbor button.speaker-withdraw-all-btn")
            self.assertEqual(button.count(), 1)
            self.assertIn("10.0.1.1", button.get_attribute("aria-label") or "")
            button.click()
            page.wait_for_selector("#confirm-modal", state="visible")
            message = page.locator("#confirm-modal-message").inner_text()
            self.assertIn("10.0.1.1", message)
            self.assertIn("Lab Example", message)
            self.assertEqual(page.locator("#confirm-modal").get_attribute("role"), "dialog")

            # Escape dismisses the confirmation only: the panel stays open,
            # nothing is posted, and focus returns to the control.
            page.keyboard.press("Escape")
            page.wait_for_selector("#confirm-modal", state="hidden")
            self.assertTrue(page.locator("#speaker-modal").is_visible())
            self.assertEqual(page.evaluate("() => window.__speakerPosts.length"), 0)
            self.assertTrue(page.evaluate(
                "() => document.activeElement.classList.contains('speaker-withdraw-all-btn')"
            ))

            button.click()
            page.wait_for_selector("#confirm-modal", state="visible")
            page.locator("#confirm-ok").click()
            page.wait_for_function("() => window.__speakerPosts.length === 1")
            self.assertEqual(page.evaluate("() => window.__speakerPosts[0].body"),
                             {"neighbor": "10.0.1.1", "all": True})
            page.wait_for_function(
                "() => document.getElementById('speaker-live').textContent.includes('Withdrew')"
            )
            self.assertEqual(page.locator("#speaker-live").inner_text(),
                             "Withdrew every route announced to 10.0.1.1.")

    def test_a_write_re_reads_the_speaker_at_once(self) -> None:
        """The panel keeps no record: after a write it polls again rather
        than patching what it showed."""
        with self._panel_page() as page:
            page.evaluate("() => { window.__speakerReads = 0; }")

            def count(route: Route) -> None:
                page.evaluate("() => { window.__speakerReads += 1; }")
                route.fulfill(status=200, content_type="application/json",
                              body=speaker_payload(V4_NEIGHBOR))

            self._route_speaker(page, count)
            self._open_panel(page)
            page.wait_for_function("() => window.__speakerReads >= 1")
            self._record_posts(page, body=json.dumps({"node": "sp1", "neighbors": ["10.0.1.1"],
                                                      "route": {"prefix": "198.18.0.0/24"}}))
            reads = page.evaluate("() => window.__speakerReads")
            page.locator("#speaker-announce-prefix").fill("198.18.0.0/24")
            page.locator("#speaker-announce-submit").click()
            # Well inside the poll interval, so this read is the write's own.
            page.wait_for_function(f"() => window.__speakerReads > {reads}", timeout=1500)


GENERATED = [
    {"prefix": "45.10.0.0/16", "next_hop": "10.0.1.2", "as_path": [65001, 3356], "community": [],
     "large_community": [], "med": None},
    {"prefix": "77.2.0.0/17", "next_hop": "10.0.1.2", "as_path": [65001], "community": [],
     "large_community": [], "med": None},
    {"prefix": "91.200.4.0/22", "next_hop": "10.0.1.2", "as_path": [65001, 1299, 2914],
     "community": [], "large_community": [], "med": None},
]


class SpeakerPanelGenerateTests(SpeakerPanelCase):
    """Drives the generate form."""

    def _record_generate(self, page: Page, status: int = 200, body: str = "{}") -> None:
        page.evaluate("() => { window.__speakerPosts = []; }")

        def record(route: Route) -> None:
            page.evaluate(
                "([url, body]) => { window.__speakerPosts.push({ url, body: JSON.parse(body) }); }",
                [route.request.url, route.request.post_data or "null"],
            )
            route.fulfill(status=status, content_type="application/json", body=body)

        page.route("**/api/labs/*/speakers/sp1/generate", record)

    def test_the_generate_form_offers_every_neighbor_and_no_all(self) -> None:
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR, V6_NEIGHBOR_DOWN))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-generate-neighbor option').length === 2"
            )
            select = page.locator("#speaker-generate-neighbor")
            self.assertEqual(
                select.locator("option").all_inner_texts(),
                ["10.0.1.1 (AS 65000, IPv4)", "2001:db8:1::1 (AS 65000, IPv6)"],
            )
            self.assertEqual(select.input_value(), "10.0.1.1")
            self.assertEqual(page.locator("#speaker-generate-count").input_value(), "20")

    def test_a_burst_posts_the_options_and_lists_what_it_announced(self) -> None:
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-generate-neighbor option').length === 1"
            )
            self._record_generate(page, body=json.dumps(
                {"node": "sp1", "neighbor": "10.0.1.1", "announced": GENERATED}))
            # The next poll shows the burst under the Neighbor.
            landed = {**V4_NEIGHBOR, "announced": V4_NEIGHBOR["announced"] + GENERATED}  # type: ignore[operator]
            self._answer(page, speaker_payload(landed))

            page.locator("#speaker-generate-count").fill("3")
            page.locator("#speaker-generate-sub-prefixes").fill("2")
            page.locator("#speaker-generate-prefix-seed").fill("42")
            page.locator("#speaker-generate-as-path-seed").fill("7")
            page.locator("#speaker-generate-submit").click()

            page.wait_for_function("() => window.__speakerPosts.length === 1")
            post = page.evaluate("() => window.__speakerPosts[0]")
            self.assertTrue(post["url"].endswith("/api/labs/fake/speakers/sp1/generate"))
            self.assertEqual(post["body"], {
                "neighbor": "10.0.1.1", "count": 3, "sub_prefixes": 2, "prefix_seed": 42, "as_path_seed": 7,
            })
            page.wait_for_selector("#speaker-generate-result", state="visible")
            self.assertEqual(
                page.locator("#speaker-generate-result-heading").inner_text(),
                "Announced 3 prefixes to 10.0.1.1",
            )
            self.assertEqual(
                page.locator("#speaker-generate-result li").all_inner_texts(),
                ["45.10.0.0/16", "77.2.0.0/17", "91.200.4.0/22"],
            )
            self.assertEqual(page.locator("#speaker-live").inner_text(),
                             "Announced 3 generated prefixes to 10.0.1.1.")
            page.wait_for_function(
                "() => document.querySelector('details.speaker-announced summary').textContent.includes('4 prefixes')",
                timeout=10000,
            )

    def test_optional_fields_left_empty_are_not_sent(self) -> None:
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-generate-neighbor option').length === 1"
            )
            self._record_generate(page, body=json.dumps(
                {"node": "sp1", "neighbor": "10.0.1.1", "announced": GENERATED[:1]}))
            page.locator("#speaker-generate-count").fill("1")
            page.locator("#speaker-generate-submit").click()
            page.wait_for_function("() => window.__speakerPosts.length === 1")
            self.assertEqual(page.evaluate("() => window.__speakerPosts[0].body"),
                             {"neighbor": "10.0.1.1", "count": 1})
            page.wait_for_selector("#speaker-generate-result", state="visible")
            self.assertEqual(page.locator("#speaker-generate-result-heading").inner_text(),
                             "Announced 1 prefix to 10.0.1.1")

    def test_a_refused_burst_shows_the_reason(self) -> None:
        with self._panel_page() as page:
            self._answer(page, speaker_payload(V4_NEIGHBOR))
            self._open_panel(page)
            page.wait_for_function(
                "() => document.querySelectorAll('#speaker-generate-neighbor option').length === 1"
            )
            self._record_generate(page, status=400, body=json.dumps(
                {"detail": "count '5000' is not a number between 1 and 1000"}))
            page.locator("#speaker-generate-count").fill("5000")
            page.locator("#speaker-generate-submit").click()
            page.wait_for_selector("#speaker-write-error", state="visible")
            self.assertEqual(page.locator("#speaker-write-error").inner_text(),
                             "count '5000' is not a number between 1 and 1000")
            self.assertFalse(page.locator("#speaker-generate-result").is_visible())


if __name__ == "__main__":
    unittest.main()
