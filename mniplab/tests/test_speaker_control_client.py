"""Unit tests for the Speaker's control client, against an in-process FIFO pair.

ExaBGP's pipe helper is imitated here line for line: it holds ``exabgp.in``
open for reading, and for every line of an answer it opens ``exabgp.out``
for writing without blocking and drops the line if nobody holds the other
end. What is pinned is the framing a Learner depends on — an answer is whole
when ``done`` arrives, ``error`` names the command since ExaBGP gives no
reason, ``shutdown`` and silence are failures — and the two rules the pipe
imposes: the response end is open before the command is written, and two
commands never share the byte stream.
"""

from __future__ import annotations

import errno
import os
import select
import tempfile
import threading
import time
import unittest
from typing import Dict, List, Optional
from unittest.mock import patch

from mniplab.errors import ExaBGPControlError
from mniplab.exabgp import control_client
from mniplab.exabgp.control_client import SpeakerControlClient


class FakeExaBGPPipe:
    """A scripted stand-in for the daemon's end of the FIFO pair.

    Attributes:
        answers: Command line → the lines to answer with, terminator included.
        dropped: Answer lines nobody was reading ``exabgp.out`` for.
        line_delay: Seconds to wait between the lines of one answer.
    """

    def __init__(self, run_dir: str, answers: Dict[str, List[str]], line_delay: float = 0.0) -> None:
        self.in_path = os.path.join(run_dir, "exabgp.in")
        self.out_path = os.path.join(run_dir, "exabgp.out")
        self.answers = answers
        self.dropped: List[str] = []
        self.received: List[str] = []
        self.line_delay = line_delay
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "FakeExaBGPPipe":
        """Create the FIFO pair, unless it exists, and start answering."""
        for path in (self.in_path, self.out_path):
            if not os.path.exists(path):
                os.mkfifo(path)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self

    def stop(self) -> None:
        """Stop answering and release the command pipe."""
        self._stop.set()
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        reader = os.open(self.in_path, os.O_RDWR | os.O_NONBLOCK)
        self._ready.set()
        buf = b""
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([reader], [], [], 0.02)
                if not ready:
                    continue
                buf += os.read(reader, 4096)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._answer(line.decode())
        finally:
            os.close(reader)

    def _answer(self, command: str) -> None:
        self.received.append(command)
        for line in self.answers.get(command, ["error"]):
            if self.line_delay:
                time.sleep(self.line_delay)
            try:
                writer = os.open(self.out_path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno == errno.ENXIO:
                    self.dropped.append(line)
                    continue
                raise
            try:
                os.write(writer, f"{line}\n".encode())
            finally:
                os.close(writer)


class SpeakerControlClientTests(unittest.TestCase):
    """One command at a time over the Speaker's FIFO pair."""

    def setUp(self) -> None:
        self.run_dir = tempfile.mkdtemp()
        self.addCleanup(self._remove_run_dir)
        self.pipe: Optional[FakeExaBGPPipe] = None

    def _remove_run_dir(self) -> None:
        for name in os.listdir(self.run_dir):
            os.unlink(os.path.join(self.run_dir, name))
        os.rmdir(self.run_dir)

    def responder(self, answers: Dict[str, List[str]], **kwargs: float) -> FakeExaBGPPipe:
        """Start a scripted daemon end on this test's FIFO pair."""
        self.pipe = FakeExaBGPPipe(self.run_dir, answers, **kwargs).start()
        self.addCleanup(self.pipe.stop)
        return self.pipe

    def client(self, timeout: float = 2.0) -> SpeakerControlClient:
        """A client on this test's FIFO pair, for Speaker ``sp1``."""
        return SpeakerControlClient(self.run_dir, speaker="sp1", timeout=timeout)

    def test_a_done_terminated_answer_is_returned_whole(self) -> None:
        self.responder({"show adj-rib out": [
            "10.0.1.1 ipv4 unicast 203.0.113.0/24 next-hop 10.0.1.2",
            "2001:db8:1::1 ipv6 unicast 2001:db8:100::/48 next-hop 2001:db8:1::2",
            "done",
        ]})
        lines = self.client().execute("show adj-rib out")
        self.assertEqual(lines, [
            "10.0.1.1 ipv4 unicast 203.0.113.0/24 next-hop 10.0.1.2",
            "2001:db8:1::1 ipv6 unicast 2001:db8:100::/48 next-hop 2001:db8:1::2",
        ])

    def test_an_empty_answer_is_an_empty_list(self) -> None:
        self.responder({"show adj-rib out": ["done"]})
        self.assertEqual(self.client().execute("show adj-rib out"), [])

    def test_error_raises_naming_the_command(self) -> None:
        """ExaBGP's ``error`` carries no reason, so the command is the message."""
        self.responder({"neighbor 10.9.9.9 announce route 10.1.0.0/24 next-hop 10.0.1.2": ["error"]})
        command = "neighbor 10.9.9.9 announce route 10.1.0.0/24 next-hop 10.0.1.2"
        with self.assertRaises(ExaBGPControlError) as ctx:
            self.client().execute(command)
        self.assertIn(command, str(ctx.exception))
        self.assertEqual(ctx.exception.command, command)

    def test_shutdown_raises(self) -> None:
        self.responder({"show neighbor json": ["shutdown"]})
        with self.assertRaises(ExaBGPControlError) as ctx:
            self.client().execute("show neighbor json")
        self.assertIn("shutting down", str(ctx.exception))

    def test_silence_past_the_timeout_raises(self) -> None:
        """A daemon that never terminates its answer is reported, not waited on."""
        self.responder({"show neighbor json": ["[]"]})
        started = time.monotonic()
        with self.assertRaises(ExaBGPControlError) as ctx:
            self.client(timeout=0.3).execute("show neighbor json")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIn("sp1", str(ctx.exception))
        self.assertIn("0.3", str(ctx.exception))

    def test_a_slow_answer_is_not_a_timeout_while_lines_keep_coming(self) -> None:
        """The timeout is silence, not total duration: each line resets it."""
        self.responder({"show adj-rib in": ["a", "b", "c", "done"]}, line_delay=0.15)
        self.assertEqual(self.client(timeout=0.3).execute("show adj-rib in"), ["a", "b", "c"])

    def test_the_response_pipe_is_open_before_the_command_is_written(self) -> None:
        """The daemon drops any line nobody is reading, so the reader comes first.

        The order is pinned directly — which pipe the client opens first —
        because a wrong order loses the answer only when the responder beats
        the client to the pipe, which the scheduler decides.
        """
        pipe = self.responder({"version": ["exabgp 5.0.13", "done"]})
        opened: List[str] = []
        real_open = os.open

        def recording_open(path: str, flags: int, *args: int) -> int:
            opened.append(path)
            return real_open(path, flags, *args)

        with patch.object(control_client.os, "open", recording_open):
            self.assertEqual(self.client().execute("version"), ["exabgp 5.0.13"])
        # The responder's own per-line opens of exabgp.out follow the client's two.
        self.assertEqual(opened[:2], [pipe.out_path, pipe.in_path])
        self.assertEqual(pipe.dropped, [])

    def test_a_command_with_a_line_break_is_refused_before_it_is_written(self) -> None:
        pipe = self.responder({"version": ["exabgp 5.0.13", "done"]})
        with self.assertRaises(ExaBGPControlError):
            self.client().execute("version\nshutdown")
        self.assertEqual(pipe.received, [])

    def test_missing_fifos_name_the_speaker_and_the_daemon(self) -> None:
        """No FIFO pair means the Node never prepared one; the message says where."""
        with self.assertRaises(ExaBGPControlError) as ctx:
            self.client().execute("version")
        self.assertIn("sp1", str(ctx.exception))
        self.assertIn(self.run_dir, str(ctx.exception))

    def test_no_reader_on_the_command_pipe_means_the_daemon_is_not_running(self) -> None:
        os.mkfifo(os.path.join(self.run_dir, "exabgp.in"))
        os.mkfifo(os.path.join(self.run_dir, "exabgp.out"))
        with self.assertRaises(ExaBGPControlError) as ctx:
            self.client().execute("version")
        self.assertIn("running", str(ctx.exception))
        self.assertIn("sp1", str(ctx.exception))

    def test_is_running_reports_whether_the_daemon_answers(self) -> None:
        """The Node's readiness check: a daemon that answers ``version`` is up."""
        os.mkfifo(os.path.join(self.run_dir, "exabgp.in"))
        os.mkfifo(os.path.join(self.run_dir, "exabgp.out"))
        client = self.client(timeout=0.3)
        self.assertFalse(client.is_running())
        pipe = FakeExaBGPPipe(self.run_dir, {"version": ["exabgp 5.0.13", "done"]}).start()
        self.addCleanup(pipe.stop)
        self.assertTrue(client.is_running())

    def test_concurrent_commands_are_serialised(self) -> None:
        """Two threads on one Speaker each get their own whole answer."""
        self.responder({
            "show adj-rib out": ["out-1", "out-2", "out-3", "done"],
            "show adj-rib in": ["in-1", "in-2", "in-3", "done"],
        }, line_delay=0.03)
        client = self.client()
        results: Dict[str, List[str]] = {}

        def run(command: str) -> None:
            results[command] = client.execute(command)

        threads = [threading.Thread(target=run, args=(c,)) for c in ("show adj-rib out", "show adj-rib in")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(results["show adj-rib out"], ["out-1", "out-2", "out-3"])
        self.assertEqual(results["show adj-rib in"], ["in-1", "in-2", "in-3"])


if __name__ == "__main__":
    unittest.main()
