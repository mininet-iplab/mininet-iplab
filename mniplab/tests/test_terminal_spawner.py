"""Unit tests for TerminalSpawner — the nsenter attach behind a Terminal.

The spawner's contract changed with the move off ``mnexec -a``: entering a
PID namespace makes ``nsenter`` fork, so the pid ``spawn`` returns must be
the process actually running inside the Node, not the pty child it exec'd.
These tests pin the argv the child exec's and the pid the parent returns,
without ever starting a real namespace.
"""

import os
import unittest
from unittest.mock import patch

from mniplab.labhandle import NodeAttachment
from mniplab.webserver import terminal_spawner
from mniplab.webserver.terminal_spawner import TerminalSpawner, _sole_child_pid


class SpawnArgvTests(unittest.TestCase):
    """The child exec's the shared nsenter recipe, never mnexec."""

    def _captured_exec(self):
        """Patch pty.fork and os.execvp so the child branch is captured, not run.

        Returns:
            A (record, run) pair: ``record`` is the dict the exec'd argv is
            captured into, ``run`` drives TerminalSpawner.spawn.
        """
        record = {}

        def fake_fork():
            # 0 exercises the child branch in-process: os.execvp is faked
            # too, so nothing is actually exec'd, and the branch falls
            # through to the parent path with child_pid == 0.
            return 0, -1

        def fake_execvp(binary, argv):
            record["binary"] = binary
            record["argv"] = argv

        return record, fake_fork, fake_execvp

    def test_execs_nsenter_with_the_shared_recipe(self):
        record, fake_fork, fake_execvp = self._captured_exec()
        node = NodeAttachment("h1", 1234, "/tmp/mniplab/h1")

        with patch.object(terminal_spawner.pty, "fork", side_effect=fake_fork), \
             patch.object(terminal_spawner.os, "execvp", side_effect=fake_execvp), \
             patch.object(
                 terminal_spawner, "_resolve_inner_pid", return_value=4242
             ):
            TerminalSpawner().spawn(node)

        self.assertEqual(record.get("binary"), "nsenter")
        argv = record.get("argv", [])
        self.assertEqual(argv[:2], ["nsenter", "-t"])
        self.assertIn("1234", argv)
        for flag in ("-m", "-u", "-n", "-p"):
            self.assertIn(flag, argv)
        self.assertIn("--", argv)

    def test_execs_a_shell_with_the_same_prompt_and_home_as_before(self):
        record, fake_fork, fake_execvp = self._captured_exec()
        node = NodeAttachment("h1", 1234, "/tmp/mniplab/h1")

        with patch.object(terminal_spawner.pty, "fork", side_effect=fake_fork), \
             patch.object(terminal_spawner.os, "execvp", side_effect=fake_execvp), \
             patch.object(
                 terminal_spawner, "_resolve_inner_pid", return_value=4242
             ):
            TerminalSpawner().spawn(node)

        command = record.get("argv", [])[-1]
        self.assertIn("HOME=/tmp/mniplab/h1", command)
        self.assertIn("PS1='h1# '", command)
        self.assertIn("cd /tmp/mniplab/h1", command)
        self.assertIn("exec bash --norc", command)


class SpawnReturnPidTests(unittest.TestCase):
    """spawn returns the pid that lives inside the Node, not the wrapper."""

    def test_returns_the_resolved_inner_pid(self):
        node = NodeAttachment("h1", 999, "/tmp/mniplab/h1")
        with patch.object(terminal_spawner.pty, "fork", return_value=(100, 5)), \
             patch.object(terminal_spawner.os, "execvp"), \
             patch.object(
                 terminal_spawner, "_resolve_inner_pid", return_value=101
             ) as resolve:
            pid, fd = TerminalSpawner().spawn(node)

        resolve.assert_called_once_with(100, 5)
        self.assertEqual((pid, fd), (101, 5))

    def test_container_host_returns_the_pty_child_unresolved(self):
        # docker exec does not fork, so there is no inner pid to resolve and
        # looking for docker's children would signal the wrong process.
        node = NodeAttachment("c1", 999, "/tmp/mniplab/c1", container_name="mn.k1.c1")
        with patch.object(terminal_spawner.pty, "fork", return_value=(100, 5)), \
             patch.object(terminal_spawner.os, "execvp"), \
             patch.object(
                 terminal_spawner, "_resolve_inner_pid"
             ) as resolve:
            pid, _ = TerminalSpawner().spawn(node)

        resolve.assert_not_called()
        self.assertEqual(pid, 100)


class SoleChildPidTests(unittest.TestCase):
    """_sole_child_pid reads procfs and tolerates a missing or empty entry."""

    def test_returns_the_first_child(self):
        with patch("builtins.open", unittest.mock.mock_open(read_data="4321 9999\n")):
            self.assertEqual(_sole_child_pid(1234), 4321)

    def test_no_children_means_none(self):
        with patch("builtins.open", unittest.mock.mock_open(read_data="")):
            self.assertIsNone(_sole_child_pid(1234))

    def test_a_missing_entry_means_none(self):
        with patch("builtins.open", side_effect=OSError):
            self.assertIsNone(_sole_child_pid(1234))


if __name__ == "__main__":
    unittest.main()
