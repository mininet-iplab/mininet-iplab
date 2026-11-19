"""Unit tests for the RPKI CA Web API routes in mniplab.webserver.api.

The Lab is a fake holding one RPKI CA, served through the real Lab handle and
the real query and object services. Only Krill's client is a stub
(:class:`KrillClientStubMixin`), so each test proves what a route says to
Krill, and what it refuses before saying anything at all.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from mniplab.errors import KrillError, RoutinatorError, RPKIObjectError
from mniplab.rpki.models import ASPA, ROA, VRP, HostedCA
from mniplab.rpki.service import RPKICAService, ValidatorService
from mniplab.tests.fakes import (
    FAKE_LAB_KEY,
    FakeLab,
    FakeNode,
    FakeTerminalCloserMixin,
    KrillClientStubMixin,
    LoginClientMixin,
    RoutinatorClientStubMixin,
)
from mniplab.webserver import registry


def _lab() -> FakeLab:
    """Build a Lab holding one RPKI CA, ``ca``, with Hosted CAs AS65002 and AS65003."""
    return FakeLab(FakeNode("ca", services=[RPKICAService([
        HostedCA(65002, ["10.1.0.0/24"], roas=["10.1.0.0/24-24 => 65002"]),
        HostedCA(65003, ["10.3.0.0/24"]),
    ])]))


def _roa_delta_error(**categories: List[Dict[str, Any]]) -> KrillError:
    """Build the error Krill answers a refused ROA delta with.

    Args:
        **categories: Krill's ``delta_error`` lists, by category name.

    Returns:
        A KrillError with Krill's ``ca-roa-delta-error`` label and body.
    """
    delta: Dict[str, Any] = {"duplicates": [], "notheld": [], "unknowns": [], "invalid_length": []}
    delta.update(categories)
    message = "ROA delta rejected:\n\n'Cannot add the following ROAs'"
    body = {"label": "ca-roa-delta-error", "msg": message, "delta_error": delta}
    return KrillError(400, "ca-roa-delta-error", message, body)


CA_PATH = "/api/labs/fake/rpki/ca/cas"
ROAS_PATH = f"{CA_PATH}/AS65002/roas"
ASPAS_PATH = f"{CA_PATH}/AS65002/aspas"
ROA_BODY = {"prefix": "10.1.0.0/24", "max_length": 25, "origin": 65002}


@pytest.mark.unit
class RPKIRouteTestCase(LoginClientMixin, FakeTerminalCloserMixin, KrillClientStubMixin, unittest.TestCase):
    """Signs in as a Learner over the real app, with an RPKI CA in the one fake Lab."""

    def setUp(self) -> None:
        super().setUp()
        registry.lab_service.lab = _lab()
        self.addCleanup(setattr, registry.lab_service, "lab", None)
        self.open_session()
        self.krill.update_roas.return_value = None
        self.krill.update_aspas.return_value = None
        self.krill.list_roas.side_effect = lambda handle: (
            [ROA.parse("10.1.0.0/24-24 => 65002")] if handle == "as65002" else []
        )
        self.krill.list_aspas.side_effect = lambda handle: (
            [ASPA.parse("AS65002 => AS65001, AS65005")] if handle == "as65002" else []
        )


@pytest.mark.unit
class RPKIListingRouteTests(RPKIRouteTestCase):
    """GET /rpki/{node}/cas, and one Hosted CA's ROAs and ASPAs."""

    def test_lists_each_hosted_ca_with_its_roas_and_aspas(self) -> None:
        response = self.client.get(CA_PATH)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["node"], "ca")
        self.assertEqual([ca["asn"] for ca in payload["cas"]], [65002, 65003])
        first = payload["cas"][0]
        self.assertEqual(first["prefixes"], ["10.1.0.0/24"])
        self.assertEqual(first["roas"], [{"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002}])
        self.assertEqual(first["aspas"], [{"customer": 65002, "providers": [65001, 65005]}])

    def test_an_unknown_node_is_refused_with_400_naming_it(self) -> None:
        response = self.client.get("/api/labs/fake/rpki/nope/cas")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Node not found: nope", response.json()["detail"])

    def test_an_unreachable_krill_is_503(self) -> None:
        self.krill.list_roas.side_effect = KrillError(None, "", "no answer on the socket")

        response = self.client.get(CA_PATH)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "no answer on the socket")

    def test_a_hosted_ca_read_names_its_roas(self) -> None:
        response = self.client.get(ROAS_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "node": "ca",
            "asn": 65002,
            "roas": [{"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002}],
        })

    def test_a_hosted_ca_read_names_its_aspas(self) -> None:
        response = self.client.get(ASPAS_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "node": "ca",
            "asn": 65002,
            "aspas": [{"customer": 65002, "providers": [65001, 65005]}],
        })

    def test_a_hosted_ca_the_lab_lacks_is_refused_with_400(self) -> None:
        response = self.client.get(f"{CA_PATH}/AS65009/roas")

        self.assertEqual(response.status_code, 400)
        self.assertIn("No Hosted CA for AS65009", response.json()["detail"])

    def test_reads_require_authentication(self) -> None:
        self.client.cookies.clear()

        self.assertEqual(self.client.get(CA_PATH).status_code, 401)
        self.assertEqual(self.client.get(ROAS_PATH).status_code, 401)


@pytest.mark.unit
class RPKIROARouteTests(RPKIRouteTestCase):
    """ROA add, edit and remove at a Hosted CA: what each sends to Krill, and what it refuses."""

    def test_adding_a_roa_sends_one_delta_that_signs_it(self) -> None:
        response = self.client.post(ROAS_PATH, json=ROA_BODY)

        self.assertEqual(response.status_code, 200)
        self.krill.update_roas.assert_called_once_with(
            "as65002", added=[ROA.parse("10.1.0.0/24-25 => 65002")], removed=[],
        )
        self.assertEqual(response.json(), {
            "node": "ca",
            "asn": 65002,
            "roa": {"prefix": "10.1.0.0/24", "max_length": 25, "origin": 65002},
        })

    def test_max_length_defaults_to_the_prefix_length(self) -> None:
        response = self.client.post(ROAS_PATH, json={"prefix": "10.1.0.0/24", "origin": 65002})

        self.assertEqual(response.status_code, 200)
        self.krill.update_roas.assert_called_once_with(
            "as65002", added=[ROA.parse("10.1.0.0/24-24 => 65002")], removed=[],
        )

    def test_origin_may_be_written_with_an_as_prefix(self) -> None:
        response = self.client.post(ROAS_PATH, json={**ROA_BODY, "origin": "AS65002"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["roa"]["origin"], 65002)

    def test_editing_a_roa_sends_one_delta_that_replaces_it_including_its_max_length(self) -> None:
        response = self.client.post(ROAS_PATH, json={
            **ROA_BODY,
            "old": {"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002},
        })

        self.assertEqual(response.status_code, 200)
        self.krill.update_roas.assert_called_once_with(
            "as65002",
            added=[ROA.parse("10.1.0.0/24-25 => 65002")],
            removed=[ROA.parse("10.1.0.0/24-24 => 65002")],
        )
        self.assertEqual(response.json()["old"], {"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002})

    def test_a_notheld_refusal_is_400_naming_the_hosted_ca_and_prefix(self) -> None:
        self.krill.update_roas.side_effect = _roa_delta_error(
            notheld=[{"asn": 65002, "prefix": "10.9.0.0/24", "max_length": 24, "comment": None}]
        )

        response = self.client.post(ROAS_PATH, json={"prefix": "10.9.0.0/24", "origin": 65002})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Hosted CA AS65002 does not hold 10.9.0.0/24, so it cannot sign a ROA for it",
        )

    def test_a_max_length_outside_the_prefix_is_refused_before_any_krill_call(self) -> None:
        response = self.client.post(ROAS_PATH, json={**ROA_BODY, "max_length": 23})

        self.assertEqual(response.status_code, 400)
        self.assertIn("maxLength 23", response.json()["detail"])
        self.krill.update_roas.assert_not_called()

    def test_a_prefix_with_host_bits_is_refused_before_any_krill_call(self) -> None:
        response = self.client.post(ROAS_PATH, json={**ROA_BODY, "prefix": "10.1.0.1/24"})

        self.assertEqual(response.status_code, 400)
        self.krill.update_roas.assert_not_called()

    def test_an_empty_prefix_is_rejected_by_request_validation(self) -> None:
        response = self.client.post(ROAS_PATH, json={**ROA_BODY, "prefix": ""})

        self.assertEqual(response.status_code, 422)
        self.krill.update_roas.assert_not_called()

    def test_a_hosted_ca_the_lab_lacks_is_refused_before_any_krill_call(self) -> None:
        response = self.client.post(f"{CA_PATH}/AS65009/roas", json=ROA_BODY)

        self.assertEqual(response.status_code, 400)
        self.assertIn("No Hosted CA for AS65009", response.json()["detail"])
        self.krill.update_roas.assert_not_called()

    def test_removing_a_roa_sends_one_delta_that_withdraws_it(self) -> None:
        response = self.client.request("DELETE", ROAS_PATH, json={
            "prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002,
        })

        self.assertEqual(response.status_code, 200)
        self.krill.update_roas.assert_called_once_with(
            "as65002", added=[], removed=[ROA.parse("10.1.0.0/24-24 => 65002")],
        )

    def test_removing_a_roa_krill_does_not_hold_is_400_in_plain_words(self) -> None:
        self.krill.update_roas.side_effect = _roa_delta_error(
            unknowns=[{"asn": 65002, "prefix": "10.1.0.0/24", "max_length": 25, "comment": None}]
        )

        response = self.client.request("DELETE", ROAS_PATH, json={
            "prefix": "10.1.0.0/24", "max_length": 25, "origin": 65002,
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("is not signed here, so it cannot be removed", response.json()["detail"])

    def test_a_learner_may_write_roas(self) -> None:
        self.assertEqual(self.client.post(ROAS_PATH, json=ROA_BODY).status_code, 200)

    def test_a_roa_write_requires_authentication(self) -> None:
        self.client.cookies.clear()

        self.assertEqual(self.client.post(ROAS_PATH, json=ROA_BODY).status_code, 401)
        self.assertEqual(self.client.request("DELETE", ROAS_PATH, json=ROA_BODY).status_code, 401)

    def test_an_unreachable_krill_on_write_is_503(self) -> None:
        self.krill.update_roas.side_effect = KrillError(None, "", "no answer on the socket")

        response = self.client.post(ROAS_PATH, json=ROA_BODY)

        self.assertEqual(response.status_code, 503)


@pytest.mark.unit
class RPKIASPARouteTests(RPKIRouteTestCase):
    """ASPA add and remove at a Hosted CA, for its own customer only."""

    def test_adding_an_aspa_signs_it_for_the_hosted_ca_as_customer(self) -> None:
        response = self.client.post(ASPAS_PATH, json={"customer": 65002, "providers": [65001, 65005]})

        self.assertEqual(response.status_code, 200)
        self.krill.update_aspas.assert_called_once_with(
            "as65002", add_or_replace=[ASPA.parse("AS65002 => AS65001, AS65005")], remove=[],
        )
        self.assertEqual(response.json()["aspa"], {"customer": 65002, "providers": [65001, 65005]})

    def test_an_aspa_for_another_customer_is_refused_before_any_krill_call(self) -> None:
        response = self.client.post(ASPAS_PATH, json={"customer": 65009, "providers": [65001]})

        self.assertEqual(response.status_code, 400)
        self.assertIn("signs ASPAs only for its own ASN, not AS65009", response.json()["detail"])
        self.krill.update_aspas.assert_not_called()

    def test_an_aspa_without_a_provider_is_refused_before_any_krill_call(self) -> None:
        response = self.client.post(ASPAS_PATH, json={"customer": 65002, "providers": []})

        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one provider", response.json()["detail"])
        self.krill.update_aspas.assert_not_called()

    def test_a_krill_refusal_of_an_aspa_is_400_naming_the_hosted_ca(self) -> None:
        self.krill.update_aspas.side_effect = KrillError(400, "ca-aspa-error", "ASPA rejected:\n\nno")

        response = self.client.post(ASPAS_PATH, json={"customer": 65002, "providers": [65001]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Hosted CA AS65002 refused the ASPA change: ASPA rejected: no",
        )

    def test_removing_an_aspa_withdraws_the_customers_aspa(self) -> None:
        response = self.client.request("DELETE", ASPAS_PATH, json={"customer": 65002})

        self.assertEqual(response.status_code, 200)
        self.krill.update_aspas.assert_called_once_with("as65002", add_or_replace=[], remove=[65002])
        self.assertEqual(response.json()["customer"], 65002)

    def test_an_aspa_write_requires_authentication(self) -> None:
        self.client.cookies.clear()

        self.assertEqual(
            self.client.post(ASPAS_PATH, json={"customer": 65002, "providers": [65001]}).status_code, 401,
        )
        self.assertEqual(self.client.request("DELETE", ASPAS_PATH, json={"customer": 65002}).status_code, 401)


@pytest.mark.unit
class RPKIMutationBroadcastTests(RPKIRouteTestCase):
    """Each committed change tells every open view of the Lab which Node and kind changed."""

    def _broadcasts(self) -> AsyncMock:
        patcher = patch.object(registry.feed(FAKE_LAB_KEY), "broadcast", new=AsyncMock())
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def test_adding_a_roa_broadcasts_an_add(self) -> None:
        broadcast = self._broadcasts()

        self.client.post(ROAS_PATH, json=ROA_BODY)

        broadcast.assert_called_once_with({
            "type": "RPKI_OBJECTS_UPDATED",
            "data": {"node": "ca", "asn": 65002, "kind": "roa", "action": "add"},
        })

    def test_editing_a_roa_broadcasts_a_replace(self) -> None:
        broadcast = self._broadcasts()

        self.client.post(ROAS_PATH, json={**ROA_BODY, "old": {"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002}})

        broadcast.assert_called_once_with({
            "type": "RPKI_OBJECTS_UPDATED",
            "data": {"node": "ca", "asn": 65002, "kind": "roa", "action": "replace"},
        })

    def test_removing_a_roa_broadcasts_a_remove(self) -> None:
        broadcast = self._broadcasts()

        self.client.request("DELETE", ROAS_PATH, json={"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002})

        broadcast.assert_called_once_with({
            "type": "RPKI_OBJECTS_UPDATED",
            "data": {"node": "ca", "asn": 65002, "kind": "roa", "action": "remove"},
        })

    def test_aspa_changes_broadcast_as_aspa_kind(self) -> None:
        broadcast = self._broadcasts()

        self.client.post(ASPAS_PATH, json={"customer": 65002, "providers": [65001]})
        self.client.request("DELETE", ASPAS_PATH, json={"customer": 65002})

        self.assertEqual(broadcast.call_args_list[0].args[0]["data"]["kind"], "aspa")
        self.assertEqual(broadcast.call_args_list[0].args[0]["data"]["action"], "add")
        self.assertEqual(broadcast.call_args_list[1].args[0]["data"]["action"], "remove")

    def test_a_refused_change_broadcasts_nothing(self) -> None:
        broadcast = self._broadcasts()
        self.krill.update_roas.side_effect = RPKIObjectError("refused")

        response = self.client.post(ROAS_PATH, json=ROA_BODY)

        self.assertEqual(response.status_code, 400)
        broadcast.assert_not_called()


VRPS_PATH = "/api/labs/fake/rpki/rv/vrps"
LAST_REFRESH = "2026-11-06T13:36:00.191074841+00:00"


def _validator_lab() -> FakeLab:
    """Build a Lab with a Validator, ``rv``, refreshing every 30 seconds, and one connected Router, ``r1``."""
    r1 = FakeNode.with_addresses("r1", "10.0.15.1")
    rv = FakeNode.with_addresses("rv", "10.0.15.2", services=[ValidatorService(refresh=30)])
    return FakeLab(r1, rv)


@pytest.mark.unit
class RPKIValidatorRouteTests(
    LoginClientMixin, FakeTerminalCloserMixin, RoutinatorClientStubMixin, unittest.TestCase
):
    """GET /rpki/{node}/vrps: what a Validator serves, read-only, through the real query service."""

    def setUp(self) -> None:
        super().setUp()
        registry.lab_service.lab = _validator_lab()
        self.addCleanup(setattr, registry.lab_service, "lab", None)
        self.open_session()
        self.routinator.vrps.return_value = [VRP(prefix="10.1.0.0/24", max_length=24, origin=65002)]
        self.routinator.aspas.return_value = [{"customer": 65002, "providers": [65001]}]
        self.routinator.status.return_value = {"last_refresh": LAST_REFRESH, "rtr_clients": ["10.0.15.1"]}

    def test_lists_the_vrps_aspas_refresh_times_and_connected_routers(self) -> None:
        response = self.client.get(VRPS_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "node": "rv",
            "vrps": [{"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002}],
            "aspas": [{"customer": 65002, "providers": [65001]}],
            "refresh": 30,
            "last_refresh": "2026-11-06T13:36:00.191074+00:00",
            "next_refresh": "2026-11-06T13:36:30.191074+00:00",
            "routers": ["r1"],
        })

    def test_a_validator_before_its_first_run_with_no_router_reads_none_and_empty(self) -> None:
        self.routinator.status.return_value = {"last_refresh": None, "rtr_clients": []}

        payload = self.client.get(VRPS_PATH).json()

        self.assertIsNone(payload["last_refresh"])
        self.assertIsNone(payload["next_refresh"])
        self.assertEqual(payload["routers"], [])

    def test_a_validator_that_is_not_answering_is_503_with_its_message(self) -> None:
        message = "The Validator is not answering: no JSON for /api/v1/status"
        self.routinator.status.side_effect = RoutinatorError(message)

        response = self.client.get(VRPS_PATH)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], message)

    def test_an_unknown_node_is_refused_with_400_naming_it(self) -> None:
        response = self.client.get("/api/labs/fake/rpki/nope/vrps")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Node not found: nope", response.json()["detail"])

    def test_a_node_without_a_validator_is_refused_with_400(self) -> None:
        response = self.client.get("/api/labs/fake/rpki/r1/vrps")

        self.assertEqual(response.status_code, 400)
        self.assertIn("r1 has no Validator Service attached", response.json()["detail"])

    def test_the_read_requires_authentication(self) -> None:
        self.client.cookies.clear()

        self.assertEqual(self.client.get(VRPS_PATH).status_code, 401)

    def test_the_validator_offers_no_write_route(self) -> None:
        self.assertEqual(
            self.client.post(VRPS_PATH, json={"prefix": "10.1.0.0/24", "origin": 65002}).status_code,
            405,
        )
        self.assertEqual(self.client.delete(VRPS_PATH).status_code, 405)
        self.assertEqual(self.routinator.mock_calls, [])
