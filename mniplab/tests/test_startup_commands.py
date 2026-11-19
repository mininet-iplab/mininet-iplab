"""Per-Node Startup commands: stages, ordering, timeouts and failures."""

import os
import subprocess
import tempfile
import time
import unittest

import pytest

from mniplab.startup import StartupCommandError, StartupCommands


class ShellNode:
    """A Node whose shell is a real local shell, like a Mininet Node's."""

    def __init__(self, name, path=None):
        self.name = name
        self.env = dict(os.environ, PATH=f"{path}:{os.environ['PATH']}") if path else None

    def cmd(self, command):
        return subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, check=False, env=self.env
        ).stdout


class HungNode:
    """A Node whose shell stops answering, whatever it is asked."""

    name = "h1"

    @staticmethod
    def cmd(_command):
        time.sleep(6)
        return ""


@pytest.mark.unit
class StartupCommandsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def record(self, name):
        return os.path.join(self.tmp.name, name)

    def read(self, name):
        path = self.record(name)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as source:
            return source.read().split()

    def test_runs_only_the_requested_stage_in_listed_order(self):
        node = ShellNode("r1")
        log = self.record("r1")
        commands = StartupCommands()
        commands.add(node, f"echo first >> {log}", stage="before-daemons")
        commands.add(node, f"echo later >> {log}", stage="after-start")
        commands.add(node, f"echo second >> {log}", stage="before-daemons")

        commands.run("before-daemons")

        self.assertEqual(self.read("r1"), ["first", "second"])

    def test_nodes_in_one_stage_run_together(self):
        commands = StartupCommands()
        for index in range(4):
            commands.add(ShellNode(f"h{index}"), "sleep 0.5", stage="after-start")

        started = time.monotonic()
        commands.run("after-start")

        # Four half-second commands: together about 0.5 s, in turn 2 s.
        self.assertLess(time.monotonic() - started, 1.5)

    def test_a_command_is_stopped_at_its_timeout(self):
        commands = StartupCommands()
        commands.add(ShellNode("h1"), "sleep 3", stage="after-start", timeout=1)

        started = time.monotonic()
        with self.assertRaisesRegex(StartupCommandError, "'sleep 3' timed out after 1 s"):
            commands.run("after-start")
        self.assertLess(time.monotonic() - started, 2.5)

    def test_the_default_timeout_is_five_seconds(self):
        # A timeout(1) on the shell's PATH that records its arguments and
        # reports a timeout at once, so the test need not wait five seconds.
        bin_dir = self.record("bin")
        os.mkdir(bin_dir)
        stand_in = os.path.join(bin_dir, "timeout")
        with open(stand_in, "w", encoding="utf-8") as script:
            script.write(f'#!/bin/sh\necho "$@" > {self.record("args")}\nexit 124\n')
        os.chmod(stand_in, 0o755)
        commands = StartupCommands()
        commands.add(ShellNode("h1", path=bin_dir), "sleep 7", stage="after-start")

        with self.assertRaisesRegex(StartupCommandError, "timed out after 5 s"):
            commands.run("after-start")
        self.assertEqual(self.read("args"), ["-k", "1", "5", "sh", "-c", "sleep", "7"])

    def test_failure_stops_that_node_and_reports_it(self):
        node = ShellNode("r1")
        commands = StartupCommands()
        commands.add(node, "echo 'boom: no such device' >&2; exit 2", stage="before-daemons")
        commands.add(node, f"echo never >> {self.record('r1')}", stage="before-daemons")

        with self.assertRaises(StartupCommandError) as raised:
            commands.run("before-daemons")

        message = str(raised.exception)
        self.assertIn("Startup command on r1 (before daemons)", message)
        self.assertIn("failed with exit status 2: boom: no such device", message)
        self.assertEqual(self.read("r1"), [])

    def test_a_killed_command_is_not_reported_as_a_plain_timeout(self):
        commands = StartupCommands()
        commands.add(ShellNode("h1"), "kill -9 $$", stage="after-start")

        with self.assertRaisesRegex(StartupCommandError, "was killed"):
            commands.run("after-start")

    def test_may_fail_becomes_a_warning_and_the_node_continues(self):
        node = ShellNode("h1")
        commands = StartupCommands()
        commands.add(node, "echo unreachable; exit 1", stage="after-start", may_fail=True)
        commands.add(node, f"echo next >> {self.record('h1')}", stage="after-start")

        warnings = commands.run("after-start")

        self.assertEqual(
            warnings,
            ["Startup command on h1 (after start) 'echo unreachable; exit 1' "
             "failed with exit status 1: unreachable"],
        )
        self.assertEqual(self.read("h1"), ["next"])

    def test_the_stage_cap_limits_later_commands(self):
        node = ShellNode("h1")
        commands = StartupCommands(stage_cap=0.4)
        commands.add(node, "true", stage="after-start", timeout=60)
        commands.add(node, "sleep 3", stage="after-start", timeout=60)

        with self.assertRaisesRegex(StartupCommandError, "'sleep 3' timed out after 1 s"):
            commands.run("after-start")

    def test_a_hung_node_shell_fails_the_stage_at_its_cap(self):
        # The shell answers only after the cap and its 5 s grace have passed.
        commands = StartupCommands(stage_cap=0.1)
        commands.add(HungNode(), "true", stage="after-start")

        with self.assertRaisesRegex(StartupCommandError, "h1 did not finish"):
            commands.run("after-start")

    def test_rejects_unknown_stage_empty_command_and_out_of_range_timeout(self):
        commands = StartupCommands()
        with self.assertRaises(ValueError):
            commands.add(ShellNode("h1"), "true", stage="later")
        with self.assertRaises(ValueError):
            commands.add(ShellNode("h1"), "true", stage="after-start", timeout=61)
        with self.assertRaises(ValueError):
            commands.add(ShellNode("h1"), "  ", stage="after-start")


if __name__ == "__main__":
    unittest.main()
