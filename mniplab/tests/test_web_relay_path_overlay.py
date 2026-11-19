"""Browser tests for the Relay Path overlay in the Web UI Topology canvas.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: an ephemeral uvicorn
server, driven by headless Chromium via Playwright.

Covers the core Relay Path overlay:
- /api/topology emits a top-level relays array containing resolved relay entities.
- Topology SVG contains a dedicated <g class="layer-relay-paths"> between layer-links and layer-nodes.
- Arrowhead marker definition <marker id="relay-arrow"> exists in SVG <defs>.
- Hovering over or keyboard-focusing a router's .relay-badge previews the curved dashed Relay Path.
- Clicking or keyboard-selecting a relaying Router node pins the Relay Path on the canvas.
- Selecting a Router with multiple Relay declarations illuminates all attached paths; hovering a badge isolates one.
- Clicking "View on Canvas" in #dhcp-relay-modal closes modal and pins the Relay Path.
- Pressing Escape or clicking canvas background clears the pinned Relay Path.
- Relay Path coordinates follow moving nodes during simulation ticks.
- Shared IPv4/IPv6 relay paths render as a unified spline with midpoint chip (v4/v6, v4, or v6).
- Polite live region announcements to #topology-live-region on activation, preview, and dismissal.
"""

from typing import Any

import pytest

from mniplab.tests.browser import LiveServerCase, Page

pytestmark = pytest.mark.browser_services

AUTHORED_LAYOUT = {
    "nodes": {
        "r1": {"x": 200, "y": 200},
        "r2": {"x": 400, "y": 200},
        "h2": {"x": 600, "y": 200},
        "h3": {"x": 600, "y": 350},
    }
}


class WebRelayPathOverlayTests(LiveServerCase):
    """Verifies Relay Path overlay rendering, interaction, and accessibility."""

    def _basic_relay_topology(self) -> dict[str, Any]:
        """Return a 3-node topology with a single-family relay on r2."""
        return {
            "nodes": [
                {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"], "relays": []},
                {
                    "id": "r2",
                    "type": "router",
                    "label": "r2",
                    "ip": "10.0.0.2/24",
                    "services": [],
                    "relays": [{"subnets": ["10.20.0.0/24"]}],
                },
                {"id": "h2", "type": "host", "label": "h2", "ip": "10.20.0.2/24", "services": [], "relays": []},
            ],
            "links": [
                {"source": "r1", "target": "r2", "source_intf": "r1-eth0", "target_intf": "r2-eth0", "status": "up"},
                {"source": "r2", "target": "h2", "source_intf": "r2-eth1", "target_intf": "h2-eth0", "status": "up"},
            ],
            "relays": [
                {
                    "id": "relay-r2",
                    "relay_node": "r2",
                    "server_ip": "10.0.0.1",
                    "target_node": "r1",
                    "reachable": True,
                    "subnets": ["10.20.0.0/24"],
                    "client_nodes": ["h2"],
                    "intermediate_switch": None,
                    "families": [4],
                }
            ],
        }

    def _reset_simulation(self, page: Page) -> None:
        """Stop and reset running d3 force simulation before switching topologies."""
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            if (appState.simulation) {
                appState.simulation.stop();
                appState.simulation = null;
            }
        }""")

    def test_relay_path_interactive_controls_scenario(self) -> None:
        """Scenario testing SVG layer structure, hover preview, keyboard navigation, pinning, modal integration, and a11y reduced motion."""
        with self.signed_in_page() as page:
            # 1. Layer ordering & marker definition
            self.render_topology(page, self._basic_relay_topology(), AUTHORED_LAYOUT)
            self.assertEqual(page.locator(".layer-relay-paths").count(), 1)
            layer_order = page.evaluate("""() => {
                const g = document.querySelector('#topology > g');
                return Array.from(g.children).map(c => c.getAttribute('class'));
            }""")
            links_idx = layer_order.index("layer-links")
            relays_idx = layer_order.index("layer-relay-paths")
            nodes_idx = layer_order.index("layer-nodes")
            self.assertLess(links_idx, relays_idx)
            self.assertLess(relays_idx, nodes_idx)
            self.assertEqual(page.locator("#topology defs marker#relay-arrow").count(), 1)

            # 2. Hover preview & unhover dismissal
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 0)
            badge = page.locator(".node[aria-label*='r2'] .relay-badge")
            self.assertEqual(badge.count(), 1)
            badge.hover()
            page.wait_for_timeout(100)

            path_group = page.locator(".layer-relay-paths .relay-path-group[data-relay-id='relay-r2']")
            self.assertEqual(path_group.count(), 1)
            fwd_spline = path_group.locator(".relay-forwarding-leg .relay-path-spline")
            self.assertEqual(fwd_spline.count(), 1)
            self.assertIn("url(#relay-arrow)", fwd_spline.get_attribute("marker-end") or "")
            self.assertEqual(path_group.locator(".relay-forwarding-leg .relay-path-halo").count(), 1)
            self.assertEqual(path_group.locator(".relay-path-chip text").text_content(), "v4")

            page.mouse.move(0, 0)
            page.wait_for_timeout(100)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 0)

            # 3. Keyboard focus preview & polite announcement
            page.evaluate("""() => {
                const b = document.querySelector(".node[aria-label*='r2'] .relay-badge");
                if (b) b.focus();
            }""")
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 1)
            live_text = page.locator("#topology-live-region").text_content()
            self.assertIn("Relay path active: h2 via r2 to r1 (IPv4)", live_text or "")

            page.evaluate("""() => {
                const b = document.querySelector(".node[aria-label*='r2'] .relay-badge");
                if (b) b.blur();
            }""")
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 0)
            self.assertIn("Relay path dismissed", page.locator("#topology-live-region").text_content() or "")

            # 4. Router click selection pins path; mouse movement retains pin
            r2_node = page.locator(".node[aria-label*='r2']")
            r2_node.locator("circle").click()
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 1)
            page.mouse.move(0, 0)
            page.wait_for_timeout(100)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 1)

            # Clear terminal focus and active tabs so Escape unpins without close confirmation
            page.evaluate("() => { document.activeElement?.blur(); window.appState?.tabManager?.closeAllTabs(); }")
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 0)
            self.assertIn("Relay path dismissed", page.locator("#topology-live-region").text_content() or "")

            # 5. Keyboard Enter on router pins path; canvas background click dismisses it
            r2_node.focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 1)

            page.evaluate("""() => {
                const bg = document.querySelector('#topology rect.grid-background') || document.querySelector('#topology');
                bg.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                document.activeElement?.blur();
                if (window.appState && window.appState.tabManager) {
                    window.appState.tabManager.closeAllTabs();
                }
            }""")
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 0)

            # 6. Modal "View on Canvas" closes modal and pins path
            badge.dispatch_event("click")
            page.wait_for_timeout(200)
            modal = page.locator("#dhcp-relay-modal")
            self.assertTrue(modal.is_visible())
            page.locator("#dhcp-relay-view-canvas").click()
            page.wait_for_timeout(200)
            self.assertFalse(modal.is_visible())
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 1)
            page.evaluate("() => { document.activeElement?.blur(); window.appState?.tabManager?.closeAllTabs(); }")
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)

            # 7. Reduced motion and contrast halo verification
            r2_node.dispatch_event("click")
            page.wait_for_timeout(150)
            anim_name = page.evaluate("""() => {
                const el = document.querySelector('.layer-relay-paths .relay-forwarding-leg .relay-path-spline');
                return window.getComputedStyle(el).animationName;
            }""")
            self.assertEqual(anim_name, "relayFlow")

            page.emulate_media(reduced_motion="reduce")
            page.wait_for_timeout(100)
            reduced_anim = page.evaluate("""() => {
                const el = document.querySelector('.layer-relay-paths .relay-forwarding-leg .relay-path-spline');
                return window.getComputedStyle(el).animationName;
            }""")
            self.assertEqual(reduced_anim, "none")
            self.assertNotEqual(page.evaluate("""() => {
                const el = document.querySelector('.layer-relay-paths .relay-forwarding-leg .relay-path-spline');
                return window.getComputedStyle(el).strokeDasharray;
            }"""), "none")

            stroke_width = page.evaluate("""() => {
                const el = document.querySelector('.layer-relay-paths .relay-forwarding-leg .relay-path-halo');
                return window.getComputedStyle(el).strokeWidth;
            }""")
            self.assertEqual(stroke_width, "6px")

            page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
            page.wait_for_timeout(100)
            halo_stroke_dark = page.evaluate("""() => {
                const el = document.querySelector('.layer-relay-paths .relay-forwarding-leg .relay-path-halo');
                return window.getComputedStyle(el).stroke;
            }""")
            self.assertIn("rgba(30, 30, 30", halo_stroke_dark)

            # 8. Multiple Relays: simultaneous illumination on selection, single isolation on badge hover
            multi_topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"], "relays": []},
                    {
                        "id": "r2",
                        "type": "router",
                        "label": "r2",
                        "ip": "10.0.0.2/24",
                        "services": [],
                        "relays": [
                            {"subnets": ["10.20.0.0/24"]},
                            {"subnets": ["10.30.0.0/24"]},
                        ],
                    },
                    {"id": "h2", "type": "host", "label": "h2", "ip": "10.20.0.2/24", "services": [], "relays": []},
                    {"id": "h3", "type": "host", "label": "h3", "ip": "10.30.0.2/24", "services": [], "relays": []},
                ],
                "links": [
                    {"source": "r1", "target": "r2", "source_intf": "r1-eth0", "target_intf": "r2-eth0", "status": "up"},
                    {"source": "r2", "target": "h2", "source_intf": "r2-eth1", "target_intf": "h2-eth0", "status": "up"},
                    {"source": "r2", "target": "h3", "source_intf": "r2-eth2", "target_intf": "h3-eth0", "status": "up"},
                ],
                "relays": [
                    {
                        "id": "relay-r2-1",
                        "relay_node": "r2",
                        "server_ip": "10.0.0.1",
                        "target_node": "r1",
                        "reachable": True,
                        "subnets": ["10.20.0.0/24"],
                        "client_nodes": ["h2"],
                        "intermediate_switch": None,
                        "families": [4],
                    },
                    {
                        "id": "relay-r2-2",
                        "relay_node": "r2",
                        "server_ip": "10.0.0.1",
                        "target_node": "r1",
                        "reachable": True,
                        "subnets": ["10.30.0.0/24"],
                        "client_nodes": ["h3"],
                        "intermediate_switch": None,
                        "families": [4],
                    },
                ],
            }
            self._reset_simulation(page)
            self.render_topology(page, multi_topo, AUTHORED_LAYOUT)
            page.locator(".node[aria-label*='r2']").dispatch_event("click")
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 2)

            first_badge = page.locator(".node[aria-label*='r2'] .relay-badge").first
            first_badge.hover()
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 1)
            self.assertEqual(
                page.locator(".layer-relay-paths .relay-path-group").get_attribute("data-relay-id"),
                "relay-r2-1",
            )

            page.mouse.move(0, 0)
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-group").count(), 2)

    def test_relay_path_topology_and_movement_scenario(self) -> None:
        """Scenario testing dual-stack chips, movement tracking, intermediate switch trunking, floating terminus, and end-to-end lab."""
        from mniplab.tests.labs import load_example
        from mniplab.topology import TopologyBuilder

        with self.signed_in_page() as page:
            # 1. Shared dual-stack midpoint chip
            dual_topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"], "relays": []},
                    {
                        "id": "r2",
                        "type": "router",
                        "label": "r2",
                        "ip": "10.0.0.2/24",
                        "services": [],
                        "relays": [{"subnets": ["10.20.0.0/24", "2001:db8:20::/64"]}],
                    },
                    {"id": "h2", "type": "host", "label": "h2", "ip": "10.20.0.2/24", "services": [], "relays": []},
                ],
                "links": [
                    {"source": "r1", "target": "r2", "source_intf": "r1-eth0", "target_intf": "r2-eth0", "status": "up"},
                    {"source": "r2", "target": "h2", "source_intf": "r2-eth1", "target_intf": "h2-eth0", "status": "up"},
                ],
                "relays": [
                    {
                        "id": "relay-r2",
                        "relay_node": "r2",
                        "server_ip": "10.0.0.1",
                        "target_node": "r1",
                        "reachable": True,
                        "subnets": ["10.20.0.0/24", "2001:db8:20::/64"],
                        "client_nodes": ["h2"],
                        "intermediate_switch": None,
                        "families": [4, 6],
                    }
                ],
            }
            self.render_topology(page, dual_topo, AUTHORED_LAYOUT)
            page.locator(".node[aria-label*='r2']").dispatch_event("click")
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".layer-relay-paths .relay-path-chip text").text_content(), "v4/v6")
            self.assertIn("IPv4/IPv6", page.locator("#topology-live-region").text_content() or "")

            # 2. Node movement simulation tracking
            initial_d = page.locator(".layer-relay-paths .relay-forwarding-leg .relay-path-spline").get_attribute("d")
            updated_d = page.evaluate("""async () => {
                const { ticked } = await import('/static/js/topology/renderer.js');
                const { appState: state } = await import('/static/js/state/app-state.js');
                const node = state.simulation ? state.simulation.nodes().find(n => n.id === 'r2') : null;
                if (node) {
                    node.x += 100;
                    node.y += 100;
                    ticked();
                }
                return document.querySelector('.layer-relay-paths .relay-forwarding-leg .relay-path-spline')?.getAttribute('d');
            }""")
            self.assertIsNotNone(updated_d)
            self.assertNotEqual(initial_d, updated_d)

            # 3. Intermediate switch convergence and trunking
            self._reset_simulation(page)
            switch_topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": ["dhcp"], "relays": []},
                    {
                        "id": "r2",
                        "type": "router",
                        "label": "r2",
                        "ip": "10.0.0.2/24",
                        "services": [],
                        "relays": [{"subnets": ["10.20.0.0/24"]}],
                    },
                    {"id": "s1", "type": "switch", "label": "s1", "ip": "", "services": [], "relays": []},
                    {"id": "h2a", "type": "host", "label": "h2a", "ip": "10.20.0.2/24", "services": [], "relays": []},
                    {"id": "h2b", "type": "host", "label": "h2b", "ip": "10.20.0.3/24", "services": [], "relays": []},
                ],
                "links": [
                    {"source": "r1", "target": "r2", "status": "up"},
                    {"source": "r2", "target": "s1", "status": "up"},
                    {"source": "s1", "target": "h2a", "status": "up"},
                    {"source": "s1", "target": "h2b", "status": "up"},
                ],
                "relays": [
                    {
                        "id": "relay-r2",
                        "relay_node": "r2",
                        "server_ip": "10.0.0.1",
                        "target_node": "r1",
                        "reachable": True,
                        "subnets": ["10.20.0.0/24"],
                        "client_nodes": ["h2a", "h2b"],
                        "intermediate_switch": "s1",
                        "families": [4],
                    }
                ],
            }
            authored_switch = {
                "nodes": {
                    "r1": {"x": 100, "y": 200},
                    "r2": {"x": 300, "y": 200},
                    "s1": {"x": 500, "y": 200},
                    "h2a": {"x": 650, "y": 150},
                    "h2b": {"x": 650, "y": 250},
                }
            }
            self.render_topology(page, switch_topo, authored_switch)
            page.locator(".node[aria-label*='r2']").dispatch_event("click")
            page.wait_for_timeout(150)

            client_splines = page.locator(".layer-relay-paths .relay-client-legs .client-leg.relay-path-spline")
            self.assertEqual(client_splines.count(), 2)
            trunk_spline = page.locator(".layer-relay-paths .relay-client-legs .trunk-leg.relay-path-spline")
            self.assertEqual(trunk_spline.count(), 1)
            self.assertIn("url(#relay-arrow)", trunk_spline.get_attribute("marker-end") or "")
            self.assertIn("url(#relay-arrow)", client_splines.first.get_attribute("marker-end") or "")
            trunk_d = trunk_spline.get_attribute("d") or ""
            self.assertTrue(trunk_d.startswith("M 500 200"))
            self.assertTrue(trunk_d.endswith("300 200"))

            # 4. Unreachable floating terminus and drag tracking
            self._reset_simulation(page)
            unreachable_topo = {
                "nodes": [
                    {
                        "id": "r3",
                        "type": "router",
                        "label": "r3",
                        "ip": "10.30.0.1/24",
                        "services": [],
                        "relays": [{"subnets": ["10.30.0.0/24"]}],
                    },
                    {"id": "h3", "type": "host", "label": "h3", "ip": "10.30.0.2/24", "services": [], "relays": []},
                ],
                "links": [
                    {"source": "r3", "target": "h3", "status": "up"},
                ],
                "relays": [
                    {
                        "id": "relay-r3",
                        "relay_node": "r3",
                        "server_ip": "10.99.0.1",
                        "target_node": None,
                        "reachable": False,
                        "subnets": ["10.30.0.0/24"],
                        "client_nodes": ["h3"],
                        "intermediate_switch": None,
                        "families": [4],
                    }
                ],
            }
            authored_unreachable = {
                "nodes": {
                    "r3": {"x": 300, "y": 200},
                    "h3": {"x": 500, "y": 200},
                }
            }
            self.render_topology(page, unreachable_topo, authored_unreachable)
            page.locator(".node[aria-label*='r3']").dispatch_event("click")
            page.wait_for_timeout(150)

            terminus = page.locator(".layer-relay-paths .relay-unreachable-terminus")
            self.assertEqual(terminus.count(), 1)
            self.assertIn("10.99.0.1 (Unreachable)", terminus.text_content() or "")
            self.assertEqual(terminus.get_attribute("tabindex"), "0")
            self.assertEqual(terminus.get_attribute("role"), "img")
            self.assertIn("10.99.0.1", terminus.get_attribute("aria-label") or "")
            self.assertIn("translate(210, 200)", terminus.get_attribute("transform") or "")

            updated_transform = page.evaluate("""async () => {
                const { ticked } = await import('/static/js/topology/renderer.js');
                const { appState: state } = await import('/static/js/state/app-state.js');
                const node = state.simulation ? state.simulation.nodes().find(n => n.id === 'r3') : null;
                if (node) {
                    node.x += 100;
                    ticked();
                }
                return document.querySelector('.layer-relay-paths .relay-unreachable-terminus')?.getAttribute('transform');
            }""")
            self.assertIsNotNone(updated_transform)
            self.assertIn("translate(310, 200)", updated_transform or "")

            # 5. End-to-end lab rendering (dhcp-lab)
            self._reset_simulation(page)
            net = load_example("dhcp-lab").build_network()
            try:
                topo = TopologyBuilder.build(net)
                relays = topo.get("relays", [])
                self.assertEqual(len(relays), 1)

                r2_relay = relays[0]
                self.assertEqual(r2_relay["relay_node"], "r2")
                self.assertTrue(r2_relay["reachable"])
                self.assertEqual(r2_relay["target_node"], "r1")
                self.assertEqual(r2_relay["client_nodes"], ["h2"])
                self.assertEqual(r2_relay["families"], [4, 6])

                authored_e2e = {
                    "nodes": {
                        "r1": {"x": 200, "y": 200},
                        "sw1": {"x": 100, "y": 200},
                        "h1": {"x": 100, "y": 350},
                        "rogue": {"x": 100, "y": 50},
                        "r2": {"x": 400, "y": 200},
                        "h2": {"x": 550, "y": 200},
                        "h3": {"x": 200, "y": 350},
                        "h4": {"x": 300, "y": 350},
                        "h5": {"x": 400, "y": 350},
                        "h6": {"x": 500, "y": 350},
                    }
                }
                self.render_topology(page, topo, authored_e2e)

                # Test r2 (relayed dual-stack)
                page.locator(".node[aria-label*='r2']").dispatch_event("click")
                page.wait_for_timeout(150)
                self.assertEqual(page.locator(".layer-relay-paths .relay-path-chip text").text_content(), "v4/v6")
                self.assertEqual(page.locator(".layer-relay-paths .relay-client-legs .client-leg.relay-path-spline").count(), 1)
            finally:
                net.stop()
