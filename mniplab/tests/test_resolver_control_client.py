"""Unit tests for UnboundControlClient in mniplab/resolver/control_client.py.

Unbound closes the control connection after each command, so every test drives
one connect/send/read-to-EOF exchange against a fake ``AF_UNIX`` socket that
replays a recorded Unbound response.
"""

from __future__ import annotations

import socket
import unittest
from typing import List, Tuple

import pytest

from mniplab.errors import ResolverControlError
from mniplab.resolver.control_client import UnboundControlClient
from mniplab.tests.fakes import FakeUnixSocket

SOCKET_PATH = "/tmp/resolver-service/res1/run/unbound.sock"


def make_client(
    *sockets: FakeUnixSocket,
) -> Tuple[UnboundControlClient, List[FakeUnixSocket]]:
    """Return a client whose socket factory hands out *sockets* in order."""
    handed: List[FakeUnixSocket] = []
    queue = list(sockets)

    def factory() -> FakeUnixSocket:
        sock = queue.pop(0)
        handed.append(sock)
        return sock

    return UnboundControlClient(SOCKET_PATH, socket_factory=factory), handed


@pytest.mark.unit
class UnboundControlClientTests(unittest.TestCase):
    """Test suite for UnboundControlClient."""

    def test_execute_speaks_ubct1_and_returns_response(self) -> None:
        """A command is framed as 'UBCT1 <command>\\n' and its full reply returned."""
        sock = FakeUnixSocket(b"unbound (pid 42) is running...\n")
        client, handed = make_client(sock)

        result = client.execute("status")

        self.assertEqual(result, "unbound (pid 42) is running...\n")
        self.assertEqual(handed[0].connected_to, SOCKET_PATH)
        self.assertEqual(handed[0].sent, b"UBCT1 status\n")
        self.assertTrue(handed[0].closed)

    def test_execute_appends_arguments(self) -> None:
        """Arguments are appended to the command line."""
        sock = FakeUnixSocket(b"ok\n")
        client, handed = make_client(sock)

        client.execute("flush_zone", "example.com.")

        self.assertEqual(handed[0].sent, b"UBCT1 flush_zone example.com.\n")

    def test_execute_rejects_embedded_newline(self) -> None:
        """A newline in an argument would inject a second command, so it is refused."""
        client, _ = make_client()

        with self.assertRaises(ResolverControlError):
            client.execute("flush_zone", "example.com.\nstop")

    def test_execute_raises_on_error_response(self) -> None:
        """An 'error ...' reply becomes a ResolverControlError carrying the command."""
        sock = FakeUnixSocket(b"error unknown command 'nope'\n")
        client, _ = make_client(sock)

        with self.assertRaises(ResolverControlError) as ctx:
            client.execute("nope")

        self.assertIn("unknown command", str(ctx.exception))
        self.assertEqual(ctx.exception.command, "nope")

    def test_execute_raises_when_socket_unreachable(self) -> None:
        """A refused connection is reported against the socket path."""
        sock = FakeUnixSocket(connect_error=ConnectionRefusedError("nope"))
        client, _ = make_client(sock)

        with self.assertRaises(ResolverControlError) as ctx:
            client.execute("status")

        self.assertIn(SOCKET_PATH, str(ctx.exception))
        self.assertEqual(ctx.exception.socket_path, SOCKET_PATH)

    def test_execute_raises_on_timeout(self) -> None:
        """A socket timeout is reported rather than surfacing as a bare OSError."""
        sock = FakeUnixSocket(connect_error=socket.timeout("timed out"))
        client, _ = make_client(sock)

        with self.assertRaises(ResolverControlError):
            client.execute("status")

    def test_status_returns_raw_text(self) -> None:
        """status() returns Unbound's human-readable status block verbatim."""
        sock = FakeUnixSocket(b"version: 1.17.1\nuptime: 9 seconds\n")
        client, _ = make_client(sock)

        self.assertIn("version: 1.17.1", client.status())

    def test_stats_noreset_parses_counters(self) -> None:
        """stats_noreset() parses key=value lines into numbers, leaving counts as ints."""
        sock = FakeUnixSocket(
            b"total.num.queries=12\n"
            b"total.num.cachehits=7\n"
            b"total.recursion.time.avg=0.125000\n"
        )
        client, handed = make_client(sock)

        stats = client.stats_noreset()

        self.assertEqual(handed[0].sent, b"UBCT1 stats_noreset\n")
        self.assertEqual(stats["total.num.queries"], 12)
        self.assertIsInstance(stats["total.num.queries"], int)
        self.assertAlmostEqual(stats["total.recursion.time.avg"], 0.125)

    def test_stats_noreset_ignores_malformed_lines(self) -> None:
        """Lines without '=' or with unparseable values are skipped, not fatal."""
        sock = FakeUnixSocket(b"garbage\ntotal.num.queries=3\nbad.key=notanumber\n")
        client, _ = make_client(sock)

        stats = client.stats_noreset()

        self.assertEqual(stats, {"total.num.queries": 3})

    def test_dump_cache_returns_raw_dump(self) -> None:
        """dump_cache() returns the raw cache dump for a parser to consume."""
        dump = b"START_RRSET_CACHE\nEND_RRSET_CACHE\nSTART_MSG_CACHE\nEND_MSG_CACHE\nEOF\n"
        sock = FakeUnixSocket(dump)
        client, handed = make_client(sock)

        self.assertEqual(client.dump_cache(), dump.decode())
        self.assertEqual(handed[0].sent, b"UBCT1 dump_cache\n")

    def test_reload_keep_cache_issues_command_then_waits_for_status(self) -> None:
        """reload_keep_cache() re-reads the configuration, then returns once the daemon answers again."""
        client, handed = make_client(
            FakeUnixSocket(b"ok\n"), FakeUnixSocket(b"unbound (pid 42) is running...\n")
        )

        client.reload_keep_cache()

        self.assertEqual(handed[0].sent, b"UBCT1 reload_keep_cache\n")
        self.assertEqual(handed[1].sent, b"UBCT1 status\n")

    def test_flush_zone_and_flush_name(self) -> None:
        """flush_zone() and flush_name() target a subtree and a single name."""
        zone_sock = FakeUnixSocket(
            b"ok removed 2 rrsets, 1 messages and 0 key entries\n"
        )
        name_sock = FakeUnixSocket(b"ok\n")
        client, handed = make_client(zone_sock, name_sock)

        client.flush_zone("example.com.")
        client.flush_name("web.example.com.")

        self.assertEqual(handed[0].sent, b"UBCT1 flush_zone example.com.\n")
        self.assertEqual(handed[1].sent, b"UBCT1 flush web.example.com.\n")

    def test_is_running_reports_reachability(self) -> None:
        """is_running() answers True on a live socket and False when unreachable."""
        live, _ = make_client(FakeUnixSocket(b"unbound (pid 42) is running...\n"))
        self.assertTrue(live.is_running())

        dead, _ = make_client(FakeUnixSocket(connect_error=FileNotFoundError()))
        self.assertFalse(dead.is_running())
