"""Unit tests for DNSRecordService in mniplab/dns/record_service.py."""

from __future__ import annotations

import unittest

import pytest

from mniplab.dns.models import ARecord, Zone
from mniplab.dns.record_service import DNSRecordService
from mniplab.dns.service import DNSService
from mniplab.errors import DNSValidationError
from mniplab.tests.fakes import FakeLab, FakeNode, KnotClientStubMixin


def _make_net(node_name: str = "dns1") -> FakeLab:
    """Build a Lab holding one Node with a DNSService."""
    service = DNSService(Zone("example.com.", [ARecord("web", "10.0.0.2")]))
    return FakeLab(FakeNode(node_name, services=[service]))


@pytest.mark.unit
class DNSRecordServiceSetRecordTests(KnotClientStubMixin, unittest.TestCase):
    """Test suite for atomic record creation/update via Knot transactions."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _make_net()

    def test_set_record_commits_atomic_transaction(self) -> None:
        """set_record runs begin -> zone-set -> commit against the control socket."""
        record = DNSRecordService.set_record(
            self.net, "dns1", "example.com.", "web", "A", "10.0.0.3", ttl=300,
        )

        self.client.begin_zone.assert_called_once_with("example.com.")
        self.client.set_record.assert_called_once_with(
            "example.com.", "web", "A", "10.0.0.3", ttl=300,
        )
        self.client.diff_zone.assert_called_once_with("example.com.")
        self.client.commit_zone.assert_called_once_with("example.com.")
        self.client.abort_zone.assert_not_called()
        self.assertEqual(record["owner"], "web")
        self.assertEqual(record["type"], "A")
        self.assertEqual(record["data"], "10.0.0.3")
        self.assertEqual(record["ttl"], 300)
        self.assertEqual(record["zone"], "example.com.")

    def test_set_record_atomic_replacement_with_old_record(self) -> None:
        """set_record unsets old record and sets new record in one transaction."""
        record = DNSRecordService.set_record(
            self.net,
            "dns1",
            "example.com.",
            "web",
            "A",
            "10.0.0.4",
            ttl=300,
            old_owner="web",
            old_type="A",
            old_data="10.0.0.2",
        )

        self.client.begin_zone.assert_called_once_with("example.com.")
        self.client.unset_record.assert_called_once_with(
            "example.com.", "web", rtype="A", data="10.0.0.2"
        )
        self.client.set_record.assert_called_once_with(
            "example.com.", "web", "A", "10.0.0.4", ttl=300
        )
        self.client.diff_zone.assert_called_once_with("example.com.")
        self.client.commit_zone.assert_called_once_with("example.com.")
        self.client.abort_zone.assert_not_called()
        self.assertEqual(record["owner"], "web")
        self.assertEqual(record["data"], "10.0.0.4")
        self.assertEqual(record["old_owner"], "web")

    def test_set_record_normalizes_zone_apex(self) -> None:
        """A zone apex without a trailing dot is normalized before use."""
        DNSRecordService.set_record(self.net, "dns1", "example.com", "web", "A", "10.0.0.3")

        self.client.begin_zone.assert_called_once_with("example.com.")

    def test_set_record_aborts_on_validation_failure(self) -> None:
        """A failed commit aborts the transaction and re-raises the validation error."""
        self.client.commit_zone.side_effect = DNSValidationError(
            "cname collision", command="zone-commit", error_code="semantic",
        )

        with self.assertRaises(DNSValidationError):
            DNSRecordService.set_record(
                self.net, "dns1", "example.com.", "web", "CNAME", "other.example.com.",
            )

        self.client.abort_zone.assert_called_once_with("example.com.")

    def test_set_record_aborts_when_zone_set_fails(self) -> None:
        """A failed zone-set aborts the transaction before the error propagates."""
        self.client.set_record.side_effect = DNSValidationError(
            "invalid record data", command="zone-set", error_code="syntax",
        )

        with self.assertRaises(DNSValidationError):
            DNSRecordService.set_record(self.net, "dns1", "example.com.", "web", "A", "bad")

        self.client.commit_zone.assert_not_called()
        self.client.abort_zone.assert_called_once_with("example.com.")

    def test_set_record_rejects_empty_fields(self) -> None:
        """Empty owner, type, or data is refused before touching the socket."""
        with self.assertRaises(ValueError):
            DNSRecordService.set_record(self.net, "dns1", "example.com.", "", "A", "10.0.0.3")
        with self.assertRaises(ValueError):
            DNSRecordService.set_record(self.net, "dns1", "example.com.", "web", "", "10.0.0.3")
        with self.assertRaises(ValueError):
            DNSRecordService.set_record(self.net, "dns1", "example.com.", "web", "A", "  ")

        self.client.begin_zone.assert_not_called()

    def test_set_record_rejects_non_positive_ttl(self) -> None:
        """A TTL of zero or less is refused before touching the socket."""
        with self.assertRaises(ValueError):
            DNSRecordService.set_record(self.net, "dns1", "example.com.", "web", "A", "10.0.0.3", ttl=0)
        with self.assertRaises(ValueError):
            DNSRecordService.set_record(self.net, "dns1", "example.com.", "web", "A", "10.0.0.3", ttl=-5)

        self.client.begin_zone.assert_not_called()

    def test_set_record_rejects_partial_old_record(self) -> None:
        """A replacement must identify the complete previous record."""
        with self.assertRaisesRegex(ValueError, "provided together"):
            DNSRecordService.set_record(
                self.net,
                "dns1",
                "example.com.",
                "web",
                "A",
                "10.0.0.3",
                old_owner="web",
                old_type="A",
            )

        self.client.begin_zone.assert_not_called()


@pytest.mark.unit
class DNSRecordServiceUnsetRecordTests(KnotClientStubMixin, unittest.TestCase):
    """Test suite for atomic record removal via Knot transactions."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _make_net()

    def test_unset_record_commits_atomic_transaction(self) -> None:
        """unset_record runs begin -> zone-unset -> commit against the control socket."""
        result = DNSRecordService.unset_record(
            self.net, "dns1", "example.com.", "web", rtype="A", data="10.0.0.2",
        )

        self.client.begin_zone.assert_called_once_with("example.com.")
        self.client.unset_record.assert_called_once_with(
            "example.com.", "web", rtype="A", data="10.0.0.2",
        )
        self.client.commit_zone.assert_called_once_with("example.com.")
        self.client.diff_zone.assert_called_once_with("example.com.")
        self.assertEqual(result["owner"], "web")
        self.assertEqual(result["type"], "A")
        self.assertEqual(result["data"], "10.0.0.2")

    def test_unset_record_aborts_on_failure(self) -> None:
        """A failed unset aborts the transaction before the error propagates."""
        self.client.unset_record.side_effect = DNSValidationError(
            "record not exists", command="zone-unset", error_code="not exists",
        )

        with self.assertRaises(DNSValidationError):
            DNSRecordService.unset_record(self.net, "dns1", "example.com.", "ghost")

        self.client.commit_zone.assert_not_called()
        self.client.diff_zone.assert_not_called()
        self.client.abort_zone.assert_called_once_with("example.com.")

    def test_unset_record_rejects_empty_owner(self) -> None:
        """An empty owner is refused before touching the socket."""
        with self.assertRaises(ValueError):
            DNSRecordService.unset_record(self.net, "dns1", "example.com.", "")

        self.client.begin_zone.assert_not_called()

    def test_set_ds_record_commits_transaction(self) -> None:
        """set_record supports DS record creation in transaction."""
        record = DNSRecordService.set_record(
            self.net,
            "dns1",
            "com.",
            "example.com.",
            "DS",
            "2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
            ttl=300,
        )
        self.client.begin_zone.assert_called_once_with("com.")
        self.client.set_record.assert_called_once_with(
            "com.",
            "example.com.",
            "DS",
            "2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab",
            ttl=300,
        )
        self.client.commit_zone.assert_called_once_with("com.")
        self.assertEqual(record["type"], "DS")

    def test_trigger_dnssec_rollover_success(self) -> None:
        """trigger_dnssec_rollover delegates to KnotControlClient.zone_key_rollover."""
        self.client.zone_key_rollover.return_value = {"status": "ok"}
        res = DNSRecordService.trigger_dnssec_rollover(
            self.net, "dns1", "example.com.", key_type="zsk"
        )
        self.client.zone_key_rollover.assert_called_once_with("example.com.", "zsk")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["key_type"], "zsk")

