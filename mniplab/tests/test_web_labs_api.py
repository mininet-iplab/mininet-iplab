"""Unit tests for the Lab routes: the catalog, the Labs list, and the key in the URL.

Seam B, against the fake Lab: what the browser gets from ``/api/examples``,
``/api/labs`` and ``/api/labs/{key}``, and what a key that names no Lab
alive is told.
"""

import unittest
from unittest.mock import AsyncMock, patch

import pytest

from mniplab.tests.fakes import (
    FAKE_LAB_KEY,
    FakeLab,
    FakeNode,
    FakeTerminalCloserMixin,
    LoginClientMixin,
)
from mniplab.webserver import registry


@pytest.mark.unit
class LabRoutesTests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The catalog is under /examples; alive Labs and their key under /labs."""

    def setUp(self) -> None:
        super().setUp()
        registry.lab_service.lab = None
        registry.lab_service.last_exit = None
        self.addCleanup(setattr, registry.lab_service, "lab", None)
        self.addCleanup(setattr, registry.lab_service, "last_exit", None)
        self.open_session(role="instructor")

    def _run_fake_lab(self) -> None:
        registry.lab_service.lab = FakeLab(FakeNode.with_addresses("h1", "10.0.0.1/24"))
        registry.lab_service.lab_example = "tiny-lab"
        self.addCleanup(setattr, registry.lab_service, "lab_example", None)

    def test_the_catalog_lists_lab_examples_by_id(self) -> None:
        response = self.client.get("/api/examples")

        self.assertEqual(response.status_code, 200)
        ids = {entry["id"] for entry in response.json()["examples"]}
        self.assertIn("ospf-lab", ids)

    def test_catalog_returns_areas_in_teaching_order(self) -> None:
        payload = self.client.get("/api/examples").json()
        self.assertEqual(payload["areas"], [
            "Static", "Intra-domain", "Inter-domain", "Data Center", "Switching",
            "NAT", "DNS", "DHCP", "SP Transport", "Multicast",
        ])
        examples = {entry["id"]: entry for entry in payload["examples"]}
        self.assertEqual(examples["static-lab"]["area"], "Static")
        self.assertEqual(examples["ospf-lab"]["area"], "Intra-domain")

    def test_older_authored_example_without_area_reads_as_other(self) -> None:
        with patch("mniplab.webserver.api.load_labs_manifest", return_value=[{
            "id": "authored-" + "a" * 32, "name": "Earlier lesson", "authored": True,
        }]):
            example = self.client.get("/api/examples").json()["examples"][0]
        self.assertEqual(example["area"], "Other")

    def test_no_lab_alive_lists_nothing(self) -> None:
        response = self.client.get("/api/labs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"labs": []})

    def test_the_alive_lab_is_listed_under_its_key(self) -> None:
        self._run_fake_lab()

        rows = self.client.get("/api/labs").json()["labs"]

        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["key"], rows[0]["example"], rows[0]["nodes"]),
                         (FAKE_LAB_KEY, "tiny-lab", 1))
        detail = self.client.get(f"/api/labs/{FAKE_LAB_KEY}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json(), rows[0])

    def test_status_carries_the_key_the_routes_are_under(self) -> None:
        self._run_fake_lab()

        status = self.client.get("/api/status").json()

        self.assertTrue(status["running"])
        self.assertEqual(status["key"], FAKE_LAB_KEY)

    def test_a_key_that_is_not_the_alive_lab_is_not_found(self) -> None:
        self._run_fake_lab()

        response = self.client.get("/api/labs/other/topology")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "No Lab with key 'other' is running")

    def test_the_key_of_a_lab_whose_process_ended_says_why(self) -> None:
        registry.lab_service.last_exit = {
            "key": "a1b2", "example": "ospf-lab", "reason": "killed by SIGKILL", "at": 0,
        }

        response = self.client.get("/api/labs/a1b2/topology")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            "Lab 'ospf-lab' (a1b2) is not running: its process killed by SIGKILL",
        )
        self.assertEqual(self.client.get("/api/status").json()["last_exit"]["reason"],
                         "killed by SIGKILL")

    def test_start_takes_an_example_id_and_answers_with_the_key(self) -> None:
        started = {"status": "started", "key": "a1b2", "example": "ospf-lab",
                   "started_at": 0, "nodes": 3}
        with patch("mniplab.webserver.registry.start_lab_from_builder",
                   new=AsyncMock(return_value=started)) as start, \
             patch("mniplab.webserver.registry.broadcast_status_update", new=AsyncMock()), \
             patch("mniplab.webserver.registry.broadcast_topology_update", new=AsyncMock()):
            response = self.client.post("/api/labs", json={"example_id": "ospf-lab"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["key"], "a1b2")
        assert start.await_args is not None
        self.assertEqual(start.await_args.args[0], "ospf-lab")
        self.assertTrue(start.await_args.args[1].endswith("/examples/ospf-lab.py"))

    def test_start_refuses_an_id_outside_the_catalog(self) -> None:
        response = self.client.post("/api/labs", json={"example_id": "../etc/passwd"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown Lab Example", response.json()["detail"])

    def test_stop_is_addressed_by_the_key(self) -> None:
        self._run_fake_lab()
        with patch("mniplab.webserver.registry.stop_lab",
                   new=AsyncMock(return_value={"status": "stopped", "example": "tiny-lab"})), \
             patch("mniplab.webserver.registry.broadcast_status_update", new=AsyncMock()):
            wrong = self.client.post("/api/labs/other/stop")
            right = self.client.post(f"/api/labs/{FAKE_LAB_KEY}/stop")

        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(right.status_code, 200)
        self.assertEqual(right.json()["example"], "tiny-lab")

    def test_a_learner_may_list_but_not_start_or_stop(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.client.get("/api/labs").status_code, 200)
        self.assertEqual(self.client.post("/api/labs", json={"example_id": "ospf-lab"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/labs/{FAKE_LAB_KEY}/stop").status_code, 403)
