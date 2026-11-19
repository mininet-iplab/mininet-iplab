"""Pytest marker handling and shared fixtures for Mininet-IPLab tests."""

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache

import pytest

from mniplab.tests.fakes import CommandRecorder, FakeIntf, FakeNode

KEYMGR_STUB = """#!/usr/bin/env python3
import sys
import os
import json

args = sys.argv[1:]
keys_dir = None

for i, arg in enumerate(args):
    if arg == "-D" and i + 1 < len(args):
        keys_dir = args[i + 1]
        break

if "list" in args:
    if keys_dir and not os.path.exists(os.path.join(keys_dir, "data.mdb")):
        print("[]")
    else:
        keys = [
            {"tag": 2371, "ksk": True, "zsk": False, "algorithm": 13, "size": 256, "id": "stub_ksk", "active": 12345},
            {"tag": 2372, "ksk": False, "zsk": True, "algorithm": 13, "size": 256, "id": "stub_zsk", "active": 12345}
        ]
        print(json.dumps(keys))
    sys.exit(0)

if "generate" in args:
    if keys_dir:
        os.makedirs(keys_dir, exist_ok=True)
        mdb_file = os.path.join(keys_dir, "data.mdb")
        if not os.path.exists(mdb_file):
            with open(mdb_file, "w", encoding="utf-8") as f:
                pass
    sys.exit(0)

if "ds" in args:
    zone = "example.com."
    for i, arg in enumerate(args):
        if arg == "ds" and i > 0 and args[i - 1] != keys_dir:
            zone = args[i - 1]
            break
    print(f"{zone} DS 2371 13 2 489d870428d0859c258d4a6549a1d13f9cbe8ba9065961d152c161ab7eb723ab")
    print(f"{zone} DS 2371 13 1 489d870428d0859c258d4a6549a1d13f9cbe8ba9")
    sys.exit(0)

if "nsec3-salt" in args:
    sys.exit(0)

sys.exit(0)
"""


from typing import Iterator


@pytest.fixture(autouse=True)
def _unit_test_environment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Enable daemon bypass and stubs for tests carrying the unit mark."""
    if request.node.get_closest_marker("unit") is not None:
        monkeypatch.setenv("MNIPLAB_SKIP_DAEMON_PROBES", "1")

        if shutil.which("keymgr") is None:
            stubs_dir = tempfile.mkdtemp(prefix="mniplab_test_stubs_")
            keymgr_path = os.path.join(stubs_dir, "keymgr")
            with open(keymgr_path, "w", encoding="utf-8") as f:
                f.write(KEYMGR_STUB)
            os.chmod(keymgr_path, 0o755)
            monkeypatch.setenv("PATH", f"{stubs_dir}:{os.environ.get('PATH', '')}")
            try:
                yield
            finally:
                shutil.rmtree(stubs_dir, ignore_errors=True)
            return

    yield


@pytest.fixture
def command_recorder() -> CommandRecorder:
    """Return a recorder capturing commands a node would execute."""
    return CommandRecorder()


@pytest.fixture
def fake_node(command_recorder: CommandRecorder) -> FakeNode:
    """Return a fake node backed by the shared command recorder."""
    return FakeNode('h1', command_recorder)


@pytest.fixture
def fake_intf(fake_node: FakeNode) -> FakeIntf:
    """Return a fake interface attached to the shared fake node."""
    intf = FakeIntf('h1-eth0', fake_node)
    fake_node.intfs[0] = intf
    return intf


def _run_probe(command: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


@lru_cache(maxsize=1)
def _has_root_networking() -> tuple[bool, str]:
    """Return whether root Mininet/netlink integration tests can run."""
    if os.geteuid() != 0:
        return False, "requires root privileges"

    if not shutil.which("ip"):
        return False, "requires the iproute2 'ip' command"

    if not shutil.which("mn"):
        return False, "requires Mininet's 'mn' command"

    frr_init = os.environ.get("FRR_INIT_SCRIPT", "/usr/lib/frr/frrinit.sh")
    if not os.path.exists(frr_init):
        return False, f"requires FRR init script at {frr_init}"

    # Interface names are capped at 15 characters (IFNAMSIZ). "mniplab-pytest-"
    # is already 15, so appending a pid overflowed and the probe failed on every
    # machine — silently skipping the whole privileged suite.
    probe_name = f"mnp{os.getpid() % 100000}"
    try:
        add_proc = _run_probe(["ip", "link", "add", probe_name, "type", "dummy"])
    except (OSError, subprocess.SubprocessError):
        return False, "requires CAP_NET_ADMIN/netlink access"

    if add_proc.returncode != 0:
        return False, "requires CAP_NET_ADMIN/netlink access"

    _run_probe(["ip", "link", "del", probe_name])
    return True, ""


@lru_cache(maxsize=1)
def _has_docker() -> tuple[bool, str]:
    """Return whether Docker-backed integration tests can run."""
    if not shutil.which("docker"):
        return False, "requires Docker CLI"

    try:
        proc = _run_probe(["docker", "info"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False, "requires a reachable Docker daemon"

    if proc.returncode != 0:
        return False, "requires a reachable Docker daemon"

    return True, ""


@lru_cache(maxsize=1)
def _has_ovs() -> tuple[bool, str]:
    """Return whether switch-based Labs can be built (see ``addSwitch``)."""
    from mniplab.errors import NetworkError
    from mniplab.kernel import ensure_ovs_available

    try:
        ensure_ovs_available()
    except NetworkError as exc:
        return False, f"requires Open vSwitch ({str(exc).splitlines()[0]})"
    return True, ""


@lru_cache(maxsize=1)
def _has_browser() -> tuple[bool, str]:
    """Return whether Playwright's Chromium is installed and launchable."""
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError:
        return False, "requires the playwright package (pip install playwright)"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser.close()
    except PlaywrightError as exc:
        return False, f"requires 'playwright install chromium' ({exc})"

    return True, ""


BROWSER_AREAS = ("browser_authoring", "browser_services", "browser_classroom", "browser_workspace")


def _browser_area_errors(items) -> list:
    """Name each browser test without exactly one area, and each other test with one."""
    errors = []
    for item in items:
        areas = sorted({mark.name for mark in item.iter_markers() if mark.name in BROWSER_AREAS})
        if item.get_closest_marker("browser") is None:
            if areas:
                errors.append(f"{item.nodeid}: not a browser test, but marked {', '.join(areas)}")
        elif len(areas) != 1:
            errors.append(f"{item.nodeid}: a browser test needs exactly one of "
                          f"{', '.join(BROWSER_AREAS)}; it has {', '.join(areas) or 'none'}")
    return errors


def pytest_collection_modifyitems(config, items):
    """Check browser areas, add unit markers to unmarked tests and skip unavailable ones."""
    del config

    errors = _browser_area_errors(items)
    if errors:
        raise pytest.UsageError("\n".join(errors))

    for item in items:
        if (
            item.get_closest_marker("integration") is None
            and item.get_closest_marker("browser") is None
        ):
            item.add_marker(pytest.mark.unit)

    needs_root = any(item.get_closest_marker("requires_root") for item in items)
    root_ok, root_reason = _has_root_networking() if needs_root else (True, "")

    needs_docker = any(item.get_closest_marker("requires_docker") for item in items)
    docker_ok, docker_reason = _has_docker() if needs_docker else (True, "")

    needs_ovs = any(item.get_closest_marker("requires_ovs") for item in items)
    ovs_ok, ovs_reason = _has_ovs() if needs_ovs else (True, "")

    needs_browser = any(item.get_closest_marker("requires_browser") for item in items)
    browser_ok, browser_reason = _has_browser() if needs_browser else (True, "")

    for item in items:
        if item.get_closest_marker("requires_root") and not root_ok:
            item.add_marker(pytest.mark.skip(reason=root_reason))

        if item.get_closest_marker("requires_docker") and not docker_ok:
            item.add_marker(pytest.mark.skip(reason=docker_reason))

        if item.get_closest_marker("requires_ovs") and not ovs_ok:
            item.add_marker(pytest.mark.skip(reason=ovs_reason))

        if item.get_closest_marker("requires_browser") and not browser_ok:
            item.add_marker(pytest.mark.skip(reason=browser_reason))
