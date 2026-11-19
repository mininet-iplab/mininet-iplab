"""Web-tier tests for Multi-Lab Mode: Groups, the cap, ownership, Assignments.

Seam B, over the test client against the fake Lab.  The one boundary faked
is the child-process launch: the lifecycle service is handed a launcher
that answers with a :class:`FakeLabChild` wrapping a :class:`FakeLab`, so
every classroom rule — who may start what, who sees which Lab, who may
stop it, what a dead child looks like — is pinned by what the browser gets.
"""

import asyncio
import itertools
import os
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from mniplab.labhandle import LocalLabHandle
from mniplab.tests.fakes import (
    FakeLab,
    FakeNode,
    FakeTerminalCloserMixin,
    LoginClientMixin,
)
from mniplab.webserver import registry
from mniplab.webserver.config import settings
from mniplab.webserver.groups import Groups


class FakeLabChild:
    """A Lab process that never forks: a fake Lab behind a local handle.

    Stands in for ``LabChild`` at the one boundary these tests fake.  The
    test ends its process with :meth:`die`, the way a crash would.

    Attributes:
        key: The Lab's key.
        example: The Lab Example id it runs.
        handle: The handle the services call.
        stopped: Whether the manager asked it to stop.
        swept: Whether the manager swept its key.
        startup_warnings: Failures of Startup commands the Lab let pass.
    """

    def __init__(self, key: str, example: str, lab: FakeLab) -> None:
        self.key = key
        self.example = example
        self.handle = LocalLabHandle(lab, example=example, key=key)
        self.prompt_fd: Optional[int] = None
        self.exit_reason: Optional[str] = None
        self.stopped = False
        self.swept = False
        self.startup_warnings: List[str] = []
        self._ended = threading.Event()

    @property
    def alive(self) -> bool:
        return not self._ended.is_set()

    def die(self, reason: str) -> None:
        """End the process on its own, as a crash does."""
        self.exit_reason = reason
        self._ended.set()

    async def wait(self) -> str:
        while not self._ended.is_set():
            await asyncio.sleep(0.01)
        assert self.exit_reason is not None
        return self.exit_reason

    async def stop(self, timeout: float = 0) -> None:
        del timeout
        self.stopped = True
        self.die("stopped")
        await self.sweep()

    async def sweep(self) -> None:
        self.swept = True


class FakeLauncher:
    """Launches fake Lab processes, one key each, and remembers them.

    Attributes:
        children: Every child launched, in order.
    """

    def __init__(self) -> None:
        self.children: List[FakeLabChild] = []
        self._keys = (f"k{n}" for n in itertools.count(1))

    async def __call__(
        self,
        example_id: str,
        lab_file: str,
        config_copy: Optional[str] = None,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
    ) -> FakeLabChild:
        del lab_file, config_copy, timeout, cwd
        lab = FakeLab(
            FakeNode.with_addresses("r1", "10.0.0.1/24"),
            FakeNode.with_addresses("r2", "10.0.0.2/24"),
            lab_key=next(self._keys),
        )
        child = FakeLabChild(lab.lab_key, example_id, lab)
        self.children.append(child)
        return child


class MultiLabModeMixin:
    """Test-case mixin running the server in Multi-Lab Mode for one test.

    Labs start as fake processes through :class:`FakeLauncher`, and no Lab
    is left alive for the next test.

    Attributes:
        launcher: The fake launcher, holding every child it started.
    """

    launcher: FakeLauncher

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)  # type: ignore[attr-defined]
        self.launcher = FakeLauncher()
        launcher = patch.object(registry.lab_service, "launcher", self.launcher)
        launcher.start()
        self.addCleanup(launcher.stop)  # type: ignore[attr-defined]
        registry.lab_service.forget_all()
        self.addCleanup(registry.lab_service.forget_all)  # type: ignore[attr-defined]

    def start(self, example_id: str, group: Optional[str] = None) -> Any:
        """Start a Lab Example as the signed-in account and return the answer."""
        body: Dict = {"example_id": example_id}
        if group is not None:
            body["group"] = group
        return self.client.post("/api/labs", json=body)  # type: ignore[attr-defined]


@pytest.mark.unit
class ModeInMeTests(MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The browser learns the mode, its Group and the cap from /api/me."""

    def test_me_names_the_mode_the_group_and_the_cap(self) -> None:
        self.open_session(role="learner")

        me = self.client.get("/api/me").json()

        self.assertEqual(me["mode"], "multi")
        self.assertEqual(me["group"], "learner1")
        self.assertEqual(me["cap"], settings.lab_cap)


@pytest.mark.unit
class AssignmentTests(MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Instructor edits each Group's Assignment; a Group sees only what is assigned."""

    def setUp(self) -> None:
        super().setUp()
        self.assignments_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.assignments_dir.cleanup)
        self.assignments_file = os.path.join(self.assignments_dir.name, "assignments.json")
        self.use_assignments_file(self.assignments_file)

    def use_assignments_file(self, path: str) -> None:
        """Point the manager's Groups at ``path``, as a server start would."""
        previous = registry.groups
        registry.groups = Groups(path)
        self.addCleanup(setattr, registry, "groups", previous)

    def test_a_group_sees_and_the_instructor_sees_all(self) -> None:
        self.open_session(role="instructor")
        saved = self.client.put("/api/admin/assignments/learner1",
                                json={"example_ids": ["ospf-lab", "static-lab"]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), {"group": "learner1", "example_ids": ["ospf-lab", "static-lab"]})
        instructor_sees = {e["id"] for e in self.client.get("/api/examples").json()["examples"]}
        self.assertIn("bgp-lab", instructor_sees)

        self.open_session(role="learner")  # learner1
        learner1_sees = [e["id"] for e in self.client.get("/api/examples").json()["examples"]]
        self.sign_in(username="learner2", password="lab54321")
        learner2_sees = [e["id"] for e in self.client.get("/api/examples").json()["examples"]]

        self.assertEqual(learner1_sees, ["ospf-lab", "static-lab"])
        self.assertEqual(learner2_sees, [])

    def test_an_assignment_names_only_lab_examples_in_the_catalog(self) -> None:
        self.open_session(role="instructor")

        response = self.client.put("/api/admin/assignments/learner1",
                                   json={"example_ids": ["ospf-lab", "no-such-lab"]})

        self.assertEqual(response.status_code, 400)
        self.assertIn("no-such-lab", response.json()["detail"])

    def test_an_assignment_names_only_groups_that_can_log_in(self) -> None:
        self.open_session(role="instructor")

        response = self.client.put("/api/admin/assignments/nobody", json={"example_ids": []})

        self.assertEqual(response.status_code, 404)

    def test_assignments_survive_a_server_restart(self) -> None:
        self.open_session(role="instructor")
        self.client.put("/api/admin/assignments/learner2", json={"example_ids": ["dns-lab"]})

        self.use_assignments_file(self.assignments_file)  # a fresh manager over the same file
        listed = self.client.get("/api/admin/assignments").json()

        self.assertEqual(listed["assignments"]["learner2"], ["dns-lab"])
        self.assertEqual(listed["assignments"]["learner1"], [])
        self.assertEqual({g["name"]: g["role"] for g in listed["groups"]},
                         {"learner1": "learner", "learner2": "learner", "instructor1": "instructor"})

    def test_only_the_instructor_edits_or_lists_assignments(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.client.get("/api/admin/assignments").status_code, 403)
        self.assertEqual(
            self.client.put("/api/admin/assignments/learner1", json={"example_ids": []}).status_code,
            403,
        )

    def test_the_single_user_fallback_assigns_every_lab_example(self) -> None:
        from mniplab.webserver import auth
        with patch.object(auth.settings, "users", ""), \
             patch.object(auth.settings, "username", "solo"), \
             patch.object(auth.settings, "password", "solo12345"):
            auth.login_limiter.reset("solo")
            self.sign_in(username="solo", password="solo12345")
            solo_sees = [e["id"] for e in self.client.get("/api/examples").json()["examples"]]
            listed = self.client.get("/api/admin/assignments").json()

        self.assertIn("ospf-lab", solo_sees)
        self.assertEqual(listed["assignments"], {"solo": solo_sees})


@pytest.mark.unit
class TwoGroupsTests(MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Two Groups on one Lab Example each see only their own Lab."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session(role="instructor")
        for group in ("learner1", "learner2"):
            self.client.put(f"/api/admin/assignments/{group}", json={"example_ids": ["ospf-lab"]})

    def test_each_group_sees_its_own_lab_and_the_instructor_sees_both(self) -> None:
        self.open_session(role="learner")
        started1 = self.start("ospf-lab")
        self.sign_in(username="learner2", password="lab54321")
        started2 = self.start("ospf-lab")

        self.assertEqual((started1.status_code, started2.status_code), (200, 200))
        key1, key2 = started1.json()["key"], started2.json()["key"]
        self.assertNotEqual(key1, key2)

        # learner2 is signed in: their list, status and Topology are their Lab's.
        rows = self.client.get("/api/labs").json()["labs"]
        self.assertEqual([(r["key"], r["group"], r["example"]) for r in rows],
                         [(key2, "learner2", "ospf-lab")])
        status = self.client.get("/api/status").json()
        self.assertEqual((status["running"], status["key"], status["group"]), (True, key2, "learner2"))
        self.assertEqual(self.client.get(f"/api/labs/{key2}/topology").status_code, 200)
        self.assertEqual(self.client.get(f"/api/labs/{key2}").json()["group"], "learner2")
        # r1 reads r1 in both Labs.
        names = {n["id"] for n in self.client.get(f"/api/labs/{key2}/topology").json()["nodes"]}
        self.assertEqual(names, {"r1", "r2"})

        # The other Group's Lab is not theirs to see.
        other = self.client.get(f"/api/labs/{key1}/topology")
        self.assertEqual(other.status_code, 404)
        self.assertEqual(other.json()["detail"], f"No Lab with key '{key1}' is running")
        self.assertEqual(self.client.get(f"/api/labs/{key1}").status_code, 404)

        # A Group with no Lab of its own is refused just the same.
        self.open_session(role="instructor")
        self.client.post(f"/api/labs/{key2}/stop")
        self.sign_in(username="learner2", password="lab54321")
        self.assertEqual(self.client.get(f"/api/labs/{key1}/topology").status_code, 404)
        self.assertEqual(self.client.get(f"/api/labs/{key1}/topology").json()["detail"],
                         f"No Lab with key '{key1}' is running")

        self.open_session(role="instructor")
        seen = {(r["key"], r["group"]) for r in self.client.get("/api/labs").json()["labs"]}
        self.assertEqual(seen, {(key1, "learner1")})
        self.assertEqual(self.client.get(f"/api/labs/{key1}/topology").status_code, 200)


@pytest.mark.unit
class StartRulesTests(MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """A Group holds one Lab at a time; the cap refuses everyone alike."""

    def setUp(self) -> None:
        super().setUp()
        cap = patch.object(settings, "lab_cap", 2)
        cap.start()
        self.addCleanup(cap.stop)
        self.open_session(role="instructor")
        for group in ("learner1", "learner2"):
            self.client.put(f"/api/admin/assignments/{group}", json={"example_ids": ["ospf-lab", "static-lab"]})

    def test_a_groups_second_start_is_refused_naming_its_lab(self) -> None:
        self.open_session(role="learner")
        first = self.start("ospf-lab").json()

        second = self.start("static-lab")

        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            second.json()["detail"],
            f"Group 'learner1' already runs Lab 'ospf-lab' ({first['key']}); stop it first",
        )

    def test_the_cap_refuses_a_group_and_the_instructor_with_the_same_message(self) -> None:
        self.open_session(role="learner")
        self.assertEqual(self.start("ospf-lab").status_code, 200)
        self.sign_in(username="learner2", password="lab54321")
        self.assertEqual(self.start("ospf-lab").status_code, 200)

        self.open_session(role="instructor")
        instructor_refused = self.start("static-lab")
        self.open_session(role="learner")
        self.client.post(f"/api/labs/{self.launcher.children[0].key}/stop")
        self.sign_in(username="learner2", password="lab54321")  # still holds a Lab
        self.assertEqual(self.start("static-lab").status_code, 409)  # its own, not the cap
        self.open_session(role="learner")
        self.assertEqual(self.start("static-lab").status_code, 200)  # the freed slot
        self.open_session(role="instructor")
        again = self.start("static-lab")

        self.assertEqual(instructor_refused.status_code, 409)
        self.assertEqual(instructor_refused.json()["detail"], "Lab cap reached: 2 of 2 Labs running")
        self.assertEqual(again.json()["detail"], "Lab cap reached: 2 of 2 Labs running")

    def test_a_group_starts_only_what_is_assigned(self) -> None:
        self.open_session(role="learner")

        refused = self.start("bgp-lab")

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["detail"],
                         "Lab Example 'bgp-lab' is not in Group 'learner1's Assignment")
        self.assertEqual(self.launcher.children, [])

    def test_the_instructor_too_starts_a_group_only_on_its_assignment(self) -> None:
        refused = self.start("bgp-lab", group="learner1")
        own = self.start("bgp-lab")

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(own.status_code, 200)

    def test_the_instructor_starts_a_lab_for_a_group_and_the_group_sees_it(self) -> None:
        started = self.start("ospf-lab", group="learner2")

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["group"], "learner2")
        self.sign_in(username="learner2", password="lab54321")
        self.assertEqual(self.client.get("/api/status").json()["key"], started.json()["key"])
        self.open_session(role="learner")
        self.assertFalse(self.client.get("/api/status").json()["running"])

    def test_only_the_instructor_starts_for_another_group(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.start("ospf-lab", group="learner2").status_code, 403)
        self.assertEqual(self.launcher.children, [])


@pytest.mark.unit
class StopRulesTests(MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """A Group stops its own Lab; the Instructor stops any."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session(role="instructor")
        for group in ("learner1", "learner2"):
            self.client.put(f"/api/admin/assignments/{group}", json={"example_ids": ["ospf-lab"]})
        self.open_session(role="learner")
        self.key1 = self.start("ospf-lab").json()["key"]
        self.sign_in(username="learner2", password="lab54321")
        self.key2 = self.start("ospf-lab").json()["key"]

    def test_a_group_stops_its_own_lab_but_not_anothers(self) -> None:
        # learner2 is signed in.
        other = self.client.post(f"/api/labs/{self.key1}/stop")
        own = self.client.post(f"/api/labs/{self.key2}/stop")

        self.assertEqual(other.status_code, 404)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json()["example"], "ospf-lab")
        self.assertFalse(self.client.get("/api/status").json()["running"])
        self.assertTrue(self.launcher.children[1].stopped)
        self.assertFalse(self.launcher.children[0].stopped)

    def test_the_instructor_stops_any_groups_lab(self) -> None:
        self.open_session(role="instructor")

        self.assertEqual(self.client.post(f"/api/labs/{self.key1}/stop").status_code, 200)
        self.assertEqual(self.client.post(f"/api/labs/{self.key2}/stop").status_code, 200)
        self.assertEqual(self.client.get("/api/labs").json()["labs"], [])

    def test_stopping_the_server_stops_every_lab(self) -> None:
        stopped = asyncio.run(registry.stop_all())

        self.assertEqual({r["key"] for r in stopped}, {self.key1, self.key2})
        self.assertTrue(all(child.stopped for child in self.launcher.children))
        self.open_session(role="instructor")
        self.assertEqual(self.client.get("/api/labs").json()["labs"], [])

    def test_unassigning_a_running_labs_example_leaves_it_running(self) -> None:
        self.open_session(role="instructor")
        self.client.put("/api/admin/assignments/learner1", json={"example_ids": []})

        self.open_session(role="learner")
        status = self.client.get("/api/status").json()

        self.assertEqual((status["running"], status["key"]), (True, self.key1))
        self.assertEqual(self.client.get("/api/examples").json()["examples"], [])
        self.assertFalse(self.launcher.children[0].stopped)


@pytest.mark.unit
class DeadChildTests(MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """A Lab whose process dies is stopped, swept, and shown with its reason."""

    def test_a_dead_child_shows_as_stopped_with_its_reason(self) -> None:
        self.open_session(role="instructor")
        self.client.put("/api/admin/assignments/learner1", json={"example_ids": ["ospf-lab"]})
        with self.client:  # keeps the server's loop, and the watcher, alive between requests
            self.open_session(role="learner")
            key = self.start("ospf-lab").json()["key"]
            child = self.launcher.children[0]

            child.die("killed by SIGKILL")
            for _ in range(200):
                status = self.client.get("/api/status").json()
                if not status["running"]:
                    break
                time.sleep(0.01)

            self.assertFalse(status["running"])
            self.assertEqual(status["last_exit"]["reason"], "killed by SIGKILL")
            self.assertEqual(status["last_exit"]["key"], key)
            self.assertTrue(child.swept)
            stale = self.client.get(f"/api/labs/{key}/topology")
            self.assertEqual(stale.status_code, 404)
            self.assertEqual(stale.json()["detail"],
                             f"Lab 'ospf-lab' ({key}) is not running: its process killed by SIGKILL")
            # The Group may start again.
            self.assertEqual(self.start("ospf-lab").status_code, 200)


@pytest.mark.unit
class LabFeedOwnershipTests(MultiLabModeMixin, unittest.IsolatedAsyncioTestCase):
    """A Group's WebSockets reach its own Lab and never another Group's."""

    def setUp(self) -> None:
        super().setUp()

    async def test_a_learners_lab_prompt_is_refused_and_the_instructors_is_not(self) -> None:
        """The Lab Prompt is the Instructor's, whosever Lab it is.

        A Learner is refused by role before ownership is reached, so the
        cross-Group case it used to cover no longer arises here; ownership
        of a Lab is still pinned on the Terminal and Topology feeds.
        """
        from mniplab.tests.test_web_prompt import FakePromptWebSocket
        from mniplab.webserver.auth import create_session
        from mniplab.webserver.websocket import PromptWebSocketHandler

        started = await registry.start_lab_from_builder("ospf-lab", "unused", group="learner1")
        key = started["key"]
        master_fd, slave_fd = os.openpty()
        self.addCleanup(os.close, slave_fd)
        registry.prompt(key).attach(master_fd)
        self.addCleanup(registry.prompt(key).detach)

        other = FakePromptWebSocket(cookies={"session": create_session("learner2", "learner")})
        await PromptWebSocketHandler(other, key).run()  # type: ignore[arg-type]
        instructor = FakePromptWebSocket(cookies={"session": create_session("instructor1", "instructor")})
        await PromptWebSocketHandler(instructor, key).run()  # type: ignore[arg-type]

        self.assertEqual(
            other.sent,
            [{
                "type": "error",
                "message": "The Lab Prompt is the Instructor's; use a Node Terminal",
            }],
        )
        self.assertTrue(other.closed)
        self.assertNotIn("error", {m.get("type") for m in instructor.sent})


@pytest.mark.unit
class DashboardTests(MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Instructor's dashboard: every Group with its Lab, every Lab with its key."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session(role="instructor")
        self.client.put("/api/admin/assignments/learner1", json={"example_ids": ["ospf-lab"]})
        self.client.put("/api/admin/assignments/learner2", json={"example_ids": ["ospf-lab", "dns-lab"]})

    def test_the_dashboard_lists_groups_with_their_lab_and_labs_with_their_key(self) -> None:
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.open_session(role="instructor")

        dashboard = self.client.get("/api/admin/dashboard").json()

        self.assertEqual((dashboard["mode"], dashboard["cap"]), ("multi", settings.lab_cap))
        groups = {g["name"]: g for g in dashboard["groups"]}
        self.assertEqual(groups["learner1"]["lab"]["key"], key)
        self.assertEqual(groups["learner1"]["assignment"], ["ospf-lab"])
        self.assertIsNone(groups["learner2"]["lab"])
        self.assertEqual(groups["learner2"]["assignment"], ["ospf-lab", "dns-lab"])
        self.assertEqual(groups["instructor1"]["role"], "instructor")
        (lab,) = dashboard["labs"]
        self.assertEqual((lab["key"], lab["group"], lab["example"], lab["active_terminals"]),
                         (key, "learner1", "ospf-lab", 0))
        self.assertIn("uptime", lab)

    def test_the_dashboard_shows_a_dead_lab_as_stopped_with_its_reason(self) -> None:
        with self.client:
            self.open_session(role="learner")
            key = self.start("ospf-lab").json()["key"]
            self.launcher.children[0].die("exited with code 1")
            self.open_session(role="instructor")
            for _ in range(200):
                dashboard = self.client.get("/api/admin/dashboard").json()
                if not dashboard["labs"]:
                    break
                time.sleep(0.01)

        groups = {g["name"]: g for g in dashboard["groups"]}
        self.assertIsNone(groups["learner1"]["lab"])
        self.assertEqual(groups["learner1"]["last_exit"]["reason"], "exited with code 1")
        self.assertEqual(groups["learner1"]["last_exit"]["key"], key)

    def test_the_dashboard_is_the_instructors(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.client.get("/api/admin/dashboard").status_code, 403)

    def test_dashboard_resolves_catalog_and_preview_display_names(self) -> None:
        """Names accompany IDs, including a running Preview's draft name."""
        self.open_session(role="learner")
        self.start("ospf-lab")
        lab = registry.lab_service.lab_of("learner1")
        assert lab is not None
        lab.example = "preview-draft1"
        lab.preview = True
        self.open_session(role="instructor")
        with patch("mniplab.webserver.api.load_labs_manifest", return_value=[
            {"id": "ospf-lab", "name": "OSPF Routing Lab"},
            {"id": "authored-lesson", "name": "Published BGP Lesson"},
        ]), patch("mniplab.webserver.repositories.AuthoringRepository") as repository:
            repository.return_value.read.return_value = {
                "catalog": {"name": "Preview BGP Lesson"}, "guide": ""
            }
            dashboard = self.client.get("/api/admin/dashboard").json()

        self.assertEqual(dashboard["example_names"], {
            "ospf-lab": "OSPF Routing Lab",
            "authored-lesson": "Published BGP Lesson",
            "preview-draft1": "Preview BGP Lesson",
        })
        self.assertEqual(dashboard["labs"][0]["example"], "preview-draft1")
        self.assertFalse(dashboard["labs"][0]["has_guide"])
