"""Tests for the Knot transfer-log parser behind the Transfer Log.

The parser is a pure function over log text, so every case here is captured
output from a real Knot 3.2 run of the Secondary Nameserver Lab rather than a
mock: the three outcomes a Learner must be able to tell apart (nothing moved,
an incremental transfer, a forced full copy) and the four runtime states
(Primary unreachable, transfer refused, Zone expired, Serial unmoved).
"""

from __future__ import annotations

import unittest

import pytest

from mniplab.dns.query import parse_replication_state, parse_transfer_log

APEX = "example.com."

# A Secondary's log across start-up, an unchanged refresh, an edit on the
# Primary, and a forced retransfer.
SECONDARY_LOG = """\
2026-09-10T14:58:46+0000 info: Knot DNS 3.2.6 starting
2026-09-10T14:58:46+0000 info: [example.com.] zone will be bootstrapped
2026-09-10T14:58:46+0000 info: [example.com.] control, received command 'zone-refresh'
2026-09-10T14:58:50+0000 info: [example.com.] AXFR, incoming, remote 10.0.1.2@53, started
2026-09-10T14:58:50+0000 info: [example.com.] AXFR, incoming, remote 10.0.1.2@53, finished, 3.07 seconds, 1 messages, 844 bytes
2026-09-10T14:58:50+0000 info: [example.com.] zone stored to journal, serial 1
2026-09-10T14:58:50+0000 info: [example.com.] refresh, remote 10.0.1.2@53, zone updated, 3.07 seconds, serial none -> 1, expires in 600 seconds
2026-09-10T14:58:50+0000 info: [example.com.] control, received command 'zone-refresh'
2026-09-10T14:58:50+0000 info: [example.com.] refresh, remote 10.0.1.2@53, remote serial 1, zone is up-to-date, expires in 600 seconds
2026-09-10T14:58:53+0000 info: [example.com.] notify, incoming, remote 10.0.1.2@43120, serial 2
2026-09-10T14:58:53+0000 info: [example.com.] IXFR, incoming, remote 10.0.1.2@53, started
2026-09-10T14:58:53+0000 info: [example.com.] IXFR, incoming, remote 10.0.1.2@53, finished, 0.00 seconds, 1 messages, 277 bytes
2026-09-10T14:58:53+0000 info: [example.com.] refresh, remote 10.0.1.2@53, zone updated, 0.00 seconds, serial 1 -> 2, expires in 600 seconds
2026-09-10T14:58:58+0000 info: [example.com.] control, received command 'zone-retransfer'
2026-09-10T14:58:58+0000 info: [example.com.] AXFR, incoming, remote 10.0.1.2@53, started
2026-09-10T14:58:58+0000 info: [example.com.] AXFR, incoming, remote 10.0.1.2@53, finished, 0.00 seconds, 1 messages, 869 bytes
2026-09-10T14:58:58+0000 info: [example.com.] refresh, remote 10.0.1.2@53, zone updated, 0.00 seconds, serial 2 -> 2, expires in 600 seconds
"""

# The same window seen from the Primary, which serves rather than receives.
PRIMARY_LOG = """\
2026-09-10T14:58:46+0000 info: [example.com.] loaded, serial none -> 1, 1199 bytes
2026-09-10T14:58:50+0000 info: [example.com.] AXFR, outgoing, remote 10.0.2.2@38110, started, serial 1
2026-09-10T14:58:50+0000 info: [example.com.] AXFR, outgoing, remote 10.0.2.2@38110, finished, 0.00 seconds, 1 messages, 836 bytes
2026-09-10T14:58:53+0000 info: [example.com.] notify, outgoing, remote 10.0.2.2@53, serial 2
2026-09-10T14:58:53+0000 info: [example.com.] IXFR, outgoing, remote 10.0.2.2@56036, started, serial 1 -> 2
2026-09-10T14:58:53+0000 info: [example.com.] IXFR, outgoing, remote 10.0.2.2@56036, finished, 0.00 seconds, 1 messages, 269 bytes
"""

UNREACHABLE_LOG = """\
2026-09-10T14:58:47+0000 info: [example.com.] refresh, remote primary_example_com, address 10.0.1.2@53, failed (connection reset)
2026-09-10T14:58:47+0000 warning: [example.com.] refresh, remote primary_example_com not usable
2026-09-10T14:58:47+0000 error: [example.com.] refresh, failed (no usable master), next retry at 2026-09-10T14:59:10+0000
2026-09-10T14:58:47+0000 error: [example.com.] zone event 'refresh' failed (no usable master)
"""

REFUSED_LOG = """\
2026-09-10T14:59:52+0000 warning: [example.com.] AXFR, incoming, remote 10.0.1.2@53, server responded with error 'NOTAUTH'
2026-09-10T14:59:52+0000 info: [example.com.] refresh, remote primary_example_com, address 10.0.1.2@53, failed (not allowed)
2026-09-10T14:59:52+0000 warning: [example.com.] refresh, remote primary_example_com not usable
2026-09-10T14:59:52+0000 error: [example.com.] refresh, failed (no usable master), next retry at 2026-09-10T15:00:12+0000
"""

EXPIRED_LOG = """\
2026-09-10T15:00:55+0000 info: [example.com.] refresh, remote 10.0.1.2@53, zone updated, 4.10 seconds, serial none -> 1, expires in 10 seconds
2026-09-10T15:01:05+0000 info: [example.com.] zone expired
2026-09-10T15:01:06+0000 error: [example.com.] refresh, failed (no usable master), next retry at 2026-09-10T15:01:07+0000
"""


@pytest.mark.unit
class TransferLogParserTests(unittest.TestCase):
    """The Transfer Log parsed out of Knot's own log text."""

    def test_incoming_transfers_carry_every_field(self) -> None:
        """An event names its time, direction, type, remote, Serials and size."""
        events = parse_transfer_log(SECONDARY_LOG)

        self.assertEqual(len(events), 3)
        first = events[0]
        self.assertEqual(first.timestamp, "2026-09-10T14:58:50+0000")
        self.assertEqual(first.zone, APEX)
        self.assertEqual(first.direction, "incoming")
        self.assertEqual(first.transfer_type, "AXFR")
        self.assertEqual(first.remote, "10.0.1.2@53")
        self.assertEqual(first.outcome, "finished")
        self.assertIsNone(first.serial_from)
        self.assertEqual(first.serial_to, 1)
        self.assertEqual(first.messages, 1)
        self.assertEqual(first.bytes, 844)
        self.assertEqual(first.duration, 3.07)

    def test_the_three_outcomes_are_distinguishable(self) -> None:
        """Nothing moved, an incremental transfer, and a forced full copy differ."""
        events = parse_transfer_log(SECONDARY_LOG)

        # An unchanged Zone transfers nothing at all: only three transfers
        # appear across four refreshes.
        edit, forced = events[1], events[2]

        self.assertEqual(edit.transfer_type, "IXFR")
        self.assertEqual((edit.serial_from, edit.serial_to), (1, 2))

        self.assertEqual(forced.transfer_type, "AXFR")
        self.assertEqual((forced.serial_from, forced.serial_to), (2, 2))
        self.assertGreater(forced.bytes or 0, edit.bytes or 0)

    def test_outgoing_transfers_take_serials_from_their_start(self) -> None:
        """A Primary logs the Serial it serves on the started line."""
        events = parse_transfer_log(PRIMARY_LOG)

        self.assertEqual([e.direction for e in events], ["outgoing", "outgoing"])
        full, incremental = events
        self.assertEqual((full.transfer_type, full.serial_to), ("AXFR", 1))
        self.assertIsNone(full.serial_from)
        self.assertEqual(
            (incremental.transfer_type, incremental.serial_from, incremental.serial_to),
            ("IXFR", 1, 2),
        )

    def test_a_refused_transfer_is_a_failed_event(self) -> None:
        """A transfer the Primary's ACL refuses is logged, not lost."""
        events = parse_transfer_log(REFUSED_LOG)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].outcome, "failed")
        self.assertIn("NOTAUTH", events[0].detail)

    def test_events_narrow_to_one_zone(self) -> None:
        """A Node serving several Zones can be read one Zone at a time."""
        mixed = SECONDARY_LOG + (
            "2026-09-10T14:59:00+0000 info: [other.test.] AXFR, incoming, "
            "remote 10.0.9.9@53, finished, 0.00 seconds, 1 messages, 99 bytes\n"
        )

        self.assertEqual(len(parse_transfer_log(mixed, zone="other.test.")), 1)
        self.assertEqual(len(parse_transfer_log(mixed, zone="example.com.")), 3)
        self.assertEqual(len(parse_transfer_log(mixed, zone="example.com")), 3)

    def test_unrecognised_lines_are_skipped_rather_than_raising(self) -> None:
        """An unfamiliar log format degrades to an empty Transfer Log."""
        self.assertEqual(parse_transfer_log(""), [])
        self.assertEqual(parse_transfer_log("not a knot log at all\n\n\x00"), [])
        self.assertEqual(
            parse_transfer_log(
                "2026-09-10T14:58:50+0000 info: [example.com.] AXFR, incoming, "
                "remote 10.0.1.2@53, finished, ? seconds, ? messages, ? bytes\n"
            )[0].bytes,
            None,
        )

    def test_serials_land_on_the_transfer_they_belong_to(self) -> None:
        """A Node replicating from two Primaries must not swap their Serials."""
        two_primaries = (
            "2026-09-10T14:58:50+0000 info: [example.com.] AXFR, incoming, "
            "remote 10.0.1.2@53, finished, 0.00 seconds, 1 messages, 844 bytes\n"
            "2026-09-10T14:58:51+0000 info: [example.com.] AXFR, incoming, "
            "remote 10.0.9.9@53, finished, 0.00 seconds, 1 messages, 900 bytes\n"
            "2026-09-10T14:58:52+0000 info: [example.com.] refresh, remote 10.0.9.9@53, "
            "zone updated, 0.00 seconds, serial none -> 7, expires in 600 seconds\n"
        )

        first, second = parse_transfer_log(two_primaries)
        self.assertIsNone(first.serial_to)
        self.assertEqual(second.remote, "10.0.9.9@53")
        self.assertEqual(second.serial_to, 7)

    def test_events_serialize_for_the_api(self) -> None:
        """Each event is JSON-ready with the same field names both surfaces use."""
        payload = parse_transfer_log(SECONDARY_LOG)[1].to_dict()

        self.assertEqual(payload["transfer_type"], "IXFR")
        self.assertEqual(payload["serial_from"], 1)
        self.assertEqual(payload["bytes"], 277)
        self.assertEqual(payload["zone"], APEX)

    def test_notify_events_appear_alongside_transfers_when_requested(self) -> None:
        """Notify events appear in chronological order alongside transfers."""
        events = parse_transfer_log(SECONDARY_LOG, include_notify=True)

        self.assertEqual(len(events), 4)
        notify = events[1]
        self.assertEqual(notify.timestamp, "2026-09-10T14:58:53+0000")
        self.assertEqual(notify.transfer_type, "NOTIFY")
        self.assertEqual(notify.direction, "incoming")
        self.assertEqual(notify.remote, "10.0.1.2@43120")
        self.assertEqual(notify.serial_to, 2)
        self.assertIsNone(notify.serial_from)
        self.assertEqual(notify.outcome, "finished")

        outgoing_events = parse_transfer_log(PRIMARY_LOG, include_notify=True)
        self.assertEqual(len(outgoing_events), 3)
        self.assertEqual(outgoing_events[1].transfer_type, "NOTIFY")
        self.assertEqual(outgoing_events[1].direction, "outgoing")
        self.assertEqual(outgoing_events[1].serial_to, 2)


@pytest.mark.unit
class ReplicationStateTests(unittest.TestCase):
    """The four runtime states, each naming its cause."""

    def test_primary_unreachable_names_the_next_retry(self) -> None:
        """A Secondary that cannot reach its Primary says when it tries again."""
        state = parse_replication_state(UNREACHABLE_LOG, APEX)

        self.assertEqual(state["state"], "primary-unreachable")
        self.assertEqual(state["next_retry"], "2026-09-10T14:59:10+0000")
        self.assertIn("connection reset", state["detail"])

    def test_a_refused_transfer_is_not_reported_as_unreachable(self) -> None:
        """An ACL refusal reaches the Primary; calling it unreachable hides the cause."""
        state = parse_replication_state(REFUSED_LOG, APEX)

        self.assertEqual(state["state"], "transfer-refused")
        self.assertEqual(state["next_retry"], "2026-09-10T15:00:12+0000")
        self.assertIn("not allowed", state["detail"])

    def test_an_expired_zone_says_since_when(self) -> None:
        """Expiry is the moment the Secondary stops answering, so it carries a time."""
        state = parse_replication_state(EXPIRED_LOG, APEX)

        self.assertEqual(state["state"], "expired")
        self.assertEqual(state["since"], "2026-09-10T15:01:05+0000")

    def test_an_unmoved_serial_reports_up_to_date(self) -> None:
        """A refresh that moves nothing is a state of its own, not an absence."""
        state = parse_replication_state(SECONDARY_LOG[: SECONDARY_LOG.index("notify")], APEX)

        self.assertEqual(state["state"], "up-to-date")
        self.assertEqual(state["serial"], 1)

    def test_a_completed_transfer_reports_replicated(self) -> None:
        """The last thing that happened wins, so a later transfer clears a failure."""
        state = parse_replication_state(UNREACHABLE_LOG + SECONDARY_LOG, APEX)

        self.assertEqual(state["state"], "replicated")
        self.assertEqual(state["serial"], 2)
        self.assertIsNone(state["next_retry"])

    def test_an_unreadable_log_has_an_unknown_state(self) -> None:
        """No log and no recognised line are the same answer, and neither raises."""
        for text in ("", "garbage\n"):
            self.assertEqual(parse_replication_state(text, APEX)["state"], "unknown")
