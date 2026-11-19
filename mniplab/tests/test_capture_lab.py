"""Integration tests for Packet Capture against real Labs and a real tshark.

The unit tiers prove the parts; these prove the whole on the host: tshark
entered into a Node's namespace by the Namespace Anchor — by its PID, the same
for every Node that is not a container — the pcap written beside the feed
from the same process holding what the feed showed, two Labs with the same
names kept apart, and no capture process left behind once a Lab's Captures
are stopped.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import unittest
from typing import Any, List

import pytest

from mniplab import mnIPLab
from mniplab.labhandle import LocalLabHandle, NodeAttachment
from mniplab.tests.labs import LabCase, collect, quiet_for
from mniplab.webserver.capture import CaptureService
from mniplab.webserver.capture_spawner import ring_files

# How long a Capture is given to see what the test waits for.
WAIT = 20.0


def protocol_is(name: str) -> Any:
    return lambda packet: packet["summary"]["protocol"] == name


def captures_running(lab_dir: str) -> List[str]:
    """Capture processes still writing under a Lab directory."""
    out = subprocess.run(
        ["pgrep", "-af", "--", f"-w {lab_dir}/captures/"], capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if "pgrep" not in line]


def read_pcap(pcap_base: str, display_filter: str) -> List[str]:
    """The source addresses of the packets matching a display filter, read as
    a Learner's download is: the ring's files one after another, as one file."""
    joined = pcap_base + ".download"
    with open(joined, "wb") as out:
        for path in ring_files(pcap_base):
            with open(path, "rb") as section:
                out.write(section.read())
    try:
        return subprocess.run(
            ["tshark", "-r", joined, "-Y", display_filter, "-T", "fields", "-e", "ip.src"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
    finally:
        os.remove(joined)


class TwoLabCaptureTests(LabCase):
    """Two Labs with the same names: each Capture is its own Lab's, on disk and on the wire."""

    def two_host_lab(self, key: str) -> Any:
        net = mnIPLab(topo=None, controller=None, lab_key=key)
        h1 = net.addHost("h1")
        h2 = net.addHost("h2")
        net.addLink(h1, h2, params1={"ip": "10.9.0.1/24"}, params2={"ip": "10.9.0.2/24"})
        return self.start(net)

    def test_identically_named_interfaces_write_apart_and_see_only_their_own_lab(self) -> None:
        first = self.two_host_lab("capa")
        second = self.two_host_lab("capb")

        async def scenario() -> Any:
            service = CaptureService(grace_seconds=1)
            try:
                watching = []
                for net in (first, second):
                    handle = LocalLabHandle(net)
                    watching.append(await service.subscribe(
                        net.lab_key, net.lab_dir, handle.node_attachment("h1"), "h1-eth0",
                        capture_filter="icmp",
                    ))
                # Let both tsharks come up before the only traffic is sent.
                await asyncio.sleep(2)
                await asyncio.get_running_loop().run_in_executor(
                    None, first.get("h1").cmd, "ping -c 3 -i 0.2 10.9.0.2"
                )
                mine = await collect(watching[0], 6, protocol_is("ICMP"))
                theirs = await quiet_for(watching[1], 2)
                pcaps = [c.command.pcap_path for c in service._captures.values()]
                # Read while both still run, as a Learner downloads: the
                # ring's last file is still being written.
                downloads = {pcap: read_pcap(pcap, "icmp") for pcap in pcaps}
                running = [captures_running(net.lab_dir) for net in (first, second)]
                await service.stop_lab_captures(first.lab_key, "Lab stopped")
                stopped = [captures_running(net.lab_dir) for net in (first, second)]
                return mine, theirs, pcaps, downloads, running, stopped
            finally:
                await service.stop_all("test over")

        mine, theirs, pcaps, downloads, running, stopped = asyncio.run(scenario())

        # The filter was the kernel's: the pings' ARP never reached the feed.
        self.assertGreaterEqual(len(mine), 6)
        self.assertEqual({p["summary"]["protocol"] for p in mine}, {"ICMP"})
        self.assertEqual(theirs, [])
        self.assertEqual(len(set(pcaps)), 2)
        first_pcap = next(p for p in pcaps if p.startswith(first.lab_dir + os.sep))
        second_pcap = next(p for p in pcaps if p.startswith(second.lab_dir + os.sep))
        self.assertGreaterEqual(len(downloads[first_pcap]), 5)
        self.assertEqual(downloads[second_pcap], [])
        # Stopping one Lab's Captures leaves none of its processes behind,
        # and the other Lab's Capture running.
        self.assertTrue(all(running), running)
        self.assertEqual(stopped[0], [])
        self.assertTrue(stopped[1])

    def test_an_invalid_filter_ends_the_capture_with_tsharks_reason(self) -> None:
        net = self.two_host_lab("capc")

        async def scenario() -> Any:
            service = CaptureService(grace_seconds=1)
            try:
                viewer = await service.subscribe(
                    net.lab_key, net.lab_dir, LocalLabHandle(net).node_attachment("h1"),
                    "h1-eth0", capture_filter="udp prt 5",
                )
                deadline = time.monotonic() + WAIT
                while time.monotonic() < deadline:
                    for message in await asyncio.wait_for(viewer.get(), timeout=WAIT):
                        if message["type"] == "ended":
                            return message
                return None
            finally:
                await service.stop_all("test over")

        ended = asyncio.run(scenario())

        self.assertIsNotNone(ended)
        self.assertTrue(ended.get("filter_error"))
        self.assertIn('"udp prt 5"', ended["reason"])
        self.assertIn("syntax error", ended["reason"])


@pytest.mark.requires_ovs
class SwitchCaptureLabTests(LabCase):
    """A Switch's port is captured in the root namespace, and stops clean."""

    def switch_lab(self, key: str) -> Any:
        net = mnIPLab(topo=None, controller=None, lab_key=key)
        s1 = net.addSwitch("s1")
        h1 = net.addHost("h1")
        h2 = net.addHost("h2")
        net.addLink(h1, s1, params1={"ip": "10.9.0.1/24"})
        net.addLink(h2, s1, params1={"ip": "10.9.0.2/24"})
        return self.start(net)

    def test_a_port_shows_the_labs_own_traffic_and_leaves_no_tshark_behind(self) -> None:
        net = self.switch_lab("swca")
        other = self.switch_lab("swcb")
        handle = LocalLabHandle(net)
        ports = {p["name"]: p["interface"] for p in handle.switch_ports("s1")}
        self.assertEqual(ports, {"s1-eth1": "swca-s1-eth1", "s1-eth2": "swca-s1-eth2"})

        async def scenario() -> Any:
            service = CaptureService(grace_seconds=1)
            try:
                attachment = NodeAttachment("s1", 0, net.lab_dir)
                mine = await service.subscribe(
                    net.lab_key, net.lab_dir, attachment, "s1-eth1",
                    capture_filter="icmp", root_interface=ports["s1-eth1"],
                )
                await asyncio.sleep(2)
                await asyncio.get_running_loop().run_in_executor(
                    None, other.get("h1").cmd, "ping -c 2 -i 0.2 10.9.0.2"
                )
                quiet = await quiet_for(mine, 2)
                await asyncio.get_running_loop().run_in_executor(
                    None, net.get("h1").cmd, "ping -c 3 -i 0.2 10.9.0.2"
                )
                seen = await collect(mine, 6, protocol_is("ICMP"))
                running = captures_running(net.lab_dir)
                await service.stop_lab_captures(net.lab_key, "Lab stopped")
                return quiet, seen, running, captures_running(net.lab_dir)
            finally:
                await service.stop_all("test over")

        quiet, seen, running, stopped = asyncio.run(scenario())

        self.assertEqual(quiet, [])
        self.assertGreaterEqual(len(seen), 6)
        self.assertTrue(running)
        self.assertEqual(stopped, [])
        leftovers = subprocess.run(
            ["pgrep", "-x", "dumpcap"], capture_output=True, text=True
        ).stdout.split()
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
