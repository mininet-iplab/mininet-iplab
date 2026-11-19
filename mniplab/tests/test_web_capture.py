"""Tests for opening a Packet Capture through the registry and its WebSocket.

The registry is where a Capture meets a Lab: the Interface is checked
against the Lab's Topology, the Node is attached by its Namespace Anchor pid
exactly as a Terminal is, the pcap goes under that Lab's directory, and
stopping the Lab stops its Captures.  The handler is what a browser pane
holds: it forwards the feed and tells a closed pane from a reload.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import unittest
from types import SimpleNamespace
from typing import Any, List

from fastapi import WebSocketDisconnect

from mniplab.constants import LABS_DIR
from mniplab.tests.fakes import (
    FAKE_LAB_KEY,
    FakeCaptureSpawner,
    FakeIntf,
    FakeLab,
    FakeLink,
    FakeNode,
    FrozenSessionClockMixin,
)
from mniplab.tests.test_switch_unkeyed_ports import keyed_switch
from mniplab.webserver.auth import create_session
from mniplab.webserver.capture import CaptureService, capture_node_error
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.websocket import CaptureWebSocketHandler


def two_node_lab(key: str = FAKE_LAB_KEY, outside: bool = True) -> FakeLab:
    """A Router and a Host joined by one Link, plus an Internet Gateway.

    Args:
        key: The Lab key.
        outside: True to mark the Router's Interface toward the Gateway as
            outside, the way Router NAT declares a translating Interface.
    """
    r1 = FakeNode("r1", display_type="router")
    h1 = FakeNode("h1")
    nat = FakeNode("nat0", hidden=True, display_type="internet")
    nat.inNamespace = False  # type: ignore[attr-defined]
    r1_eth0, h1_eth0 = FakeIntf("r1-eth0", r1), FakeIntf("h1-eth0", h1)
    r1_eth1, nat_eth0 = FakeIntf("r1-eth1", r1), FakeIntf("nat0-eth0", nat)
    for node, pid in ((r1, 4101), (h1, 4102), (nat, 4103)):
        node.pid = pid
    for intf in (r1_eth0, h1_eth0, r1_eth1, nat_eth0):
        intf.node.addIntf(intf)
    if outside:
        r1_eth1.params["nat"] = "outside"
    return FakeLab(
        r1, h1, nat,
        links=[FakeLink(r1_eth0, h1_eth0), FakeLink(r1_eth1, nat_eth0)],
        lab_key=key,
    )


class CaptureNodeErrorTests(unittest.TestCase):
    """What a Node-wide Packet Capture says about each kind of Node."""

    def test_an_internet_gateway_is_refused_as_outside_the_lab(self) -> None:
        topology = {"nodes": [{"id": "nat0", "type": "internet"}]}

        self.assertEqual(capture_node_error(topology, "nat0"),
                         "nat0 is the Internet, which is outside the Lab")


class RegistryCaptureCase(unittest.IsolatedAsyncioTestCase):
    """A registry holding one fake Lab, with Captures that start nothing."""

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.spawner = FakeCaptureSpawner()
        self.registry.capture_service = CaptureService(spawner=self.spawner, grace_seconds=0.2)
        self.lab = two_node_lab()
        self.registry.lab_service.lab = self.lab

    async def asyncTearDown(self) -> None:
        await self.registry.capture_service.stop_all("test over")


class OpenCaptureTests(RegistryCaptureCase):
    """Which Interfaces may be captured, and how the capture attaches."""

    async def test_an_interface_on_a_drawn_link_is_captured_in_its_node(self) -> None:
        await self.registry.open_capture(FAKE_LAB_KEY, "h1", "h1-eth0")

        argv = self.spawner.commands[0].argv
        self.assertEqual(argv[:3], ["nsenter", "-t", "4102"])
        self.assertEqual(argv[argv.index("-i") + 1], "h1-eth0")

    async def test_the_pcap_goes_under_this_labs_directory(self) -> None:
        await self.registry.open_capture(FAKE_LAB_KEY, "r1", "r1-eth0")

        pcap = self.spawner.commands[0].pcap_path
        self.assertTrue(pcap.startswith(os.path.join(LABS_DIR, FAKE_LAB_KEY) + os.sep))

    async def test_a_hidden_internet_gateway_is_refused_as_outside_the_lab(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            await self.registry.open_capture(FAKE_LAB_KEY, "nat0", "nat0-eth0")

        self.assertEqual(str(ctx.exception), "nat0 is the Internet, which is outside the Lab")
        self.assertEqual(self.spawner.commands, [])

    async def test_a_drawn_internet_gateway_is_refused_the_same_way(self) -> None:
        self.lab.get("nat0").hidden = False
        with self.assertRaises(ValueError) as ctx:
            await self.registry.open_capture(FAKE_LAB_KEY, "nat0", "nat0-eth0")

        self.assertEqual(str(ctx.exception), "nat0 is the Internet, which is outside the Lab")
        self.assertEqual(self.spawner.commands, [])

    async def test_the_routers_end_of_a_link_to_an_internet_gateway_is_still_captured(self) -> None:
        await self.registry.open_capture(FAKE_LAB_KEY, "r1", "r1-eth1")

        self.assertEqual(self.spawner.commands[0].argv[:3], ["nsenter", "-t", "4101"])

    async def test_an_interface_the_node_lacks_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "no Interface named"):
            await self.registry.open_capture(FAKE_LAB_KEY, "h1", "r1-eth0")

    async def test_a_lab_that_is_not_running_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "No lab running"):
            await self.registry.open_capture("other", "h1", "h1-eth0")

    async def test_a_routers_nat_facing_interface_is_captured_though_not_drawn(self) -> None:
        # The Router's own end of the hidden Link lives in the Router's
        # namespace and carries traffic before translation: its own Lab's.
        await self.registry.open_capture(FAKE_LAB_KEY, "r1", "r1-eth1")

        argv = self.spawner.commands[0].argv
        self.assertEqual(argv[:3], ["nsenter", "-t", "4101"])
        self.assertEqual(argv[argv.index("-i") + 1], "r1-eth1")

    async def test_the_filter_and_the_viewer_reach_the_capture(self) -> None:
        viewer = await self.registry.open_capture(
            FAKE_LAB_KEY, "h1", "h1-eth0", capture_filter="arp", viewer="session-1"
        )

        argv = self.spawner.commands[0].argv
        self.assertEqual(argv[argv.index("-f") + 1], "arp")
        self.assertEqual(viewer.viewer, "session-1")


def switch_lab(key: str = "k7") -> FakeLab:
    """A Host linked to s1 of Lab ``key``, whose ports carry the Lab key.

    The root namespace around s1 also holds its bridge's internal port and
    another Lab's s1 port, which no request may reach.
    """
    switch = keyed_switch()
    switch.bridge_name = f"{key}-s1"
    switch.nameToIntf = {
        name: SimpleNamespace(name=name, link=None, node=switch)
        for name in (f"{key}-s1-eth1", f"{key}-s1-eth2", f"{key}-s1", "lo", "k9-s1-eth1")
    }
    h1, h2 = FakeNode("h1"), FakeNode("h2")
    h1_eth0, h2_eth0 = FakeIntf("h1-eth0", h1), FakeIntf("h2-eth0", h2)
    h1.addIntf(h1_eth0)
    h2.addIntf(h2_eth0)
    h1.pid, h2.pid = 4102, 4103
    links = []
    for host_intf, port in ((h1_eth0, f"{key}-s1-eth1"), (h2_eth0, f"{key}-s1-eth2")):
        links.append(FakeLink(host_intf, switch.nameToIntf[port]))
    return FakeLab(h1, h2, switches=[switch], links=links, lab_key=key)


class SwitchCaptureTests(unittest.IsolatedAsyncioTestCase):
    """A Switch's ports are captured in the root namespace, only this Lab's own."""

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.spawner = FakeCaptureSpawner()
        self.registry.capture_service = CaptureService(spawner=self.spawner, grace_seconds=0.2)
        self.registry.lab_service.lab = switch_lab()

    async def asyncTearDown(self) -> None:
        await self.registry.capture_service.stop_all("test over")

    async def test_a_plain_port_name_captures_the_keyed_interface_without_entering_a_node(self) -> None:
        await self.registry.open_capture("k7", "s1", "s1-eth1")

        argv = self.spawner.commands[0].argv
        self.assertEqual(argv[0], "tshark")
        self.assertNotIn("nsenter", argv)
        self.assertEqual(argv[argv.index("-i") + 1], "k7-s1-eth1")

    async def test_the_pcap_goes_under_this_labs_directory_by_node_and_plain_name(self) -> None:
        await self.registry.open_capture("k7", "s1", "s1-eth2")

        pcap = self.spawner.commands[0].pcap_path
        self.assertEqual(
            pcap, os.path.join(LABS_DIR, "k7", "captures", "s1", "s1-eth2.pcapng")
        )

    async def test_only_exact_ports_of_this_switch_are_accepted(self) -> None:
        for odd in ("k7-s1", "k7-s1-eth1", "k9-s1-eth1", "lo", "s1", "s1-eth9",
                    "", "../s1-eth1", "s1-eth1 ", "S1-eth1", "s2-eth1"):
            with self.subTest(interface=odd), self.assertRaises(ValueError):
                await self.registry.open_capture("k7", "s1", odd)

        self.assertEqual(self.spawner.commands, [])

    async def test_a_switch_this_lab_does_not_have_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.registry.open_capture("k7", "s2", "s2-eth1")

        self.assertEqual(self.spawner.commands, [])

    async def test_a_host_still_enters_its_own_namespace(self) -> None:
        await self.registry.open_capture("k7", "h1", "h1-eth0")

        self.assertEqual(self.spawner.commands[0].argv[:3], ["nsenter", "-t", "4102"])

    async def test_the_ports_are_listed_by_plain_name_without_the_keyed_interface(self) -> None:
        ports = await self.registry.capture_interfaces("k7", "s1")

        self.assertEqual([p["name"] for p in ports], ["s1-eth1", "s1-eth2"])
        self.assertEqual([p["peer"] for p in ports], ["h1", "h2"])
        self.assertTrue(all("interface" not in p and "k7" not in str(p) for p in ports))

    async def test_the_pcap_of_a_switch_port_is_found_and_a_foreign_port_refused(self) -> None:
        self.assertEqual(await self.registry.capture_pcap("k7", "s1", "s1-eth1", ""), [])
        with self.assertRaises(ValueError):
            await self.registry.capture_pcap("k7", "s1", "k9-s1-eth1", "")


class InterfaceListTests(RegistryCaptureCase):
    """Every Interface a Node has, drawn or not."""

    async def test_every_interface_of_a_node_is_listed_in_port_order(self) -> None:
        interfaces = await self.registry.capture_interfaces(FAKE_LAB_KEY, "r1")

        self.assertEqual([i["name"] for i in interfaces], ["r1-eth0", "r1-eth1"])

    async def test_a_drawn_link_names_its_peer(self) -> None:
        drawn = (await self.registry.capture_interfaces(FAKE_LAB_KEY, "r1"))[0]

        self.assertEqual((drawn["drawn"], drawn["peer"]), (True, "h1"))

    async def test_a_link_to_an_internet_gateway_is_drawn_and_names_it(self) -> None:
        gateway = (await self.registry.capture_interfaces(FAKE_LAB_KEY, "r1"))[1]

        self.assertEqual((gateway["name"], gateway["drawn"], gateway["peer"]), ("r1-eth1", True, "nat0"))

    async def test_addresses_are_listed(self) -> None:
        self.lab.hosts[1].intfList()[0].ip = "10.0.0.2"

        h1 = await self.registry.capture_interfaces(FAKE_LAB_KEY, "h1")

        self.assertEqual(h1[0]["addresses"], ["10.0.0.2"])

    async def test_an_internet_gateway_has_no_list(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            await self.registry.capture_interfaces(FAKE_LAB_KEY, "nat0")

        self.assertEqual(str(ctx.exception), "nat0 is the Internet, which is outside the Lab")


class PcapTests(RegistryCaptureCase):
    """The pcap a Capture wrote, found by what the pane knows of it."""

    async def test_the_ring_of_the_capture_asked_for_is_returned(self) -> None:
        await self.registry.open_capture(FAKE_LAB_KEY, "h1", "h1-eth0", capture_filter="arp")
        pcap = self.spawner.commands[0].pcap_path
        lab_dir = os.path.join(LABS_DIR, FAKE_LAB_KEY)
        # Leave no Lab directory behind: a stray one reads as a live Lab.
        made = lab_dir if not os.path.exists(lab_dir) else os.path.join(lab_dir, "captures")
        self.addCleanup(shutil.rmtree, made, ignore_errors=True)
        os.makedirs(os.path.dirname(pcap), exist_ok=True)
        ring = pcap[:-len(".pcapng")] + "_00001_20260923014330.pcapng"
        with open(ring, "wb") as f:
            f.write(b"pcapng")

        files = await self.registry.capture_pcap(FAKE_LAB_KEY, "h1", "h1-eth0", "arp")

        self.assertEqual(files, [ring])

    async def test_an_internet_gateways_pcap_is_never_served(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            await self.registry.capture_pcap(FAKE_LAB_KEY, "nat0", "nat0-eth0", "")

        self.assertEqual(str(ctx.exception), "nat0 is the Internet, which is outside the Lab")


class LabStopTests(RegistryCaptureCase):
    """A Capture ends with its Lab."""

    async def test_stopping_the_lab_stops_its_captures(self) -> None:
        viewer = await self.registry.open_capture(FAKE_LAB_KEY, "h1", "h1-eth0")

        await self.registry.stop_lab(FAKE_LAB_KEY)

        self.assertEqual(self.spawner.stopped, self.spawner.processes)
        messages: List[Any] = []
        while True:
            try:
                messages += await asyncio.wait_for(viewer.get(), timeout=0.05)
            except asyncio.TimeoutError:
                break
        self.assertIn("ended", [m["type"] for m in messages])


class FakeCaptureSocket:
    """A browser pane's socket: records what it is sent, and sends its inbox."""

    def __init__(self, session: str, inbox: List[Any], hold_open: float = 0.2) -> None:
        self.cookies = {"session": session}
        self.sent: List[Any] = []
        self.closes: List[Any] = []
        self._inbox = list(inbox)
        self._hold_open = hold_open

    async def accept(self) -> None:
        pass

    async def receive_json(self) -> Any:
        # The pane stays open long enough for the feed to arrive, then
        # sends its inbox and drops.
        await asyncio.sleep(self._hold_open)
        if not self._inbox:
            raise WebSocketDisconnect(code=1001)
        return self._inbox.pop(0)

    async def send_json(self, payload: Any) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closes.append((code, reason))


class CaptureHandlerTests(FrozenSessionClockMixin, RegistryCaptureCase):
    """What a pane receives, and what leaving it does to the Capture."""

    def setUp(self) -> None:
        super().setUp()
        from unittest.mock import patch

        patcher = patch("mniplab.webserver.websocket.registry", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = create_session("learner", role="learner")

    async def run_pane(
        self, inbox: List[Any], node: str = "h1", intf: str = "h1-eth0",
        capture_filter: str = "", session: Any = None,
    ):
        socket = FakeCaptureSocket(session or self.session, inbox)
        await CaptureWebSocketHandler(
            socket, FAKE_LAB_KEY, node, intf, capture_filter  # type: ignore[arg-type]
        ).run()
        return socket

    def tshark_filter(self, index: int) -> Any:
        argv = self.spawner.commands[index].argv
        return argv[argv.index("-f") + 1] if "-f" in argv else None

    async def test_the_pane_is_told_what_it_is_watching(self) -> None:
        socket = await self.run_pane([])

        self.assertEqual(socket.sent[0]["type"], "capture")
        self.assertEqual(socket.sent[0]["interface"], "h1-eth0")

    async def test_a_pane_closed_on_purpose_stops_an_unwatched_capture_at_once(self) -> None:
        await self.run_pane([{"type": "close"}])

        self.assertEqual(self.spawner.stopped, self.spawner.processes)

    async def test_a_dropped_connection_leaves_the_capture_for_a_reload(self) -> None:
        await self.run_pane([])

        self.assertEqual(self.spawner.stopped, [])
        self.assertEqual(self.registry.capture_service.captures_on(FAKE_LAB_KEY), 1)

    async def test_the_pane_opens_through_the_filter_it_asks_for(self) -> None:
        socket = await self.run_pane([], capture_filter="proto ospf")

        self.assertEqual(self.tshark_filter(0), "proto ospf")
        self.assertEqual(socket.sent[0]["filter"], "proto ospf")

    async def test_changing_the_filter_restarts_the_capture_and_says_so(self) -> None:
        socket = await self.run_pane([{"type": "filter", "filter": "arp"}, {"type": "close"}])

        self.assertEqual([self.tshark_filter(0), self.tshark_filter(1)], [None, "arp"])
        # The first Capture had no one else watching, so it stops at once.
        self.assertIn(self.spawner.processes[0], self.spawner.stopped)
        types = [m["type"] for m in socket.sent]
        restarted = socket.sent[types.index("restarted")]
        self.assertEqual(restarted["filter"], "arp")
        after = socket.sent[types.index("restarted") + 1]
        self.assertEqual((after["type"], after["filter"]), ("capture", "arp"))
        self.assertEqual(socket.closes, [])

    async def test_a_pane_whose_capture_ended_stays_open_to_try_another_filter(self) -> None:
        async def exit_soon() -> None:
            await asyncio.sleep(0.05)
            self.spawner.processes[0].exit()

        task = asyncio.create_task(exit_soon())
        socket = await self.run_pane([{"type": "filter", "filter": "arp"}])
        await task

        self.assertIn("ended", [m["type"] for m in socket.sent])
        self.assertEqual(socket.closes, [])
        self.assertEqual(self.tshark_filter(1), "arp")

    async def test_a_third_capture_for_one_viewer_is_refused_with_a_reason(self) -> None:
        await self.registry.open_capture(FAKE_LAB_KEY, "h1", "h1-eth0", viewer=self.session)
        await self.registry.open_capture(FAKE_LAB_KEY, "r1", "r1-eth0", viewer=self.session)

        socket = await self.run_pane([], node="r1", intf="r1-eth1")

        self.assertEqual(socket.sent[0]["type"], "error")
        self.assertIn("two Captures", socket.sent[0]["message"])
        self.assertEqual(len(self.spawner.commands), 2)

    async def test_a_refused_interface_is_reported_and_closed(self) -> None:
        socket = await self.run_pane([], node="nat0", intf="nat0-eth0")

        self.assertEqual(socket.sent[0]["type"], "error")
        self.assertEqual(
            socket.sent[0]["message"], "nat0 is the Internet, which is outside the Lab"
        )
        self.assertEqual(socket.closes[0][0], 1008)
        self.assertEqual(self.spawner.commands, [])


if __name__ == "__main__":
    unittest.main()
