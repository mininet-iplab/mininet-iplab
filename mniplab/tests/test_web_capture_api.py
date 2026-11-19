"""Tests for the HTTP side of Packet Capture: the Interface list and the pcap.

Both routes pass through to one registry method each, so what is pinned
here is the wiring and what the browser receives: the Interface list as
the registry gave it, and the pcap as one file a Learner can open in
Wireshark — the ring's files in order, named for what was captured — with
a refusal read out rather than a dead link.
"""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from mniplab.tests.fakes import FakeTerminalCloserMixin, LoginClientMixin


class CaptureRouteTests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Interface list and the pcap download."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def ring(self, *contents: bytes) -> list:
        paths = []
        for n, content in enumerate(contents, start=1):
            path = os.path.join(self.tmp.name, f"h1-eth0_{n:05d}_20260923014330.pcapng")
            with open(path, "wb") as f:
                f.write(content)
            paths.append(path)
        return paths

    def test_a_nodes_interfaces_are_listed(self) -> None:
        interfaces = [
            {"name": "r1-eth0", "addresses": ["10.0.0.1"], "peer": "h1", "drawn": True},
            {"name": "r1-eth1", "addresses": ["100.64.0.2"], "peer": None, "drawn": False},
        ]
        with patch("mniplab.webserver.registry.capture_interfaces",
                   new=AsyncMock(return_value=interfaces)) as listed:
            response = self.client.get("/api/labs/fake/capture/interfaces/r1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"interfaces": interfaces})
        listed.assert_awaited_once_with("fake", "r1")

    def test_the_pcap_is_the_ring_in_order_as_one_file(self) -> None:
        files = self.ring(b"first-section|", b"second-section")
        with patch("mniplab.webserver.registry.capture_pcap",
                   new=AsyncMock(return_value=files)) as found:
            response = self.client.get(
                "/api/labs/fake/capture/pcap",
                params={"node": "h1", "interface": "h1-eth0", "filter": "arp"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"first-section|second-section")
        found.assert_awaited_once_with("fake", "h1", "h1-eth0", "arp")
        disposition = response.headers["content-disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn("h1-eth0", disposition)
        self.assertTrue(disposition.rstrip('"').endswith(".pcapng"))

    def test_nothing_captured_yet_is_said_plainly(self) -> None:
        with patch("mniplab.webserver.registry.capture_pcap", new=AsyncMock(return_value=[])):
            response = self.client.get(
                "/api/labs/fake/capture/pcap", params={"node": "h1", "interface": "h1-eth0"}
            )

        self.assertEqual(response.status_code, 404)
        self.assertIn("No pcap", response.json()["detail"])

    def test_a_refused_interface_is_read_out(self) -> None:
        with patch("mniplab.webserver.registry.capture_pcap",
                   new=AsyncMock(side_effect=ValueError("No Node named nat0 in this Lab"))):
            response = self.client.get(
                "/api/labs/fake/capture/pcap", params={"node": "nat0", "interface": "nat0-eth0"}
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("nat0", response.text)

    def test_both_routes_need_a_signed_in_viewer(self) -> None:
        self.client.cookies.clear()

        for path in ("/api/labs/fake/capture/interfaces/r1",
                     "/api/labs/fake/capture/pcap?node=h1&interface=h1-eth0"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)


if __name__ == "__main__":
    unittest.main()
