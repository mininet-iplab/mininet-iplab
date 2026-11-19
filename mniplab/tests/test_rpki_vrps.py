"""Unit tests for the Validator reads in mniplab/rpki/query.py.

The Validator and the Routers are resolved from a fake Lab for real. The
Routinator client is the stub installed by :class:`RoutinatorClientStubMixin`,
except where a test asks what the real client runs inside the Node.
"""

from __future__ import annotations

import unittest

import pytest

from mniplab.errors import RoutinatorError
from mniplab.rpki.models import VRP
from mniplab.rpki.query import RPKIQueryService
from mniplab.rpki.routinator_client import RoutinatorClient
from mniplab.rpki.service import ValidatorService
from mniplab.tests.fakes import FakeLab, FakeNode, RoutinatorClientStubMixin

LAST_REFRESH = "2026-11-06T13:36:00.191074841+00:00"


def _lab() -> FakeLab:
    """Build a Lab with a Validator, ``rv``, refreshing every 30 seconds, and two Routers.

    ``r1`` holds 10.0.12.1 on one interface and 10.0.15.1 on the link to ``rv``.
    ``r2`` holds 10.0.12.2.
    """
    r1 = FakeNode.with_addresses("r1", "10.0.12.1", "10.0.15.1")
    r2 = FakeNode.with_addresses("r2", "10.0.12.2")
    rv = FakeNode.with_addresses("rv", "10.0.15.2", services=[ValidatorService(refresh=30)])
    return FakeLab(r1, r2, rv)


@pytest.mark.unit
class ValidatorLookupTests(unittest.TestCase):
    """Finding the Validator a Lab Prompt command names."""

    def test_find_validator_without_a_node_name_returns_the_labs_one_validator(self) -> None:
        node, service = RPKIQueryService.find_validator(_lab())

        self.assertEqual(node.name, "rv")
        self.assertEqual(service.refresh, 30)

    def test_find_validator_names_a_node_the_lab_lacks(self) -> None:
        with self.assertRaisesRegex(ValueError, "Node not found: nope"):
            RPKIQueryService.find_validator(_lab(), "nope")

    def test_find_validator_refuses_a_node_without_a_validator(self) -> None:
        with self.assertRaisesRegex(ValueError, "r1 has no Validator Service attached"):
            RPKIQueryService.find_validator(_lab(), "r1")

    def test_find_validator_refuses_a_lab_without_a_validator(self) -> None:
        with self.assertRaisesRegex(ValueError, "no Validator"):
            RPKIQueryService.find_validator(FakeLab(FakeNode("r1")))

    def test_find_validator_refuses_two_validators_when_no_node_is_named(self) -> None:
        net = FakeLab(
            FakeNode("rv1", services=[ValidatorService()]),
            FakeNode("rv2", services=[ValidatorService(refresh=60)]),
        )

        with self.assertRaisesRegex(ValueError, "rv1, rv2"):
            RPKIQueryService.find_validator(net)
        node, service = RPKIQueryService.find_validator(net, "rv2")
        self.assertEqual((node.name, service.refresh), ("rv2", 60))


@pytest.mark.unit
class RoutinatorClientSeamTests(unittest.TestCase):
    """The client the query service builds runs its reads inside the Validator Node."""

    def test_get_routinator_builds_a_client_that_constructs_without_running_anything(self) -> None:
        net = _lab()

        _node, _service, client = RPKIQueryService.get_routinator(net)

        self.assertIsInstance(client, RoutinatorClient)
        self.assertEqual(net.get("rv").recorder.commands, [])

    def test_a_read_runs_curl_on_the_validator_node_against_its_http_endpoint(self) -> None:
        net = _lab()
        rv = net.get("rv")
        rv.recorder.respond_to("curl", '{"roas": [], "aspas": []}')
        _node, _service, client = RPKIQueryService.get_routinator(net)

        self.assertEqual(client.vrps(), [])

        self.assertEqual(
            rv.recorder.issued("curl"),
            ["curl -sf --max-time 5 http://127.0.0.1:8323/json"],
        )

    def test_a_status_read_quotes_its_path_and_asks_the_status_endpoint(self) -> None:
        net = _lab()
        rv = net.get("rv")
        rv.recorder.respond_to("curl", '{"rtr": {"clients": {}}}')
        _node, _service, client = RPKIQueryService.get_routinator(net)

        client.status()

        self.assertEqual(
            rv.recorder.issued("curl"),
            ["curl -sf --max-time 5 http://127.0.0.1:8323/api/v1/status"],
        )


@pytest.mark.unit
class ValidatorVRPQueryTests(RoutinatorClientStubMixin, unittest.TestCase):
    """What the query service reports for a Validator: its VRPs, refresh times and Routers."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _lab()
        self.client.vrps.return_value = [VRP(prefix="10.1.0.0/24", max_length=24, origin=65002)]
        self.client.aspas.return_value = [{"customer": 65002, "providers": [65001]}]
        self.client.status.return_value = {
            "last_refresh": LAST_REFRESH,
            "rtr_clients": ["10.0.15.1"],
        }

    def test_get_vrps_reports_the_vrps_aspas_refresh_times_and_connected_routers(self) -> None:
        view = RPKIQueryService.get_vrps(self.net)

        self.assertEqual(view, {
            "node": "rv",
            "vrps": [{"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002}],
            "aspas": [{"customer": 65002, "providers": [65001]}],
            "refresh": 30,
            "last_refresh": "2026-11-06T13:36:00.191074+00:00",
            "next_refresh": "2026-11-06T13:36:30.191074+00:00",
            "routers": ["r1"],
        })

    def test_next_refresh_is_the_last_refresh_plus_the_validators_interval_across_midnight(self) -> None:
        self.client.status.return_value = {
            "last_refresh": "2026-11-06T23:59:55+00:00",
            "rtr_clients": [],
        }

        view = RPKIQueryService.get_vrps(self.net)

        self.assertEqual(view["next_refresh"], "2026-11-07T00:00:25+00:00")

    def test_no_refresh_yet_reads_as_none_for_both_refresh_times(self) -> None:
        self.client.status.return_value = {"last_refresh": None, "rtr_clients": []}

        view = RPKIQueryService.get_vrps(self.net)

        self.assertIsNone(view["last_refresh"])
        self.assertIsNone(view["next_refresh"])

    def test_no_connected_router_reads_as_an_empty_list(self) -> None:
        self.client.status.return_value = {"last_refresh": LAST_REFRESH, "rtr_clients": []}

        self.assertEqual(RPKIQueryService.get_vrps(self.net)["routers"], [])

    def test_a_connected_address_is_named_by_the_node_whose_interface_holds_it(self) -> None:
        self.client.status.return_value = {
            "last_refresh": LAST_REFRESH,
            "rtr_clients": ["10.0.12.2", "10.0.15.1"],
        }

        self.assertEqual(RPKIQueryService.get_vrps(self.net)["routers"], ["r2", "r1"])

    def test_a_connected_address_no_node_carries_stays_as_the_address(self) -> None:
        self.client.status.return_value = {
            "last_refresh": LAST_REFRESH,
            "rtr_clients": ["10.0.15.1", "10.9.9.9"],
        }

        self.assertEqual(RPKIQueryService.get_vrps(self.net)["routers"], ["r1", "10.9.9.9"])

    def test_a_validator_that_is_not_answering_raises_routinator_error(self) -> None:
        self.client.status.side_effect = RoutinatorError(
            "The Validator is not answering: no JSON for /api/v1/status"
        )

        with self.assertRaisesRegex(RoutinatorError, "not answering"):
            RPKIQueryService.get_vrps(self.net)

    def test_get_vrps_names_a_node_the_lab_lacks_before_asking_routinator(self) -> None:
        with self.assertRaisesRegex(ValueError, "Node not found: nope"):
            RPKIQueryService.get_vrps(self.net, "nope")
        self.assertEqual(self.client.mock_calls, [])
