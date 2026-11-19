"""Shared scaffolding for the Lab Authoring API-tier tests.

The authoring HTTP fixtures, definition-shaped node and Pool builders, the
recording Lab launchers, and the shipped definitions prepared as drafts —
everything more than one test module crosses, so no test module imports
another.
"""

import asyncio
import os
import subprocess
import tempfile
import unittest
from typing import Any, Awaitable, Callable, Dict, List
from unittest.mock import patch

import httpx2
import pytest

from mniplab.labprocess import LabChild
from mniplab.persistent_config import ConfigCopies
from mniplab.tests.labs import EXAMPLES_DIR
from mniplab.tests.test_web_multi_lab import FakeLauncher
from mniplab.webserver import auth, create_app, registry
from mniplab.webserver.authoring import AuthoringRepository, shipped_definition
from mniplab.webserver.config import settings
from mniplab.webserver.groups import Groups


PREPARED = ("static-lab", "ospf-lab", "vlan-lab",
            "static-lab-nat", "nat-lab", "bgp-lab",
            "bgp-path-lab", "bgp-ispixp-lab", "bgp-isp-lab",
            "dhcp-lab", "dns-lab", "dnssec-lab", "bgp-dc-lab",
            "secure-bgp-lab")


def draft_from_shipped(repository: AuthoringRepository, example_id: str) -> dict:
    """Save a shipped definition as a new draft of ``repository``."""
    draft = repository.create()
    draft.update(shipped_definition(example_id, EXAMPLES_DIR))
    return repository.save(draft["id"], draft["revision"], draft)


def router_node(node_id, name, protocols=("OSPF",), frr_config="",
                startup=(), fast_timers=True):
    """A Router node in the current definition shape."""
    return {
        "id": node_id, "name": name, "type": "router", "protocols": list(protocols),
        "frr_config": frr_config, "fast_timers": fast_timers,
        "startup": list(startup),
    }


def host_node(node_id, name, ipv4_gateway="", ipv6_gateway="", startup=()):
    """A Host node in the current definition shape."""
    return {
        "id": node_id, "name": name, "type": "host", "ipv4_gateway": ipv4_gateway,
        "ipv6_gateway": ipv6_gateway, "startup": list(startup),
    }


def dhcp_pool(cidr, start, end, **options):
    """A DHCP Pool in the definition shape, with every option at its default."""
    pool = {"cidr": cidr, "start": start, "end": end, "lease_time": None,
            "preferred_lifetime": None, "router": "", "dns_server": "",
            "reservations": []}
    pool.update(options)
    return pool


class AuthoringAPIFixture(unittest.TestCase):
    """Real HTTP application over temporary authoring and Group storage."""

    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        for target in (
            "mniplab.webserver.api.authored_labs_root",
            "mniplab.webserver.repositories.authored_labs_root",
        ):
            patcher = patch(target, return_value=self.storage.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        previous_groups = registry.groups
        registry.groups = Groups(os.path.join(self.storage.name, "assignments.json"))
        self.addCleanup(setattr, registry, "groups", previous_groups)
        previous_copies = registry.config_copies
        registry.config_copies = ConfigCopies(
            os.path.join(self.storage.name, "group-config")
        )
        self.addCleanup(setattr, registry, "config_copies", previous_copies)
        self.previous_users = auth.settings.users
        auth.settings.users = (
            "learner1:lab12345:learner,"
            "instructor1:lab12345:instructor,instructor2:lab12345:instructor"
        )
        self.addCleanup(setattr, auth.settings, "users", self.previous_users)
        for username in ("learner1", "instructor1", "instructor2"):
            auth.login_limiter.reset(username)
            self.addCleanup(auth.login_limiter.reset, username)
        registry.lab_service.forget_all()
        self.addCleanup(registry.lab_service.forget_all)
        # The Lab handle boundary: each Router's running configuration by name.
        self.running_configs: dict = {}
        running = patch(
            "mniplab.labhandle.LocalLabHandle._op_get_running_config",
            lambda _handle, node: self.running_configs.get(
                node, f"frr version 10\nhostname {node}\n!\nend\n"
            ),
        )
        if getattr(self, "fake_running_configs", True):
            running.start()
            self.addCleanup(running.stop)

    def _as(self, role, operation):
        async def run():
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(
                    app=create_app(), raise_app_exceptions=False
                ), base_url="http://test"
            ) as client:
                username = "instructor1" if role == "instructor" else "learner1"
                login = await client.post("/api/login", auth=(username, "lab12345"))
                self.assertEqual(login.status_code, 200)
                await operation(client)
        asyncio.run(run())

    OSPF = "router ospf\n network 10.0.0.0/30 area 0\n"

    async def _router_draft(self, client, frr_config=OSPF, persistent_config=True):
        draft = (await client.post("/api/authoring/drafts", json={})).json()
        draft["address_family"] = "dual"
        draft["catalog"]["name"] = "Routed lesson"
        draft["catalog"]["area"] = "Intra-domain"
        draft["persistent_config"] = persistent_config
        draft["topology"] = {
            "nodes": [
                router_node("r", "r1", frr_config=frr_config, startup=[
                    {"stage": "before-daemons", "command": "ip addr add 10.9.9.9/32 dev lo",
                     "timeout": 5, "may_fail": False},
                ]),
                host_node("h", "h1", ipv4_gateway="10.0.0.1", ipv6_gateway="2001:db8::1"),
            ],
            "links": [{"id": "link-a", "ends": [
                {"node_id": "r", "interface_id": "if-000000000001", "port": 0,
                 "ipv4": "10.0.0.1/30", "ipv6": "2001:db8::1/64"},
                {"node_id": "h", "interface_id": "if-000000000002", "port": 0,
                 "ipv4": "10.0.0.2/30", "ipv6": "2001:db8::2/64"},
            ]}],
        }
        saved = await client.put(
            f"/api/authoring/drafts/{draft['id']}",
            json={"revision": draft["revision"], "document": draft},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    async def _dhcp_draft(self, client):
        """A Router serving one Subnet to a Host client, and one left unserved."""
        draft = (await client.post("/api/authoring/drafts", json={})).json()
        draft["address_family"] = "dual"
        draft["catalog"]["name"] = "DHCP lesson"
        draft["catalog"]["area"] = "DHCP"
        router = router_node("r", "r1", protocols=())
        router["dhcp"] = {"pools": [dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")]}
        draft["topology"] = {
            "nodes": [router, host_node("h", "h1"), host_node("u", "h3")],
            "links": [
                {"id": "served", "ends": [
                    {"node_id": "r", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "10.50.0.1/24", "ipv6": ""},
                    {"node_id": "h", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "", "ipv6": "", "dhcp4": True},
                ]},
                {"id": "unserved", "ends": [
                    {"node_id": "r", "interface_id": "if-000000000003", "port": 1,
                     "ipv4": "10.52.0.1/24", "ipv6": ""},
                    {"node_id": "u", "interface_id": "if-000000000004", "port": 0,
                     "ipv4": "", "ipv6": "", "dhcp4": True},
                ]},
            ],
        }
        return draft

    def _launch_with(self, mode, launcher):
        patchers = [
            patch.object(settings, "lab_mode", mode),
            patch.object(registry.lab_service, "launcher", launcher),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _buildable_draft(self, client, name="Two Host Lesson"):
        draft = (await client.post("/api/authoring/drafts", json={})).json()
        draft["address_family"] = "dual"
        draft["catalog"]["name"] = name
        draft["catalog"]["area"] = "Static"
        draft["topology"] = {
            "nodes": [
                {"id": "a", "name": "h1", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
                {"id": "b", "name": "h2", "type": "host",
                     "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
            ],
            "links": [{"id": "link-a", "ends": [
                {"node_id": "a", "interface_id": "if-000000000001", "port": 0,
                 "ipv4": "192.0.2.1/24", "ipv6": "2001:db8::1/64"},
                {"node_id": "b", "interface_id": "if-000000000002", "port": 0,
                 "ipv4": "192.0.2.2/24", "ipv6": "2001:db8::2/64"},
            ]}],
        }
        draft["layout"]["nodes"] = {
            "a": {"x": 100, "y": 300}, "b": {"x": 800, "y": 300}
        }
        saved = await client.put(
            f"/api/authoring/drafts/{draft['id']}",
            json={"revision": draft["revision"], "document": draft},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()


class RecordingLauncher(FakeLauncher):
    """Remember each builder's Seeds and the configuration copy it was given."""

    def __init__(self, warnings=None) -> None:
        super().__init__()
        self.calls: list = []
        self.warnings = warnings or []

    async def __call__(self, example_id, lab_file, config_copy=None, timeout=None, cwd: str | None = None):
        seeds: dict[str, dict[str, str]] = {}
        seed_root = os.path.join(os.path.dirname(lab_file), "frr-config")
        for name in sorted(os.listdir(seed_root)) if os.path.isdir(seed_root) else []:
            seeds[name] = {}
            for filename in os.listdir(os.path.join(seed_root, name)):
                with open(os.path.join(seed_root, name, filename), encoding="utf-8") as source:
                    seeds[name][filename] = source.read()
        with open(lab_file, encoding="utf-8") as source:
            builder = source.read()
        self.calls.append({"config_copy": config_copy, "seeds": seeds, "builder": builder})
        child = await super().__call__(example_id, lab_file, config_copy, timeout)
        child.startup_warnings = list(self.warnings)
        return child


class SourceRecordingLauncher(FakeLauncher):
    """A fake Lab process launcher that keeps each builder script it ran."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list = []

    async def __call__(self, example_id, lab_file, config_copy=None, timeout=None, cwd: str | None = None):
        with open(lab_file, encoding="utf-8") as source:
            self.sources.append(source.read())
        return await super().__call__(example_id, lab_file, config_copy, timeout, cwd)


class PackageRecordingLauncher(FakeLauncher):
    """Keeps what each Lab process would see: script, working directory and Seeds."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: list = []

    async def __call__(self, example_id, lab_file, config_copy=None, timeout=None, cwd=None):
        seeds = {}
        root = cwd or os.path.dirname(lab_file)
        for directory, _, names in os.walk(os.path.join(root, "frr-config")):
            for name in names:
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as seed:
                    seeds[os.path.relpath(path, root)] = seed.read()
        with open(lab_file, encoding="utf-8") as source:
            self.runs.append({"example": example_id, "source": source.read(), "cwd": cwd,
                              "lab_file": lab_file, "seeds": seeds, "config_copy": config_copy})
        return await super().__call__(example_id, lab_file, config_copy, timeout, cwd)


class SeedingLauncher(PackageRecordingLauncher):
    """Creates the Group's copy directory, as a Lab seeding a persistent Router does."""

    async def __call__(self, example_id, lab_file, config_copy=None, timeout=None, cwd=None):
        if config_copy is not None:
            os.makedirs(config_copy, exist_ok=True)
        return await super().__call__(example_id, lab_file, config_copy, timeout, cwd)


def root_interfaces() -> List[str]:
    """The interface names in the host's own network namespace."""
    output = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True,
                            check=True).stdout
    return sorted(line.split(":")[1].strip().split("@")[0] for line in output.splitlines())


class RealLabAuthoringFixture(AuthoringAPIFixture):
    """The authoring fixture with Previews started as real Lab processes."""

    pytestmark = [pytest.mark.integration, pytest.mark.requires_root]

    def _real_labs(self, mode: str) -> None:
        # A server has one event loop; each test here runs its own, and a
        # registry-wide lock contended in one loop is bound to it.
        for patcher in (patch.object(settings, "lab_mode", mode),
                        patch.object(registry.lab_service, "launcher", LabChild.launch),
                        patch.object(registry.lab_service, "lab_lock", asyncio.Lock()),
                        patch.object(registry.terminal_service, "terminals_lock", asyncio.Lock()),
                        patch.object(registry.capture_service, "_lock", asyncio.Lock())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, exercise: Callable[[httpx2.AsyncClient], Awaitable[None]]) -> None:
        """Run ``exercise`` as the Instructor, stopping every Lab it left running."""
        async def stopping(client: httpx2.AsyncClient) -> None:
            try:
                await exercise(client)
            finally:
                for lab in registry.lab_service.labs():
                    await registry.stop_lab(lab.key)
        self._as("instructor", stopping)

    async def _saved(self, client: httpx2.AsyncClient, topology: Dict[str, Any], *,
                     address_family: str = "ipv4",
                     persistent_config: bool = False) -> Dict[str, Any]:
        draft = (await client.post("/api/authoring/drafts", json={})).json()
        draft["catalog"]["name"] = "Acceptance scenario"
        draft["address_family"] = address_family
        draft["persistent_config"] = persistent_config
        draft["topology"] = topology
        saved = await client.put(f"/api/authoring/drafts/{draft['id']}",
                                 json={"revision": draft["revision"], "document": draft})
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    async def _changes(self, client: httpx2.AsyncClient, url: str) -> List[Dict[str, Any]]:
        """The supported runtime changes the draft's running Preview offers."""
        review = await client.get(f"{url}/keep")
        self.assertEqual(review.status_code, 200, review.text)
        return review.json()["features"]

    async def _keep(self, client: httpx2.AsyncClient, url: str, revision: int,
                       feature: Dict[str, Any]) -> httpx2.Response:
        """Keep one offered change exactly as it was reviewed."""
        return await client.post(f"{url}/keep", json={
            "revision": revision, "selections": [],
            "features": [{"id": feature["id"], "digest": feature["digest"]}]})
