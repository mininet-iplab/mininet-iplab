"""Unit tests for DNSQueryService in mniplab/dns/query.py.

The Node and Service are resolved from a fake Lab for real; only the client on
Knot's control socket is a stub, installed by :class:`KnotClientStubMixin`.
"""

from __future__ import annotations

import json
import unittest
from typing import Iterable, Union
from unittest.mock import Mock, patch

import pytest

from mniplab.dns import ARecord, DNSService, SecondaryZone, Zone
from mniplab.dns.query import DNSQueryService
from mniplab.dns.service import AnyZone
from mniplab.tests.fakes import FakeLab, FakeNode, KnotClientStubMixin


def _lab(*zones: Union[AnyZone, Iterable[AnyZone]]) -> FakeLab:
    """Build a Lab holding one Node, dns1, serving these Zones."""
    return FakeLab(FakeNode("dns1", services=[DNSService(*zones)]))


@pytest.mark.unit
class DNSQueryServiceLookupTests(unittest.TestCase):
    """Naming a Node the Lab lacks, or one without a DNS Service, is an error."""

    def test_find_service_node_not_found(self) -> None:
        """get_dns_info raises ValueError when node is not found."""
        with self.assertRaises(ValueError) as ctx:
            DNSQueryService.get_dns_info(FakeLab(), "missing")
        self.assertIn("Node not found: missing", str(ctx.exception))

    def test_find_service_no_dns_service(self) -> None:
        """get_dns_info raises ValueError when node has no DNSService."""
        with self.assertRaises(ValueError) as ctx:
            DNSQueryService.get_dns_info(FakeLab(FakeNode("h1")), "h1")
        self.assertIn("has no DNS Service attached", str(ctx.exception))


@pytest.mark.unit
class DNSQueryServiceTests(KnotClientStubMixin, unittest.TestCase):
    """What each query says to Knot, and how it shapes the answer."""

    def test_get_dns_info_success(self) -> None:
        """get_dns_info returns structured dictionary about node DNS service.

        get_dns_info tolerates a missing socket rather than refusing, so it
        opens its own client instead of going through get_client; the socket
        and the client are stubbed here by hand.
        """
        net = _lab(Zone("example.com.", [ARecord("web", "10.0.0.1")]))

        with patch("mniplab.dns.query.os.path.exists", return_value=True), patch(
            "mniplab.dns.query.KnotControlClient"
        ) as client_cls:
            client = client_cls.return_value
            client.__enter__.return_value = client
            client.get_zones.return_value = ["example.com."]
            info = DNSQueryService.get_dns_info(net, "dns1")

        self.assertEqual(info["node"], "dns1")
        self.assertTrue(info["running"])
        self.assertEqual(info["socket_state"], "active")
        self.assertEqual(info["zones"], ["example.com."])
        self.assertEqual(info["listeners"], ["0.0.0.0@53", "::@53"])

    def test_get_dns_records_success(self) -> None:
        """get_dns_records returns flat records list from control client."""
        net = _lab(Zone("example.com.", [ARecord("web", "10.0.0.1")]))
        expected_records = [
            {"owner": "web.example.com.", "ttl": 300, "type": "A", "data": "10.0.0.1"}
        ]
        self.client.get_flat_records.return_value = expected_records

        records = DNSQueryService.get_dns_records(net, "dns1", "example.com")

        self.assertEqual(records, expected_records)
        self.client.get_flat_records.assert_called_once_with("example.com.")

    def test_get_dns_stats_and_reset_stats_success(self) -> None:
        """get_dns_stats and reset_dns_stats call through to the control client."""
        net = _lab(Zone("example.com.", [ARecord("web", "10.0.0.1")]))
        expected_stats = {
            "request-protocol": {"udp4": 5},
            "response-code": {"NOERROR": 5},
        }
        self.client.get_stats.return_value = expected_stats

        stats = DNSQueryService.get_dns_stats(net, "dns1")
        self.assertEqual(stats, expected_stats)
        self.client.get_stats.assert_called_once_with(["example.com."])

        DNSQueryService.reset_dns_stats(net, "dns1")
        self.client.reset_stats.assert_called_once_with(["example.com."])

    def test_get_dns_zone_status_includes_primary_ns_and_serial(self) -> None:
        """get_dns_zone_status returns role, serial, and primary_ns."""
        net = _lab(Zone("example.com.", [ARecord("web", "10.0.0.1")], host_node="dns1"))
        self.client.get_zone_status.return_value = {"role": "master", "serial": 1}
        self.client.get_flat_records.return_value = [{
            "owner": "example.com.",
            "ttl": 3600,
            "type": "SOA",
            "data": "live.example.com. admin.example.com. 2 3600 900 604800 300",
        }]

        res = DNSQueryService.get_dns_zone_status(net, "dns1", "example.com.")

        self.assertEqual(res["node"], "dns1")
        self.assertEqual(res["zone"], "example.com.")
        self.assertEqual(res["primary_ns"], "live.example.com.")
        self.assertEqual(res["serial"], 1)
        self.assertEqual(res["status"]["primary_ns"], "live.example.com.")

    # keymgr is only consulted when the Zone's keys directory exists on disk.
    @patch("os.path.exists", return_value=True)
    def test_get_zone_dnssec_signed_and_unsigned(self, mock_exists: Mock) -> None:
        """get_zone_dnssec queries keymgr for signed zones and returns empty for unsigned/secondary."""
        signed_zone = Zone("example.com.", [ARecord("web", "10.0.0.1")], dnssec=True)
        unsigned_zone = Zone("unsigned.com.", [ARecord("web", "10.0.0.1")], dnssec=False)
        sec_zone = SecondaryZone("secondary.com.", primary="10.0.0.1")
        net = _lab([signed_zone, unsigned_zone, sec_zone])

        # Secondary
        res_sec = DNSQueryService.get_zone_dnssec(net, "dns1", "secondary.com.")
        self.assertTrue(res_sec["is_secondary"])
        self.assertFalse(res_sec["dnssec"])

        # Unsigned
        res_un = DNSQueryService.get_zone_dnssec(net, "dns1", "unsigned.com.")
        self.assertFalse(res_un["dnssec"])
        self.assertEqual(res_un["keys"], [])

        # Signed with mocked subprocess
        keys_stdout = json.dumps([
            {"tag": 2371, "ksk": True, "zsk": False, "algorithm": 13, "size": 256, "id": "key1", "active": 12345}
        ])
        ds_stdout = "example.com. DS 2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab\n"

        def mock_subp(cmd, *args, **kwargs):
            m = Mock()
            m.returncode = 0
            if "list" in cmd:
                m.stdout = keys_stdout
            elif "ds" in cmd:
                m.stdout = ds_stdout
            else:
                m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=mock_subp):
            res_signed = DNSQueryService.get_zone_dnssec(net, "dns1", "example.com.")
            self.assertTrue(res_signed["dnssec"])
            self.assertEqual(len(res_signed["keys"]), 1)
            self.assertEqual(res_signed["keys"][0]["tag"], 2371)
            self.assertTrue(res_signed["keys"][0]["ksk"])
            self.assertEqual(len(res_signed["ds_records"]), 1)
            self.assertEqual(res_signed["ds_records"][0]["key_tag"], 2371)

    @patch("os.path.exists", return_value=True)
    def test_get_zone_dnssec_reports_the_salt_the_zone_is_signed_with(self, mock_exists: Mock) -> None:
        """The NSEC3 salt is read from the signing keys, not from what the Zone declared."""
        random_salt = Zone("random.example.", dnssec=True, nsec3=True)
        declared = Zone("declared.example.", dnssec=True, nsec3=True, nsec3_salt="aabb")
        bare = Zone("bare.example.", dnssec=True, nsec3=True, nsec3_salt="")
        plain = Zone("plain.example.", dnssec=True)
        net = _lab([random_salt, declared, bare, plain])
        kasp_salts = {"random.example.": "CD8CCFD7D0B2ABFE", "declared.example.": "AABB",
                      "bare.example.": "-"}
        asked: list = []

        def mock_subp(cmd, *args, **kwargs):
            m = Mock()
            m.returncode = 0
            m.stdout = ""
            if "nsec3-salt" in cmd:
                zone = cmd[cmd.index("nsec3-salt") - 1]
                asked.append(zone)
                m.stdout = f"Current salt: {kasp_salts[zone]}\n"
            return m

        with patch("subprocess.run", side_effect=mock_subp):
            salts = {
                zone.name: DNSQueryService.get_zone_dnssec(net, "dns1", zone.name)["nsec3_salt"]
                for zone in (random_salt, declared, bare, plain)
            }

        self.assertEqual(salts, {"random.example.": "cd8ccfd7d0b2abfe", "declared.example.": "aabb",
                                 "bare.example.": None, "plain.example.": None})
        self.assertNotIn("plain.example.", asked)

    @patch("os.path.exists", return_value=True)
    def test_get_zone_dnssec_falls_back_to_the_declared_salt_when_keymgr_fails(
        self, mock_exists: Mock
    ) -> None:
        """A keymgr that cannot be read leaves the salt the Zone declared."""
        declared = Zone("declared.example.", dnssec=True, nsec3=True, nsec3_salt="aabb")
        net = _lab([declared])

        def mock_subp(cmd, *args, **kwargs):
            m = Mock()
            m.returncode = 1 if "nsec3-salt" in cmd else 0
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=mock_subp):
            details = DNSQueryService.get_zone_dnssec(net, "dns1", "declared.example.")

        self.assertEqual(details["nsec3_salt"], "aabb")


if __name__ == "__main__":
    unittest.main()
