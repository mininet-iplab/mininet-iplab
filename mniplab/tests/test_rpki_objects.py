"""Unit tests for the RPKI query and object services in mniplab/rpki/query.py and objects.py.

The Node and its RPKI CA Service are resolved from a fake Lab for real; only
the client on Krill's socket is a stub, installed by :class:`KrillClientStubMixin`.
"""

from __future__ import annotations

import os
import unittest
from typing import Any, Dict, List

import pytest

from mniplab.errors import KrillError, RPKINotHeldError, RPKIObjectError
from mniplab.rpki.krill_client import KrillClient
from mniplab.rpki.models import ASPA, ROA, HostedCA
from mniplab.rpki.objects import RPKIObjectService
from mniplab.rpki.query import RPKIQueryService
from mniplab.rpki.service import RPKICAService
from mniplab.tests.fakes import FakeLab, FakeNode, KrillClientStubMixin


def _lab() -> FakeLab:
    """Build a Lab holding one RPKI CA, ``ca``, with Hosted CAs AS65002 and AS65003."""
    return FakeLab(FakeNode("ca", services=[RPKICAService([
        HostedCA(65002, ["10.1.0.0/24"], roas=["10.1.0.0/24-24 => 65002"]),
        HostedCA(65003, ["10.3.0.0/24"]),
    ])]))


def _service(net: FakeLab) -> RPKICAService:
    """Return the RPKI CA Service on the Lab's ``ca`` Node."""
    service = net.get("ca").services[0]
    assert isinstance(service, RPKICAService)
    return service


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


@pytest.mark.unit
class RPKIQueryServiceLookupTests(unittest.TestCase):
    """Finding the RPKI CA a Lab Prompt command names, and its Hosted CAs."""

    def test_find_ca_without_a_node_name_returns_the_labs_one_rpki_ca(self) -> None:
        node, service = RPKIQueryService.find_ca(_lab())
        self.assertEqual(node.name, "ca")
        self.assertEqual([hosted.asn for hosted in service.hosted_cas], [65002, 65003])

    def test_find_ca_names_a_node_the_lab_lacks(self) -> None:
        with self.assertRaisesRegex(ValueError, "Node not found: nope"):
            RPKIQueryService.find_ca(_lab(), "nope")

    def test_find_ca_refuses_a_node_without_an_rpki_ca(self) -> None:
        with self.assertRaisesRegex(ValueError, "r1 has no RPKI CA Service attached"):
            RPKIQueryService.find_ca(FakeLab(FakeNode("r1")), "r1")

    def test_find_ca_refuses_a_lab_without_an_rpki_ca(self) -> None:
        with self.assertRaisesRegex(ValueError, "no RPKI CA"):
            RPKIQueryService.find_ca(FakeLab(FakeNode("r1")))

    def test_find_ca_refuses_two_rpki_cas_when_no_node_is_named(self) -> None:
        net = FakeLab(
            FakeNode("ca1", services=[RPKICAService([HostedCA(65002, ["10.1.0.0/24"])])]),
            FakeNode("ca2", services=[RPKICAService([HostedCA(65003, ["10.3.0.0/24"])])]),
        )
        with self.assertRaisesRegex(ValueError, "ca1, ca2"):
            RPKIQueryService.find_ca(net)
        node, _service = RPKIQueryService.find_ca(net, "ca2")
        self.assertEqual(node.name, "ca2")

    def test_hosted_ca_accepts_the_asn_in_any_spelling(self) -> None:
        service = _service(_lab())
        for spelling in (65002, "65002", "AS65002"):
            with self.subTest(spelling=spelling):
                self.assertEqual(RPKIQueryService.hosted_ca(service, spelling).asn, 65002)

    def test_hosted_ca_refuses_an_asn_no_hosted_ca_holds_and_lists_the_others(self) -> None:
        with self.assertRaisesRegex(ValueError, "No Hosted CA for AS65009") as ctx:
            RPKIQueryService.hosted_ca(_service(_lab()), "AS65009")
        self.assertIn("AS65002, AS65003", str(ctx.exception))

    def test_hosted_ca_refuses_text_that_is_not_an_asn(self) -> None:
        with self.assertRaises(ValueError):
            RPKIQueryService.hosted_ca(_service(_lab()), "ca")

    def test_get_client_constructs_a_client_on_the_socket_without_connecting(self) -> None:
        net = _lab()
        socket_path = net.rpki_ca_socket_path("ca")
        os.makedirs(os.path.dirname(socket_path), exist_ok=True)
        with open(socket_path, "w", encoding="utf-8"):
            pass

        _node, _service, client = RPKIQueryService.get_client(net)

        self.assertIsInstance(client, KrillClient)
        self.assertEqual(client.socket_path, socket_path)

    def test_get_client_refuses_a_missing_krill_socket(self) -> None:
        with self.assertRaisesRegex(ValueError, "Krill socket not found"):
            RPKIQueryService.get_client(_lab(), "ca")


@pytest.mark.unit
class RPKIQueryServiceListTests(KrillClientStubMixin, unittest.TestCase):
    """What the query service reads from Krill for each Hosted CA."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _lab()
        self.client.list_roas.side_effect = lambda handle: (
            [ROA.parse("10.1.0.0/24-24 => 65002")] if handle == "as65002" else []
        )
        self.client.list_aspas.side_effect = lambda handle: (
            [ASPA.parse("AS65002 => AS65001, AS65005")] if handle == "as65002" else []
        )

    def test_list_cas_reads_each_hosted_ca_as_plain_data(self) -> None:
        listing = RPKIQueryService.list_cas(self.net, "ca")

        self.assertEqual(listing, [
            {
                "asn": 65002,
                "handle": "as65002",
                "prefixes": ["10.1.0.0/24"],
                "roas": [{"prefix": "10.1.0.0/24", "max_length": 24, "origin": 65002}],
                "aspas": [{"customer": 65002, "providers": [65001, 65005]}],
            },
            {
                "asn": 65003,
                "handle": "as65003",
                "prefixes": ["10.3.0.0/24"],
                "roas": [],
                "aspas": [],
            },
        ])

    def test_list_cas_refuses_a_node_the_lab_lacks_before_asking_krill(self) -> None:
        with self.assertRaisesRegex(ValueError, "Node not found: nope"):
            RPKIQueryService.list_cas(self.net, "nope")
        self.assertEqual(self.client.mock_calls, [])

    def test_list_cas_lets_an_unreachable_krill_surface_as_a_krill_error(self) -> None:
        self.client.list_roas.side_effect = KrillError(None, "unreachable", "no answer")
        with self.assertRaises(KrillError):
            RPKIQueryService.list_cas(self.net, "ca")

    def test_get_hosted_ca_reads_one_hosted_ca_as_the_listing_does(self) -> None:
        listing = RPKIQueryService.get_hosted_ca(self.net, "ca", "AS65002")

        self.assertEqual(listing, RPKIQueryService.list_cas(self.net, "ca")[0])

    def test_get_hosted_ca_refuses_an_asn_no_hosted_ca_holds_before_asking_krill(self) -> None:
        with self.assertRaisesRegex(ValueError, "No Hosted CA for AS65009"):
            RPKIQueryService.get_hosted_ca(self.net, "ca", "AS65009")
        self.client.list_roas.assert_not_called()


@pytest.mark.unit
class RPKIObjectServiceROATests(KrillClientStubMixin, unittest.TestCase):
    """ROA changes at a Hosted CA: what each sends, and what it refuses before sending."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _lab()

    def test_add_roa_sends_one_delta_that_signs_it(self) -> None:
        result = RPKIObjectService.add_roa(self.net, "ca", "AS65002", "10.1.0.0/24-25 => 65002")

        self.client.update_roas.assert_called_once_with(
            "as65002", added=[ROA.parse("10.1.0.0/24-25 => 65002")], removed=[]
        )
        self.assertEqual(result, {
            "node": "ca",
            "asn": 65002,
            "roa": {"prefix": "10.1.0.0/24", "max_length": 25, "origin": 65002},
        })

    def test_remove_roa_sends_one_delta_that_withdraws_it(self) -> None:
        RPKIObjectService.remove_roa(self.net, "ca", 65002, "10.1.0.0/24-24 => 65002")

        self.client.update_roas.assert_called_once_with(
            "as65002", added=[], removed=[ROA.parse("10.1.0.0/24-24 => 65002")]
        )

    def test_replace_roa_withdraws_the_old_and_signs_the_new_in_one_delta(self) -> None:
        RPKIObjectService.replace_roa(
            self.net, "ca", 65002, "10.1.0.0/24-24 => 65002", "10.1.0.0/24-25 => 65002"
        )

        self.client.update_roas.assert_called_once_with(
            "as65002",
            added=[ROA.parse("10.1.0.0/24-25 => 65002")],
            removed=[ROA.parse("10.1.0.0/24-24 => 65002")],
        )

    def test_roa_models_are_accepted_as_well_as_their_text(self) -> None:
        RPKIObjectService.add_roa(self.net, "ca", 65002, ROA.parse("10.1.0.0/24-24 => 65002"))

        self.client.update_roas.assert_called_once_with(
            "as65002", added=[ROA.parse("10.1.0.0/24-24 => 65002")], removed=[]
        )

    def test_an_origin_outside_the_hosted_cas_resources_is_allowed(self) -> None:
        RPKIObjectService.add_roa(self.net, "ca", 65002, "10.1.0.0/24-24 => 65009")

        self.client.update_roas.assert_called_once_with(
            "as65002", added=[ROA.parse("10.1.0.0/24-24 => 65009")], removed=[]
        )

    def test_an_invalid_roa_is_refused_before_any_krill_call(self) -> None:
        invalid = (
            "10.1.0.1/24-24 => 65002",  # host bits set
            "10.1.0.0/24-23 => 65002",  # maxLength shorter than the prefix
            "10.1.0.0/24-33 => 65002",  # maxLength beyond IPv4
            "10.1.0.0/24-24 => AS0",  # ASN outside 1 to 4294967295
            "10.1.0.0/24-24 65002",  # no arrow
            "not-a-prefix => 65002",
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    RPKIObjectService.add_roa(self.net, "ca", 65002, text)
                self.assertEqual(self.client.mock_calls, [])

    def test_a_hosted_ca_the_lab_lacks_is_refused_before_any_krill_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "No Hosted CA for AS65009"):
            RPKIObjectService.add_roa(self.net, "ca", 65009, "10.1.0.0/24-24 => 65002")
        self.assertEqual(self.client.mock_calls, [])

    def test_notheld_becomes_a_typed_error_naming_the_hosted_ca_and_prefix(self) -> None:
        self.client.update_roas.side_effect = _roa_delta_error(
            notheld=[{"asn": 65003, "prefix": "10.1.0.0/24", "max_length": 24, "comment": None}]
        )

        with self.assertRaises(RPKINotHeldError) as ctx:
            RPKIObjectService.add_roa(self.net, "ca", 65003, "10.1.0.0/24-24 => 65003")

        self.assertEqual(
            str(ctx.exception),
            "Hosted CA AS65003 does not hold 10.1.0.0/24, so it cannot sign a ROA for it",
        )
        self.assertEqual(ctx.exception.hosted_ca, 65003)
        self.assertEqual(ctx.exception.prefixes, ["10.1.0.0/24"])
        self.assertIsInstance(ctx.exception, ValueError)

    def test_notheld_for_several_prefixes_names_each_of_them(self) -> None:
        self.client.update_roas.side_effect = _roa_delta_error(notheld=[
            {"asn": 65003, "prefix": "10.1.0.0/24", "max_length": 24, "comment": None},
            {"asn": 65003, "prefix": "10.2.0.0/24", "max_length": 24, "comment": None},
        ])

        with self.assertRaises(RPKINotHeldError) as ctx:
            RPKIObjectService.add_roa(self.net, "ca", 65003, "10.1.0.0/24-24 => 65003")

        self.assertEqual(
            str(ctx.exception),
            "Hosted CA AS65003 does not hold 10.1.0.0/24, 10.2.0.0/24, so it cannot sign a ROA for them",
        )

    def test_unknowns_on_remove_become_an_rpki_object_error_in_plain_words(self) -> None:
        self.client.update_roas.side_effect = _roa_delta_error(
            unknowns=[{"asn": 65002, "prefix": "10.1.0.0/24", "max_length": 24, "comment": None}]
        )

        with self.assertRaises(RPKIObjectError) as ctx:
            RPKIObjectService.remove_roa(self.net, "ca", 65002, "10.1.0.0/24-24 => 65002")

        self.assertNotIsInstance(ctx.exception, RPKINotHeldError)
        self.assertEqual(
            str(ctx.exception),
            "Hosted CA AS65002 refused the ROA change: "
            "10.1.0.0/24-24 => 65002 is not signed here, so it cannot be removed",
        )

    def test_duplicates_become_an_rpki_object_error_in_plain_words(self) -> None:
        self.client.update_roas.side_effect = _roa_delta_error(
            duplicates=[{"asn": 65002, "prefix": "10.1.0.0/24", "max_length": 24, "comment": None}]
        )

        with self.assertRaisesRegex(RPKIObjectError, "is already signed here"):
            RPKIObjectService.add_roa(self.net, "ca", 65002, "10.1.0.0/24-24 => 65002")

    def test_a_refusal_without_a_delta_carries_krill_message_on_one_line(self) -> None:
        self.client.update_roas.side_effect = KrillError(
            400, "ca-roa-delta-error", "ROA delta rejected:\n\nsomething went wrong"
        )

        with self.assertRaises(RPKIObjectError) as ctx:
            RPKIObjectService.add_roa(self.net, "ca", 65002, "10.1.0.0/24-24 => 65002")

        self.assertEqual(
            str(ctx.exception),
            "Hosted CA AS65002 refused the ROA change: ROA delta rejected: something went wrong",
        )

    def test_an_unreachable_krill_stays_a_krill_error(self) -> None:
        self.client.update_roas.side_effect = KrillError(None, "unreachable", "no answer")

        with self.assertRaises(KrillError) as ctx:
            RPKIObjectService.add_roa(self.net, "ca", 65002, "10.1.0.0/24-24 => 65002")

        self.assertNotIsInstance(ctx.exception, ValueError)


@pytest.mark.unit
class RPKIObjectServiceASPATests(KrillClientStubMixin, unittest.TestCase):
    """ASPA changes at a Hosted CA: only its own customer, and Krill's refusals."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _lab()

    def test_add_aspa_signs_it_for_the_hosted_ca_as_customer(self) -> None:
        result = RPKIObjectService.add_aspa(self.net, "ca", 65002, "AS65002 => AS65001, AS65005")

        self.client.update_aspas.assert_called_once_with(
            "as65002",
            add_or_replace=[ASPA.parse("AS65002 => AS65001, AS65005")],
            remove=[],
        )
        self.assertEqual(result["aspa"], {"customer": 65002, "providers": [65001, 65005]})

    def test_an_aspa_for_another_customer_is_refused_before_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "signs ASPAs only for its own ASN, not AS65009"):
            RPKIObjectService.add_aspa(self.net, "ca", 65002, "AS65009 => AS65001")
        self.assertEqual(self.client.mock_calls, [])

    def test_an_aspa_without_providers_is_refused_before_writing(self) -> None:
        with self.assertRaises(ValueError):
            RPKIObjectService.add_aspa(self.net, "ca", 65002, "AS65002 =>")
        self.assertEqual(self.client.mock_calls, [])

    def test_remove_aspa_withdraws_the_hosted_cas_own_customer(self) -> None:
        RPKIObjectService.remove_aspa(self.net, "ca", 65002, "AS65002")

        self.client.update_aspas.assert_called_once_with(
            "as65002", add_or_replace=[], remove=[65002]
        )

    def test_remove_aspa_for_another_customer_is_refused_before_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "not AS65009"):
            RPKIObjectService.remove_aspa(self.net, "ca", 65002, 65009)
        self.assertEqual(self.client.mock_calls, [])

    def test_a_krill_refusal_of_an_aspa_becomes_an_rpki_object_error(self) -> None:
        self.client.update_aspas.side_effect = KrillError(
            400, "ca-aspa-unknown-customer-as", "AS65002 has no ASPA to remove"
        )

        with self.assertRaisesRegex(
            RPKIObjectError,
            "Hosted CA AS65002 refused the ASPA change: AS65002 has no ASPA to remove",
        ):
            RPKIObjectService.remove_aspa(self.net, "ca", 65002, 65002)
