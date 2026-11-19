"""Web-tier tests for Persistent Config per Group: seed, copy, resume, reset.

Seam B, over the test client against the fake Lab.  The one boundary faked
is the child-process launch: the launcher stands in for a Lab process
whose Routers declare a seed, and seeds the Group's copy the way
``addRouter`` does in a real one.  What a Learner and the Instructor can
see — the files under the copies' root, the seed, the catalog rows, the
dashboard and the refusals — is what the tests read.
"""

import itertools
import os
import tempfile
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from mniplab.persistent_config import ConfigCopies, seed_router_copy
from mniplab.tests.fakes import FakeLab, FakeNode, FakeTerminalCloserMixin, LoginClientMixin
from mniplab.tests.test_web_multi_lab import FakeLabChild
from mniplab.webserver import registry
from mniplab.webserver.config import settings
from mniplab.webserver.groups import Groups

ROUTERS = ("r1", "r2")


class SeedingLauncher:
    """Launches fake Lab processes whose Routers may declare a seed.

    A Lab Example named in ``seeds`` is one with Persistent Config: its
    Routers ``r1`` and ``r2`` declare that seed template, and a process
    launched for a Group seeds the Group's copy from it exactly as
    ``addRouter`` does.  Any other Lab Example is Ephemeral Config and
    writes nothing.

    Attributes:
        children: Every child launched, in order.
        copies: The copy directory each launch was handed, in order.
    """

    def __init__(self, seeds: Dict[str, str]) -> None:
        self.seeds = seeds
        self.children: List[FakeLabChild] = []
        self.copies: List[Optional[str]] = []
        self._keys = (f"k{n}" for n in itertools.count(1))

    async def __call__(
        self,
        example_id: str,
        lab_file: str,
        config_copy: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> FakeLabChild:
        del lab_file, timeout
        self.copies.append(config_copy)
        seed = self.seeds.get(example_id)
        if seed is not None and config_copy is not None:
            for name in ROUTERS:
                seed_router_copy(config_copy, seed.replace("%(name)s", name), name)
        lab = FakeLab(
            FakeNode.with_addresses("r1", "10.0.0.1/24"),
            FakeNode.with_addresses("r2", "10.0.0.2/24"),
            lab_key=next(self._keys),
        )
        child = FakeLabChild(lab.lab_key, example_id, lab)
        self.children.append(child)
        return child


class PersistentConfigCase(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Multi-Lab Mode with a seed for ``ospf-lab`` and copies under a temp root."""

    def setUp(self) -> None:
        super().setUp()
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seed_root = os.path.join(self.tmp.name, "frr-config", "ospf-lab")
        for name in ROUTERS:
            os.makedirs(os.path.join(self.seed_root, name))
            self.write(os.path.join(self.seed_root, name, "frr.conf"), "instructor-authored\n")
        self.launcher = SeedingLauncher({"ospf-lab": os.path.join(self.seed_root, "%(name)s")})
        launcher = patch.object(registry.lab_service, "launcher", self.launcher)
        launcher.start()
        self.addCleanup(launcher.stop)
        self.copies = ConfigCopies(os.path.join(self.tmp.name, "group-config"))
        for attr, value in (
            ("groups", Groups(os.path.join(self.tmp.name, "assignments.json"))),
            ("config_copies", self.copies),
        ):
            previous = getattr(registry, attr)
            setattr(registry, attr, value)
            self.addCleanup(setattr, registry, attr, previous)
        registry.lab_service.forget_all()
        self.addCleanup(registry.lab_service.forget_all)
        registry.groups.assign("learner1", ["ospf-lab", "dns-lab"])
        registry.groups.assign("learner2", ["ospf-lab"])

    @staticmethod
    def write(path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def read(path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def start(self, example_id: str, clean: bool = False) -> Any:
        body: Dict = {"example_id": example_id}
        if clean:
            body["clean"] = True
        return self.client.post("/api/labs", json=body)

    def stop(self, key: str) -> None:
        self.assertEqual(self.client.post(f"/api/labs/{key}/stop").status_code, 200)

    def copy_conf(self, group: str, name: str = "r1") -> str:
        return os.path.join(self.copies.copy_dir(group, "ospf-lab"), name, "frr.conf")


@pytest.mark.unit
class SeedAndResumeTests(PersistentConfigCase):
    """A Group's first start seeds its copy; later starts resume it."""

    def test_the_first_start_seeds_the_groups_copy_from_the_seed(self) -> None:
        self.open_session(role="learner")  # learner1

        started = self.start("ospf-lab")

        self.assertEqual(started.status_code, 200)
        self.assertEqual(self.launcher.copies, [self.copies.copy_dir("learner1", "ospf-lab")])
        self.assertEqual(self.read(self.copy_conf("learner1")), "instructor-authored\n")
        self.assertEqual(self.read(self.copy_conf("learner1", "r2")), "instructor-authored\n")

    def test_write_memory_survives_a_stop_and_the_next_start_resumes_it(self) -> None:
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.write(self.copy_conf("learner1"), "written by learner1\n")  # `write memory`
        self.stop(key)

        again = self.start("ospf-lab")

        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.read(self.copy_conf("learner1")), "written by learner1\n")
        self.assertEqual(self.read(os.path.join(self.seed_root, "r1", "frr.conf")),
                         "instructor-authored\n")


@pytest.mark.unit
class FailedStartTests(PersistentConfigCase):
    """A copy seeded by a build that never became a Lab is not kept."""

    def test_a_first_start_that_fails_leaves_no_copy_to_resume(self) -> None:
        from mniplab.labprocess import LabStartError

        async def failing(example_id: str, lab_file: str, config_copy: Optional[str] = None):
            await self.launcher(example_id, lab_file, config_copy)  # the build seeded the copy
            raise LabStartError(f"Lab '{example_id}' failed to start: boom")

        with patch.object(registry.lab_service, "launcher", failing):
            self.open_session(role="learner")
            failed = self.start("ospf-lab")

        self.assertEqual(failed.status_code, 500)
        self.assertFalse(self.copies.exists("learner1", "ospf-lab"))
        self.assertFalse(self.client.get("/api/examples").json()["examples"][0]["resumes"])

    def test_a_failed_start_keeps_a_copy_the_group_already_had(self) -> None:
        from mniplab.labprocess import LabStartError
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.write(self.copy_conf("learner1"), "written by learner1\n")
        self.stop(key)

        async def failing(example_id: str, lab_file: str, config_copy: Optional[str] = None):
            raise LabStartError(f"Lab '{example_id}' failed to start: boom")

        with patch.object(registry.lab_service, "launcher", failing):
            self.assertEqual(self.start("ospf-lab").status_code, 500)

        self.assertEqual(self.read(self.copy_conf("learner1")), "written by learner1\n")


@pytest.mark.unit
class StartCleanTests(PersistentConfigCase):
    """A Learner's "start clean" throws the copy away and starts from the seed."""

    def test_start_clean_re_seeds_the_copy(self) -> None:
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.write(self.copy_conf("learner1"), "written by learner1\n")
        self.stop(key)

        clean = self.start("ospf-lab", clean=True)

        self.assertEqual(clean.status_code, 200)
        self.assertEqual(self.read(self.copy_conf("learner1")), "instructor-authored\n")

    def test_start_clean_while_the_groups_lab_is_alive_is_refused_naming_it(self) -> None:
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.write(self.copy_conf("learner1"), "written by learner1\n")

        refused = self.start("ospf-lab", clean=True)

        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["detail"],
                         f"Group 'learner1' already runs Lab 'ospf-lab' ({key}); stop it first")
        self.assertEqual(self.read(self.copy_conf("learner1")), "written by learner1\n")


@pytest.mark.unit
class TwoGroupsTests(PersistentConfigCase):
    """Two Groups on one Lab Example never share a file, and neither writes the seed."""

    def test_two_groups_copies_are_disjoint_and_the_seed_is_unmodified(self) -> None:
        self.open_session(role="learner")
        self.start("ospf-lab")
        self.sign_in(username="learner2", password="lab54321")
        self.start("ospf-lab")
        self.write(self.copy_conf("learner1"), "written by learner1\n")
        self.write(self.copy_conf("learner2"), "written by learner2\n")

        self.assertEqual(self.read(self.copy_conf("learner1")), "written by learner1\n")
        self.assertEqual(self.read(self.copy_conf("learner2")), "written by learner2\n")
        self.assertEqual(self.read(os.path.join(self.seed_root, "r1", "frr.conf")),
                         "instructor-authored\n")
        copy1, copy2 = self.launcher.copies
        assert copy1 is not None and copy2 is not None
        self.assertNotEqual(copy1, copy2)
        self.assertFalse(copy1.startswith(copy2 + os.sep) or copy2.startswith(copy1 + os.sep))


@pytest.mark.unit
class InstructorResetTests(PersistentConfigCase):
    """The Instructor resets one Group's copy, or every Group's, to the seed."""

    def reset(self, example_id: str, group: Optional[str] = None) -> Any:
        body: Dict = {"example_id": example_id}
        if group is not None:
            body["group"] = group
        return self.client.post("/api/admin/config/reset", json=body)

    def seed_both_groups_then_stop(self) -> None:
        self.open_session(role="learner")
        key1 = self.start("ospf-lab").json()["key"]
        self.sign_in(username="learner2", password="lab54321")
        key2 = self.start("ospf-lab").json()["key"]
        self.write(self.copy_conf("learner1"), "written by learner1\n")
        self.write(self.copy_conf("learner2"), "written by learner2\n")
        self.open_session(role="instructor")
        self.stop(key1)
        self.stop(key2)

    def test_reset_of_one_group_discards_only_that_groups_copy(self) -> None:
        self.seed_both_groups_then_stop()

        reset = self.reset("ospf-lab", group="learner1")

        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json(), {"example": "ospf-lab", "reset": ["learner1"]})
        self.assertFalse(self.copies.exists("learner1", "ospf-lab"))
        self.assertEqual(self.read(self.copy_conf("learner2")), "written by learner2\n")
        # The Group's next start seeds again.
        self.open_session(role="learner")
        self.start("ospf-lab")
        self.assertEqual(self.read(self.copy_conf("learner1")), "instructor-authored\n")

    def test_reset_of_every_group_discards_every_copy(self) -> None:
        self.seed_both_groups_then_stop()

        reset = self.reset("ospf-lab")

        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json(), {"example": "ospf-lab", "reset": ["learner1", "learner2"]})
        self.assertFalse(self.copies.exists("learner1", "ospf-lab"))
        self.assertFalse(self.copies.exists("learner2", "ospf-lab"))
        self.assertEqual(self.read(os.path.join(self.seed_root, "r1", "frr.conf")),
                         "instructor-authored\n")

    def test_reset_while_the_groups_lab_is_alive_is_refused_naming_the_lab(self) -> None:
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.write(self.copy_conf("learner1"), "written by learner1\n")
        self.open_session(role="instructor")

        one = self.reset("ospf-lab", group="learner1")
        every = self.reset("ospf-lab")

        self.assertEqual(one.status_code, 409)
        self.assertEqual(one.json()["detail"],
                         f"Group 'learner1' runs Lab 'ospf-lab' ({key}); stop it first")
        self.assertEqual(every.status_code, 409)
        self.assertEqual(every.json()["detail"],
                         f"Group 'learner1' runs Lab 'ospf-lab' ({key}); stop it first")
        self.assertEqual(self.read(self.copy_conf("learner1")), "written by learner1\n")

    def test_a_lab_on_another_example_does_not_block_the_reset(self) -> None:
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.stop(key)
        self.start("dns-lab")
        self.open_session(role="instructor")

        reset = self.reset("ospf-lab", group="learner1")

        self.assertEqual(reset.status_code, 200)
        self.assertFalse(self.copies.exists("learner1", "ospf-lab"))

    def test_reset_names_only_groups_and_examples_that_exist_and_is_the_instructors(self) -> None:
        self.open_session(role="instructor")
        self.assertEqual(self.reset("no-such-lab").status_code, 400)
        self.assertEqual(self.reset("ospf-lab", group="nobody").status_code, 404)
        self.assertEqual(self.reset("ospf-lab", group="learner1").json(),
                         {"example": "ospf-lab", "reset": []})  # nothing to discard

        self.open_session(role="learner")
        self.assertEqual(self.reset("ospf-lab").status_code, 403)


@pytest.mark.unit
class NextStartTests(PersistentConfigCase):
    """The Group page says whether the next start resumes or seeds; the dashboard names the copies."""

    def examples(self) -> Dict[str, Dict]:
        return {e["id"]: e for e in self.client.get("/api/examples").json()["examples"]}

    def test_the_catalog_says_seed_before_the_first_start_and_resume_after(self) -> None:
        self.open_session(role="learner")
        before = self.examples()["ospf-lab"]
        key = self.start("ospf-lab").json()["key"]
        self.stop(key)
        after = self.examples()["ospf-lab"]

        self.assertEqual((before["persistent_config"], before["resumes"]), (True, False))
        self.assertEqual((after["persistent_config"], after["resumes"]), (True, True))
        # Another Group's copy is not this Group's.
        self.sign_in(username="learner2", password="lab54321")
        self.assertFalse(self.examples()["ospf-lab"]["resumes"])

    def test_an_ephemeral_config_example_offers_neither_control(self) -> None:
        self.open_session(role="learner")
        key = self.start("dns-lab").json()["key"]
        self.stop(key)

        row = self.examples()["dns-lab"]
        self.assertEqual((row["persistent_config"], row["resumes"]), (False, False))
        self.assertFalse(self.copies.exists("learner1", "dns-lab"))
        self.open_session(role="instructor")
        groups = {g["name"]: g for g in self.client.get("/api/admin/dashboard").json()["groups"]}
        self.assertEqual(groups["learner1"]["config_copies"], [])

    def test_the_dashboard_names_each_groups_copies(self) -> None:
        self.open_session(role="learner")
        self.start("ospf-lab")
        self.open_session(role="instructor")

        groups = {g["name"]: g for g in self.client.get("/api/admin/dashboard").json()["groups"]}

        self.assertEqual(groups["learner1"]["config_copies"], ["ospf-lab"])
        self.assertEqual(groups["learner2"]["config_copies"], [])
