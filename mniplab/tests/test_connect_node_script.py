"""Unit tests for util/connect_node.sh: choosing the Lab, and refusing.

The script tells a Node's process by the Lab-directory binds in its
mounts, which only a real Lab has, so the paths that end in a Node are
exercised against two real Labs in ``test_multi_lab_concurrency``. Here:
which Lab a bare, ``--lab`` or ``MNIPLAB_LAB_KEY`` invocation picks, what
it lists, and how it refuses.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest


class ConnectNodeScriptTests(unittest.TestCase):
    """Test suite for connect_node.sh CLI utility."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.labs_dir = os.path.join(self.test_dir, "labs")
        os.makedirs(self.labs_dir)

        # A fake bin directory: an nsenter that just runs its command, and a
        # ps naming one r1 shell per fake PID. No fake PID's mountinfo holds
        # a Lab directory, so a lookup by key can only refuse.
        self.bin_dir = os.path.join(self.test_dir, "bin")
        os.makedirs(self.bin_dir)
        self._write_tool("nsenter", 'while [[ $# -gt 0 ]]; do a=$1; shift; [[ $a == "--" ]] && break; done\nexec "$@"\n')
        self._write_tool("ps", 'echo "4242 bash mininet:r1"\necho "4243 bash mininet:r1"\n')

        self.script_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "util", "connect_node.sh")
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_tool(self, name: str, body: str) -> None:
        path = os.path.join(self.bin_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n" + body)
        os.chmod(path, stat.S_IRWXU)

    def _lab(self, key: str, *nodes: str) -> None:
        for node in nodes:
            os.makedirs(os.path.join(self.labs_dir, key, "nodes", node))

    def _run_script(
        self,
        args: list[str],
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["MNIPLAB_TMP_DIR"] = self.test_dir
        env["PATH"] = f"{self.bin_dir}:{env.get('PATH', '')}"
        env.pop("MNIPLAB_LAB_KEY", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [self.script_path] + args,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_refuses_when_multiple_labs_alive_without_flag_or_env(self) -> None:
        """With two Labs alive, bare invocation refuses and lists both keys."""
        self._lab("labAlpha", "r1")
        self._lab("labBeta", "r1")

        res = self._run_script(["r1", "hostname"])

        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Multiple alive Labs detected", res.stderr)
        self.assertIn("labAlpha", res.stderr)
        self.assertIn("labBeta", res.stderr)

    def test_refuses_when_no_lab_is_alive(self) -> None:
        """With no Lab directory at all there is nothing to attach to."""
        res = self._run_script(["r1", "hostname"])

        self.assertNotEqual(res.returncode, 0)
        self.assertIn("No alive Lab", res.stderr)

    def test_refuses_a_process_that_is_not_in_the_chosen_lab(self) -> None:
        """A shell of the right name in another Lab is never attached to."""
        self._lab("labAlpha", "r1")
        self._lab("labBeta", "r1")

        res = self._run_script(["--lab", "labAlpha", "r1", "hostname"])

        self.assertEqual(res.returncode, 2)
        self.assertIn("belongs to Lab 'labAlpha'", res.stderr)
        self.assertIn("4242", res.stderr)
        self.assertNotIn("CALLED", res.stdout)

    def test_env_var_chooses_the_lab_like_the_flag(self) -> None:
        """MNIPLAB_LAB_KEY names the Lab the way --lab does."""
        self._lab("labAlpha", "r1")
        self._lab("labBeta", "r1")

        res = self._run_script(["r1", "hostname"], env_extra={"MNIPLAB_LAB_KEY": "labBeta"})

        self.assertEqual(res.returncode, 2)
        self.assertIn("belongs to Lab 'labBeta'", res.stderr)

    def test_single_alive_lab_is_chosen_without_a_key(self) -> None:
        """With one Lab alive, the bare form addresses it."""
        self._lab("labSolo", "r1")

        res = self._run_script(["r1", "hostname"])

        self.assertEqual(res.returncode, 2)
        self.assertIn("belongs to Lab 'labSolo'", res.stderr)

    def test_list_names_the_nodes_of_the_lab_directory(self) -> None:
        """--list lists the Nodes under labs/<key>/nodes/."""
        self._lab("labSolo", "r1", "h1", "ex1")

        res = self._run_script(["--list"])

        self.assertEqual(res.returncode, 0)
        self.assertEqual(sorted(res.stdout.split()), ["ex1", "h1", "r1"])

    def test_list_follows_the_chosen_lab(self) -> None:
        """--list with --lab lists that Lab's Nodes, not the other's."""
        self._lab("labAlpha", "r1", "h1")
        self._lab("labBeta", "r2")

        res = self._run_script(["--lab", "labBeta", "--list"])

        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.split(), ["r2"])

    def test_warns_about_a_node_the_lab_directory_does_not_know(self) -> None:
        """A name absent from labs/<key>/nodes/ is warned about before the lookup."""
        self._lab("labSolo", "h1")

        res = self._run_script(["r1", "hostname"])

        self.assertIn("not in the known host list", res.stderr)


if __name__ == "__main__":
    unittest.main()
