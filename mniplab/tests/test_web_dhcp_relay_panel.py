"""Browser tests for the DHCP Relay activity panel in the Web UI.

Follows the pattern from ``test_web_dhcp_leases_panel.py``: spins up an
ephemeral uvicorn server, signs in as a *learner*, renders synthetic
topology data, and fakes the REST endpoint with ``page.route`` (a real
running Lab needs root, which these tests deliberately do not stand up).

Covers showing Relay activity in the Web UI:
- A relaying Node's Relay badge opens a panel, the same way a Service's
  badge opens its panel.
- The panel shows what the Relay forwarded: the source address it used and
  the Service it forwarded to, for both address families.
- A Relay that forwarded nothing is shown as exactly that, distinct from a
  Relay that is not running.
- The Relay is not editable from the panel — it is Lab shape, not Exercise
  Configuration.
- The panel points at the Node's Terminal as the full, unabridged record.
- The panel meets WCAG 2.2 AA: a real dialog with headers and a labelled
  table, keyboard reachable, announced to assistive technology.
"""

import contextlib
import json
from typing import Any, Callable, Iterator

import pytest

from mniplab.tests.browser import LiveServerCase, Page, Route

pytestmark = pytest.mark.browser_services

AUTHORED_LAYOUT = {
    "nodes": {
        "r1": {"x": 200, "y": 200},
        "h1": {"x": 400, "y": 200},
        "srv": {"x": 600, "y": 200},
    }
}

TOPOLOGY = {
    "nodes": [
        {
            "id": "r1",
            "type": "router",
            "label": "r1",
            "ip": "10.50.0.1",
            "services": [],
            "relays": [{"subnets": ["10.50.0.0/24", "2001:db8:50::/64"]}],
        },
        {"id": "h1", "type": "host", "label": "h1", "ip": "10.50.0.2", "services": [], "relays": []},
        {"id": "srv", "type": "host", "label": "srv", "ip": "10.0.0.2", "services": ["dhcp"], "relays": []},
    ],
    "links": [
        {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
    ],
}


class DhcpRelayPanelTests(LiveServerCase):
    """Drives the DHCP Relay panel opened from a Node's Topology badge."""

    @contextlib.contextmanager
    def _panel_page(self) -> Iterator[Page]:
        """Yield a page signed in as a learner with ``TOPOLOGY`` rendered."""
        with self.signed_in_page(role="learner") as page:
            self.render_topology(page, TOPOLOGY, AUTHORED_LAYOUT)
            yield page

    def _route_activity(
        self,
        page: Page,
        handler: Callable[[Route], None],
    ) -> None:
        page.route("**/api/labs/*/dhcp/relay/activity/*", handler)

    def _fulfill_activity(
        self,
        page: Page,
        payload: dict[str, Any],
        status: int = 200,
    ) -> None:
        self._route_activity(page, lambda route: route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload),
        ))

    def _open_panel(self, page: Page) -> None:
        # dispatch_event, not click(): Playwright's click() moves the mouse
        # in steps and d3.drag() swallows the gesture (same note as the
        # leases panel tests).
        page.locator(".node[aria-label*='r1'] .relay-badge").dispatch_event("click")
        page.wait_for_selector("#dhcp-relay-modal", state="visible")

    def test_dhcp_relay_panel_end_to_end_scenario(self) -> None:
        """Full scenario: keyboard open, accessibility semantics, empty state, live update, terminal, close button, escape."""
        activity = [
            {"source": "10.50.0.1", "server": "10.0.0.2"},
            {"source": "2001:db8:50::1", "server": "2001:db8::2"},
        ]
        with self._panel_page() as page:
            # 1. Start with empty activity: open panel from keyboard and assert accessibility
            self._fulfill_activity(page, {"success": True, "activity": []})

            badge = page.locator(".node[aria-label*='r1'] .relay-badge")
            badge.focus()
            page.keyboard.press("Enter")
            page.wait_for_selector("#dhcp-relay-modal", state="visible")

            # Dialog and table accessibility semantics (WCAG)
            modal = page.locator("#dhcp-relay-modal")
            self.assertEqual(modal.get_attribute("role"), "dialog")
            self.assertEqual(modal.get_attribute("aria-modal"), "true")
            self.assertEqual(modal.get_attribute("aria-labelledby"), "dhcp-relay-modal-title")
            table = page.locator("#dhcp-relay-activity-table")
            table_aria_label = table.get_attribute("aria-label")
            assert table_aria_label is not None
            self.assertIn("Relay", table_aria_label)
            for th in page.locator("#dhcp-relay-activity-table thead th").all():
                self.assertEqual(th.get_attribute("scope"), "col")
            self.assertEqual(
                page.locator("#dhcp-relay-status").get_attribute("aria-live"),
                "polite",
            )
            self.assertTrue(
                page.evaluate(
                    "() => document.getElementById('dhcp-relay-modal').contains(document.activeElement)"
                )
            )

            # 2. Empty activity state display
            page.wait_for_function(
                "() => document.getElementById('dhcp-relay-status').textContent.length > 0"
            )
            status = page.locator("#dhcp-relay-status")
            self.assertEqual(status.inner_text(), "This Relay has forwarded nothing yet.")
            self.assertFalse(status.evaluate("el => el.classList.contains('dhcp-leases-status-error')"))
            self.assertEqual(page.locator("#dhcp-relay-activity-tbody tr").count(), 0)

            # 3. Read-only lab shape: no edit form controls, explanation note displayed
            self.assertEqual(page.locator("#dhcp-relay-modal input").count(), 0)
            self.assertEqual(page.locator("#dhcp-relay-modal select").count(), 0)
            self.assertEqual(page.locator("#dhcp-relay-modal textarea").count(), 0)
            self.assertEqual(page.locator("#dhcp-relay-modal button[type='submit']").count(), 0)
            note = page.locator(".dhcp-relay-note").inner_text()
            self.assertIn("part of the Lab's setup", note)
            self.assertIn("cannot be changed or removed", note)
            self.assertIn("Terminal", note)

            # 4. Dynamic activity update via polling
            self._fulfill_activity(page, {"success": True, "activity": activity})
            page.wait_for_function(
                "() => document.querySelectorAll('#dhcp-relay-activity-tbody tr').length === 2",
                timeout=10000,
            )

            self.assertEqual(
                page.locator("#dhcp-relay-modal-title").inner_text(),
                "r1 — DHCP Relay",
            )
            self.assertEqual(
                page.locator("#dhcp-relay-subnets").inner_text(),
                "This Node relays DHCP requests for: 10.50.0.0/24, 2001:db8:50::/64",
            )

            rows = page.locator("#dhcp-relay-activity-tbody tr")
            self.assertEqual(
                rows.nth(0).locator("td").all_inner_texts(),
                ["10.50.0.1", "10.0.0.2 (srv)"],
            )
            self.assertEqual(
                rows.nth(1).locator("td").all_inner_texts(),
                ["2001:db8:50::1", "2001:db8::2"],
            )
            headers = page.locator("#dhcp-relay-activity-table thead th").all_inner_texts()
            self.assertEqual(headers, ["Source address used", "Forwarded to"])
            self.assertEqual(page.locator("#dhcp-relay-status").inner_text(), "2 forwarded requests")

            # 5. Terminal button opens node terminal
            page.evaluate(
                """async () => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    window.__openedTerminals = [];
                    const original = appState.tabManager.openTerminal.bind(appState.tabManager);
                    appState.tabManager.openTerminal = (name) => {
                        window.__openedTerminals.push(name);
                        return original(name);
                    };
                }"""
            )
            page.locator("#dhcp-relay-terminal-btn").click()
            page.wait_for_function("() => window.__openedTerminals.length === 1")
            self.assertEqual(page.evaluate("() => window.__openedTerminals"), ["r1"])

            # 6. Modal close button closes panel
            page.locator("#dhcp-relay-modal-close").click()
            page.wait_for_selector("#dhcp-relay-modal", state="hidden")

            # 7. Reopening and dismissing via Escape restores focus to badge
            self._open_panel(page)
            page.keyboard.press("Escape")
            page.wait_for_selector("#dhcp-relay-modal", state="hidden")
            self.assertEqual(
                page.evaluate("() => document.activeElement && document.activeElement.classList.contains('relay-badge')"),
                True,
            )

    def test_relay_not_running_is_distinct_from_forwarded_nothing(self) -> None:
        """A Relay whose log cannot be read is a fault, styled and worded apart."""
        detail = (
            "Could not read relay log at /tmp/dhcp-relay/r1/dnsmasq.log. "
            "Is the DHCP Relay running?"
        )
        with self._panel_page() as page:
            self._fulfill_activity(page, {"detail": detail}, status=400)
            self._open_panel(page)
            page.wait_for_function(
                "() => document.getElementById('dhcp-relay-status').textContent.length > 0"
            )

            status = page.locator("#dhcp-relay-status")
            self.assertEqual(status.inner_text(), detail)
            self.assertTrue(status.evaluate("el => el.classList.contains('dhcp-leases-status-error')"))
            self.assertNotEqual(status.inner_text(), "This Relay has forwarded nothing yet.")
            self.assertEqual(page.locator("#dhcp-relay-activity-tbody tr").count(), 0)
