"""Unit tests for DNS inspection CLI commands in mniplab/cli.py."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest
from mniplab.tests.fakes import FakeLab, cli_output


@pytest.mark.unit
class ShowDNSCommandTests(unittest.TestCase):
    """Test suite for mnIPLabCLI.do_show_dns, do_show_dns_records, and do_show_dns_stats."""

    # --- show_dns ---

    def test_show_dns_requires_node_argument(self) -> None:
        """do_show_dns prints usage when node argument is missing."""
        self.assertIn("Usage: show_dns <node>", cli_output("show_dns", ""))

    def test_show_dns_success_output(self) -> None:
        """do_show_dns prints status, socket path, listeners, and hosted zones."""
        fake_info = {
            "node": "dns1",
            "running": True,
            "socket_path": "/tmp/knot-service/dns1/run/knot.sock",
            "socket_state": "active",
            "listeners": ["0.0.0.0@53", "::@53"],
            "zones": ["example.com.", "0.0.10.in-addr.arpa."],
        }
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_info",
            return_value=fake_info,
        ):
            printed = cli_output("show_dns", "dns1")

        self.assertIn("DNS Service on dns1", printed)
        self.assertIn("Status: Running", printed)
        self.assertIn("/tmp/knot-service/dns1/run/knot.sock (active)", printed)
        self.assertIn("0.0.0.0@53, ::@53", printed)
        self.assertIn("example.com.", printed)
        self.assertIn("0.0.10.in-addr.arpa.", printed)

    def test_show_dns_no_service_error(self) -> None:
        """do_show_dns prints error message when node has no DNS service."""
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_info",
            side_effect=ValueError("dns1 has no DNS Service attached"),
        ):
            printed = cli_output("show_dns", "dns1")

        self.assertIn("Error: dns1 has no DNS Service attached", printed)

    # --- show_dns_records ---

    def test_show_dns_records_requires_arguments(self) -> None:
        """do_show_dns_records prints usage when node or zone is missing."""
        self.assertIn(
            "Usage: show_dns_records <node> <zone>", cli_output("show_dns_records", "")
        )
        self.assertIn(
            "Usage: show_dns_records <node> <zone>",
            cli_output("show_dns_records", "dns1"),
        )

    def test_show_dns_records_success_table_output(self) -> None:
        """do_show_dns_records formats records in a tabular format (Owner, TTL, Type, Data)."""
        fake_records = [
            {"owner": "example.com.", "ttl": 3600, "type": "SOA", "data": "ns.example.com. admin.example.com. 1 3600 900 604800 300"},
            {"owner": "example.com.", "ttl": 3600, "type": "NS", "data": "ns.example.com."},
            {"owner": "web.example.com.", "ttl": 300, "type": "A", "data": "10.0.0.1"},
        ]
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_records",
            return_value=fake_records,
        ):
            printed = cli_output("show_dns_records", "dns1 example.com.")

        self.assertIn("DNS Records for zone example.com. on dns1", printed)
        self.assertIn("Owner", printed)
        self.assertIn("TTL", printed)
        self.assertIn("Type", printed)
        self.assertIn("Data", printed)
        self.assertIn("web.example.com.", printed)
        self.assertIn("10.0.0.1", printed)

    def test_show_dns_records_empty_output(self) -> None:
        """do_show_dns_records informs user when no records are found in zone."""
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_records",
            return_value=[],
        ):
            printed = cli_output("show_dns_records", "dns1 example.com.")

        self.assertIn("No records found in zone example.com. on dns1", printed)

    # --- show_dns_stats ---

    def test_show_dns_stats_requires_node_argument(self) -> None:
        """do_show_dns_stats prints usage when node argument is missing."""
        self.assertIn("Usage: show_dns_stats <node>", cli_output("show_dns_stats", ""))

    def test_show_dns_stats_success_output(self) -> None:
        """do_show_dns_stats formats live query statistics."""
        fake_stats = {
            "request-protocol": {"udp4": 5, "tcp4": 1},
            "server-operation": {"query": 6},
            "response-code": {"NOERROR": 4, "NXDOMAIN": 2},
            "request-bytes": {"query": 300},
            "response-bytes": {"reply": 450},
        }
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_stats",
            return_value=fake_stats,
        ):
            printed = cli_output("show_dns_stats", "dns1")

        self.assertIn("DNS Query Statistics for dns1", printed)
        self.assertIn("Request Protocols:", printed)
        self.assertIn("udp4: 5", printed)
        self.assertIn("tcp4: 1", printed)
        self.assertIn("Server Operations:", printed)
        self.assertIn("query: 6", printed)
        self.assertIn("Response Codes:", printed)
        self.assertIn("NOERROR: 4", printed)
        self.assertIn("NXDOMAIN: 2", printed)
        self.assertIn("Traffic Volume:", printed)
        self.assertIn("request query: 300 B", printed)
        self.assertIn("response reply: 450 B", printed)

    def test_show_dns_stats_reset(self) -> None:
        """do_show_dns_stats --reset invokes reset_dns_stats and prints confirmation."""
        net = FakeLab()
        with patch(
            "mniplab.dns.query.DNSQueryService.reset_dns_stats"
        ) as mock_reset:
            printed = cli_output("show_dns_stats", "dns1 --reset", net=net)

        mock_reset.assert_called_once_with(net, "dns1")
        self.assertIn("Query statistics reset for dns1", printed)

    def test_show_dns_stats_reset_error(self) -> None:
        """do_show_dns_stats --reset prints error when service fails."""
        with patch(
            "mniplab.dns.query.DNSQueryService.reset_dns_stats",
            side_effect=ValueError("Socket not found"),
        ):
            printed = cli_output("show_dns_stats", "dns1 --reset")

        self.assertIn("Error: Socket not found", printed)


@pytest.mark.unit
class DNSRecordAndDNSSECCommandTests(unittest.TestCase):
    """do_dns_records, do_dnssec_keys, do_dnssec_rollover, and do_show_cache."""

    def test_dns_records_add_and_del_ds(self) -> None:
        """do_dns_records adds and deletes DS records via DNSRecordService."""
        net = FakeLab()

        with patch("mniplab.dns.record_service.DNSRecordService.set_record") as mock_set:
            mock_set.return_value = {"owner": "example.com.", "type": "DS"}
            cli_output(
                "dns_records",
                "tld1 com. add example.com. DS 2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab 300",
                net=net,
            )
            mock_set.assert_called_once_with(
                net,
                "tld1",
                "com.",
                "example.com.",
                "DS",
                "2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
                ttl=300,
            )

        with patch("mniplab.dns.record_service.DNSRecordService.unset_record") as mock_unset:
            mock_unset.return_value = {"owner": "example.com.", "type": "DS"}
            cli_output("dns_records", "tld1 com. del example.com. DS", net=net)
            mock_unset.assert_called_once_with(
                net, "tld1", "com.", "example.com.", rtype="DS", data=None
            )

    def test_dnssec_keys_command(self) -> None:
        """do_dnssec_keys queries DNSSEC state and prints keys and DS records."""
        keys_info = {
            "node": "auth1",
            "zone": "example.com.",
            "dnssec": True,
            "nsec3": False,
            "nsec3_salt": None,
            "is_secondary": False,
            "keys": [
                {
                    "tag": 2371,
                    "ksk": True,
                    "zsk": False,
                    "algorithm": 13,
                    "size": 256,
                    "active": 1700000000,
                    "id": "abc123keyid",
                }
            ],
            "ds_records": [
                {
                    "owner": "example.com.",
                    "key_tag": 2371,
                    "algorithm": 13,
                    "digest_type": 2,
                    "digest": "489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
                }
            ],
        }

        with patch("mniplab.dns.query.DNSQueryService.get_zone_dnssec", return_value=keys_info):
            output = cli_output("dnssec_keys", "auth1 example.com.")
        self.assertIn("2371", output)
        self.assertIn("KSK", output)
        self.assertIn("example.com.", output)

    def test_dnssec_rollover_command(self) -> None:
        """do_dnssec_rollover triggers rollover via DNSRecordService."""
        net = FakeLab()

        with patch("mniplab.dns.record_service.DNSRecordService.trigger_dnssec_rollover") as mock_roll:
            mock_roll.return_value = {"status": "success"}
            output = cli_output("dnssec_rollover", "auth1 example.com. zsk", net=net)
        mock_roll.assert_called_once_with(net, "auth1", "example.com.", "zsk")
        self.assertIn("Triggered ZSK rollover", output)

    def test_show_cache_displays_validation_status_tags_and_ad(self) -> None:
        """do_show_cache renders [SECURE], [INSECURE], [BOGUS] tags and AD flag."""
        from mniplab.resolver.models import CacheEntry, ResolverMetrics

        metrics = ResolverMetrics(queries=1, hits=1, misses=0, dnssec_secure=1)
        entries = [
            CacheEntry("web.example.com.", "A", 300, "10.0.5.2", security="secure", authenticated_data=True),
            CacheEntry("bad.example.com.", "A", 300, "10.0.5.99", security="bogus", authenticated_data=False),
            CacheEntry("plain.example.com.", "A", 300, "10.0.5.3", security="insecure", authenticated_data=False),
        ]

        with patch("mniplab.resolver.query.ResolverQueryService.get_metrics", return_value=metrics), \
             patch("mniplab.resolver.query.ResolverQueryService.get_cache", return_value=entries):
            output = cli_output("show_cache", "res1")
        self.assertIn("[SECURE]", output)
        self.assertIn("[BOGUS]", output)
        self.assertIn("[INSECURE]", output)
        self.assertIn("AD", output)


if __name__ == "__main__":
    unittest.main()
