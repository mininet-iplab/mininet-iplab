"""Tests for a Network Tools ping or traceroute streamed over its WebSocket.

The command is built from the Lab's two short reads and run outside the
Lab, entering the source Node by its Namespace Anchor pid.  The handler is
what the panel holds: it forwards each line as it is printed, stops the
tool as Ctrl+C would on ``stop``, and kills it when the page goes.  The
handler tests run a real local process in place of the Node's tool, so the
signals they send are real.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from fastapi import WebSocketDisconnect

from mniplab.labhandle import NodeAttachment
from mniplab.tests.fakes import FAKE_LAB_KEY, FrozenSessionClockMixin
from mniplab.tests.test_web_capture import two_node_lab
from mniplab.webserver import websocket as ws_module
from mniplab.webserver.auth import create_session
from mniplab.webserver.network_tool import build_bandwidth_argv, build_tool_argv
from mniplab.webserver.session import SessionRegistry
from mniplab.webserver.websocket import NetworkToolWebSocketHandler

H1 = NodeAttachment(node="h1", pid=4102, home_dir="/tmp/h1")
R1 = NodeAttachment(node="r1", pid=4101, home_dir="/tmp/r1")

# Stands in for ping: one line at once, then one a second; on SIGINT it
# prints a summary and exits, as ping does.
FAKE_PING = """
import signal, sys, time
def summary(*_):
    print("--- summary ---", flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, summary)
print("PING start", flush=True)
for seq in range(100):
    print(f"reply {seq}", flush=True)
    time.sleep(1)
"""


class BuildToolArgvTests(unittest.TestCase):
    """What a test is asked to run, inside the source Node."""

    def tool(self, argv: List[str]) -> List[str]:
        return argv[argv.index("--") + 1:]

    def test_a_ping_enters_the_source_node_and_sends_the_count_asked_for(self) -> None:
        argv = build_tool_argv(H1, "ping", "10.0.0.2", count=7)

        self.assertEqual(argv[:3], ["nsenter", "-t", "4102"])
        self.assertEqual(self.tool(argv), ["ping", "-c", "7", "10.0.0.2"])

    def test_an_ipv6_ping_asks_ping_for_ipv6(self) -> None:
        argv = build_tool_argv(H1, "ping", "2001:db8::2", ipv6=True)

        self.assertEqual(self.tool(argv), ["ping", "-6", "-c", "4", "2001:db8::2"])

    def test_a_traceroute_is_numeric_unless_hops_are_resolved(self) -> None:
        for ipv6, resolve, expected in (
            (False, False, ["traceroute", "-n", "10.0.0.2"]),
            (False, True, ["traceroute", "10.0.0.2"]),
            (True, False, ["traceroute6", "-n", "10.0.0.2"]),
            (True, True, ["traceroute6", "10.0.0.2"]),
        ):
            with self.subTest(ipv6=ipv6, resolve=resolve):
                argv = build_tool_argv(H1, "traceroute", "10.0.0.2", ipv6=ipv6, resolve=resolve)
                self.assertEqual(self.tool(argv), expected)

    def test_a_target_that_is_not_one_plain_address_or_name_is_refused(self) -> None:
        for target in ("-f", "--help", "x; reboot", "a b", "", "x" * 254):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    build_tool_argv(H1, "ping", target)

    def test_a_count_outside_one_to_a_hundred_is_refused(self) -> None:
        for count in (0, 101):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    build_tool_argv(H1, "ping", "10.0.0.2", count=count)

    def test_an_unknown_tool_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_tool_argv(H1, "iperf", "10.0.0.2")


class RegistryToolArgvTests(unittest.IsolatedAsyncioTestCase):
    """How the registry turns a panel's request into a command."""

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.lab = two_node_lab()
        self.registry.lab_service.lab = self.lab

    async def test_a_node_name_becomes_its_default_interfaces_live_address(self) -> None:
        self.lab.get("r1").defaultIntf().updateIP = lambda: "10.0.0.1"

        argv = await self.registry.network_tool_argv(FAKE_LAB_KEY, "ping", "h1", "r1", count=2)

        self.assertEqual(argv[:3], ["nsenter", "-t", "4102"])
        self.assertEqual(argv[-3:], ["-c", "2", "10.0.0.1"])

    async def test_an_internet_gateway_source_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.registry.network_tool_argv(FAKE_LAB_KEY, "ping", "nat0", "10.0.0.1")

    async def test_a_lab_that_is_not_running_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.registry.network_tool_argv("nope", "ping", "h1", "10.0.0.1")


# What a page sends when its first message is not JSON at all.
NOT_JSON = "not json"


class FakeToolSocket:
    """The panel's socket: sends its inbox at the times given, records what it is sent."""

    def __init__(self, session: str, inbox: List[Any]) -> None:
        self.cookies = {"session": session}
        self.sent: List[Any] = []
        self.closes: List[Any] = []
        # Each entry is (seconds to wait first, message); None drops the connection.
        self._inbox = list(inbox)

    async def accept(self) -> None:
        pass

    async def receive_json(self) -> Any:
        if not self._inbox:
            await asyncio.Event().wait()  # an open page that says nothing more
        delay, message = self._inbox.pop(0)
        await asyncio.sleep(delay)
        if message is None:
            raise WebSocketDisconnect(code=1001)
        if message == NOT_JSON:
            raise json.JSONDecodeError("Expecting value", "pin", 0)
        return message

    async def send_json(self, payload: Any) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closes.append((code, reason))

    def output(self) -> str:
        return "".join(m["data"] for m in self.sent if m["type"] == "output")


class NetworkToolHandlerTests(FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase):
    """What the panel receives, and what Stop and leaving do to the tool."""

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.registry.lab_service.lab = two_node_lab()
        self.started: List[asyncio.subprocess.Process] = []
        real_start = ws_module.start_tool

        async def start_fake_ping(argv: List[str]) -> asyncio.subprocess.Process:
            self.argv = argv
            process = await real_start([sys.executable, "-c", FAKE_PING])
            self.started.append(process)
            return process

        for target, value in (("registry", self.registry), ("start_tool", start_fake_ping)):
            patcher = patch(f"mniplab.webserver.websocket.{target}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = create_session("learner", role="learner")
        self.start = (0, {"type": "start", "tool": "ping", "source": "h1",
                          "destination": "10.0.0.2", "count": 100})

    async def run_panel(self, inbox: List[Any], session: Optional[str] = None) -> FakeToolSocket:
        socket = FakeToolSocket(session or self.session, inbox)
        await asyncio.wait_for(
            NetworkToolWebSocketHandler(socket, FAKE_LAB_KEY).run(),  # type: ignore[arg-type]
            timeout=10,
        )
        return socket

    def assert_gone(self, process: asyncio.subprocess.Process) -> None:
        self.assertIsNotNone(process.returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)

    async def test_stop_interrupts_the_tool_and_its_summary_still_arrives(self) -> None:
        socket = await self.run_panel([self.start, (1.5, {"type": "stop"})])

        self.assertEqual(socket.sent[0], {"type": "started"})
        self.assertIn("reply 1\n", socket.output())
        self.assertTrue(socket.output().endswith("--- summary ---\n"))
        self.assertEqual(socket.sent[-1], {"type": "exit", "code": 0, "stopped": True})
        self.assertEqual(socket.closes, [(1000, "")])
        self.assert_gone(self.started[0])

    async def test_lines_arrive_as_they_are_printed_not_at_the_end(self) -> None:
        socket = FakeToolSocket(self.session, [self.start])
        run = asyncio.ensure_future(
            NetworkToolWebSocketHandler(socket, FAKE_LAB_KEY).run()  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.8)
        try:
            self.assertIn("reply 0\n", socket.output())
            self.assertNotIn("exit", [m["type"] for m in socket.sent])
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        self.assert_gone(self.started[0])

    async def test_a_dropped_connection_kills_the_tool(self) -> None:
        await self.run_panel([self.start, (0.5, None)])

        self.assert_gone(self.started[0])

    async def test_the_command_runs_in_the_source_node(self) -> None:
        await self.run_panel([self.start, (0.2, None)])

        self.assertEqual(self.argv[:3], ["nsenter", "-t", "4102"])

    async def test_a_request_that_cannot_run_is_reported_and_nothing_starts(self) -> None:
        for message in (
            {"type": "start", "tool": "ping", "source": "h1", "destination": "x; reboot"},
            {"type": "start", "tool": "ping", "source": "h1", "destination": ""},
            {"type": "start", "tool": "ping", "source": "h1", "destination": "10.0.0.2",
             "count": "4"},
            {"type": "start", "tool": "ping", "source": "nat0", "destination": "10.0.0.2"},
            {"type": "stop"},
        ):
            with self.subTest(message=message):
                socket = await self.run_panel([(0, message)])

                self.assertEqual(socket.sent[0]["type"], "error")
                self.assertEqual(socket.closes[0][0], 1008)
        self.assertEqual(self.started, [])

    async def test_a_third_run_at_once_for_one_login_is_refused(self) -> None:
        ws_module._tool_runs[self.session] = ws_module.MAX_TOOL_RUNS_PER_SESSION
        self.addCleanup(ws_module._tool_runs.pop, self.session, None)

        socket = await self.run_panel([self.start])

        self.assertIn("stop one first", socket.sent[0]["message"])
        self.assertEqual(self.started, [])

    async def test_connections_opened_together_cannot_all_pass_the_cap(self) -> None:
        with patch.object(ws_module, "MAX_TOOL_RUNS_PER_SESSION", 1):
            sockets = await asyncio.gather(
                self.run_panel([(0.3, self.start[1]), (0.2, None)]),
                self.run_panel([(0.3, self.start[1]), (0.2, None)]),
            )

        self.assertEqual(len(self.started), 1)
        refused = [s for s in sockets if s.sent and s.sent[0]["type"] == "error"]
        self.assertEqual(len(refused), 1)
        self.assertIn("stop one first", refused[0].sent[0]["message"])

    async def test_a_tool_that_cannot_start_is_reported_with_why(self) -> None:
        async def nsenter_missing(argv: List[str]) -> Any:
            raise FileNotFoundError(2, "No such file or directory", "nsenter")

        with patch.object(ws_module, "start_tool", nsenter_missing):
            socket = await self.run_panel([self.start])

        self.assertEqual(socket.sent, [{"type": "error",
                                        "message": "Could not start ping: No such file or directory"}])
        self.assertEqual(socket.closes[0][0], 1008)
        self.assertNotIn(self.session, ws_module._tool_runs)

    async def test_a_first_message_that_is_not_json_is_refused_plainly(self) -> None:
        socket = await self.run_panel([(0, NOT_JSON)])

        self.assertEqual(socket.sent, [{"type": "error", "message": "Expected a start message"}])
        self.assertEqual(self.started, [])

    async def test_a_start_that_names_no_tool_is_refused_as_naming_none(self) -> None:
        socket = await self.run_panel(
            [(0, {"type": "start", "source": "h1", "destination": "10.0.0.2"})])

        self.assertEqual(socket.sent, [
            {"type": "error", "message": "Unknown Network Tools test: None"}])
        self.assertEqual(self.started, [])

    async def test_a_finished_run_no_longer_counts_against_the_login(self) -> None:
        await self.run_panel([self.start, (0.2, None)])

        self.assertNotIn(self.session, ws_module._tool_runs)

    async def test_a_page_that_is_not_signed_in_is_closed(self) -> None:
        socket = await self.run_panel([self.start], session="nobody")

        self.assertEqual(socket.sent, [{"type": "error", "message": "Not signed in; sign in again"}])
        self.assertEqual(socket.closes[0][0], 1008)
        self.assertEqual(self.started, [])


# Stands in for iperf3's server: it serves until it is killed.
FAKE_IPERF_SERVER = """
import time
time.sleep(600)
"""

# Stands in for iperf3's client: a line a second; on SIGINT it prints its
# summary and exits, as iperf3 does.
FAKE_IPERF_CLIENT = """
import signal, sys, time
def summary(*_):
    print("--- summary ---", flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, summary)
for interval in range(100):
    print(f"interval {interval}", flush=True)
    time.sleep(1)
"""

# As FAKE_IPERF_CLIENT, but its summary on SIGINT is the JSON iperf3 -J prints.
FAKE_IPERF_JSON_CLIENT = """
import json, signal, sys, time
def summary(*_):
    print(json.dumps({"end": {"sum_sent": {"bits_per_second": 941000000.0}}}), flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, summary)
for interval in range(100):
    print(f"interval {interval}", flush=True)
    time.sleep(1)
"""


class BuildBandwidthArgvTests(unittest.TestCase):
    """What a bandwidth test is asked to run, in its server Node and its client Node."""

    def command(self, argv: List[str]) -> List[str]:
        return argv[argv.index("--") + 1:]

    def test_the_server_enters_its_node_and_serves_one_client(self) -> None:
        server, _ = build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2")

        self.assertEqual(server[:3], ["nsenter", "-t", "4102"])
        self.assertEqual(self.command(server), ["iperf3", "-s", "-1"])

    def test_the_client_enters_its_node_and_connects_for_the_length_asked_for(self) -> None:
        _, client = build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2", seconds=7)

        self.assertEqual(client[:3], ["nsenter", "-t", "4101"])
        self.assertEqual(self.command(client),
                         ["iperf3", "-c", "10.0.0.2", "-t", "7", "--forceflush"])

    def test_udp_adds_the_udp_flag(self) -> None:
        _, client = build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2", protocol="udp")

        self.assertEqual(self.command(client),
                         ["iperf3", "-c", "10.0.0.2", "-t", "10", "-u", "--forceflush"])

    def test_json_output_asks_for_json_instead_of_flushed_lines(self) -> None:
        _, client = build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2", json_output=True)

        self.assertEqual(self.command(client), ["iperf3", "-c", "10.0.0.2", "-t", "10", "-J"])

    def test_a_length_outside_one_to_sixty_seconds_is_refused(self) -> None:
        for seconds in (0, 61):
            with self.subTest(seconds=seconds):
                with self.assertRaisesRegex(ValueError, "between 1 and 60 seconds"):
                    build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2", seconds=seconds)

    def test_a_protocol_other_than_tcp_or_udp_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2", protocol="sctp")

    def test_an_address_that_is_not_one_plain_address_or_name_is_refused(self) -> None:
        for address in ("-x", "", "10.0.0.2; reboot"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    build_bandwidth_argv(H1, R1, "iperf3", address)

    def test_a_tool_that_is_not_a_bandwidth_test_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_bandwidth_argv(H1, R1, "ping", "10.0.0.2")

    def test_iperf3_with_a_bandwidth_adds_its_b_flag_to_the_client_for_tcp_and_udp(self) -> None:
        for protocol, flags in (("tcp", []), ("udp", ["-u"])):
            with self.subTest(protocol=protocol):
                _, client = build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2",
                                                 protocol=protocol, bandwidth="1.5M")

                self.assertEqual(self.command(client),
                                 ["iperf3", "-c", "10.0.0.2", "-t", "10", *flags,
                                  "--forceflush", "-b", "1.5M"])

    def test_a_bandwidth_that_is_not_a_number_with_k_m_or_g_is_refused(self) -> None:
        refused = ("10X", "10m", "M", "-5M", "0M", "1.1G", "2G", "10 M", "10M;x", "10M\n")
        for protocol in ("tcp", "udp"):
            for bandwidth in refused:
                with self.subTest(protocol=protocol, bandwidth=bandwidth):
                    with self.assertRaisesRegex(
                            ValueError,
                            "Bandwidth must be a number with K, M or G, up to 1G, such as 10M"):
                        build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2",
                                             protocol=protocol, bandwidth=bandwidth)

    def test_the_plain_iperf_is_no_longer_a_bandwidth_test(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown Network Tools test"):
            build_bandwidth_argv(H1, R1, "iperf", "10.0.0.2")

    def test_a_bandwidth_of_k_m_or_g_up_to_1g_is_passed_to_the_iperf3_client(self) -> None:
        for bandwidth in ("500K", "1.5M", "1G", "1000M"):
            with self.subTest(bandwidth=bandwidth):
                _, client = build_bandwidth_argv(H1, R1, "iperf3", "10.0.0.2",
                                                 bandwidth=bandwidth)

                self.assertEqual(self.command(client)[-2:], ["-b", bandwidth])


class BandwidthTestArgvTests(unittest.IsolatedAsyncioTestCase):
    """How the registry turns a panel's bandwidth request into two commands."""

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.lab = two_node_lab()
        self.registry.lab_service.lab = self.lab
        self.lab.get("h1").defaultIntf().ip = "10.0.0.2"

    async def test_the_client_connects_to_the_servers_first_address(self) -> None:
        server, client = await self.registry.bandwidth_test_argv(
            FAKE_LAB_KEY, "iperf3", "h1", "r1", seconds=5)

        self.assertEqual(server[:3], ["nsenter", "-t", "4102"])
        self.assertEqual(client[:3], ["nsenter", "-t", "4101"])
        self.assertEqual(client[-5:], ["-c", "10.0.0.2", "-t", "5", "--forceflush"])

    async def test_the_bandwidth_reaches_the_client_command(self) -> None:
        _, tcp_client = await self.registry.bandwidth_test_argv(
            FAKE_LAB_KEY, "iperf3", "h1", "r1", bandwidth="2M")
        _, udp_client = await self.registry.bandwidth_test_argv(
            FAKE_LAB_KEY, "iperf3", "h1", "r1", protocol="udp", bandwidth="2M")

        self.assertEqual(tcp_client[-2:], ["-b", "2M"])
        self.assertEqual(udp_client[-2:], ["-b", "2M"])

    async def test_a_server_with_no_address_is_refused(self) -> None:
        self.lab.get("h1").defaultIntf().ip = None

        with self.assertRaisesRegex(ValueError, "Server 'h1' has no IP address"):
            await self.registry.bandwidth_test_argv(FAKE_LAB_KEY, "iperf3", "h1", "r1")

    async def test_a_node_in_the_host_machines_network_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.registry.bandwidth_test_argv(FAKE_LAB_KEY, "iperf3", "nat0", "r1")
        with self.assertRaises(ValueError):
            await self.registry.bandwidth_test_argv(FAKE_LAB_KEY, "iperf3", "h1", "nat0")

    async def test_a_lab_that_is_not_running_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.registry.bandwidth_test_argv("nope", "iperf3", "h1", "r1")


class BandwidthHandlerTests(FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase):
    """What the panel receives for a bandwidth test, and what ends its processes."""

    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.lab = two_node_lab()
        self.registry.lab_service.lab = self.lab
        self.lab.get("h1").defaultIntf().ip = "10.0.0.2"
        # Each call to start_tool, as (argv, quiet); each process, server first.
        self.calls: List[Any] = []
        self.started: List[asyncio.subprocess.Process] = []
        self.real_start = ws_module.start_tool
        for target, value in (
            ("registry", self.registry),
            ("start_tool", self.start_fake_iperf),
            ("SERVER_START_DELAY", 0),
        ):
            patcher = patch(f"mniplab.webserver.websocket.{target}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = create_session("learner", role="learner")

    async def start_fake_iperf(
        self, argv: List[str], *, quiet: bool = False
    ) -> asyncio.subprocess.Process:
        """Start a fake iperf3 in place of the real one, choosing it by its command."""
        self.calls.append((argv, quiet))
        if "-s" in argv:
            script = FAKE_IPERF_SERVER
        elif "-J" in argv:
            script = FAKE_IPERF_JSON_CLIENT
        else:
            script = FAKE_IPERF_CLIENT
        process = await self.real_start([sys.executable, "-c", script], quiet=quiet)
        self.started.append(process)
        return process

    async def start_server_only(
        self, argv: List[str], *, quiet: bool = False
    ) -> asyncio.subprocess.Process:
        """Start the server, and fail to start the client as a missing nsenter would."""
        if "-s" not in argv:
            raise FileNotFoundError(2, "No such file or directory", "nsenter")
        return await self.start_fake_iperf(argv, quiet=quiet)

    @staticmethod
    def request(**fields: Any) -> Dict[str, Any]:
        """An iperf3 start message from h1 (server) to r1 (client), with any fields changed."""
        return {"type": "start", "tool": "iperf3", "server": "h1", "client": "r1", **fields}

    async def run_panel(self, inbox: List[Any]) -> FakeToolSocket:
        socket = FakeToolSocket(self.session, inbox)
        await asyncio.wait_for(
            NetworkToolWebSocketHandler(socket, FAKE_LAB_KEY).run(),  # type: ignore[arg-type]
            timeout=10,
        )
        return socket

    def assert_gone(self, process: asyncio.subprocess.Process) -> None:
        self.assertIsNotNone(process.returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)

    async def test_the_server_starts_quietly_and_first_and_the_client_second(self) -> None:
        await self.run_panel([(0, self.request()), (0.3, None)])

        (server_argv, server_quiet), (client_argv, client_quiet) = self.calls
        self.assertIn("-s", server_argv)
        self.assertTrue(server_quiet)
        self.assertIn("-c", client_argv)
        self.assertEqual(client_argv[-5:], ["-c", "10.0.0.2", "-t", "10", "--forceflush"])
        self.assertFalse(client_quiet)

    async def test_lines_arrive_as_printed_and_stop_interrupts_the_client(self) -> None:
        socket = await self.run_panel([(0, self.request()), (1.5, {"type": "stop"})])

        self.assertEqual(socket.sent[0], {"type": "started"})
        self.assertIn("interval 0\n", socket.output())
        self.assertTrue(socket.output().endswith("--- summary ---\n"))
        self.assertEqual(socket.sent[-1], {"type": "exit", "code": 0, "stopped": True})
        self.assertEqual(socket.closes, [(1000, "")])
        self.assert_gone(self.started[0])
        self.assert_gone(self.started[1])

    async def test_lines_arrive_as_they_are_printed_not_at_the_end(self) -> None:
        socket = FakeToolSocket(self.session, [(0, self.request())])
        run = asyncio.ensure_future(
            NetworkToolWebSocketHandler(socket, FAKE_LAB_KEY).run()  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.8)
        try:
            self.assertIn("interval 0\n", socket.output())
            self.assertNotIn("exit", [m["type"] for m in socket.sent])
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        self.assert_gone(self.started[0])
        self.assert_gone(self.started[1])

    async def test_a_dropped_connection_ends_both_processes(self) -> None:
        await self.run_panel([(0, self.request()), (0.5, None)])

        self.assert_gone(self.started[0])
        self.assert_gone(self.started[1])

    async def test_a_second_test_on_the_same_server_is_refused_until_the_first_ends(self) -> None:
        first = asyncio.ensure_future(
            self.run_panel([(0, self.request()), (1.5, {"type": "stop"})])
        )
        await asyncio.sleep(0.5)

        refused = await self.run_panel([(0, self.request())])

        self.assertEqual(refused.sent, [{
            "type": "error",
            "message": "An iperf3 test is already running on h1; try again when it finishes",
        }])
        self.assertEqual(len(self.calls), 2)
        await first

        accepted = await self.run_panel([(0, self.request()), (0.3, None)])

        self.assertEqual(accepted.sent[0], {"type": "started"})
        self.assertEqual(len(self.calls), 4)

    async def test_with_json_output_one_summary_arrives_at_the_end_and_no_lines(self) -> None:
        socket = await self.run_panel(
            [(0, self.request(json_output=True)), (1.5, {"type": "stop"})]
        )

        outputs = [m["data"] for m in socket.sent if m["type"] == "output"]
        self.assertEqual(len(outputs), 1)
        self.assertIn("*** Bandwidth (sent): 941.00 Mbps", outputs[0])
        self.assertNotIn("interval", outputs[0])
        self.assertEqual(socket.sent[-1], {"type": "exit", "code": 0, "stopped": True})

    async def test_a_start_without_a_server_or_client_or_a_whole_duration_is_refused(self) -> None:
        for message, reason in (
            ({"type": "start", "tool": "iperf3", "client": "r1"}, "Select server and client"),
            ({"type": "start", "tool": "iperf3", "server": "h1"}, "Select server and client"),
            (self.request(client=""), "Select server and client"),
            (self.request(seconds="10"), "Duration must be a whole number"),
            (self.request(seconds=True), "Duration must be a whole number"),
            (self.request(seconds=61), "Duration must be between 1 and 60 seconds"),
        ):
            with self.subTest(message=message):
                socket = await self.run_panel([(0, message)])

                self.assertEqual(socket.sent, [{"type": "error", "message": reason}])
                self.assertEqual(socket.closes[0][0], 1008)
        self.assertEqual(self.calls, [])

    async def test_a_client_that_cannot_start_is_reported_and_the_server_is_ended(self) -> None:
        with patch.object(ws_module, "start_tool", self.start_server_only):
            socket = await self.run_panel([(0, self.request())])

        self.assertEqual(socket.sent, [{
            "type": "error",
            "message": "Could not start iperf3: No such file or directory",
        }])
        self.assertEqual(socket.closes[0][0], 1008)
        self.assert_gone(self.started[0])

    async def test_a_bandwidth_reaches_the_client_command(self) -> None:
        await self.run_panel([(0, self.request(bandwidth="2M")), (0.3, None)])
        self.assertEqual(self.calls[1][0][-2:], ["-b", "2M"])

    async def test_a_start_naming_the_plain_iperf_is_refused_and_starts_nothing(self) -> None:
        socket = await self.run_panel([(0, self.request(tool="iperf"))])

        self.assertEqual(socket.sent, [{
            "type": "error",
            "message": "Unknown Network Tools test: 'iperf'",
        }])
        self.assertEqual(socket.closes[0][0], 1008)
        self.assertEqual(self.calls, [])

    async def test_an_empty_or_missing_bandwidth_means_none(self) -> None:
        for fields in ({"bandwidth": ""}, {"bandwidth": None}, {}):
            with self.subTest(fields=fields):
                await self.run_panel([(0, self.request(**fields)), (0.3, None)])

                self.assertNotIn("-b", self.calls[-1][0])

    async def test_a_bandwidth_that_is_not_text_is_refused(self) -> None:
        for bandwidth in (10, ["10M"], True):
            with self.subTest(bandwidth=bandwidth):
                socket = await self.run_panel([(0, self.request(bandwidth=bandwidth))])

                self.assertEqual(socket.sent, [{
                    "type": "error",
                    "message": "Bandwidth must be a number with K, M or G, up to 1G, such as 10M",
                }])
                self.assertEqual(socket.closes[0][0], 1008)
        self.assertEqual(self.calls, [])

    async def test_a_bandwidth_that_is_not_k_m_or_g_up_to_1g_is_refused_before_anything_starts(
            self) -> None:
        socket = await self.run_panel([
            (0, self.request(protocol="udp", bandwidth="10X")),
        ])

        self.assertEqual(socket.sent, [{
            "type": "error",
            "message": "Bandwidth must be a number with K, M or G, up to 1G, such as 10M",
        }])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
