"""Browser test: the Tools modal tracks the lab's node list after page load.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``:
a real uvicorn server on a real port, driven by headless Chromium over real
HTTP. ``/api/labs/fake/topology`` is intercepted with Playwright's ``page.route`` so a
lab can appear to start or stop after the page has loaded without standing up
a real Mininet lab (which needs root, unavailable to these browser tests) —
the tool dropdowns must track that, not just what was active at page load.
"""

import json
import unittest

import pytest

from mniplab.webserver.auth import create_session
from mniplab.tests.browser import BrowserContext, LiveServerCase, Page

pytestmark = pytest.mark.browser_workspace


class ToolsModalRefreshTests(LiveServerCase):
    """Drives the Tools modal against a topology that changes after page load."""

    def _authenticated_page(
        self, topology: dict, initial_node_count: int
    ) -> tuple[BrowserContext, Page]:
        """Return a signed-in page whose /api/topology always echoes `topology`.

        Editing `topology` in place after this returns changes what the next
        /api/topology request sees, simulating a lab starting or stopping
        post-load. Opens Tools once and waits for its initial node fetch before
        closing it, so callers can safely mutate `topology` without racing it.
        """
        session_id = create_session("tools-refresh", "instructor")
        assert self.browser is not None
        context = self.browser.new_context()
        context.add_cookies([
            {"name": "session", "value": session_id, "url": self.base_url},
        ])
        page = context.new_page()
        page.route(
            "**/api/labs/*/topology",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(topology),
            ),
        )
        page.goto(self.base_url + "/")
        page.wait_for_selector("#app:not(.hidden)")
        page.evaluate(
            "async () => { window.__networkConsole = "
            "(await import('/static/js/state/app-state.js')).appState.networkConsole; }"
        )
        with page.expect_response("**/api/labs/*/topology"):
            page.click("#tools-btn")
        page.wait_for_function(
            f"() => window.__networkConsole.nodes.length === {initial_node_count}"
        )
        page.get_by_role("button", name="Close tools", exact=True).click()
        return context, page

    def test_tools_modal_shows_nodes_that_appear_after_page_load(self):
        topology = {"nodes": [], "links": []}
        context, page = self._authenticated_page(topology, initial_node_count=0)
        try:
            # A lab starts (or the active lab changes) after the page has
            # already loaded and already fetched the (then-empty) topology.
            topology["nodes"] = [
                {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1"},
                {"id": "r2", "type": "router", "label": "r2", "ip": "10.0.0.2"},
            ]
            topology["links"] = [{
                "source": "r1",
                "target": "r2",
                "source_intf": "r1-eth0",
                "target_intf": "r2-eth0",
                "status": "up",
            }]

            page.click("#tools-btn")
            page.wait_for_selector("#tools-modal", state="visible")
            # Opening the modal re-fetches nodes asynchronously; wait for that
            # to land rather than racing it.
            page.wait_for_function("() => window.__networkConsole.nodes.length === 2")

            options = page.locator("#ping-source option").all_inner_texts()
            self.assertIn(
                "r1", options,
                "the Tools modal's node dropdowns should reflect nodes that "
                "became active after the page loaded, without a manual reload",
            )
        finally:
            context.close()

    def test_tools_modal_clears_nodes_after_lab_stops(self):
        topology = {
            "nodes": [
                {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1"},
                {"id": "r2", "type": "router", "label": "r2", "ip": "10.0.0.2"},
            ],
            "links": [{
                "source": "r1",
                "target": "r2",
                "source_intf": "r1-eth0",
                "target_intf": "r2-eth0",
                "status": "up",
            }],
        }
        context, page = self._authenticated_page(topology, initial_node_count=2)
        try:
            # The lab stops after page load; /api/topology now answers the
            # way the real server does with no lab running (503, no "nodes").
            topology.clear()
            topology["detail"] = "No lab running"

            page.click("#tools-btn")
            page.wait_for_selector("#tools-modal", state="visible")
            page.wait_for_function("() => window.__networkConsole.nodes.length === 0")

            options = page.locator("#ping-source option").all_inner_texts()
            self.assertNotIn(
                "r1", options,
                "the Tools modal's node dropdowns should clear once the lab "
                "backing them has stopped, not keep showing its old nodes",
            )
        finally:
            context.close()


if __name__ == "__main__":
    unittest.main()
