"""Node icons on the running-Lab canvas, shared with Lab Authoring.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``. The Lab's own
topology draws an Internet Gateway as type ``internet``, which the cloud test
below covers; the authoring type ``nat`` is injected directly here, so this
checks the shared icon and colour module, not what the server lists.
"""

import pytest

from mniplab.tests.browser import LiveServerCase, expect

pytestmark = pytest.mark.browser_workspace

AUTHORED_LAYOUT = {"nodes": {"r1": {"x": 200, "y": 200}, "sp1": {"x": 400, "y": 200},
                             "nat1": {"x": 200, "y": 400}}}


class RunningLabNodeIconTests(LiveServerCase):
    """The running-Lab canvas draws Speakers cleanly and Internet Gateways distinctly."""

    def test_speaker_draws_without_errors_and_nat_differs_from_router(self) -> None:
        with self.signed_in_page() as page:
            errors: list[str] = []
            page.on("console", lambda message: errors.append(message.text)
                    if message.type == "error" else None)
            topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "10.0.0.1/24", "services": []},
                    {"id": "sp1", "type": "bgp-speaker", "label": "sp1", "ip": "10.0.0.2/24",
                     "services": []},
                    {"id": "nat1", "type": "nat", "label": "nat1", "ip": "10.0.1.1/24", "services": []},
                ],
                "links": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)
            self.assertEqual([error for error in errors if "attribute d" in error], [])

            def drawn(name: str) -> tuple[str, str]:
                path = page.locator(f".node[aria-label*='{name}'] svg path").first
                return path.get_attribute("d") or "", path.get_attribute("fill") or ""

            nat_color = page.evaluate(
                "() => getComputedStyle(document.documentElement).getPropertyValue('--nat-color').trim()")
            router, nat = drawn("r1"), drawn("nat1")
            self.assertNotEqual(nat[0], router[0])
            self.assertEqual(nat[1], nat_color)
            self.assertNotEqual(router[1], nat_color)

    def test_an_internet_gateway_is_a_captioned_cloud_with_no_actions(self) -> None:
        with self.signed_in_page() as page:
            topo = {
                "nodes": [
                    {"id": "r1", "type": "router", "label": "r1", "ip": "203.0.113.2/30", "services": []},
                    {"id": "nat1", "type": "internet", "label": "nat1", "ip": "203.0.113.1/30",
                     "services": []},
                    {"id": "sp1", "type": "bgp-speaker", "label": "sp1", "ip": "10.0.0.2/24",
                     "services": []},
                ],
                "links": [{"source": "r1", "target": "nat1", "source_intf": "r1-eth1",
                           "target_intf": "nat1-eth0", "source_addresses": ["203.0.113.2"],
                           "target_addresses": ["203.0.113.1"], "status": "up", "params": {}}],
                "relays": [],
                "translators": [],
            }
            self.render_topology(page, topo, AUTHORED_LAYOUT)

            cloud = page.locator(".node[aria-label='Internet nat1']")
            expect(cloud).to_have_attribute("role", "img")
            expect(cloud.locator("text.node-label")).to_have_text("Internet")
            expect(cloud.locator("text.node-label")).to_be_visible()

            # The Internet's shape differs from the Speaker's cloud, not only its colour.
            speaker = page.locator(".node[aria-label*='sp1'] svg path").first
            internet = cloud.locator("svg path").first
            self.assertNotEqual(internet.get_attribute("d"), speaker.get_attribute("d"))

            cloud.dispatch_event("click")
            expect(page.locator("#node-context-menu")).to_be_hidden()

            page.locator("line.link").first.dispatch_event("click")
            expect(page.locator("#link-config-btn")).to_be_hidden()
            expect(page.locator("#link-disconnect-btn")).to_be_hidden()
            expect(page.locator("#link-reconnect-btn")).to_be_hidden()
            expect(page.locator("#link-capture-btn")).to_be_visible()
            expect(page.locator("text.link-label", has_text="203.0.113.1")).to_be_visible()
            expect(page.locator("text.link-label", has_text="203.0.113.2")).to_be_visible()
