"""Tests for the CaptureService: one shared feed per Interface, many viewers.

Driven with a fake spawner whose "tshark" is a real pipe the test writes
PDML into, so the service reads, splits and fans out exactly as it would
live — only the process is missing.  What is asserted is what a viewer
would see: one process however many watch, a second viewer attaching
rather than being refused, a slow viewer losing packets and being told,
a reload surviving the grace window and a Capture ending after it, and a
stopped Lab taking its Captures with it.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional

from mniplab.labhandle import NodeAttachment
from mniplab.tests.fakes import FakeCaptureSpawner as FakeSpawner
from mniplab.webserver.capture import (
    MAX_CAPTURES_PER_VIEWER,
    CaptureLimitError,
    CaptureService,
    RateGuard,
    capture_target_error,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pdml"
DHCP = (FIXTURES / "dhcp.pdml").read_bytes()
FIRST_PACKET = DHCP[:DHCP.index(b"</packet>") + len(b"</packet>")]
ONE_PACKET = FIRST_PACKET[FIRST_PACKET.index(b"<packet>"):] + b"\n"

H1 = NodeAttachment("h1", 1234, "/tmp/h1")
R1 = NodeAttachment("r1", 4321, "/tmp/r1")


async def settle() -> None:
    """Let the event loop run the reader and any tasks it woke."""
    for _ in range(30):
        await asyncio.sleep(0.01)


async def drain(subscriber) -> List[dict]:
    """Every message a subscriber has waiting, without blocking."""
    messages: List[dict] = []
    while True:
        try:
            batch = await asyncio.wait_for(subscriber.get(), timeout=0.05)
        except asyncio.TimeoutError:
            return messages
        messages.extend(batch)


class CaptureServiceCase(unittest.IsolatedAsyncioTestCase):
    """A service with a fake spawner and a short grace window."""

    grace = 0.2

    async def asyncSetUp(self) -> None:
        self.spawner = FakeSpawner()
        self.service = CaptureService(spawner=self.spawner, grace_seconds=self.grace)

    async def asyncTearDown(self) -> None:
        await self.service.stop_all("test over")

    async def open(self, attachment=H1, interface="h1-eth0", key="k1", capture_filter="", viewer=None):
        return await self.service.subscribe(
            key, f"/tmp/labs/{key}", attachment, interface,
            capture_filter=capture_filter, viewer=viewer,
        )


class SharedFeedTests(CaptureServiceCase):
    """One process per Interface, fanned out to every viewer."""

    async def test_the_first_viewer_starts_the_capture(self):
        await self.open()

        self.assertEqual(len(self.spawner.commands), 1)

    async def test_a_second_viewer_attaches_rather_than_starting_another(self):
        first = await self.open()
        second = await self.open()

        self.assertEqual(len(self.spawner.commands), 1)
        self.assertIsNot(first, second)

    async def test_every_viewer_sees_each_packet(self):
        first = await self.open()
        second = await self.open()

        self.spawner.processes[0].emit(DHCP)
        await settle()

        for subscriber in (first, second):
            packets = [m for m in await drain(subscriber) if m["type"] == "packet"]
            self.assertEqual(len(packets), 4)

    async def test_a_packet_carries_its_summary_and_its_whole_pdml(self):
        viewer = await self.open()

        self.spawner.processes[0].emit(ONE_PACKET)
        await settle()

        packet = [m for m in await drain(viewer) if m["type"] == "packet"][0]
        self.assertEqual(packet["summary"]["protocol"], "DHCP")
        self.assertTrue(packet["pdml"].startswith("<packet>"))
        self.assertTrue(packet["pdml"].endswith("</packet>"))

    async def test_a_viewer_is_first_told_what_it_is_watching(self):
        viewer = await self.open()

        hello = (await drain(viewer))[0]

        self.assertEqual(hello["type"], "capture")
        self.assertEqual((hello["node"], hello["interface"]), ("h1", "h1-eth0"))

    async def test_different_interfaces_get_their_own_process(self):
        await self.open()
        await self.open(R1, "r1-eth0")

        self.assertEqual(len(self.spawner.commands), 2)

    async def test_the_same_interface_name_in_two_labs_is_two_captures(self):
        await self.open(key="k1")
        await self.open(key="k2")

        self.assertEqual(len(self.spawner.commands), 2)
        paths = {command.pcap_path for command in self.spawner.commands}
        self.assertEqual(len(paths), 2)

    async def test_a_late_viewer_sees_what_it_missed(self):
        await self.open()
        self.spawner.processes[0].emit(DHCP)
        await settle()

        late = await self.open()

        packets = [m for m in await drain(late) if m["type"] == "packet"]
        self.assertEqual(len(packets), 4)


class SlowViewerTests(CaptureServiceCase):
    """A stalled browser loses packets; it never stalls the feed."""

    async def asyncSetUp(self) -> None:
        self.spawner = FakeSpawner()
        self.service = CaptureService(spawner=self.spawner, grace_seconds=self.grace, queue_size=4)

    async def test_a_slow_viewer_drops_and_is_told_how_many(self):
        slow = await self.open()
        fast = await self.open()
        await drain(fast)
        await drain(slow)

        self.spawner.processes[0].emit(ONE_PACKET * 3)
        await settle()
        fast_packets = [m for m in await drain(fast) if m["type"] == "packet"]
        # The fast viewer keeps up; the slow one never reads, so the next
        # three overflow its queue of four after one more fits.
        self.spawner.processes[0].emit(ONE_PACKET * 3)
        await settle()
        fast_packets += [m for m in await drain(fast) if m["type"] == "packet"]

        slow_messages = await drain(slow)
        self.assertEqual(len(fast_packets), 6)
        self.assertEqual(len([m for m in slow_messages if m["type"] == "packet"]), 4)
        self.assertEqual(
            [m["count"] for m in slow_messages if m["type"] == "dropped"], [2]
        )


class LifetimeTests(CaptureServiceCase):
    """A Capture outlives a reload and ends after the grace window."""

    async def test_a_capture_survives_its_last_viewer_for_the_grace_window(self):
        viewer = await self.open()
        await self.service.unsubscribe(viewer)

        await asyncio.sleep(self.grace / 2)
        again = await self.open()

        self.assertEqual(self.spawner.stopped, [])
        self.assertEqual(len(self.spawner.commands), 1)
        self.assertIsNotNone(again)

    async def test_a_capture_stops_once_the_grace_window_passes_unwatched(self):
        viewer = await self.open()
        await self.service.unsubscribe(viewer)

        await asyncio.sleep(self.grace * 2)

        self.assertEqual(self.spawner.stopped, self.spawner.processes)
        self.assertEqual(self.service.captures_on("k1"), 0)

    async def test_a_viewer_who_closes_the_pane_stops_it_at_once(self):
        viewer = await self.open()
        await self.service.unsubscribe(viewer, linger=False)

        self.assertEqual(self.spawner.stopped, self.spawner.processes)

    async def test_closing_the_pane_leaves_a_capture_others_still_watch(self):
        viewer = await self.open()
        await self.open()
        await self.service.unsubscribe(viewer, linger=False)

        self.assertEqual(self.spawner.stopped, [])

    async def test_a_capture_whose_tshark_exits_ends_for_its_viewers(self):
        viewer = await self.open()

        self.spawner.processes[0].exit()
        await settle()

        ended = [m for m in await drain(viewer) if m["type"] == "ended"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(self.service.captures_on("k1"), 0)

    async def test_a_capture_that_fails_to_start_says_why(self):
        with tempfile.TemporaryDirectory() as lab_dir:
            viewer = await self.service.subscribe("k1", lab_dir, H1, "h1-eth9")
            log_path = self.spawner.commands[0].log_path
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "w") as log:
                log.write('Running as user "root" and group "root". This could be dangerous.\n'
                          "Capturing on 'h1-eth9'\n"
                          'tshark: There is no device named "h1-eth9".\n'
                          "(No such device exists)\n\ntshark: \n0 packets captured\n")

            self.spawner.processes[0].exit()
            await settle()

            ended = [m for m in await drain(viewer) if m["type"] == "ended"]
        self.assertEqual(ended[0]["reason"], 'There is no device named "h1-eth9".')

    async def test_stopping_a_lab_stops_its_captures_and_no_others(self):
        mine = await self.open(key="k1")
        await self.open(R1, "r1-eth0", key="k1")
        await self.open(key="k2")

        await self.service.stop_lab_captures("k1", "Lab stopped")

        self.assertEqual(self.spawner.stopped, self.spawner.processes[:2])
        self.assertEqual(self.service.captures_on("k1"), 0)
        self.assertEqual(self.service.captures_on("k2"), 1)
        ended = [m for m in await drain(mine) if m["type"] == "ended"]
        self.assertEqual(ended[0]["reason"], "Lab stopped")


class FilterTests(CaptureServiceCase):
    """A filter is part of what a Capture is: another filter is another Capture."""

    async def test_the_filter_reaches_tshark(self):
        await self.open(capture_filter="udp port 67 or udp port 68")

        argv = self.spawner.commands[0].argv
        self.assertEqual(argv[argv.index("-f") + 1], "udp port 67 or udp port 68")

    async def test_viewers_with_the_same_filter_share_one_process(self):
        await self.open(capture_filter="arp")
        await self.open(capture_filter="arp")

        self.assertEqual(len(self.spawner.commands), 1)

    async def test_another_filter_on_the_same_interface_is_its_own_capture(self):
        unfiltered = await self.open()
        await self.open(capture_filter="arp")

        self.assertEqual(len(self.spawner.commands), 2)
        # The classmate watching unfiltered is not restarted under them.
        self.assertEqual(self.spawner.stopped, [])
        self.assertEqual((await drain(unfiltered))[0]["filter"], "")

    async def test_a_new_filter_leaves_the_rings_of_captures_still_running(self):
        await self.open()
        await self.open(R1, "r1-eth0")
        await self.open(capture_filter="arp")

        # Only the unfiltered h1-eth0 Capture shares the Interface.
        self.assertEqual(self.spawner.kept[2], [self.spawner.commands[0].pcap_path])

    async def test_a_viewer_is_told_the_filter_it_is_watching_through(self):
        viewer = await self.open(capture_filter="  proto ospf ")

        hello = (await drain(viewer))[0]
        self.assertEqual(hello["filter"], "proto ospf")

    async def test_an_unusable_filter_is_refused_before_anything_starts(self):
        with self.assertRaises(ValueError):
            await self.open(capture_filter="arp\x07")

        self.assertEqual(self.spawner.commands, [])

    async def test_an_invalid_expression_ends_the_capture_saying_why(self):
        with tempfile.TemporaryDirectory() as lab_dir:
            viewer = await self.service.subscribe(
                "k1", lab_dir, H1, "h1-eth0", capture_filter="udp prt 5"
            )
            log_path = self.spawner.commands[0].log_path
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "w") as log:
                # tshark's own words, as it wrote them for this expression.
                log.write('Running as user "root" and group "root". This could be dangerous.\n'
                          "Capturing on 'h1-eth0'\n"
                          "tshark: Invalid capture filter \"udp prt 5\" for interface 'h1-eth0'.\n\n"
                          "That string isn't a valid capture filter (can't parse filter "
                          "expression: syntax error).\n"
                          "See the User's Guide for a description of the capture filter syntax.\n"
                          "tshark: \n0 packets captured\n")

            self.spawner.processes[0].exit()
            await settle()

            ended = [m for m in await drain(viewer) if m["type"] == "ended"][0]
        self.assertEqual(
            ended["reason"],
            'The filter "udp prt 5" is not a valid capture filter: '
            "can't parse filter expression: syntax error",
        )
        self.assertTrue(ended["filter_error"])


class ViewerLimitTests(CaptureServiceCase):
    """Two Captures per viewer: exactly what a before-and-after comparison needs."""

    async def test_a_viewer_may_watch_two_interfaces(self):
        await self.open(viewer="alice")
        await self.open(R1, "r1-eth0", viewer="alice")

        self.assertEqual(MAX_CAPTURES_PER_VIEWER, 2)
        self.assertEqual(len(self.spawner.commands), 2)

    async def test_a_third_is_refused_with_a_reason(self):
        await self.open(viewer="alice")
        await self.open(R1, "r1-eth0", viewer="alice")

        with self.assertRaises(CaptureLimitError) as refused:
            await self.open(R1, "r1-eth1", viewer="alice")

        self.assertIn("two", str(refused.exception).lower())
        self.assertEqual(len(self.spawner.commands), 2)

    async def test_the_limit_is_each_viewers_own(self):
        await self.open(viewer="alice")
        await self.open(R1, "r1-eth0", viewer="alice")

        await self.open(R1, "r1-eth1", viewer="bob")

        self.assertEqual(len(self.spawner.commands), 3)

    async def test_reopening_a_capture_already_held_is_not_a_third(self):
        # A reload reconnects before the server has seen the old pane go.
        await self.open(viewer="alice")
        await self.open(R1, "r1-eth0", viewer="alice")

        await self.open(viewer="alice")

        self.assertEqual(len(self.spawner.commands), 2)

    async def test_closing_one_frees_its_place(self):
        first = await self.open(viewer="alice")
        await self.open(R1, "r1-eth0", viewer="alice")
        await self.service.unsubscribe(first, linger=False)

        await self.open(R1, "r1-eth1", viewer="alice")

        self.assertEqual(len(self.spawner.commands), 3)

    async def test_joining_a_classmates_capture_counts_toward_the_limit(self):
        await self.open(viewer="bob")
        await self.open(viewer="alice")
        await self.open(R1, "r1-eth0", viewer="alice")

        with self.assertRaises(CaptureLimitError):
            await self.open(R1, "r1-eth1", viewer="alice")


class RateGuardTests(unittest.TestCase):
    """Above 200 packets a second the feed degrades to summaries, and says so."""

    def feed(self, guard: RateGuard, rate: int, seconds: float, start: float) -> List[bool]:
        changes = []
        count = int(rate * seconds)
        for i in range(count):
            change = guard.observe(start + i / rate)
            if change is not None:
                changes.append(change)
        return changes

    def test_a_lesson_rate_never_trips_it(self):
        guard = RateGuard()

        self.assertEqual(self.feed(guard, 30, 5, 0.0), [])
        self.assertFalse(guard.active)

    def test_sustained_flooding_trips_it_once(self):
        guard = RateGuard()

        self.assertEqual(self.feed(guard, 400, 3, 0.0), [True])
        self.assertTrue(guard.active)

    def test_it_holds_between_the_two_thresholds_so_it_cannot_flap(self):
        guard = RateGuard()
        self.feed(guard, 400, 2, 0.0)

        self.assertEqual(self.feed(guard, 150, 3, 2.0), [])
        self.assertTrue(guard.active)

    def test_it_releases_below_one_hundred(self):
        guard = RateGuard()
        self.feed(guard, 400, 2, 0.0)

        self.assertEqual(self.feed(guard, 50, 3, 2.0), [False])


class GuardedFeedTests(CaptureServiceCase):
    """With the guard on, viewers get summaries only and are told why."""

    async def test_a_flood_sends_summaries_without_trees(self):
        viewer = await self.open()
        self.service.clock = iter(i / 1000 for i in range(10 ** 6)).__next__

        self.spawner.processes[0].emit(ONE_PACKET * 220)
        await settle()

        messages = await drain(viewer)
        guard = [m for m in messages if m["type"] == "rate_guard"]
        packets = [m for m in messages if m["type"] == "packet"]
        self.assertEqual(guard[0]["active"], True)
        self.assertIn("pdml", packets[0])
        self.assertNotIn("pdml", packets[-1])
        self.assertEqual(packets[-1]["summary"]["protocol"], "DHCP")


class TargetTests(unittest.TestCase):
    """Only an Interface a Router, Host, Speaker or Switch port lists may be captured."""

    TOPOLOGY = {
        "nodes": [
            {"id": "r1", "type": "router"},
            {"id": "h1", "type": "host"},
            {"id": "ex1", "type": "bgp-speaker"},
            {"id": "s1", "type": "switch"},
            {"id": "c1", "type": "container"},
        ],
        "links": [
            {"source": "r1", "target": "h1", "source_intf": "r1-eth0", "target_intf": "h1-eth0"},
            {"source": "ex1", "target": "r1", "source_intf": "ex1-eth0", "target_intf": "r1-eth1"},
            {"source": "s1", "target": "r1", "source_intf": "s1-eth1", "target_intf": "r1-eth2"},
            {"source": "c1", "target": "r1", "source_intf": "c1-eth0", "target_intf": "r1-eth3"},
        ],
    }

    INTERFACES = {
        "r1": ["r1-eth0", "r1-eth1", "r1-eth2", "r1-eth3", "r1-eth4"],
        "h1": ["h1-eth0"],
        "ex1": ["ex1-eth0"],
        "s1": ["s1-eth1"],
        "c1": ["c1-eth0"],
    }

    def error(self, node: str, interface: str) -> Optional[str]:
        return capture_target_error(
            self.TOPOLOGY, node, interface, self.INTERFACES.get(node, [])
        )

    def test_either_end_of_a_link_on_a_router_host_or_speaker_is_allowed(self):
        for node, interface in (("r1", "r1-eth0"), ("h1", "h1-eth0"), ("ex1", "ex1-eth0"), ("r1", "r1-eth2")):
            with self.subTest(node=node, interface=interface):
                self.assertIsNone(self.error(node, interface))

    def test_an_interface_whose_link_is_not_drawn_is_allowed(self):
        # r1-eth4 faces a hidden NAT Gateway: in r1's namespace, r1's traffic.
        self.assertIsNone(self.error("r1", "r1-eth4"))

    def test_an_interface_the_node_does_not_have_is_refused(self):
        self.assertIn("r1-eth0", self.error("h1", "r1-eth0") or "")

    def test_a_node_not_in_the_topology_is_refused(self):
        # A NAT Gateway is hidden from the Topology, and its root-namespace
        # uplink carries every Lab's traffic: never selectable.
        self.assertIsNotNone(self.error("nat0", "nat0-eth0"))

    def test_a_port_the_switch_lists_is_allowed_and_any_other_name_is_refused(self):
        self.assertIsNone(self.error("s1", "s1-eth1"))
        for odd in ("k7-s1", "k7-s1-eth1", "s1-eth2", "lo", ""):
            with self.subTest(interface=odd):
                self.assertIn("no Interface named", self.error("s1", odd) or "")

    def test_a_container_host_is_refused_for_now(self):
        self.assertIn("Container Host", self.error("c1", "c1-eth0") or "")


if __name__ == "__main__":
    unittest.main()
