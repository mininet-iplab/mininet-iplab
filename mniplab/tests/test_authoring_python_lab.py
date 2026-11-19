"""A downloaded Python Lab Example runs on its own, away from the checkout."""

import io
import os
from pathlib import Path
import pty
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

import httpx2

from mniplab.tests.labs import LabCase
from mniplab.tests.authoring_fixtures import draft_from_shipped, host_node, router_node
from mniplab.webserver.authoring import AuthoringRepository
from mniplab.webserver.authoring_python import package_archive

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GUIDE = "# Exported lesson\n\nPing h2 from h1 through r1.\n"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class UnpackedPackage:
    """A package unpacked outside the checkout, its script run under a real PTY."""

    def __init__(self, root: str, script: str, *args: str, env: dict) -> None:
        self.master, slave = pty.openpty()
        self.process = subprocess.Popen(
            [sys.executable, script, *args], cwd=root, env=env,
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        os.close(slave)
        self.output = ""

    def expect(self, text: str, timeout: float = 90.0, after: int = 0) -> str:
        """Read the terminal until ``text`` appears after ``after``; fail with what was seen."""
        deadline = time.monotonic() + timeout
        while text not in self.output[after:]:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.process.poll() is not None:
                raise AssertionError(f"{text!r} never appeared:\n{self.output[-3000:]}")
            ready, _, _ = select.select([self.master], [], [], min(remaining, 1.0))
            if ready:
                try:
                    self.output += os.read(self.master, 65536).decode("utf-8", "replace")
                except OSError:
                    continue
        return self.output

    def send(self, line: str) -> None:
        os.write(self.master, f"{line}\r".encode())  # Return, as a person types it

    def run(self, command: str, text: str) -> None:
        """Type a command at the prompt, see ``text``, and wait for the next prompt.

        The CLI discards input typed ahead of its prompt, so, as a person
        does, nothing is typed until the prompt is back.
        """
        start = len(self.output)
        self.send(command)
        self.expect(text, after=start)
        self.expect("mininet-iplab>", after=self.output.index(text, start))

    def finish(self) -> int:
        """Leave the CLI; kill the process group if it does not end."""
        if self.process.poll() is None:
            try:
                self.send("exit")
                self.process.wait(timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        os.close(self.master)
        subprocess.run([sys.executable, "-m", "mniplab", "cleanup"], cwd=CHECKOUT,
                       capture_output=True, check=False)
        return self.process.returncode


class DownloadedPythonLabTests(LabCase):
    """The zip's script builds its Lab from its own Seeds, Guide and Layout."""

    def setUp(self) -> None:
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        repository = AuthoringRepository(storage.name)
        draft = repository.create()
        draft["catalog"]["name"] = "Exported lesson"
        draft["guide"] = GUIDE
        draft["topology"] = {
            "nodes": [
                router_node("r", "r1", frr_config="router ospf\n ospf router-id 9.9.9.9\n"),
                host_node("a", "h1", ipv4_gateway="10.0.1.1"),
                host_node("b", "h2", ipv4_gateway="10.0.2.1"),
            ],
            "links": [
                {"id": "one", "ends": [
                    {"node_id": "a", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "10.0.1.2/24", "ipv6": ""},
                    {"node_id": "r", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "10.0.1.1/24", "ipv6": ""}]},
                {"id": "two", "ends": [
                    {"node_id": "b", "interface_id": "if-000000000003", "port": 0,
                     "ipv4": "10.0.2.2/24", "ipv6": ""},
                    {"node_id": "r", "interface_id": "if-000000000004", "port": 1,
                     "ipv4": "10.0.2.1/24", "ipv6": ""}]},
            ],
        }
        draft["layout"]["nodes"] = {"r": {"x": 500, "y": 200}, "a": {"x": 100, "y": 600},
                                    "b": {"x": 900, "y": 600}}
        saved = repository.save(draft["id"], draft["revision"], draft)
        self.repository, self.draft = repository, saved
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        with zipfile.ZipFile(io.BytesIO(package_archive(repository, saved))) as archive:
            archive.extractall(elsewhere.name)
        self.root = os.path.join(elsewhere.name, "exported-lesson")
        self.env = {**os.environ, "PYTHONPATH": CHECKOUT}
        subprocess.run([sys.executable, "-m", "mniplab", "cleanup"], cwd=CHECKOUT,
                       capture_output=True, check=False)

    def test_cli_run_reads_its_seed_and_routes_between_hosts(self) -> None:
        lab = UnpackedPackage(self.root, "exported-lesson.py", env=self.env)
        try:
            lab.expect("mininet-iplab>")
            lab.run("r1 vtysh -c 'show running-config'", "ospf router-id 9.9.9.9")
            lab.run("h1 ping -c 2 -W 2 10.0.2.2", "2 received")
        finally:
            self.assertEqual(lab.finish(), 0, lab.output[-3000:])

    def test_web_run_serves_the_packaged_guide_and_layout(self) -> None:
        port = _free_port()
        env = {**self.env, "MNIPLAB_USERS": "teacher:Teach3r01:instructor"}
        lab = UnpackedPackage(self.root, "exported-lesson.py", "--enable-web",
                              "--web-host", "127.0.0.1", "--web-port", str(port), env=env)
        try:
            with httpx2.Client(base_url=f"http://127.0.0.1:{port}") as client:
                deadline = time.monotonic() + 90
                while True:
                    try:
                        login = client.post("/api/login", auth=("teacher", "Teach3r01"))
                        break
                    except httpx2.TransportError:
                        if time.monotonic() > deadline or lab.process.poll() is not None:
                            raise AssertionError(lab.output)
                        time.sleep(0.5)
                self.assertEqual(login.status_code, 200, login.text)
                guide = client.get("/api/load_guide", params={"example": "exported-lesson"})
                self.assertEqual(guide.status_code, 200, guide.text)
                self.assertEqual(guide.text, GUIDE)
                layout = client.get("/api/load_layout", params={"example": "exported-lesson"})
                self.assertEqual(layout.json()["layout"]["nodes"],
                                 {"r1": {"x": 500, "y": 200}, "h1": {"x": 100, "y": 600},
                                  "h2": {"x": 900, "y": 600}})
        finally:
            lab.finish()


    def test_native_export_runs_complete_both_modes_cli_and_web(self) -> None:
        """An external package imports its helper/data and serves its own assets."""
        conflict = tempfile.TemporaryDirectory(dir=CHECKOUT, prefix=".package-conflict-")
        self.addCleanup(conflict.cleanup)
        Path(conflict.name, "package_helper.py").write_text(
            "raise RuntimeError('checkout helper used')\n", encoding="utf-8")
        Path(conflict.name, "data").mkdir()
        Path(conflict.name, "data/input.bin").write_bytes(b"wrong checkout bytes")
        helper = (
            "from pathlib import Path\n"
            "def verify(net):\n"
            "    assert Path('data/input.bin').read_bytes() == b'\\x00\\xfflesson\\r\\n'\n"
            "    net.addHost('bundlehost')\n"
        )
        files = {
            "package_helper.py": {"encoding": "utf-8", "content": helper},
            "data/input.bin": {"encoding": "base64", "content": "AP9sZXNzb24NCg=="},
            "presentation/image.png": {"encoding": "base64", "content": "iVBORw0KGgr/AA==",
                                       "role": "presentation"},
        }
        canvas = {**self.draft, "lesson_files": files,
                  "code_section": "from package_helper import verify\nverify(net)\n"}
        canvas = self.repository.save(canvas["id"], canvas["revision"], canvas)
        script = Path(self.root, "exported-lesson.py").read_text().replace(
            "    return net", "    from package_helper import verify\n    verify(net)\n    return net")
        seeds = {str(path.relative_to(self.root)): path.read_text()
                 for path in Path(self.root, "frr-config").rglob("frr.conf")}
        python = self.repository.create_python(
            canvas["catalog"], script, seeds, GUIDE,
            self.repository.layout_for_runtime(canvas), lesson_files=files)
        for document in (canvas, python):
            for web in (False, True):
                with self.subTest(mode=document.get("kind", "canvas"), web=web):
                    with tempfile.TemporaryDirectory() as external:
                        with zipfile.ZipFile(io.BytesIO(package_archive(self.repository, document))) as archive:
                            archive.extractall(external)
                        root = os.path.join(external, "exported-lesson")
                        port = _free_port()
                        args = ("--enable-web", "--web-host", "127.0.0.1", "--web-port", str(port)) if web else ()
                        lab = UnpackedPackage(root, "exported-lesson.py", *args, env={
                            **self.env, "MNIPLAB_USERS": "teacher:Teach3r01:instructor",
                            "PYTHONPATH": conflict.name + os.pathsep + CHECKOUT})
                        try:
                            lab.expect("mininet-iplab>")
                            lab.run("nodes", "bundlehost")
                            lab.run("r1 vtysh -c 'show running-config'", "ospf router-id 9.9.9.9")
                            if web:
                                with httpx2.Client(base_url=f"http://127.0.0.1:{port}") as client:
                                    login = client.post("/api/login", auth=("teacher", "Teach3r01"))
                                    self.assertEqual(login.status_code, 200, login.text)
                                    guide = client.get("/api/load_guide", params={"example": "exported-lesson"})
                                    self.assertEqual(guide.text, GUIDE)
                                    layout = client.get("/api/load_layout", params={"example": "exported-lesson"})
                                    self.assertEqual(layout.json()["layout"]["nodes"]["r1"],
                                                     {"x": 500, "y": 200})
                        finally:
                            self.assertEqual(lab.finish(), 0, lab.output[-3000:])


class PythonDraftLabTests(LabCase):
    """A Python draft's Lab runs from its own folder, as Preview and the Catalog start it."""

    def setUp(self) -> None:
        subprocess.run([sys.executable, "-m", "mniplab", "cleanup"], cwd=CHECKOUT,
                       capture_output=True, check=False)
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.repository = AuthoringRepository(storage.name)
        self.root = storage.name

    def _copy(self, python=None, extra_route=""):
        """A Python draft of static-lab's generated script and Seeds, as Keep leaves one."""
        source = draft_from_shipped(self.repository, "static-lab")
        with tempfile.TemporaryDirectory() as package:
            script = os.path.join(package, "lab.py")
            self.repository.write_builder(source, script)
            with open(script, encoding="utf-8") as generated:
                python = python or generated.read()
            files = {}
            for directory, _, names in os.walk(os.path.join(package, "frr-config")):
                for name in names:
                    path = os.path.join(directory, name)
                    with open(path, encoding="utf-8") as seed:
                        files[os.path.relpath(path, package).replace(os.sep, "/")] = seed.read()
        files["frr-config/r1/frr.conf"] += extra_route
        return self.repository.create_python(
            {**source["catalog"], "name": f"{source['catalog']['name']} (Python)"},
            python, files, source["guide"], self.repository.layout_for_runtime(source))

    def test_downloaded_python_draft_runs_from_its_unpacked_folder(self) -> None:
        draft = self._copy()
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        with zipfile.ZipFile(io.BytesIO(package_archive(self.repository, draft))) as archive:
            archive.extractall(elsewhere.name)
        root = os.path.join(elsewhere.name, "static-routing-lab-python")
        lab = UnpackedPackage(root, "static-routing-lab-python.py",
                              env={**os.environ, "PYTHONPATH": CHECKOUT})
        try:
            lab.expect("mininet-iplab>")
            lab.run("h1 ping -c 3 -W 2 192.168.2.2", "bytes from 192.168.2.2")
        finally:
            self.assertEqual(lab.finish(), 0, lab.output[-3000:])


class SavedBundleLabTests(LabCase):
    """Both saved source modes run helper/data inputs outside the checkout."""

    def test_bundle_helper_and_data_override_conflicting_checkout_inputs(self) -> None:
        import asyncio
        from pathlib import Path
        from unittest.mock import patch
        from mniplab.labprocess import LabChild, LabStartError

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as storage, tempfile.TemporaryDirectory() as package:
                repository = AuthoringRepository(storage)
                # A checkout-side import search path with deliberately wrong input.
                # The package must take precedence, including its data working dir.
                with tempfile.TemporaryDirectory(dir=CHECKOUT, prefix=".bundle-conflict-") as conflict:
                    Path(conflict, "bundle_helper.py").write_text("raise RuntimeError('checkout helper used')\n")
                    Path(conflict, "hosts.txt").write_text("wrong1,wrong2")
                    for kind in ("python", "canvas"):
                        with self.subTest(kind=kind):
                            call = "from bundle_helper import add\nadd(net)\n"
                            if kind == "python":
                                draft = repository.create_python(
                                    {"name": "Bundle lesson", "description": "", "protocols": []},
                                    "from mniplab import mnIPLab\ndef build_network():\n"
                                    "    net = mnIPLab(topo=None, controller=None)\n"
                                    "    from bundle_helper import add\n    add(net)\n    return net\n",
                                    {}, "", {"version": 1, "nodes": {}})
                            else:
                                draft = repository.create()
                                draft["code_section"] = call
                                draft["topology"]["nodes"] = [host_node("h-canvas", "hc")]
                            draft["lesson_files"] = {
                                "bundle_helper.py": {"encoding": "utf-8", "content":
                                    "from pathlib import Path\ndef add(net):\n"
                                    "    first, second = Path('hosts.txt').read_text().split(',')\n"
                                    "    h1 = net.addHost(first)\n    h2 = net.addHost(second)\n"
                                    "    net.addLink(h1, h2, params1={'ip': '10.77.0.1/24'}, params2={'ip': '10.77.0.2/24'})\n"},
                                "hosts.txt": {"encoding": "utf-8", "content": "h1,h2"},
                            }
                            draft = repository.save(draft["id"], draft["revision"], draft)
                            root = os.path.join(package, kind)
                            script = os.path.join(root, "lab.py")
                            repository.write_builder(draft, script)
                            with patch.dict(os.environ, {"PYTHONPATH": conflict + os.pathsep + CHECKOUT}):
                                child = await LabChild.launch("bundle-lesson", script, cwd=root)
                                try:
                                    self.assertEqual({node['id'] for node in child.handle.topology()['nodes']},
                                                     {"h1", "h2"} | ({"hc"} if kind == "canvas" else set()))
                                    self.assertIn("1 received", child.handle.run_ping("h1", "10.77.0.2", 1)["output"])
                                finally:
                                    await child.stop()
                                # Fresh materialization after rename never sees the old data.
                                draft["lesson_files"]["renamed.txt"] = draft["lesson_files"].pop("hosts.txt")
                                draft = repository.save(draft["id"], draft["revision"], draft)
                                renamed = os.path.join(package, kind + "-renamed")
                                repository.write_builder(draft, os.path.join(renamed, "lab.py"))
                                with self.assertRaisesRegex(LabStartError, "hosts.txt"):
                                    await LabChild.launch("bundle-renamed", os.path.join(renamed, "lab.py"), cwd=renamed)
                                del draft["lesson_files"]["bundle_helper.py"]
                                draft = repository.save(draft["id"], draft["revision"], draft)
                                deleted = os.path.join(package, kind + "-deleted")
                                repository.write_builder(draft, os.path.join(deleted, "lab.py"))
                                with self.assertRaisesRegex(LabStartError, "No module named 'bundle_helper'"):
                                    await LabChild.launch("bundle-deleted", os.path.join(deleted, "lab.py"), cwd=deleted)
        asyncio.run(exercise())
