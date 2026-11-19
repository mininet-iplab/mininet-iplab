"""Unit tests for KnotControlClient in mniplab/dns/control_client.py."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

import pytest
from libknot.control import (
    KnotCtlData,
    KnotCtlDataIdx,
    KnotCtlErrorConnect,
    KnotCtlErrorReceive,
    KnotCtlErrorRemote,
    KnotCtlErrorSend,
)

from mniplab.dns.control_client import KnotControlClient
from mniplab.errors import DNSControlError, DNSValidationError


def _make_knot_data(**kwargs: str) -> KnotCtlData:
    """Helper to construct a KnotCtlData instance with specified fields."""
    data = KnotCtlData()
    for key, value in kwargs.items():
        idx = getattr(KnotCtlDataIdx, key.upper())
        data[idx] = value
    return data


@pytest.mark.unit
class KnotControlClientTests(unittest.TestCase):
    """Test suite for KnotControlClient."""

    def setUp(self) -> None:
        self.mock_ctl = MagicMock()
        KnotControlClient.clear_baselines()
        self.client = KnotControlClient(
            "/tmp/test-knot.sock",
            ctl_factory=lambda: self.mock_ctl,
        )

    def test_connect_and_close(self) -> None:
        """Client connects to the specified socket path and closes cleanly."""
        self.client.connect()
        self.mock_ctl.connect.assert_called_once_with("/tmp/test-knot.sock")
        self.mock_ctl.set_timeout.assert_called_once_with(5)

        self.client.close()
        self.mock_ctl.close.assert_called_once()

    def test_context_manager_lifecycle(self) -> None:
        """Context manager connects on enter and closes on exit."""
        with self.client as client:
            self.assertIs(client, self.client)
            self.mock_ctl.connect.assert_called_once_with("/tmp/test-knot.sock")
        self.mock_ctl.close.assert_called_once()

    def test_get_zones(self) -> None:
        """get_zones sends zone-status and returns list of hosted zone names."""
        self.mock_ctl.receive_block.return_value = {
            "example.com.": {"role": "master"},
            "0.0.10.in-addr.arpa.": {"role": "master"},
        }
        zones = self.client.get_zones()
        self.assertEqual(zones, ["example.com.", "0.0.10.in-addr.arpa."])
        self.mock_ctl.send_block.assert_called_once_with(cmd="zone-status")

    def test_get_records(self) -> None:
        """get_records sends zone-read and extracts zone records mapping."""
        records_data = {
            "example.com.": {
                "example.com.": {
                    "SOA": {"ttl": "3600", "data": ["ns.example.com. admin.example.com. 1 3600 900 604800 300"]},
                    "NS": {"ttl": "3600", "data": ["ns.example.com."]},
                },
                "web.example.com.": {
                    "A": {"ttl": "300", "data": ["10.0.0.2"]},
                },
            }
        }
        self.mock_ctl.receive_block.return_value = records_data
        records = self.client.get_records("example.com.")
        self.assertEqual(records, records_data["example.com."])
        self.mock_ctl.send_block.assert_called_once_with(cmd="zone-read", zone="example.com.")

    def test_get_flat_records(self) -> None:
        """get_flat_records flattens zone-read results into a list of record dicts."""
        self.mock_ctl.receive_block.return_value = {
            "example.com.": {
                "web.example.com.": {
                    "A": {"ttl": "300", "data": ["10.0.0.2", "10.0.0.3"]},
                },
                "alias.example.com.": {
                    "CNAME": {"ttl": "600", "data": ["web.example.com."]},
                },
            }
        }
        flat = self.client.get_flat_records("example.com.")
        self.assertEqual(len(flat), 3)
        self.assertEqual(
            flat[0],
            {"owner": "web.example.com.", "ttl": 300, "type": "A", "data": "10.0.0.2"},
        )
        self.assertEqual(
            flat[1],
            {"owner": "web.example.com.", "ttl": 300, "type": "A", "data": "10.0.0.3"},
        )
        self.assertEqual(
            flat[2],
            {"owner": "alias.example.com.", "ttl": 600, "type": "CNAME", "data": "web.example.com."},
        )

    def test_get_zone_status(self) -> None:
        """get_zone_status queries zone-status for a specific zone."""
        status_data = {
            "example.com.": {
                "role": "master",
                "serial": "42",
                "transaction": "-",
            }
        }
        self.mock_ctl.receive_block.return_value = status_data
        res = self.client.get_zone_status("example.com.")
        self.assertEqual(res["serial"], "42")
        self.assertEqual(res["role"], "master")
        self.mock_ctl.send_block.assert_called_once_with(cmd="zone-status", zone="example.com.")

    def test_get_stats_and_reset_stats(self) -> None:
        """get_stats reports live query stats and reset_stats creates a baseline."""
        raw_stats = {
            "mod-stats": {
                "request-protocol": {"udp4": 5, "tcp4": 2},
                "server-operation": {"query": 7},
                "response-code": {"NOERROR": 6, "NXDOMAIN": 1},
                "request-bytes": {"query": 350},
                "response-bytes": {"reply": 520},
            }
        }
        self.mock_ctl.receive_stats.return_value = raw_stats

        stats = self.client.get_stats()
        self.assertEqual(stats["request-protocol"]["udp4"], 5)
        self.assertEqual(stats["server-operation"]["query"], 7)
        self.assertEqual(stats["response-code"]["NOERROR"], 6)

        # Reset statistics
        self.client.reset_stats()

        # Stats after reset should report 0 for unchanged raw counters
        stats_after_reset = self.client.get_stats()
        self.assertEqual(stats_after_reset["request-protocol"]["udp4"], 0)
        self.assertEqual(stats_after_reset["server-operation"]["query"], 0)
        self.assertEqual(stats_after_reset["response-code"]["NOERROR"], 0)

        # Increment raw stats by 2 queries
        updated_stats = {
            "mod-stats": {
                "request-protocol": {"udp4": 7, "tcp4": 2},
                "server-operation": {"query": 9},
                "response-code": {"NOERROR": 8, "NXDOMAIN": 1},
                "request-bytes": {"query": 450},
                "response-bytes": {"reply": 670},
            }
        }
        self.mock_ctl.receive_stats.return_value = updated_stats

        stats_incremented = self.client.get_stats()
        self.assertEqual(stats_incremented["request-protocol"]["udp4"], 2)
        self.assertEqual(stats_incremented["server-operation"]["query"], 2)
        self.assertEqual(stats_incremented["response-code"]["NOERROR"], 2)
        self.assertEqual(stats_incremented["response-code"]["NXDOMAIN"], 0)

    def test_get_stats_includes_per_zone_query_type_counters(self) -> None:
        """get_stats aggregates mod-stats query-type counters for hosted zones."""
        global_stats = {
            "mod-stats": {
                "server-operation": {"query": 3},
                "response-code": {"NOERROR": 3},
            }
        }
        zone_stats = {
            "zone": {
                "example.com.": {
                    "mod-stats": {"query-type": {"A": 2, "AAAA": 1}},
                }
            }
        }
        self.mock_ctl.receive_stats.side_effect = [global_stats, zone_stats]

        stats = self.client.get_stats(["example.com."])

        self.assertEqual(stats["server-operation"]["query"], 3)
        self.assertEqual(stats["query-type"], {"A": 2, "AAAA": 1})
        self.mock_ctl.send_block.assert_any_call(
            cmd="zone-stats",
            zone="example.com.",
            section="mod-stats",
            item="query-type",
        )

    def test_reset_stats_includes_per_zone_query_type_baseline(self) -> None:
        """reset_stats baselines both global and per-zone counters."""
        global_stats = {
            "mod-stats": {
                "server-operation": {"query": 3},
                "response-code": {"NOERROR": 3},
            }
        }
        zone_stats = {
            "zone": {
                "example.com.": {
                    "mod-stats": {"query-type": {"A": 2}},
                }
            }
        }
        self.mock_ctl.receive_stats.side_effect = [global_stats, zone_stats]

        self.client.reset_stats(["example.com."])

        self.mock_ctl.receive_stats.side_effect = [global_stats, zone_stats]
        stats = self.client.get_stats(["example.com."])
        self.assertEqual(stats["query-type"]["A"], 0)

    def test_the_two_transfer_verbs_send_two_knot_commands(self) -> None:
        """refresh checks the Serial; retransfer takes a full copy regardless."""
        self.mock_ctl.receive_block.return_value = {}

        self.client.refresh_zone("example.com.")
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-refresh", zone="example.com."
        )

        self.client.retransfer_zone("example.com.")
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-retransfer", zone="example.com."
        )

    def test_transaction_lifecycle_commit(self) -> None:
        """Transaction begin, set_record, diff_zone, and commit sequence."""
        self.mock_ctl.receive_block.return_value = {}
        self.client.begin_zone("example.com.")
        self.mock_ctl.send_block.assert_called_with(cmd="zone-begin", zone="example.com.")

        self.client.set_record("example.com.", "web", "A", "10.0.0.1", ttl=300)
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-set",
            zone="example.com.",
            owner="web",
            ttl="300",
            rtype="A",
            data="10.0.0.1",
        )

        diff_response = {
            "example.com.": {
                "web.example.com.": {
                    "A": {"ttl": "300", "data": ["10.0.0.1"]},
                }
            }
        }
        self.mock_ctl.receive_block.return_value = diff_response
        diff = self.client.diff_zone("example.com.")
        self.assertEqual(diff, diff_response["example.com."])
        self.mock_ctl.send_block.assert_called_with(cmd="zone-diff", zone="example.com.")

        self.client.commit_zone("example.com.")
        self.mock_ctl.send_block.assert_called_with(cmd="zone-commit", zone="example.com.")

    def test_transaction_lifecycle_abort(self) -> None:
        """Transaction begin, unset_record, and abort sequence."""
        self.mock_ctl.receive_block.return_value = {}
        self.client.begin_zone("example.com.")
        self.client.unset_record("example.com.", "web", rtype="A", data="10.0.0.1")
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-unset",
            zone="example.com.",
            owner="web",
            rtype="A",
            data="10.0.0.1",
        )

        self.client.abort_zone("example.com.")
        self.mock_ctl.send_block.assert_called_with(cmd="zone-abort", zone="example.com.")

    def test_semantic_check_raises_dns_validation_error(self) -> None:
        """KnotCtlErrorRemote with 'semantic check' is mapped to DNSValidationError."""
        data = _make_knot_data(
            command="zone-commit",
            error="semantic check",
            zone="example.com.",
        )
        self.mock_ctl.receive_block.side_effect = KnotCtlErrorRemote("semantic check", data)

        with self.assertRaises(DNSValidationError) as ctx:
            self.client.commit_zone("example.com.")

        err = ctx.exception
        self.assertEqual(err.command, "zone-commit")
        self.assertEqual(err.error_code, "semantic check")
        self.assertEqual(err.zone, "example.com.")
        self.assertIn("semantic check", str(err))

    def test_invalid_item_raises_dns_validation_error(self) -> None:
        """KnotCtlErrorRemote with 'invalid item' is mapped to DNSValidationError."""
        data = _make_knot_data(
            command="zone-set",
            error="invalid item",
            zone="example.com.",
            owner="web",
            type="CNAME",
        )
        self.mock_ctl.receive_block.side_effect = KnotCtlErrorRemote("invalid item", data)

        with self.assertRaises(DNSValidationError) as ctx:
            self.client.set_record("example.com.", "web", "CNAME", "target.")

        err = ctx.exception
        self.assertEqual(err.command, "zone-set")
        self.assertEqual(err.error_code, "invalid item")

    def test_connect_failure_raises_dns_control_error(self) -> None:
        """KnotCtlErrorConnect maps to DNSControlError."""
        self.mock_ctl.connect.side_effect = KnotCtlErrorConnect("No such file or directory")

        with self.assertRaises(DNSControlError) as ctx:
            self.client.connect()

        self.assertIn("No such file or directory", str(ctx.exception))

    def test_general_remote_error_raises_dns_control_error(self) -> None:
        """Non-validation remote error maps to DNSControlError."""
        data = _make_knot_data(
            command="zone-read",
            error="server error",
            zone="example.com.",
        )
        self.mock_ctl.receive_block.side_effect = KnotCtlErrorRemote("server error", data)

        with self.assertRaises(DNSControlError) as ctx:
            self.client.get_records("example.com.")

        err = ctx.exception
        self.assertNotIsInstance(err, DNSValidationError)
        self.assertEqual(err.error_code, "server error")

    def test_set_ds_record_in_transaction(self) -> None:
        """set_ds_record stages a DSRecord via zone-set."""
        from mniplab.dns.models import DSRecord

        ds = DSRecord(
            name="example.com.",
            key_tag=2371,
            algorithm=13,
            digest_type=2,
            digest="489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
            ttl=300,
        )
        self.mock_ctl.receive_block.return_value = {}
        self.client.set_ds_record("com.", ds)
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-set",
            zone="com.",
            owner="example.com.",
            ttl="300",
            rtype="DS",
            data="2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
        )

    def test_unset_ds_record_in_transaction(self) -> None:
        """unset_ds_record removes a DSRecord via zone-unset."""
        from mniplab.dns.models import DSRecord

        ds = DSRecord(
            name="example.com.",
            key_tag=2371,
            algorithm=13,
            digest_type=2,
            digest="489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
        )
        self.mock_ctl.receive_block.return_value = {}
        self.client.unset_ds_record("com.", "example.com.", ds_record=ds)
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-unset",
            zone="com.",
            owner="example.com.",
            rtype="DS",
            data="2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
        )

    def test_zone_key_rollover(self) -> None:
        """zone_key_rollover issues zone-key-rollover for zsk and ksk."""
        self.mock_ctl.receive_block.return_value = {"example.com.": {"status": "ok"}}
        res = self.client.zone_key_rollover("example.com.", "zsk")
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-key-rollover",
            zone="example.com.",
            rtype="zsk",
        )
        self.assertEqual(res, {"example.com.": {"status": "ok"}})

        self.client.zone_key_rollover("example.com.", "ksk")
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-key-rollover",
            zone="example.com.",
            rtype="ksk",
        )

    def test_zone_key_rollover_invalid_type_raises(self) -> None:
        """zone_key_rollover validates that key_type is zsk or ksk."""
        with self.assertRaises(DNSValidationError):
            self.client.zone_key_rollover("example.com.", "invalid")

    def test_zone_ksk_submitted(self) -> None:
        """zone_ksk_submitted issues zone-ksk-submitted block."""
        self.mock_ctl.receive_block.return_value = {"example.com.": {"status": "ok"}}
        res = self.client.zone_ksk_submitted("example.com.")
        self.mock_ctl.send_block.assert_called_with(
            cmd="zone-ksk-submitted",
            zone="example.com.",
        )
        self.assertEqual(res, {"example.com.": {"status": "ok"}})

    def test_diff_ds_records(self) -> None:
        """diff_ds_records filters raw zone diff to DS entries."""
        with patch.object(self.client, "diff_zone", return_value=[
            {"owner": "sub.example.com.", "type": "DS", "data": "2371 13 2 abc"},
            {"owner": "sub.example.com.", "type": "A", "data": "10.0.0.1"},
            "+sub2.example.com. 300 DS 1234 13 2 def",
        ]):
            ds_diffs = self.client.diff_ds_records("example.com.")
            self.assertEqual(len(ds_diffs), 2)
            self.assertEqual(ds_diffs[0]["type"], "DS")
            self.assertIn("DS", ds_diffs[1]["raw"])


if __name__ == "__main__":
    unittest.main()


@pytest.mark.unit
class KnotControlClientIsRunningTests(unittest.TestCase):
    """is_running() opens, sends ``status``, and always closes again."""

    def setUp(self) -> None:
        self.mock_ctl = MagicMock()
        self.client = KnotControlClient(
            "/tmp/test-knot.sock", ctl_factory=lambda: self.mock_ctl
        )

    def test_is_running_when_status_round_trips(self) -> None:
        self.mock_ctl.receive_block.return_value = {}

        self.assertTrue(self.client.is_running())

        self.mock_ctl.send_block.assert_called_once_with(cmd="status")
        self.mock_ctl.close.assert_called_once()

    def test_not_running_when_the_socket_refuses(self) -> None:
        self.mock_ctl.connect.side_effect = KnotCtlErrorConnect("no such socket")

        self.assertFalse(self.client.is_running())

        self.mock_ctl.send_block.assert_not_called()

