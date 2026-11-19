"""Tests for the Activity State derived on the instructor's dashboard.

Each Group row carries an Activity State — Active, Idle, Stopped (with a
reason) or Absent — derived at request time from the Sessions and
Terminals that exist then. Nothing is stored: the frozen clock ages the
Sessions and Terminals so every state falls out deterministically.
"""

import time
import unittest
from typing import Any, Dict

import pytest

from mniplab.tests.fakes import (
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    LoginClientMixin,
)
from mniplab.tests.test_web_multi_lab import MultiLabModeMixin
from mniplab.webserver import registry
from mniplab.webserver import auth
from mniplab.webserver.config import settings
from mniplab.webserver.services import Terminal


@pytest.mark.unit
class ActivityStateTests(
    MultiLabModeMixin,
    LoginClientMixin,
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    unittest.TestCase,
):
    """The four Activity States, derived at request time, nothing stored."""

    def setUp(self) -> None:
        """Sign in the Instructor and give each Learner an Assignment."""
        super().setUp()
        self.open_session(role="instructor")
        self.client.put(
            "/api/admin/assignments/learner1", json={"example_ids": ["ospf-lab"]}
        )
        self.client.put(
            "/api/admin/assignments/learner2", json={"example_ids": ["ospf-lab"]}
        )

    def dashboard(self) -> Dict[str, Any]:
        """The dashboard, fetched as the signed-in Instructor."""
        return self.client.get("/api/admin/dashboard").json()

    def groups(self) -> Dict[str, Dict[str, Any]]:
        """The dashboard's Group rows keyed by Group name."""
        return {group["name"]: group for group in self.dashboard()["groups"]}

    def insert_terminal(self, lab_key: str, last_activity: float) -> None:
        """Register a live Terminal on the Lab with ``lab_key``."""
        terminal = Terminal(
            id=f"t-{lab_key}",
            node="r1",
            client_id="client-1",
            username="learner1",
            lab_key=lab_key,
            terminal_fd=0,
            child_pid=0,
            created_at=self.clock(),
            last_activity=last_activity,
        )
        registry.terminal_service.terminals[terminal.id] = terminal
        self.addCleanup(registry.terminal_service.terminals.pop, terminal.id, None)

    def test_a_group_without_a_session_is_absent_even_while_its_lab_runs(self) -> None:
        """A running Lab whose Learner never signed in reads Absent."""
        started = self.start("ospf-lab", group="learner1")
        self.assertEqual(started.status_code, 200)

        group = self.groups()["learner1"]

        self.assertEqual(group["activity_state"], "absent")
        self.assertIsNone(group["stop_reason"])

    def test_last_learner_input_is_separate_from_presence_and_lab_status(self) -> None:
        """Only current Learner Sessions contribute, independent of Lab state."""
        first = auth.create_session("learner1", "learner")
        second = auth.create_session("learner1", "learner")
        instructor = auth.create_session("learner1", "instructor")
        unrelated = auth.create_session("learner2", "learner")
        self.assertIsNone(self.groups()["learner1"]["last_learner_input"])
        auth.record_terminal_input(first)
        self.clock.advance(10)
        auth.record_terminal_input(second)
        expected = self.clock()
        self.clock.advance(10)
        auth.record_terminal_input(instructor)
        auth.record_terminal_input(unrelated)
        group = self.groups()["learner1"]
        self.assertIsNone(group["lab"])
        self.assertEqual(group["session_count"], 3)
        self.assertEqual(group["last_learner_input"], expected)
        del auth.active_sessions[second]
        self.assertEqual(self.groups()["learner1"]["last_learner_input"], expected - 10)

    def test_a_group_with_a_fresh_session_and_running_lab_is_active(self) -> None:
        """A signed-in Learner with a running Lab reads Active."""
        self.open_session(role="learner")
        started = self.start("ospf-lab")
        self.assertEqual(started.status_code, 200)
        self.open_session(role="instructor")

        group = self.groups()["learner1"]

        self.assertEqual(group["activity_state"], "active")
        self.assertIsNone(group["stop_reason"])

    def test_activity_right_at_the_threshold_still_reads_active(self) -> None:
        """Quiet for exactly the threshold is still Active."""
        self.open_session(role="learner")
        self.assertEqual(self.start("ospf-lab").status_code, 200)
        self.clock.advance(settings.activity_idle_threshold)
        self.open_session(role="instructor")

        self.assertEqual(self.groups()["learner1"]["activity_state"], "active")

    def test_a_quiet_group_past_the_threshold_reads_idle(self) -> None:
        """Quiet one second past the threshold reads Idle."""
        self.open_session(role="learner")
        self.assertEqual(self.start("ospf-lab").status_code, 200)
        self.clock.advance(settings.activity_idle_threshold + 1)
        self.open_session(role="instructor")

        self.assertEqual(self.groups()["learner1"]["activity_state"], "idle")

    def test_a_terminal_on_the_groups_lab_keeps_it_active(self) -> None:
        """A recent Terminal on the Group's own Lab keeps it Active."""
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.clock.advance(settings.activity_idle_threshold + 1)
        self.insert_terminal(key, self.clock() - 10)
        self.open_session(role="instructor")

        self.assertEqual(self.groups()["learner1"]["activity_state"], "active")

    def test_a_terminal_older_than_the_threshold_reads_idle(self) -> None:
        """A Terminal older than the threshold cannot keep the Group Active."""
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        self.clock.advance(settings.activity_idle_threshold + 1)
        self.insert_terminal(key, self.clock() - settings.activity_idle_threshold - 1)
        self.open_session(role="instructor")

        self.assertEqual(self.groups()["learner1"]["activity_state"], "idle")

    def test_a_terminal_on_another_groups_lab_does_not_count(self) -> None:
        """Terminal activity is attributed by the Lab's key, not the Learner."""
        self.open_session(role="learner")
        self.start("ospf-lab")
        self.sign_in(username="learner2", password="lab54321")
        key2 = self.start("ospf-lab").json()["key"]
        self.clock.advance(settings.activity_idle_threshold + 1)
        self.insert_terminal(key2, self.clock())
        self.open_session(role="instructor")

        groups = self.groups()

        self.assertEqual(groups["learner1"]["activity_state"], "idle")
        self.assertEqual(groups["learner2"]["activity_state"], "active")

    def test_a_stopped_lab_reads_stopped_with_no_exit_reason(self) -> None:
        """A Lab the Learner stopped on purpose reads Stopped, 'stopped'."""
        self.open_session(role="learner")
        key = self.start("ospf-lab").json()["key"]
        stopped = self.client.post(f"/api/labs/{key}/stop")
        self.assertEqual(stopped.status_code, 200)
        self.open_session(role="instructor")

        group = self.groups()["learner1"]

        self.assertEqual(group["activity_state"], "stopped")
        self.assertEqual(group["stop_reason"], "stopped")

    def test_a_dead_lab_reads_stopped_with_its_exit_reason(self) -> None:
        """A Lab whose process died reads Stopped with the exit reason."""
        with self.client:
            self.open_session(role="learner")
            self.start("ospf-lab")
            self.launcher.children[0].die("killed by SIGKILL")
            self.open_session(role="instructor")
            for _ in range(200):
                if not self.dashboard()["labs"]:
                    break
                time.sleep(0.01)

        group = self.groups()["learner1"]

        self.assertEqual(group["activity_state"], "stopped")
        self.assertEqual(group["stop_reason"], "killed by SIGKILL")

    def test_the_group_rows_keep_their_shape(self) -> None:
        """The new fields are additive; the existing row shape is unchanged."""
        dashboard = self.dashboard()

        self.assertEqual(set(dashboard), {"mode", "cap", "groups", "labs", "example_names"})
        for group in dashboard["groups"]:
            self.assertEqual(
                set(group),
                {
                    "name",
                    "role",
                    "assignment",
                    "config_copies",
                    "incompatible_config_copies",
                    "lab",
                    "last_exit",
                    "activity_state",
                    "stop_reason",
                    "session_count",
                    "last_activity",
                    "last_learner_input",
                },
            )
