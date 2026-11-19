"""Tests for the command behind a Packet Capture and the spawner that runs it.

The command is a pure function of the Node's attachment, the Interface and
the Lab directory, so what tshark is asked to do — which namespace, which
Interface, which format, where the pcap goes and how big it may grow — is
asserted here without a namespace, a Lab or tshark.  The pcap path is the
case that matters most: a peer project names it by Node and Interface
alone, so two Labs each holding an ``h1-eth0`` write to one file.

The spawner is the only code that starts a process; its tests patch the
start, as the Terminal spawner's do, and assert what would have been run.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

from mniplab.constants import LABS_DIR
from mniplab.labhandle import NodeAttachment
from mniplab.webserver import capture_spawner
from mniplab.webserver.capture_spawner import (
    CaptureSpawner,
    RING_FILE_KB,
    RING_FILES,
    build_capture_command,
    ring_files,
)

H1 = NodeAttachment("h1", 1234, "/tmp/mniplab/labs/k1/home/h1")


def lab_dir(key: str) -> str:
    return os.path.join(LABS_DIR, key)


class CaptureCommandTests(unittest.TestCase):
    """What tshark is asked to do, and where."""

    def test_attaches_by_the_namespace_anchor_with_the_shared_recipe(self):
        argv = build_capture_command(H1, "h1-eth0", lab_dir("k1")).argv

        self.assertEqual(argv[:3], ["nsenter", "-t", "1234"])
        for flag in ("-m", "-u", "-n", "-p"):
            self.assertIn(flag, argv[:argv.index("--")])
        self.assertEqual(argv[argv.index("--") + 1], "tshark")

    def test_a_root_namespace_interface_is_captured_without_entering_a_node(self):
        switch = NodeAttachment("s1", 0, lab_dir("k1"))

        command = build_capture_command(
            switch, "s1-eth1", lab_dir("k1"), root_interface="k1-s1-eth1"
        )

        self.assertEqual(command.argv[0], "tshark")
        self.assertNotIn("nsenter", command.argv)
        self.assertEqual(command.argv[command.argv.index("-i") + 1], "k1-s1-eth1")
        # Files carry the plain name and the Node, under this Lab's directory.
        self.assertEqual(
            command.pcap_path,
            os.path.join(lab_dir("k1"), "captures", "s1", "s1-eth1.pcapng"),
        )

    def test_a_root_namespace_interface_name_must_be_a_safe_name(self):
        switch = NodeAttachment("s1", 0, lab_dir("k1"))
        for odd in ("", "../x", "a b", "-eth1"):
            with self.subTest(odd=odd), self.assertRaises(ValueError):
                build_capture_command(switch, "s1-eth1", lab_dir("k1"), root_interface=odd)

    def test_captures_the_named_interface_as_streaming_pdml(self):
        argv = build_capture_command(H1, "h1-eth0", lab_dir("k1")).argv
        tshark = argv[argv.index("--") + 1:]

        self.assertEqual(tshark[tshark.index("-i") + 1], "h1-eth0")
        self.assertEqual(tshark[tshark.index("-T") + 1], "pdml")
        # -l flushes each packet as it is dissected; without it a quiet
        # link shows a packet only when the next one arrives.
        self.assertIn("-l", tshark)
        # -n keeps the dissector off DNS, which a Lab may not have.
        self.assertIn("-n", tshark)

    def test_writes_the_pcap_from_the_same_process(self):
        command = build_capture_command(H1, "h1-eth0", lab_dir("k1"))
        argv = command.argv

        self.assertEqual(argv[argv.index("-w") + 1], command.pcap_path)

    def test_the_pcap_is_a_bounded_ring(self):
        argv = build_capture_command(H1, "h1-eth0", lab_dir("k1")).argv
        ring = [argv[i + 1] for i, word in enumerate(argv) if word == "-b"]

        self.assertCountEqual(ring, [f"filesize:{RING_FILE_KB}", f"files:{RING_FILES}"])

    def test_the_pcap_lives_in_the_lab_directory(self):
        command = build_capture_command(H1, "h1-eth0", lab_dir("k1"))

        self.assertTrue(command.pcap_path.startswith(lab_dir("k1") + os.sep))
        self.assertIn("h1", command.pcap_path)
        self.assertIn("h1-eth0", command.pcap_path)

    def test_two_labs_capturing_the_same_interface_name_never_share_a_pcap(self):
        # The defect this guards against is real: a peer project writes
        # every capture to a fixed name under the shared temporary directory,
        # so parallel Labs overwrite each other's file.
        first = build_capture_command(H1, "h1-eth0", lab_dir("k1"))
        second = build_capture_command(
            NodeAttachment("h1", 5678, "/tmp/mniplab/labs/k2/home/h1"),
            "h1-eth0",
            lab_dir("k2"),
        )

        self.assertNotEqual(first.pcap_path, second.pcap_path)
        self.assertNotEqual(first.log_path, second.log_path)

    def test_a_name_that_could_leave_the_lab_directory_is_refused(self):
        for node, intf in (("h1", "../eth0"), ("../h1", "eth0"), ("h1", "a/b"), ("h1", "")):
            with self.subTest(node=node, intf=intf):
                with self.assertRaises(ValueError):
                    build_capture_command(
                        NodeAttachment(node, 1234, "/tmp/h"), intf, lab_dir("k1")
                    )

    def test_a_container_host_is_refused_for_now(self):
        container = NodeAttachment("c1", 1234, "/tmp/h", container_name="mn.k1.c1")

        with self.assertRaisesRegex(ValueError, "Container Host"):
            build_capture_command(container, "c1-eth0", lab_dir("k1"))


class CaptureFilterTests(unittest.TestCase):
    """A filter is BPF, handed to the kernel at capture time."""

    def tshark(self, capture_filter: str) -> list:
        argv = build_capture_command(H1, "h1-eth0", lab_dir("k1"), capture_filter).argv
        return argv[argv.index("--") + 1:]

    def test_a_filter_is_one_argument_to_f(self):
        tshark = self.tshark("udp port 67 or udp port 68")

        self.assertEqual(tshark[tshark.index("-f") + 1], "udp port 67 or udp port 68")

    def test_no_filter_captures_everything(self):
        self.assertNotIn("-f", self.tshark(""))

    def test_surrounding_whitespace_is_not_part_of_the_filter(self):
        tshark = self.tshark("  arp  ")

        self.assertEqual(tshark[tshark.index("-f") + 1], "arp")

    def test_each_filter_writes_its_own_pcap(self):
        # A Capture is its Interface and its filter: a classmate watching
        # the same Interface unfiltered keeps a file of their own.
        unfiltered = build_capture_command(H1, "h1-eth0", lab_dir("k1"))
        dhcp = build_capture_command(H1, "h1-eth0", lab_dir("k1"), "udp port 67 or udp port 68")
        ospf = build_capture_command(H1, "h1-eth0", lab_dir("k1"), "proto ospf")

        self.assertEqual(len({unfiltered.pcap_path, dhcp.pcap_path, ospf.pcap_path}), 3)
        self.assertEqual(os.path.basename(unfiltered.pcap_path), "h1-eth0.pcapng")
        self.assertEqual(os.path.dirname(dhcp.pcap_path), os.path.dirname(unfiltered.pcap_path))

    def test_the_same_filter_writes_the_same_pcap(self):
        first = build_capture_command(H1, "h1-eth0", lab_dir("k1"), "arp")
        again = build_capture_command(H1, "h1-eth0", lab_dir("k1"), "arp ")

        self.assertEqual(first.pcap_path, again.pcap_path)

    def test_a_filter_with_control_characters_is_refused(self):
        with self.assertRaisesRegex(ValueError, "filter"):
            build_capture_command(H1, "h1-eth0", lab_dir("k1"), "arp\nor icmp")

    def test_an_overlong_filter_is_refused(self):
        with self.assertRaisesRegex(ValueError, "filter"):
            build_capture_command(H1, "h1-eth0", lab_dir("k1"), "arp or " * 100)


class RingFileTests(unittest.TestCase):
    """The files tshark's ring buffer writes, found again for the download."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.command = build_capture_command(H1, "h1-eth0", self.tmp.name)
        self.directory = os.path.dirname(self.command.pcap_path)
        os.makedirs(self.directory)

    def touch(self, name: str) -> str:
        path = os.path.join(self.directory, name)
        with open(path, "wb") as f:
            f.write(b"x")
        return path

    def test_the_ring_is_listed_oldest_first(self):
        newer = self.touch("h1-eth0_00008_20260923014331.pcapng")
        older = self.touch("h1-eth0_00007_20260923014330.pcapng")

        self.assertEqual(ring_files(self.command.pcap_path), [older, newer])

    def test_another_filters_ring_is_not_listed(self):
        mine = self.touch("h1-eth0_00001_20260923014330.pcapng")
        dhcp = build_capture_command(H1, "h1-eth0", self.tmp.name, "arp")
        stem = os.path.splitext(os.path.basename(dhcp.pcap_path))[0]
        self.touch(f"{stem}_00001_20260923014330.pcapng")

        self.assertEqual(ring_files(self.command.pcap_path), [mine])

    def test_nothing_captured_yet_is_an_empty_ring(self):
        self.assertEqual(ring_files(self.command.pcap_path), [])

    def ring_of(self, capture_filter: str) -> str:
        command = build_capture_command(H1, "h1-eth0", self.tmp.name, capture_filter)
        stem = os.path.splitext(os.path.basename(command.pcap_path))[0]
        return self.touch(f"{stem}_00001_20260923014330.pcapng")

    def spawn(self, keep=()) -> None:
        with patch.object(capture_spawner.subprocess, "Popen"):
            CaptureSpawner().spawn(self.command, keep=keep)

    def test_a_new_capture_replaces_the_rings_ended_captures_left(self):
        # Every filter tried writes its own ring; without this, trial and
        # error on one Interface would pile rings up until the Lab stopped.
        self.ring_of("")
        self.ring_of("arp")

        self.spawn()

        self.assertEqual([n for n in os.listdir(self.directory) if n.endswith(".pcapng")], [])

    def test_a_running_captures_ring_is_kept(self):
        running = build_capture_command(H1, "h1-eth0", self.tmp.name, "arp")
        kept = self.ring_of("arp")

        self.spawn(keep=[running.pcap_path])

        self.assertTrue(os.path.exists(kept))

    def test_another_interfaces_ring_is_left_alone(self):
        # h1-eth1 must not be taken for a filtered h1-eth0, nor h1-eth0
        # for a prefix of h1-eth01.
        other = self.touch("h1-eth01_00001_20260923014330.pcapng")

        self.spawn()

        self.assertTrue(os.path.exists(other))


class CaptureSpawnerTests(unittest.TestCase):
    """The spawner starts exactly the command it was given."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.command = build_capture_command(H1, "h1-eth0", self.tmp.name)

    def _spawn(self):
        with patch.object(capture_spawner.subprocess, "Popen") as popen:
            CaptureSpawner().spawn(self.command)
        return popen

    def test_runs_the_built_argv(self):
        popen = self._spawn()

        self.assertEqual(popen.call_args.args[0], self.command.argv)

    def test_packets_arrive_on_a_pipe(self):
        popen = self._spawn()

        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.PIPE)

    def test_runs_in_its_own_process_group(self):
        # nsenter forks into the PID namespace and tshark runs dumpcap, so
        # stopping a Capture signals the group rather than one pid.
        popen = self._spawn()

        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_creates_the_directory_the_pcap_and_log_go_in(self):
        self._spawn()

        self.assertTrue(os.path.isdir(os.path.dirname(self.command.pcap_path)))
        self.assertTrue(os.path.exists(self.command.log_path))


class FakeGroup:
    """A process group as ``os.killpg`` sees it: signal 0 probes, others kill.

    ``lingers`` is how many probes still find a member after the leader —
    nsenter — has gone: tshark flushing its pcap outlives the nsenter that
    started it.  ``ignores`` names signals the group survives.
    """

    def __init__(self, lingers: int = 0, ignores=()) -> None:
        self.lingers = lingers
        self.ignores = set(ignores)
        self.sent: list = []
        self.gone = False

    def killpg(self, pid: int, sig: int) -> None:
        if sig == 0:
            if self.gone or self.lingers <= 0:
                raise ProcessLookupError
            self.lingers -= 1
            return
        if self.gone:
            raise ProcessLookupError
        self.sent.append(sig)
        if sig not in self.ignores:
            self.lingers = min(self.lingers, 3)


class CaptureStopTests(unittest.TestCase):
    """Stopping signals the whole group, and returns only once all of it is gone."""

    def stop(self, group: FakeGroup, process=None) -> unittest.mock.Mock:
        process = process or unittest.mock.Mock(pid=4242)
        with patch.object(capture_spawner.os, "killpg", side_effect=group.killpg), \
                patch.object(capture_spawner.time, "sleep"):
            CaptureSpawner().stop(process)
        return process

    def test_a_group_that_exits_on_sigterm_is_not_killed(self):
        group = FakeGroup()

        self.stop(group)

        self.assertEqual(group.sent, [capture_spawner.signal.SIGTERM])

    def test_it_waits_for_tshark_to_go_after_nsenter_has(self):
        # nsenter dies on SIGTERM at once; tshark is still writing the pcap.
        group = FakeGroup(lingers=3)

        self.stop(group)

        self.assertEqual(group.lingers, 0)
        self.assertEqual(group.sent, [capture_spawner.signal.SIGTERM])

    def test_a_group_that_ignores_sigterm_is_killed(self):
        group = FakeGroup(lingers=10 ** 6, ignores={capture_spawner.signal.SIGTERM})

        with patch.object(capture_spawner.time, "monotonic",
                          side_effect=(i * 0.5 for i in range(10 ** 6))):
            self.stop(group)

        self.assertEqual(
            group.sent, [capture_spawner.signal.SIGTERM, capture_spawner.signal.SIGKILL]
        )
        self.assertEqual(group.lingers, 0)

    def test_a_leader_that_outlives_sigterm_is_killed(self):
        group = FakeGroup()
        process = unittest.mock.Mock(pid=4242)
        process.wait.side_effect = [subprocess.TimeoutExpired("tshark", 2), 0]

        self.stop(group, process)

        self.assertEqual(
            group.sent, [capture_spawner.signal.SIGTERM, capture_spawner.signal.SIGKILL]
        )

    def test_a_group_already_gone_is_left_alone(self):
        group = FakeGroup()
        group.gone = True

        process = self.stop(group)

        process.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
