"""Browser tests for Service badges in the Topology canvas.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: an ephemeral uvicorn
server, driven by headless Chromium via Playwright.

Covers the Service badge in the Topology:
- A Node with a DHCP Service shows a badge in the Topology.
- Its display type is unchanged — a Router still draws as a Router.
- A Node with no Service shows no badge.
- Two Nodes serving the same Subnet each show their own badge.
- The badge is announced to assistive technology, not conveyed by colour or shape alone.
- The badge survives topology updates broadcast over the WebSocket.
- A Relay badge names its covered Subnets and composes with a Service badge.
"""

import pytest

from mniplab.tests.browser import LiveServerCase

pytestmark = pytest.mark.browser_services

AUTHORED_LAYOUT = {
    "nodes": {
        "r1": {"x": 200, "y": 200},
        "r2": {"x": 400, "y": 200},
        "h1": {"x": 200, "y": 400},
    }
}


class TopologyServiceBadgeTests(LiveServerCase):
    """Verifies service badge display and accessibility in the Topology canvas."""

    def test_node_with_dhcp_shows_badge_and_preserves_display_type(self) -> None:
        """A Router offering DHCP shows a DHCP badge, keeps router display type, and host without service has no badge."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"]},
                    {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.2/24", "services": []},
                ],
                "links": [
                    {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
                ],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            # r1 must have a DHCP service badge with visible text
            badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(badge.count(), 1)
            badge_text = badge.locator(".service-badge-text").text_content()
            self.assertEqual(badge_text, "DHCP")

            # r1's display type is unchanged — router icon svg is rendered
            r1_node = page.locator(".node[aria-label*='r1']")
            r1_icon = r1_node.locator("svg path")
            self.assertGreater(r1_icon.count(), 0)

            # h1 has no services and must have no badge
            h1_badges = page.locator(".node[aria-label*='h1'] .service-badge")
            self.assertEqual(h1_badges.count(), 0)

            # Assistive technology announcement via aria-label
            r1_aria = r1_node.get_attribute("aria-label")
            self.assertIn("Services: DHCP", r1_aria or "")

            h1_node = page.locator(".node[aria-label*='h1']")
            h1_aria = h1_node.get_attribute("aria-label")
            self.assertNotIn("Services:", h1_aria or "")

    def test_two_nodes_serving_same_subnet_each_show_badge(self) -> None:
        """Two nodes offering DHCP on the same subnet each show their own badge."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"]},
                    {"id": "r2", "type": "router", "label": "r2", "ip": "10.0.0.254/24", "services": ["dhcp"]},
                    {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.2/24", "services": []},
                ],
                "links": [
                    {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
                    {"source": "r2", "target": "h1", "source_intf": "r2-eth0", "target_intf": "h1-eth1", "status": "up"},
                ],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            r1_badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(r1_badge.count(), 1)
            self.assertEqual(r1_badge.locator(".service-badge-text").text_content(), "DHCP")

            r2_badge = page.locator(".node[aria-label*='r2'] .service-badge-dhcp")
            self.assertEqual(r2_badge.count(), 1)
            self.assertEqual(r2_badge.locator(".service-badge-text").text_content(), "DHCP")

            h1_badges = page.locator(".node[aria-label*='h1'] .service-badge")
            self.assertEqual(h1_badges.count(), 0)

    def test_node_with_multiple_services_shows_composed_badges(self) -> None:
        """A node offering multiple services shows composed badges and announces both."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp", "dns"]},
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            badges = page.locator(".node[aria-label*='r1'] .service-badge")
            self.assertEqual(badges.count(), 2)

            texts = [badges.nth(i).locator(".service-badge-text").text_content() for i in range(2)]
            self.assertEqual(texts, ["DHCP", "DNS"])

            r1_aria = page.locator(".node[aria-label*='r1']").get_attribute("aria-label")
            self.assertIn("Services: DHCP, DNS", r1_aria or "")

    def test_dns_node_context_menu_opens_dns_modal(self) -> None:
        """A DNS node offers an accessible context-menu action for its modal."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {
                        "id": "dns1",
                        "type": "host",
                        "label": "dns1",
                        "ip": "10.0.0.1/24",
                        "services": ["dns"],
                        "service_details": [{"name": "dns", "badge": "DNS", "zones": ["example.com."]}],
                    },
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            page.locator(".node[aria-label*='dns1']").click(button="right")
            page.wait_for_selector("#node-context-menu:not(.hidden)")
            self.assertEqual(page.locator("#node-dns-btn").get_attribute("role"), "menuitem")

            page.locator("#node-dns-btn").click()
            page.wait_for_selector("#dns-modal[style*='flex']")
            self.assertIn("dns1", page.locator("#dns-modal-title").text_content() or "")

            # The menu entry is gone by the time the modal closes, so focus
            # returns to the Node whose menu opened it, not to the body.
            page.keyboard.press("Escape")
            page.wait_for_selector("#dns-modal", state="hidden")
            self.assertEqual(
                page.evaluate("() => document.activeElement.getAttribute('aria-label')"),
                page.locator(".node[aria-label*='dns1']").get_attribute("aria-label"),
            )

    def test_dns_modal_renders_query_types_and_zone_state(self) -> None:
        """The DNS modal shows query-type counters and Knot's zone state."""
        with self.signed_in_page() as page:
            page.route(
                "**/api/labs/*/dns/dns1/stats",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"node":"dns1","stats":{"server-operation":{"query":3},"query-type":{"A":2,"AAAA":1},"response-code":{"NOERROR":2,"NXDOMAIN":1}}}',
                ),
            )
            page.route(
                "**/api/labs/*/dns/dns1/zones/example.com./status",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"node":"dns1","zone":"example.com.","status":{"role":"master","serial":42,"transaction":"-"},"serial":42,"primary_ns":"ns1.example.com."}',
                ),
            )
            topo = {
                "nodes": [
                    {
                        "id": "dns1",
                        "type": "host",
                        "label": "dns1",
                        "ip": "10.0.0.1/24",
                        "services": ["dns"],
                        "service_details": [{"name": "dns", "badge": "DNS", "zones": ["example.com."]}],
                    },
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)
            page.locator(".node[aria-label*='dns1'] .service-badge-dns").click()
            page.wait_for_selector("#dns-modal[style*='flex']")
            page.wait_for_function(
                "() => document.querySelector('#dns-stat-query-types').textContent.includes('A')"
            )

            self.assertIn("A", page.locator("#dns-stat-query-types").text_content() or "")
            self.assertIn("AAAA", page.locator("#dns-stat-query-types").text_content() or "")
            self.assertEqual(page.locator("#dns-status-serial").text_content(), "42")
            self.assertEqual(page.locator("#dns-status-state").text_content(), "-")

    def test_dns_broadcast_resets_an_open_edit_form(self) -> None:
        """A record broadcast clears an edit draft before it can submit stale old values."""
        with self.signed_in_page() as page:
            page.route(
                "**/api/labs/*/dns/dns1/zones/example.com./records",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"node":"dns1","zone":"example.com.","records":[{"owner":"web","ttl":300,"type":"A","data":"10.0.0.2"}]}',
                ),
            )
            page.route(
                "**/api/labs/*/dns/dns1/zones/example.com./status",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"node":"dns1","zone":"example.com.","status":{"role":"master","serial":42,"transaction":"-"},"serial":42,"primary_ns":"ns1.example.com."}',
                ),
            )
            topo = {
                "nodes": [
                    {
                        "id": "dns1",
                        "type": "host",
                        "label": "dns1",
                        "ip": "10.0.0.1/24",
                        "services": ["dns"],
                        "service_details": [{"name": "dns", "badge": "DNS", "zones": ["example.com."]}],
                    },
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)
            page.locator(".node[aria-label*='dns1'] .service-badge-dns").click()
            page.wait_for_selector("#dns-modal[style*='flex']")
            page.wait_for_function("() => document.querySelector('#dns-records-tbody').textContent.includes('web')")

            page.locator(".dns-record-action-btn").first.click()
            self.assertEqual(page.locator("#dns-record-submit").text_content(), "Save Record")
            page.evaluate("""
                async () => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    appState.wsMessageHandler({
                        type: 'DNS_RECORD_UPDATED',
                        data: {node: 'dns1', zone: 'example.com.', owner: 'web', type: 'A', data: '10.0.0.9', action: 'replace'},
                    });
                }
            """)
            page.wait_for_function("() => document.querySelector('#dns-record-submit').textContent === 'Add Record'")

    def test_dns_form_rejects_invalid_owner_before_request(self) -> None:
        """The record form rejects malformed DNS owners before sending a request."""
        with self.signed_in_page() as page:
            page.route(
                "**/api/labs/*/dns/dns1/zones/example.com./records",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"node":"dns1","zone":"example.com.","records":[]}',
                ),
            )
            page.route(
                "**/api/labs/*/dns/dns1/zones/example.com./status",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"node":"dns1","zone":"example.com.","status":{"role":"master","serial":42,"transaction":"-"},"serial":42,"primary_ns":"ns1.example.com."}',
                ),
            )
            topo = {
                "nodes": [{
                    "id": "dns1",
                    "type": "host",
                    "label": "dns1",
                    "ip": "10.0.0.1/24",
                    "services": ["dns"],
                    "service_details": [{"name": "dns", "badge": "DNS", "zones": ["example.com."]}],
                }],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)
            page.locator(".node[aria-label*='dns1'] .service-badge-dns").click()
            page.wait_for_selector("#dns-modal[style*='flex']")
            page.locator("#dns-record-owner").fill("foo..example")
            page.locator("#dns-record-data").fill("10.0.0.9")
            page.locator("#dns-record-submit").click()
            page.wait_for_function(
                "() => document.querySelector('#dns-record-error').textContent.includes('valid DNS name')"
            )
            self.assertEqual(page.locator("#dns-record-submit").text_content(), "Add Record")

    def test_relay_badge_names_subnets_and_composes_with_service(self) -> None:
        """A Router can show a distinct Relay badge alongside its DHCP Service badge."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {
                        "id": "r1",
                        "type": "router",
                        "label": "r1",
                        "ip": "10.0.0.1/24",
                        "services": ["dhcp"],
                        "relays": [{
                            "subnets": ["10.50.0.0/24", "2001:db8:50::/64"],
                        }],
                    },
                    {
                        "id": "h1",
                        "type": "host",
                        "label": "h1",
                        "ip": "10.50.0.2/24",
                        "services": [],
                        "relays": [],
                    },
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            r1_node = page.locator(".node[aria-label*='r1']")
            service_badge = r1_node.locator(".service-badge-dhcp")
            relay_badge = r1_node.locator(".relay-badge")

            self.assertEqual(service_badge.count(), 1)
            self.assertEqual(relay_badge.count(), 1)
            # The badge is stacked: a heading, then one Subnet per line tagged
            # with its address family.
            self.assertEqual(
                [t.strip() for t in relay_badge.locator(".relay-badge-text tspan").all_text_contents()],
                ["DHCP RELAY", "v4", "10.50.0.0/24", "v6", "2001:db8:50::/64"],
            )
            self.assertEqual(
                relay_badge.locator("title").text_content(),
                "Relay: 10.50.0.0/24, 2001:db8:50::/64 — Relay for Subnets",
            )
            self.assertEqual(r1_node.locator(".service-badge, .relay-badge").count(), 2)

            # The Router remains a Router, and the Relay is announced by text as
            # well as by its non-colour badge treatment.
            self.assertGreater(r1_node.locator("svg path").count(), 0)
            r1_aria = r1_node.get_attribute("aria-label")
            self.assertIn("Services: DHCP", r1_aria or "")
            self.assertIn("Relays: 10.50.0.0/24, 2001:db8:50::/64", r1_aria or "")

            # The Relay badge is a control, the same way a Service badge is:
            # it opens the Relay's activity panel from the Topology.
            self.assertEqual(relay_badge.get_attribute("role"), "button")
            self.assertEqual(relay_badge.get_attribute("tabindex"), "0")
            relay_aria = relay_badge.get_attribute("aria-label")
            self.assertIn("Relay for Subnets", relay_aria or "")
            self.assertIn("press Enter to view what the Relay forwarded", relay_aria or "")

            # A Node with neither role still carries empty, separate fields and
            # draws no badge.
            self.assertEqual(page.locator(".node[aria-label*='h1'] .service-badge").count(), 0)
            self.assertEqual(page.locator(".node[aria-label*='h1'] .relay-badge").count(), 0)

    def test_badge_survives_websocket_topology_update(self) -> None:
        """The badge survives topology updates broadcast over the WebSocket."""
        with self.signed_in_page() as page:
            # Initial topology without services
            initial_topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": []},
                    {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.2/24", "services": []},
                ],
                "links": [
                    {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
                ],
            }
            self.render_topology(page, initial_topo, AUTHORED_LAYOUT)

            # Initially no badge on r1
            self.assertEqual(page.locator(".node[aria-label*='r1'] .service-badge").count(), 0)

            # Deliver a topology_update via loadTopologyFromData (as happens over WebSocket)
            updated_topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"]},
                    {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.2/24", "services": []},
                ],
                "links": [
                    {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
                ],
            }
            page.evaluate(
                """async (topo) => {
                    const { loadTopologyFromData } = await import('/static/js/lab/lifecycle.js');
                    await loadTopologyFromData(topo);
                }""",
                updated_topo,
            )
            page.wait_for_timeout(300)

            # r1 now shows badge
            r1_badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(r1_badge.count(), 1)
            self.assertEqual(r1_badge.locator(".service-badge-text").text_content(), "DHCP")
            self.assertIn("Services: DHCP", page.locator(".node[aria-label*='r1']").get_attribute("aria-label") or "")

            # Send a second update where both r1 and h1 have services
            second_update = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"]},
                    {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.2/24", "services": ["dhcp"]},
                ],
                "links": [
                    {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
                ],
            }
            page.evaluate(
                """async (topo) => {
                    const { loadTopologyFromData } = await import('/static/js/lab/lifecycle.js');
                    await loadTopologyFromData(topo);
                }""",
                second_update,
            )
            page.wait_for_timeout(300)

            # Both still / now have badges
            self.assertEqual(page.locator(".node[aria-label*='r1'] .service-badge-dhcp").count(), 1)
            self.assertEqual(page.locator(".node[aria-label*='h1'] .service-badge-dhcp").count(), 1)

            # Send a third update with dual-stack service_details
            third_update = {
                "nodes": [
                    {
                        "id": "r1",
                        "type": "router",
                        "label": "r1",
                        "ip": "10.0.0.1/24",
                        "services": ["dhcp"],
                        "service_details": [{"name": "dhcp", "badge": "DHCP · DHCPv6", "families": [4, 6]}],
                    },
                    {"id": "h1", "type": "host", "label": "h1", "ip": "10.0.0.2/24", "services": []},
                ],
                "links": [
                    {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
                ],
            }
            page.evaluate(
                """async (topo) => {
                    const { loadTopologyFromData } = await import('/static/js/lab/lifecycle.js');
                    await loadTopologyFromData(topo);
                }""",
                third_update,
            )
            page.wait_for_timeout(300)

            r1_badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(r1_badge.count(), 1)
            self.assertEqual(r1_badge.locator(".service-badge-text").text_content(), "DHCP · DHCPv6")
            self.assertIn("Services: DHCP · DHCPv6", page.locator(".node[aria-label*='r1']").get_attribute("aria-label") or "")

    def test_node_with_ipv6_dhcp_shows_dhcpv6_badge(self) -> None:
        """A node offering only IPv6 DHCP displays a 'DHCPv6' badge and announces it."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {
                        "id": "r1",
                        "type": "router",
                        "label": "r1",
                        "ip": "2001:db8:1::1/64",
                        "services": ["dhcp"],
                        "service_details": [{"name": "dhcp", "badge": "DHCPv6", "families": [6]}],
                    },
                    {"id": "h1", "type": "host", "label": "h1", "ip": "2001:db8:1::2/64", "services": []},
                ],
                "links": [
                    {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0", "status": "up"},
                ],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(badge.count(), 1)
            self.assertEqual(badge.locator(".service-badge-text").text_content(), "DHCPv6")

            # Preserves display type
            r1_node = page.locator(".node[aria-label*='r1']")
            self.assertGreater(r1_node.locator("svg path").count(), 0)

            # Accessible announcements
            r1_aria = r1_node.get_attribute("aria-label")
            self.assertIn("Services: DHCPv6", r1_aria or "")
            badge_aria = badge.get_attribute("aria-label")
            self.assertEqual(badge_aria, "DHCPv6 Service — press Enter to view Leases")
            self.assertEqual(badge.locator("title").text_content(), "DHCPv6 Service")

    def test_node_with_dual_stack_dhcp_shows_single_dual_family_badge(self) -> None:
        """A node offering dual-stack DHCP shows exactly one badge reading 'DHCP · DHCPv6'."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {
                        "id": "r1",
                        "type": "router",
                        "label": "r1",
                        "ip": "10.0.0.1/24",
                        "services": ["dhcp"],
                        "service_details": [{"name": "dhcp", "badge": "DHCP · DHCPv6", "families": [4, 6]}],
                    },
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            # Exactly one badge drawn for the single dual-family service
            all_badges = page.locator(".node[aria-label*='r1'] .service-badge")
            self.assertEqual(all_badges.count(), 1)

            badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(badge.count(), 1)
            self.assertEqual(badge.locator(".service-badge-text").text_content(), "DHCP · DHCPv6")

            r1_node = page.locator(".node[aria-label*='r1']")
            self.assertIn("Services: DHCP · DHCPv6", r1_node.get_attribute("aria-label") or "")
            badge_aria = badge.get_attribute("aria-label")
            self.assertEqual(badge_aria, "DHCP · DHCPv6 Service — press Enter to view Leases")
            self.assertEqual(badge.locator("title").text_content(), "DHCP · DHCPv6 Service")

    def test_badge_updates_when_learner_pool_edit_changes_families_served(self) -> None:
        """The badge updates in place when a dhcp_pool_update broadcast arrives."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {
                        "id": "r1",
                        "type": "router",
                        "label": "r1",
                        "ip": "10.0.0.1/24",
                        "services": ["dhcp"],
                        "service_details": [{"name": "dhcp", "badge": "DHCP", "families": [4]}],
                    },
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(badge.locator(".service-badge-text").text_content(), "DHCP")

            # Broadcast a pool update that adds an IPv6 pool (now dual-family)
            page.evaluate(
                """async (msg) => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    if (appState && appState.wsMessageHandler) {
                        appState.wsMessageHandler(msg);
                    }
                }""",
                {
                    "type": "dhcp_pool_update",
                    "data": {
                        "node": "r1",
                        "pool": {"cidr": "2001:db8:1::/64", "family": 6},
                        "pools": [
                            {"cidr": "10.0.0.0/24", "family": 4},
                            {"cidr": "2001:db8:1::/64", "family": 6},
                        ],
                    },
                },
            )
            page.wait_for_timeout(300)

            # Badge updates to dual-stack, still 1 badge
            self.assertEqual(page.locator(".node[aria-label*='r1'] .service-badge").count(), 1)
            self.assertEqual(badge.locator(".service-badge-text").text_content(), "DHCP · DHCPv6")
            r1_node = page.locator(".node[aria-label*='r1']")
            self.assertIn("Services: DHCP · DHCPv6", r1_node.get_attribute("aria-label") or "")

            # Broadcast a pool update where only IPv6 pool remains
            page.evaluate(
                """async (msg) => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    if (appState && appState.wsMessageHandler) {
                        appState.wsMessageHandler(msg);
                    }
                }""",
                {
                    "type": "dhcp_pool_update",
                    "data": {
                        "node": "r1",
                        "pool": {"cidr": "2001:db8:1::/64", "family": 6},
                        "pools": [
                            {"cidr": "2001:db8:1::/64", "family": 6},
                        ],
                    },
                },
            )
            page.wait_for_timeout(300)

            # Badge updates to IPv6 only
            self.assertEqual(page.locator(".node[aria-label*='r1'] .service-badge").count(), 1)
            self.assertEqual(badge.locator(".service-badge-text").text_content(), "DHCPv6")
            self.assertIn("Services: DHCPv6", r1_node.get_attribute("aria-label") or "")

    def test_service_badge_renders_and_meets_contrast_in_dark_theme(self) -> None:
        """The service badge renders and maintains accessibility under dark theme."""
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {
                        "id": "r1",
                        "type": "router",
                        "label": "r1",
                        "ip": "10.0.0.1/24",
                        "services": ["dhcp"],
                        "service_details": [{"name": "dhcp", "badge": "DHCP · DHCPv6", "families": [4, 6]}],
                    },
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            # Switch to dark theme
            page.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
            page.wait_for_timeout(200)

            badge = page.locator(".node[aria-label*='r1'] .service-badge-dhcp")
            self.assertEqual(badge.count(), 1)
            self.assertEqual(badge.locator(".service-badge-text").text_content(), "DHCP · DHCPv6")

            # Verify badge is visible and correctly styled in dark theme
            badge_bg = badge.locator(".service-badge-bg")
            self.assertTrue(badge_bg.is_visible())
