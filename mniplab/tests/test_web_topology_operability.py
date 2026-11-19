"""Browser tests for the mouse-free Topology canvas.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``:
a real uvicorn server on a real port, driven by headless Chromium over real HTTP.
The Topology canvas must be
operable without a mouse or multi-touch: a focused Node can be repositioned by
keyboard (Arrow keys nudge its pinned position, Ctrl+Arrow still jumps focus to a
neighbor), explicit Zoom In / Zoom Out controls drive the existing zoom behavior,
and a Topology Group is keyboard-reachable with a non-empty accessible name.
"""

import unittest

import pytest

from mniplab.tests.browser import LiveServerCase, Page

pytestmark = pytest.mark.browser_workspace

# A small, fully-authored topology: two nodes joined by one link, each with a
# canonical position so renderTopology places them statically (no force
# simulation) and pins them. The stable positions make the nudge and zoom
# assertions deterministic.
TOPOLOGY = {
    "nodes": [
        {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1"},
        {"id": "r2", "type": "router", "label": "r2", "ip": "10.0.0.2"},
    ],
    "links": [
        {
            "source": "r1",
            "target": "r2",
            "source_intf": "r1-eth0",
            "target_intf": "r2-eth0",
            "status": "up",
        }
    ],
}
AUTHORED_LAYOUT = {"nodes": {"r1": {"x": 300, "y": 200}, "r2": {"x": 500, "y": 200}}}


class TopologyOperabilityTests(LiveServerCase):
    """Drives the Topology canvas by keyboard and single-pointer controls alone."""

    def _node_x(self, page: Page, node_id: str) -> float:
        return page.evaluate(
            """async (id) => {
                const { appState } = await import('/static/js/state/app-state.js');
                return appState.simulation.nodes().find(n => n.id === id).x;
            }""",
            node_id,
        )

    def test_node_nudges_by_keyboard_and_ctrl_arrow_moves_focus(self):
        with self.signed_in_page() as page:
            self.render_topology(page, TOPOLOGY, AUTHORED_LAYOUT, settle_ms=900)

            node = page.locator('g.node', has_text="r1")
            node.focus()
            self.assertTrue(
                page.evaluate(
                    "() => document.activeElement.classList.contains('node') "
                    "&& document.activeElement.__data__.id === 'r1'"
                ),
                "the r1 node did not receive focus",
            )

            x_before = self._node_x(page, "r1")
            page.keyboard.press("ArrowRight")
            x_after = self._node_x(page, "r1")
            self.assertGreater(
                x_after, x_before,
                "a plain Arrow key should nudge the focused node's position",
            )
            # The nudge pins the node the same way a drag does (fx tracks x).
            self.assertEqual(
                page.evaluate(
                    """async () => {
                        const { appState } = await import('/static/js/state/app-state.js');
                        const n = appState.simulation.nodes().find(n => n.id === 'r1');
                        return n.fx;
                    }"""
                ),
                x_after,
                "the nudged node should be pinned (fx tracks x), like a drag",
            )

            # Ctrl+Arrow keeps the original neighbor-jump behavior: focus moves
            # to the connected neighbor in that direction instead of nudging.
            page.keyboard.press("Control+ArrowRight")
            self.assertTrue(
                page.evaluate(
                    "() => document.activeElement && "
                    "document.activeElement.__data__ && "
                    "document.activeElement.__data__.id === 'r2'"
                ),
                "Ctrl+Arrow should move focus to the neighbor in that direction",
            )

    def test_zoom_in_and_out_controls_change_the_zoom_transform(self):
        with self.signed_in_page() as page:
            self.render_topology(page, TOPOLOGY, AUTHORED_LAYOUT, settle_ms=900)

            scale_before = page.evaluate(
                "() => document.querySelector('#topology').__zoom.k"
            )

            page.evaluate("() => document.getElementById('zoom-in').click()")
            page.wait_for_timeout(250)  # the 150ms zoom transition
            scale_in = page.evaluate(
                "() => document.querySelector('#topology').__zoom.k"
            )
            self.assertGreater(
                scale_in, scale_before,
                "Zoom In should increase the zoom transform's scale",
            )

            page.evaluate("() => document.getElementById('zoom-out').click()")
            page.wait_for_timeout(250)
            scale_out = page.evaluate(
                "() => document.querySelector('#topology').__zoom.k"
            )
            self.assertLess(
                scale_out, scale_in,
                "Zoom Out should decrease the zoom transform's scale",
            )

    def test_group_is_keyboard_reachable_with_an_accessible_name(self):
        with self.signed_in_page() as page:
            self.render_topology(page, TOPOLOGY, AUTHORED_LAYOUT, settle_ms=900)

            # Create a two-node group the same way the UI does, then render it.
            page.evaluate(
                """async () => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    const { updateGroups } = await import('/static/js/topology/groups.js');
                    appState.groups.push({ id: 'group-test', name: 'Test Group', nodes: ['r1', 'r2'] });
                    updateGroups();
                }"""
            )

            group = page.locator("g.group-item")
            self.assertEqual(group.count(), 1, "expected exactly one rendered group")

            self.assertEqual(group.get_attribute("tabindex"), "0")
            self.assertEqual(group.get_attribute("role"), "button")

            aria_label = group.get_attribute("aria-label") or ""
            self.assertTrue(aria_label.strip(), "the group has no accessible name")
            self.assertIn("Test Group", aria_label)
            self.assertIn("2 nodes", aria_label)

            # A tabindex=0 SVG group is focusable; focusing it should land
            # focus on the group element itself.
            group.focus()
            self.assertTrue(
                page.evaluate(
                    "() => document.activeElement.classList.contains('group-item')"
                ),
                "the group is not keyboard-focusable",
            )

    def test_fit_centers_in_the_drawing_area_after_workspace_resize(self) -> None:
        """Fit uses the visible canvas and leaves Authored Layout positions intact."""
        for width, height in ((1864, 898), (1024, 768)):
            with self.subTest(width=width), self.signed_in_page() as page:
                page.set_viewport_size({"width": width, "height": height})
                self.render_topology(page, TOPOLOGY, AUTHORED_LAYOUT, settle_ms=900)
                positions = page.locator("g.node").evaluate_all(
                    "nodes => nodes.map(node => node.getAttribute('transform'))"
                )
                page.set_viewport_size({"width": width - 100, "height": height})
                page.wait_for_timeout(600)
                page.locator("#fit-view").click()
                page.wait_for_timeout(850)
                geometry = page.locator("#topology").evaluate(
                    """svg => {
                        const drawing = svg.parentElement.getBoundingClientRect();
                        const box = svg.getBoundingClientRect();
                        const controls = document.getElementById(
                            'topology-canvas-controls'
                        ).getBoundingClientRect();
                        const connection = document.getElementById('ws-status').getBoundingClientRect();
                        const card = svg.closest(
                            '.topology-container'
                        ).getBoundingClientRect();
                        const panel = document.querySelector(
                            '[data-pane=topology]'
                        ).getBoundingClientRect();
                        const points = [...svg.querySelectorAll('g.node')].map(node => {
                            const point = svg.createSVGPoint();
                            return point.matrixTransform(node.getScreenCTM());
                        });
                        const xs = points.map(point => point.x);
                        const ys = points.map(point => point.y);
                        return {
                            centerX: (Math.min(...xs) + Math.max(...xs)) / 2,
                            centerY: (Math.min(...ys) + Math.max(...ys)) / 2,
                            drawingX: drawing.x + drawing.width / 2,
                            drawingY: drawing.y + drawing.height / 2,
                            svgBottom: box.bottom, drawingBottom: drawing.bottom,
                            controlsTop: controls.top, controlsLeft: controls.left,
                            connectionRight: connection.right,
                            leftGap: card.left - panel.left,
                            rightGap: panel.right - card.right
                        };
                    }"""
                )
                self.assertAlmostEqual(
                    geometry["leftGap"], geometry["rightGap"], delta=1
                )
                self.assertAlmostEqual(
                    geometry["centerX"], geometry["drawingX"], delta=2
                )
                self.assertAlmostEqual(
                    geometry["centerY"], geometry["drawingY"], delta=2
                )
                self.assertLessEqual(
                    geometry["svgBottom"], geometry["drawingBottom"] + 1
                )
                self.assertGreaterEqual(
                    geometry["controlsTop"], geometry["drawingBottom"]
                )
                # The connection status leads the footer, before the controls.
                self.assertLessEqual(
                    geometry["connectionRight"], geometry["controlsLeft"]
                )
                self.assertEqual(
                    page.locator("g.node").evaluate_all(
                        "nodes => nodes.map(node => node.getAttribute('transform'))"
                    ),
                    positions,
                )


if __name__ == "__main__":
    unittest.main()
