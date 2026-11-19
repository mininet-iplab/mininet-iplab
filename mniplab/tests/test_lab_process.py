"""Seam C: the Lab-process boundary.

A real child process builds a tiny Lab, the manager's handle reaches it over
the socket, the child is killed, and what comes back is a diagnostic naming
the Lab; the service marks the Lab stopped and its keyed objects are gone.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import pytest

from mniplab.constants import MNIPLAB_TMP_DIR
from mniplab.errors import LabUnavailableError
from mniplab.labrpc import RemoteLabHandle
from mniplab.tests.fakes import FakeTerminalRegistry
from mniplab.webserver.services import LabLifecycleService

TINY_LAB = textwrap.dedent('''
    from mniplab import mnIPLab


    def build_network():
        net = mnIPLab(topo=None, controller=None)
        h1 = net.addHost("h1")
        h2 = net.addHost("h2")
        net.addLink(h1, h2, params1={"ip": "10.0.0.1/24"}, params2={"ip": "10.0.0.2/24"})
        return net
''')


@pytest.mark.integration
@pytest.mark.requires_root
class LabProcessTests(unittest.TestCase):
    """One Lab in a child process, reached through the service's handle."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lab_file = os.path.join(self.tmp.name, "tiny-lab.py")
        with open(self.lab_file, "w") as f:
            f.write(TINY_LAB)

    def test_a_killed_lab_is_diagnosed_marked_stopped_and_swept(self) -> None:
        async def scenario() -> None:
            service = LabLifecycleService()
            exits = []

            async def on_exit(key: str, reason: str) -> None:
                exits.append((key, reason))

            service.on_lab_exited = on_exit

            started = await service.start_lab_from_builder("tiny-lab", self.lab_file)
            self.assertNotIn("error", started, started)
            key = started["key"]
            handle = service.handle
            assert handle is not None and isinstance(handle, RemoteLabHandle)
            self.assertEqual(handle.key, key)

            # The handle answers over the socket like an in-process Lab.
            self.assertEqual(started["nodes"], 2)
            topology = handle.topology()
            self.assertEqual({n["id"] for n in topology["nodes"]}, {"h1", "h2"})
            ping = handle.run_ping("h1", "h2", 1)
            self.assertTrue(ping.get("success"), ping)
            self.assertEqual(handle.node_attachment("h1").node, "h1")
            self.assertTrue(os.path.isdir(os.path.join(MNIPLAB_TMP_DIR, "labs", key, "home", "h1")))
            self.assertTrue(os.path.exists(handle.socket_path))

            running = service.get(key)
            assert running is not None and running.child is not None
            child, watcher = running.child, running.watcher
            assert watcher is not None
            os.kill(child.process.pid, signal.SIGKILL)
            await asyncio.wait_for(watcher, 60)

            with self.assertRaises(LabUnavailableError) as ctx:
                handle.status()
            self.assertIn("Lab 'tiny-lab'", str(ctx.exception))
            self.assertIn(key, str(ctx.exception))

            self.assertIsNone(service.lab)
            status = await service.get_status()
            self.assertFalse(status["running"])
            self.assertEqual(status["last_exit"]["reason"], "killed by SIGKILL")
            self.assertEqual(exits, [(key, "killed by SIGKILL")])
            self.assertFalse(os.path.exists(os.path.join(MNIPLAB_TMP_DIR, "labs", key)))
            self.assertFalse(os.path.exists(handle.socket_path))

        asyncio.run(scenario())

    def test_a_stopped_lab_ends_its_process_cleanly(self) -> None:
        async def scenario() -> None:
            service = LabLifecycleService()
            started = await service.start_lab_from_builder("tiny-lab", self.lab_file)
            self.assertNotIn("error", started, started)
            running = service.get(started["key"])
            assert running is not None and running.child is not None
            child = running.child
            socket_path = child.handle.socket_path

            stopped = await service.stop_lab(started["key"], FakeTerminalRegistry())  # type: ignore[arg-type]

            self.assertEqual(stopped, {"status": "stopped", "key": started["key"],
                                       "group": None, "example": "tiny-lab"})
            self.assertEqual(child.exit_reason, "stopped")
            self.assertIsNone(service.lab)
            self.assertFalse(os.path.exists(socket_path))
            self.assertFalse(os.path.exists(os.path.join(MNIPLAB_TMP_DIR, "labs", started["key"])))

        asyncio.run(scenario())

    def test_a_lab_that_fails_to_build_reports_why(self) -> None:
        broken = os.path.join(self.tmp.name, "broken-lab.py")
        with open(broken, "w") as f:
            f.write("def build_network():\n    raise ValueError('no such topology')\n")

        async def scenario() -> None:
            service = LabLifecycleService()
            result = await service.start_lab_from_builder("broken-lab", broken)
            self.assertIn("no such topology", result["error"])
            self.assertIsNone(service.lab)

        asyncio.run(scenario())

    def test_a_lab_whose_builder_hangs_times_out_is_killed_and_swept(self) -> None:
        hanging = os.path.join(self.tmp.name, "hanging-lab.py")
        with open(hanging, "w") as f:
            f.write("import time\ndef build_network():\n    time.sleep(30)\n")

        async def scenario() -> None:
            service = LabLifecycleService()
            result = await service.start_lab_from_builder(
                "hanging-lab", hanging, timeout=1.0
            )
            self.assertFalse(result["running"])
            self.assertIn("build timed out after 1 s", result["error"])
            self.assertIsNone(service.lab)

        asyncio.run(scenario())

    def test_ctrl_c_in_the_lab_prompt_leaves_a_web_ui_call_its_answer(self) -> None:
        # After Ctrl+C the Prompt interrupts every Node still busy; that must
        # never be a Node a Web UI call is using. The same step runs as the
        # Prompt starts, just after the Lab reports ready.
        async def scenario() -> None:
            service = LabLifecycleService()
            started = await service.start_lab_from_builder("tiny-lab", self.lab_file)
            self.assertNotIn("error", started, started)
            running = service.get(started["key"])
            assert running is not None and running.child is not None
            handle = RemoteLabHandle(started["key"], "tiny-lab", timeout=20)
            loop = asyncio.get_running_loop()
            try:
                ping = loop.run_in_executor(None, handle.run_ping, "h1", "10.0.0.2", 4)
                await asyncio.sleep(1)
                assert running.child.prompt_fd is not None
                os.write(running.child.prompt_fd, b"\x03")
                result = await ping
                self.assertTrue(result.get("success"), result)
                self.assertIn("4 received", result.get("output", ""), result)
            finally:
                await service.stop_lab(started["key"], FakeTerminalRegistry())  # type: ignore[arg-type]

        asyncio.run(scenario())

    def test_stopping_a_lab_ends_a_command_its_prompt_is_running(self) -> None:
        async def scenario() -> None:
            service = LabLifecycleService()
            started = await service.start_lab_from_builder("tiny-lab", self.lab_file)
            self.assertNotIn("error", started, started)
            running = service.get(started["key"])
            assert running is not None and running.child is not None
            child = running.child
            assert child.prompt_fd is not None
            os.write(child.prompt_fd, b"h1 ping -i 0.7 10.0.0.2\n")
            await asyncio.sleep(2)

            began = time.monotonic()
            stopped = await service.stop_lab(started["key"], FakeTerminalRegistry())  # type: ignore[arg-type]

            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(child.exit_reason, "stopped")
            self.assertLess(time.monotonic() - began, 20)
            self.assertNotIn("ping -i 0.7 10.0.0.2", subprocess.run(
                ["ps", "-eo", "args"], capture_output=True, text=True, check=True).stdout)
            self.assertFalse(os.path.exists(os.path.join(MNIPLAB_TMP_DIR, "labs", started["key"])))

        asyncio.run(scenario())

    def test_a_stop_ends_the_lab_in_time_even_while_an_operation_runs(self) -> None:
        async def scenario() -> None:
            service = LabLifecycleService()
            started = await service.start_lab_from_builder("tiny-lab", self.lab_file)
            self.assertNotIn("error", started, started)
            running = service.get(started["key"])
            assert running is not None and running.child is not None
            child = running.child
            loop = asyncio.get_running_loop()
            # A Web UI operation that holds the Lab for a long time.
            long_ping = loop.run_in_executor(None, child.handle.run_ping, "h1", "10.0.0.2", 1000)
            await asyncio.sleep(1)
            began = time.monotonic()
            try:
                await asyncio.wait_for(child.stop(timeout=5), 60)
            finally:
                if child.alive:
                    child.process.kill()
            self.assertLess(time.monotonic() - began, 20)
            self.assertFalse(child.alive)
            with self.assertRaises(LabUnavailableError):
                await long_ping
            self.assertFalse(os.path.exists(os.path.join(MNIPLAB_TMP_DIR, "labs", started["key"])))

        asyncio.run(scenario())

    def test_stopping_a_lab_ends_even_a_prompt_command_that_ignores_ctrl_c(self) -> None:
        async def scenario() -> None:
            service = LabLifecycleService()
            started = await service.start_lab_from_builder("tiny-lab", self.lab_file)
            self.assertNotIn("error", started, started)
            running = service.get(started["key"])
            assert running is not None and running.child is not None
            child = running.child
            assert child.prompt_fd is not None
            os.write(child.prompt_fd, b"h1 sh -c 'trap \"\" INT; sleep 600'\n")
            await asyncio.sleep(1)

            stopped = await service.stop_lab(started["key"], FakeTerminalRegistry())  # type: ignore[arg-type]

            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(child.exit_reason, "stopped")
            self.assertFalse(os.path.exists(os.path.join(MNIPLAB_TMP_DIR, "labs", started["key"])))

        asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.requires_root
@pytest.mark.parametrize("lab_mode", ["single", "multi"])
def test_lab_restarts_without_module_execution_warning(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    lab_mode: str,
) -> None:
    """Starting and restarting a Lab never re-executes an imported module."""
    from mniplab.webserver.config import settings

    monkeypatch.setenv("MNIPLAB_LAB_MODE", lab_mode)
    monkeypatch.setattr(settings, "lab_mode", lab_mode)
    lab_file = tmp_path / "tiny-lab.py"
    lab_file.write_text(TINY_LAB, encoding="utf-8")

    async def scenario() -> None:
        service = LabLifecycleService()
        terminals = FakeTerminalRegistry()
        try:
            for _ in range(2):
                started = await service.start_lab_from_builder(
                    "tiny-lab", str(lab_file), cwd=str(tmp_path)
                )
                assert "error" not in started, started
                key = started["key"]
                try:
                    assert started["nodes"] == 2
                    running = service.get(key)
                    assert running is not None and running.child is not None
                    child = running.child
                    socket_path = child.handle.socket_path
                finally:
                    stopped = await service.stop_lab(key, terminals)  # type: ignore[arg-type]
                assert stopped["status"] == "stopped"
                assert child.process.returncode == 0
                assert not os.path.exists(socket_path)
                assert not os.path.exists(os.path.join(MNIPLAB_TMP_DIR, "labs", key))
                captured = capfd.readouterr()
                assert "'mniplab.labprocess' found in sys.modules" not in captured.err, captured.err
        finally:
            for running in service.labs():
                await service.stop_lab(running.key, terminals)  # type: ignore[arg-type]
            service.executor.shutdown(wait=True)

    asyncio.run(scenario())
