"""Tests for the Secondary Nameserver's Web UI panel.

Browser coverage drives the Secondary's DNS panel on the DNS Test Lab, with a
Secondary on ``ns2``.  Replication under real daemons is proved on the DNS
Test Lab too, in test_web_authoring_dns_lab.py.
"""

from __future__ import annotations

import time

import pytest

from mniplab.webserver import registry
from mniplab.tests.browser import LiveServerMixin, run_async
from mniplab.dns import DNSService, SecondaryZone
from mniplab.tests.labs import LabCase, build_dns_test_lab

APEX = "example.com."


class DNSSecondaryLabWebUIIntegrationTests(LiveServerMixin, LabCase):
    """Browser tests for the Secondary Nameserver Web UI panel."""

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.requires_browser,
        pytest.mark.requires_root,
    ]

    def setUp(self) -> None:
        from mniplab.dns.control_client import KnotControlClient

        KnotControlClient.clear_baselines()

        self.net = self.lab()
        nodes = build_dns_test_lab(self.net)
        nodes["ns2"].addService(DNSService(SecondaryZone(APEX, primary=nodes["auth1"])))
        self.start(self.net)
        result = run_async(registry.start_lab(self.net, "dns-secondary-test"))
        self.assertNotIn("error", result)
        self.addCleanup(lambda: run_async(registry.stop_lab(result["key"])))

    def test_secondary_dns_panel_renders_read_only_and_links_to_primary(self) -> None:
        """The Secondary modal displays read-only records, transfer log, and links to Primary."""
        client1 = self.net.get("client1")
        deadline = time.time() + 60
        while time.time() < deadline:
            if "10.0.5.2" in client1.cmd("dig @10.0.6.2 web.example.com A +short"):
                break
            time.sleep(1)

        with self.signed_in_page() as page:
            # 1. Topology distinguishes ns2 with 'DNS · SECONDARY' badge
            page.wait_for_function("""
                () => document.querySelector('.node[aria-label*="ns2"]')
            """)
            badge = page.locator(".node[aria-label*='ns2'] .service-badge-dns")
            badge.wait_for()
            badge_text = badge.locator(".service-badge-text").text_content()
            self.assertEqual((badge_text or "").strip(), "DNS · SECONDARY")

            # 2. Click badge to open DNS modal
            badge.press("Enter")
            page.wait_for_selector("#dns-modal[style*='flex']")

            # 3. Secondary section is visible
            page.wait_for_selector("#dns-secondary-section:not(.hidden)")
            page.wait_for_selector("#dns-secondary-notice:not(.hidden)")

            # 4. Header contains Primary and Serial
            primary_text = page.locator("#dns-secondary-primary").inner_text()
            self.assertIn("auth1", primary_text)
            serial_text = page.locator("#dns-secondary-serial").inner_text()
            self.assertEqual(serial_text.strip(), "1")

            # 5. Records form is hidden, no action delete buttons
            self.assertTrue(page.locator("#dns-record-form").is_hidden())
            self.assertEqual(page.locator(".dns-record-delete").count(), 0)

            # 6. Transfer log displays AXFR
            page.wait_for_selector("#dns-transfers-tbody tr")
            log_text = page.locator("#dns-transfers-tbody").inner_text()
            self.assertIn("AXFR", log_text)

            # 7. Clicking primary link opens auth1's editor
            page.locator("#dns-primary-link").click()
            page.wait_for_function("""
                () => document.querySelector('#dns-modal-title').textContent.includes('auth1')
            """)
            # On auth1, secondary section is hidden and record form is visible
            self.assertTrue(page.locator("#dns-secondary-section").is_hidden())
            self.assertTrue(page.locator("#dns-record-form").is_visible())
