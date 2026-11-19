"""Browser tests for the DHCP Leases panel in the Web UI.

Follows the pattern from ``test_web_topology_service_badge.py`` (render
synthetic topology data directly via ``renderTopology``) and
``test_web_tools_refresh.py`` (``page.route`` to fake a REST endpoint without
a real running Lab, which these browser tests can't stand up — it needs root).

Covers the Lease table and its subsequent DHCPv6 enhancements:
- Selecting a Node with a DHCP Service opens a panel listing its Leases and Pools.
- Each Lease shows its address, its client (MAC or DUID), and expiration.
- Dual-stack support: IPv4 and IPv6 tables with single-family filtering.
- Relayed IPv6 lease client information and clipboard actions.
- Pool editor for IPv4 and IPv6 with family-specific fields, reservations, and live sync.
- Validation rejections, error states, and WCAG 2.2 AA accessibility.
"""

import contextlib
import json
import time
import unittest
from typing import Any, Callable, Iterator

import pytest

from mniplab.tests.browser import LiveServerCase, Page, Route

pytestmark = pytest.mark.browser_services

AUTHORED_LAYOUT = {
    "nodes": {
        "r1": {"x": 200, "y": 200},
        "h1": {"x": 400, "y": 200},
    }
}

TOPOLOGY = {
    "nodes": [
        {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1", "services": ["dhcp"]},
        {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.2", "services": []},
    ],
    "links": [
        {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
    ],
}


class DhcpLeasesPanelTests(LiveServerCase):
    """Drives the DHCP Leases panel opened from a Node's Topology badge."""

    @contextlib.contextmanager
    def _panel_page(self) -> Iterator[Page]:
        """Yield a page signed in as a learner with ``TOPOLOGY`` rendered."""
        with self.signed_in_page(role="learner") as page:
            self.render_topology(page, TOPOLOGY, AUTHORED_LAYOUT)
            yield page

    def _route_leases(self, page: Page, handler: Callable[[Route], None]) -> None:
        page.route("**/api/labs/*/dhcp/leases/*", handler)

    def _route_pools(self, page: Page, handler: Callable[[Route], None]) -> None:
        page.route("**/api/labs/*/dhcp/pools/*", handler)

    def _open_panel(self, page: Page) -> None:
        # dispatch_event, not click(): Playwright's click() moves the mouse
        # to the target in steps, and that movement — even a single pixel —
        # is enough for the node's d3.drag() to treat the gesture as a drag
        # and swallow the resulting click (d3's default clickDistance is 0).
        page.locator(".node[aria-label*='r1'] .service-badge-dhcp").dispatch_event("click")
        page.wait_for_selector("#dhcp-leases-modal", state="visible")

    def test_dhcp_leases_table_end_to_end_scenario(self) -> None:
        """Full scenario: dual-stack tables, DUID formatting, copy buttons, applied status, single-family filtering, live update, and keyboard a11y."""
        now = int(time.time())
        topo = {
            "nodes": [
                {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1", "ips": ["10.0.0.1", "2001:db8::1"], "services": ["dhcp"]},
                {"id": "h1", "type": "host", "label": "h1", "ip": "192.168.1.5", "ips": ["192.168.1.5", "10.0.0.2", "2001:db8::2", "10.0.0.7"], "services": []},
            ],
            "links": [
                {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
            ],
        }
        v4_leases = [
            {"ip-address": "10.0.0.2", "hw-address": "aa:bb:cc:dd:ee:ff", "hostname": "h1", "valid-lft": 3600, "cltt": now},
            {"ip-address": "10.0.0.99", "hw-address": "11:22:33:44:55:66", "hostname": "", "valid-lft": 3600, "cltt": now},
            {"ip-address": "10.0.0.7", "hw-address": "aa:bb:cc:dd:ee:00", "hostname": "", "valid-lft": 3600, "cltt": now},
        ]
        v6_leases = [
            {"ip-address": "2001:db8::2", "hostname": "h1", "duid": "00:01:00:01:aa:bb:cc:dd:ee:ff", "preferred-lft": 1800, "valid-lft": 3600, "cltt": now},
            {"ip-address": "2001:db8::99", "duid": "00:01:00:01:11:22:33:44:55:66", "preferred-lft": 1800, "valid-lft": 3600, "cltt": now},
            {"ip-address": "2001:db8:50::50", "duid": "00:01:00:01:aa:bb:cc:dd:ee:ff", "hw-address": "00:00:00:00:00:02", "preferred-lft": 1800, "valid-lft": 3600, "cltt": now},
        ]

        with self._panel_page() as page:
            self.render_topology(page, topo, AUTHORED_LAYOUT)
            self._route_leases(page, lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True, "v4": v4_leases, "v6": v6_leases}),
            ))
            self._route_pools(page, lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True, "pools": []}),
            ))

            # 1. Keyboard accessibility to open panel
            badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(badge.get_attribute("tabindex"), "0")
            self.assertEqual(badge.get_attribute("role"), "button")
            badge.focus()
            page.keyboard.press("Enter")
            page.wait_for_selector("#dhcp-leases-modal", state="visible")

            page.wait_for_function(
                "() => document.querySelectorAll('#dhcp-leases-tbody tr').length === 3 && "
                "document.querySelectorAll('#dhcp-v6-leases-tbody tr').length === 3"
            )

            # Headers & Table visibility
            self.assertTrue(page.locator("#dhcp-v4-leases-container").is_visible())
            self.assertTrue(page.locator("#dhcp-v6-leases-container").is_visible())
            self.assertEqual(page.locator("#dhcp-v4-leases-heading").inner_text(), "IPv4 Leases")
            self.assertEqual(page.locator("#dhcp-v6-leases-heading").inner_text(), "IPv6 Leases")
            self.assertEqual(page.locator("#dhcp-leases-table thead th:visible").all_inner_texts(), ["Address", "Client", "Expires", "Applied"])
            self.assertEqual(page.locator("#dhcp-v6-leases-table thead th:visible").all_inner_texts(), ["Address", "Client (DUID)", "Preferred Expires", "Valid Expires", "Applied"])

            # IPv4 Leases contents (hostname vs mac, applied on primary and non-default interface)
            v4_rows = page.locator("#dhcp-leases-tbody tr")
            r0 = v4_rows.nth(0).locator("td").all_inner_texts()
            self.assertEqual(r0[0], "10.0.0.2")
            self.assertEqual(r0[1], "h1")
            self.assertNotEqual(r0[2].strip(), "")
            self.assertEqual(r0[3], "Applied")

            r1 = v4_rows.nth(1).locator("td").all_inner_texts()
            self.assertEqual(r1[0], "10.0.0.99")
            self.assertEqual(r1[1], "11:22:33:44:55:66")
            self.assertEqual(r1[3], "Not applied (server record only)")

            r2 = v4_rows.nth(2).locator("td").all_inner_texts()
            self.assertEqual(r2[0], "10.0.0.7")
            self.assertEqual(r2[3], "Applied")

            # IPv6 Leases contents (DUID formatting, copy buttons, lifetimes)
            v6_rows = page.locator("#dhcp-v6-leases-tbody tr")
            v6_r0 = v6_rows.nth(0).locator("td").all_inner_texts()
            self.assertEqual(v6_r0[0], "2001:db8::2")
            c0 = v6_rows.nth(0).locator("td").nth(1)
            self.assertEqual(c0.locator(".dhcp-lease-hostname").inner_text(), "h1")
            self.assertEqual(c0.locator(".dhcp-lease-duid").inner_text(), "00:01:00:01:aa:bb:cc:dd:ee:ff")
            copy_btn = c0.locator(".dhcp-lease-copy-btn")
            self.assertEqual(copy_btn.inner_text(), "Copy DUID")
            self.assertEqual(copy_btn.get_attribute("aria-label"), "Copy DUID 00:01:00:01:aa:bb:cc:dd:ee:ff")
            self.assertEqual(v6_r0[4], "Applied")

            v6_r1 = v6_rows.nth(1).locator("td").all_inner_texts()
            self.assertEqual(v6_r1[0], "2001:db8::99")
            self.assertEqual(v6_rows.nth(1).locator("td").nth(1).locator(".dhcp-lease-duid").inner_text(), "00:01:00:01:11:22:33:44:55:66")
            self.assertEqual(v6_r1[4], "Not applied (server record only)")

            # Relayed IPv6 lease displaying hardware address alongside DUID & Copy MAC
            relayed_client = v6_rows.nth(2).locator("td").nth(1)
            self.assertEqual(relayed_client.locator(".dhcp-lease-duid").inner_text(), "00:01:00:01:aa:bb:cc:dd:ee:ff")
            self.assertEqual(relayed_client.locator(".dhcp-lease-hw").inner_text(), "00:00:00:00:00:02")
            copy_mac_btn = relayed_client.locator(".dhcp-lease-copy-mac-btn")
            self.assertEqual(copy_mac_btn.inner_text(), "Copy MAC")
            self.assertEqual(copy_mac_btn.get_attribute("aria-label"), "Copy MAC 00:00:00:00:00:02")
            copy_mac_btn.click()
            page.wait_for_function("() => document.getElementById('dhcp-lease-copy-status').textContent.toLowerCase().includes('hardware address')")

            # 2. Live polling update adds a new lease dynamically
            dynamic_leases = {"v4": list(v4_leases) + [{"ip-address": "10.0.0.55", "hw-address": "ff:ff:ff:ff:ff:ff", "hostname": "dynamic", "valid-lft": 3600, "cltt": now}], "v6": []}
            self._route_leases(page, lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"success": True, **dynamic_leases}),
            ))
            page.wait_for_function("() => document.querySelectorAll('#dhcp-leases-tbody tr').length === 4", timeout=7000)
            self.assertIn("10.0.0.55", page.locator("#dhcp-leases-tbody").inner_text())

            # 3. Keyboard Escape closes panel
            page.keyboard.press("Escape")
            page.wait_for_selector("#dhcp-leases-modal", state="hidden")

            # 4. Single-family filtering: v4 only hides v6 table, v6 only hides v4 table
            self._route_leases(page, lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"success": True, "v4": v4_leases}),
            ))
            self._open_panel(page)
            page.wait_for_function("() => document.querySelectorAll('#dhcp-leases-tbody tr').length === 3")
            self.assertTrue(page.locator("#dhcp-v4-leases-container").is_visible())
            self.assertFalse(page.locator("#dhcp-v6-leases-container").is_visible())
            page.keyboard.press("Escape")
            page.wait_for_selector("#dhcp-leases-modal", state="hidden")

            self._route_leases(page, lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"success": True, "v6": v6_leases}),
            ))
            self._open_panel(page)
            page.wait_for_function("() => document.querySelectorAll('#dhcp-v6-leases-tbody tr').length === 3")
            self.assertFalse(page.locator("#dhcp-v4-leases-container").is_visible())
            self.assertTrue(page.locator("#dhcp-v6-leases-container").is_visible())

    def test_dhcp_pool_editor_end_to_end_scenario(self) -> None:
        """Full pool editor workflow: IPv4 and IPv6 fields, reservation rows, DUID/MAC switching, submissions, and WebSocket broadcast sync."""
        v4_pool: dict[str, Any] = {
            "family": 4, "cidr": "10.0.0.0/24", "start": "10.0.0.100", "end": "10.0.0.200",
            "interface": "r1-eth0", "lease_time": 120, "router": "10.0.0.1", "dns_server": "8.8.8.8",
            "reservations": [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.50", "hostname": "h1"}],
        }
        v6_pool: dict[str, Any] = {
            "family": 6, "cidr": "2001:db8:1::/64", "start": "2001:db8:1::100", "end": "2001:db8:1::200",
            "interface": "r1-eth0", "preferred_lifetime": 300, "lease_time": 600, "router": "", "dns_server": "",
            "reservations": [],
        }
        submitted_payloads: list[dict[str, Any]] = []

        def handle_pool_route(route: Route) -> None:
            if route.request.method == "POST":
                data = json.loads(route.request.post_data or "{}")
                submitted_payloads.append(data)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"success": True, "pool": data, "pools": [data]}),
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"success": True, "pools": [v4_pool, v6_pool]}),
                )

        with self._panel_page() as page:
            self._route_leases(page, lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True, "leases": []}),
            ))
            self._route_pools(page, handle_pool_route)
            self._open_panel(page)
            page.wait_for_selector(".dhcp-pool-card")

            # --- Phase 1: IPv4 Pool inspection & field validation ---
            v4_card = page.locator(".dhcp-pool-card").nth(0)
            self.assertEqual(v4_card.locator("input[name='lease_time']").input_value(), "120")
            self.assertEqual(v4_card.locator("input[name='router']").input_value(), "10.0.0.1")
            self.assertEqual(v4_card.locator("input[name='dns_server']").input_value(), "8.8.8.8")
            self.assertEqual(v4_card.locator("input[name='preferred_lifetime']").count(), 0)

            # Field labels and ordering
            lease_id = v4_card.locator("input[name='lease_time']").get_attribute("id")
            router_id = v4_card.locator("input[name='router']").get_attribute("id")
            dns_id = v4_card.locator("input[name='dns_server']").get_attribute("id")
            self.assertEqual(v4_card.locator(f"label[for='{lease_id}']").inner_text(), "Lease time (seconds)")
            self.assertEqual(v4_card.locator(f"label[for='{router_id}']").inner_text(), "Router")
            self.assertEqual(v4_card.locator(f"label[for='{dns_id}']").inner_text(), "DNS server")

            # Existing reservation row check
            self.assertEqual(v4_card.locator(".dhcp-reservation-row").count(), 1)
            mac_input = v4_card.locator("input[name='reservation-mac']").first
            self.assertEqual(mac_input.input_value(), "aa:bb:cc:dd:ee:ff")

            # Add blank reservation row and delete it
            v4_card.locator(".dhcp-reservation-add-btn").click()
            self.assertEqual(v4_card.locator(".dhcp-reservation-row").count(), 2)
            v4_card.locator(".dhcp-reservation-remove-btn").nth(1).click()
            self.assertEqual(v4_card.locator(".dhcp-reservation-row").count(), 1)

            # Submit IPv4 updates
            v4_card.locator("input[name='start']").fill("10.0.0.120")
            v4_card.locator("input[name='end']").fill("10.0.0.180")
            v4_card.locator(".dhcp-pool-submit-btn").click()
            page.wait_for_function(
                "() => document.querySelectorAll('.dhcp-pool-feedback')[0]?.textContent.includes('successfully')"
            )
            self.assertGreaterEqual(len(submitted_payloads), 1)
            self.assertEqual(submitted_payloads[0]["cidr"], "10.0.0.0/24")
            self.assertEqual(submitted_payloads[0]["start"], "10.0.0.120")
            self.assertEqual(submitted_payloads[0]["end"], "10.0.0.180")

            # --- Phase 2: IPv6 Pool inspection & DUID/MAC reservations ---
            v6_card = page.locator(".dhcp-pool-card").nth(1)
            self.assertEqual(v6_card.locator("input[name='router']").count(), 0)
            pref_input = v6_card.locator("input[name='preferred_lifetime']")
            self.assertEqual(pref_input.count(), 1)
            self.assertEqual(pref_input.input_value(), "300")
            pref_id = pref_input.get_attribute("id")
            self.assertEqual(v6_card.locator(f"label[for='{pref_id}']").inner_text(), "Preferred lifetime (seconds)")
            self.assertTrue(pref_input.get_attribute("aria-describedby"))

            # Add IPv6 reservation (defaults to DUID)
            v6_card.locator(".dhcp-reservation-add-btn").click()
            id_type = v6_card.locator("select[name='reservation-id-type']")
            self.assertEqual(id_type.input_value(), "duid")
            duid_input = v6_card.locator("input[name='reservation-duid']")
            self.assertTrue(duid_input.is_visible())
            self.assertFalse(v6_card.locator("input[name='reservation-mac']").is_visible())

            # Switch to MAC and verify clear
            duid_input.fill("00:01:00:01:aa:bb:cc:dd:ee:ff")
            id_type.select_option("mac")
            self.assertFalse(duid_input.is_visible())
            mac_field = v6_card.locator("input[name='reservation-mac']")
            self.assertTrue(mac_field.is_visible())
            self.assertEqual(mac_field.input_value(), "")

            # Switch back to DUID and fill for submit
            id_type.select_option("duid")
            duid_input.fill("00:01:00:01:aa:bb:cc:dd:ee:ff")
            v6_card.locator("input[name='reservation-ip']").fill("2001:db8:1::50")
            v6_card.locator("input[name='start']").fill("2001:db8:1::50")
            v6_card.locator("input[name='end']").fill("2001:db8:1::ff")
            v6_card.locator("input[name='preferred_lifetime']").fill("120")

            v6_card.locator(".dhcp-pool-submit-btn").click()
            page.wait_for_function(
                "() => document.querySelectorAll('.dhcp-pool-feedback')[1]?.textContent.includes('successfully')"
            )
            self.assertEqual(len(submitted_payloads), 2)
            v6_sub = submitted_payloads[1]
            self.assertEqual(v6_sub["cidr"], "2001:db8:1::/64")
            self.assertEqual(v6_sub["start"], "2001:db8:1::50")
            self.assertEqual(v6_sub["end"], "2001:db8:1::ff")
            self.assertEqual(v6_sub["preferred_lifetime"], 120)
            self.assertEqual(v6_sub["router"], "")
            self.assertEqual(v6_sub["reservations"], [
                {"mac": "", "duid": "00:01:00:01:aa:bb:cc:dd:ee:ff", "ip": "2001:db8:1::50", "hostname": ""}
            ])

            # --- Phase 3: Live WebSocket updates for both families ---
            page.evaluate(
                """() => {
                    import('/static/js/state/app-state.js').then(({ appState }) => {
                        appState.wsMessageHandler({
                            type: 'dhcp_pool_update',
                            data: {
                                node: 'r1',
                                pool: {
                                    cidr: '10.0.0.0/24',
                                    start: '10.0.0.50',
                                    end: '10.0.0.75'
                                }
                            }
                        });
                        appState.wsMessageHandler({
                            type: 'dhcp_pool_update',
                            data: {
                                node: 'r1',
                                pool: {
                                    cidr: '2001:db8:1::/64',
                                    start: '2001:db8:1::100',
                                    end: '2001:db8:1::200',
                                    preferred_lifetime: 60
                                }
                            }
                        });
                    });
                }"""
            )
            page.wait_for_function(
                "() => document.querySelectorAll(\"input[name='start']\")[0]?.value === '10.0.0.50' && "
                "document.querySelector(\"input[name='preferred_lifetime']\")?.value === '60'"
            )
            self.assertEqual(v4_card.locator("input[name='end']").input_value(), "10.0.0.75")
            self.assertEqual(v6_card.locator("input[name='preferred_lifetime']").input_value(), "60")

    def test_dhcp_leases_and_pools_error_states(self) -> None:
        """Error and boundary conditions: empty pools/leases, Kea daemon failure, and submission rejections with accessible error roles."""
        pools = [
            {"family": 4, "cidr": "10.0.0.0/24", "start": "10.0.0.100", "end": "10.0.0.200", "reservations": []},
            {"family": 6, "cidr": "2001:db8:1::/64", "start": "2001:db8:1::100", "end": "2001:db8:1::200", "preferred_lifetime": 300, "reservations": []},
        ]

        def handle_pool_route(route: Route) -> None:
            if route.request.method == "POST":
                data = json.loads(route.request.post_data or "{}")
                if data.get("family") == 6 or "2001:" in data.get("start", ""):
                    route.fulfill(
                        status=400,
                        content_type="application/json",
                        body=json.dumps({"detail": "Start address 2001:db8:1::ff0 is outside subnet 2001:db8:1::/64"}),
                    )
                else:
                    route.fulfill(
                        status=400,
                        content_type="application/json",
                        body=json.dumps({"detail": "Start address 10.0.0.220 cannot be after end address 10.0.0.150"}),
                    )
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"success": True, "pools": pools}))

        with self._panel_page() as page:
            # 1. Daemon down / server unreachable
            self._route_leases(page, lambda route: route.fulfill(
                status=400, content_type="application/json", body=json.dumps({"detail": "kea-dhcp4 is not reachable for r1"}),
            ))
            self._route_pools(page, handle_pool_route)
            self._open_panel(page)

            page.wait_for_function(
                "() => document.getElementById('dhcp-leases-status').textContent.includes('not reachable')"
            )
            status_el = page.locator("#dhcp-leases-status")
            self.assertTrue(status_el.is_visible())
            self.assertIn("not reachable", status_el.inner_text().lower())

            # 2. Empty leases & empty pools state
            self._route_leases(page, lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"success": True, "leases": []}),
            ))
            page.keyboard.press("Escape")
            page.wait_for_selector("#dhcp-leases-modal", state="hidden")
            self._open_panel(page)

            page.wait_for_function(
                "() => document.getElementById('dhcp-leases-status').textContent === 'No Leases yet.'"
            )
            status = page.locator("#dhcp-leases-status")
            self.assertNotIn("dhcp-leases-status-error", status.get_attribute("class") or "")
            self.assertEqual(page.locator("#dhcp-leases-tbody tr").count(), 0)
            self.assertEqual(page.locator("#dhcp-leases-table thead th:visible").count(), 4)

            # 3. Submission validation rejection: IPv4
            v4_card = page.locator(".dhcp-pool-card").nth(0)
            start_v4 = v4_card.locator("input[name='start']")
            end_v4 = v4_card.locator("input[name='end']")
            start_v4.fill("10.0.0.220")
            end_v4.fill("10.0.0.150")
            v4_card.locator(".dhcp-pool-submit-btn").click()

            page.wait_for_function(
                "() => document.querySelectorAll('.dhcp-pool-feedback')[0]?.textContent.includes('cannot be after')"
            )
            feedback_v4 = v4_card.locator(".dhcp-pool-feedback")
            self.assertEqual(feedback_v4.get_attribute("role"), "alert")
            self.assertEqual(feedback_v4.get_attribute("aria-live"), "polite")
            feedback_id = feedback_v4.get_attribute("id")
            self.assertEqual(start_v4.get_attribute("aria-invalid"), "true")
            self.assertEqual(end_v4.get_attribute("aria-invalid"), "true")
            self.assertEqual(start_v4.get_attribute("aria-describedby"), feedback_id)
            self.assertEqual(end_v4.get_attribute("aria-describedby"), feedback_id)

            # 4. Submission validation rejection: IPv6
            v6_card = page.locator(".dhcp-pool-card").nth(1)
            start_v6 = v6_card.locator("input[name='start']")
            start_v6.fill("2001:db8:1::ff0")
            v6_card.locator(".dhcp-pool-submit-btn").click()

            page.wait_for_function(
                "() => document.querySelectorAll('.dhcp-pool-feedback')[1]?.textContent.includes('outside subnet')"
            )
            feedback_v6 = v6_card.locator(".dhcp-pool-feedback")
            self.assertIn("outside subnet", feedback_v6.inner_text())
            self.assertEqual(feedback_v6.get_attribute("role"), "alert")
            self.assertEqual(feedback_v6.get_attribute("aria-live"), "polite")


if __name__ == "__main__":
    unittest.main()
