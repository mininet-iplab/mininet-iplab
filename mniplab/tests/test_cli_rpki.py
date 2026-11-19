"""Unit tests for the RPKI Lab Prompt commands in mniplab/cli.py.

Each command runs through ``cli_output`` against a fake Lab; Krill's socket is
the stub client installed by :class:`KrillClientStubMixin`.
"""

from __future__ import annotations

import re
import unittest

import pytest

from mniplab.errors import KrillError, RoutinatorError
from mniplab.rpki.models import ASPA, ROA, VRP, HostedCA
from mniplab.rpki.service import RPKICAService, ValidatorService
from mniplab.tests.fakes import (
    FakeLab,
    FakeNode,
    KrillClientStubMixin,
    RoutinatorClientStubMixin,
    cli_output,
)


def _lab() -> FakeLab:
    """Build a Lab with one RPKI CA, ``ca``, holding AS65002 (with a ROA and an ASPA) and AS65003."""
    return FakeLab(FakeNode("ca", services=[RPKICAService([
        HostedCA(
            65002,
            ["10.1.0.0/24"],
            roas=["10.1.0.0/24-24 => 65002"],
            aspas=["AS65002 => AS65001"],
        ),
        HostedCA(65003, ["10.3.0.0/24"]),
    ])]))


@pytest.mark.unit
class ShowRPKICommandTests(KrillClientStubMixin, unittest.TestCase):
    """show_rpki: each Hosted CA with its resources, ROAs and ASPAs."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _lab()
        self.client.list_roas.side_effect = lambda handle: (
            [ROA.parse("10.1.0.0/24-24 => 65002")] if handle == "as65002" else []
        )
        self.client.list_aspas.side_effect = lambda handle: (
            [ASPA.parse("AS65002 => AS65001")] if handle == "as65002" else []
        )

    def test_show_rpki_lists_each_hosted_ca_with_its_resources_roas_and_aspas(self) -> None:
        printed = cli_output("show_rpki", "ca", net=self.net)

        self.assertEqual(printed, (
            "*** RPKI CA on ca:\n"
            "    AS65002 (as65002)\n"
            "      Resources: 10.1.0.0/24\n"
            "      ROAs:\n"
            "        10.1.0.0/24-24 => 65002\n"
            "      ASPAs:\n"
            "        AS65002 => AS65001\n"
            "    AS65003 (as65003)\n"
            "      Resources: 10.3.0.0/24\n"
            "      ROAs:\n"
            "        none\n"
            "      ASPAs:\n"
            "        none\n"
        ))

    def test_show_rpki_without_a_node_reads_the_labs_one_rpki_ca(self) -> None:
        printed = cli_output("show_rpki", "", net=self.net)

        self.assertTrue(printed.startswith("*** RPKI CA:\n"))
        self.assertIn("AS65002 (as65002)", printed)

    def test_show_rpki_names_a_node_the_lab_lacks(self) -> None:
        self.assertEqual(
            cli_output("show_rpki", "nope", net=self.net),
            "Error: Node not found: nope\n",
        )

    def test_show_rpki_prints_a_krill_failure_as_an_error(self) -> None:
        self.client.list_roas.side_effect = KrillError(None, "unreachable", "no answer")

        self.assertEqual(
            cli_output("show_rpki", "ca", net=self.net),
            "Error: unreachable: no answer\n",
        )


@pytest.mark.unit
class RPKIRoasCommandTests(KrillClientStubMixin, unittest.TestCase):
    """rpki_roas: adding and removing a ROA at a Hosted CA through the Lab Prompt."""

    USAGE = "Usage: rpki_roas <node> <asn> add|remove <prefix>[-<maxlen>] => <origin>"

    def setUp(self) -> None:
        super().setUp()
        self.net = _lab()

    def test_rpki_roas_without_its_arguments_prints_usage_and_sends_nothing(self) -> None:
        self.assertIn(self.USAGE, cli_output("rpki_roas", "", net=self.net))
        self.assertIn(self.USAGE, cli_output("rpki_roas", "ca 65002 add", net=self.net))
        self.client.update_roas.assert_not_called()

    def test_rpki_roas_add_signs_the_roa_and_says_so(self) -> None:
        printed = cli_output("rpki_roas", "ca 65002 add 10.1.0.0/24-25 => 65002", net=self.net)

        self.assertEqual(printed, "Added ROA 10.1.0.0/24-25 => 65002 at AS65002 on ca\n")
        self.client.update_roas.assert_called_once_with(
            "as65002", added=[ROA.parse("10.1.0.0/24-25 => 65002")], removed=[]
        )

    def test_rpki_roas_remove_withdraws_the_roa_and_says_so(self) -> None:
        printed = cli_output("rpki_roas", "ca 65002 remove 10.1.0.0/24 => 65002", net=self.net)

        self.assertEqual(printed, "Removed ROA 10.1.0.0/24-24 => 65002 at AS65002 on ca\n")
        self.client.update_roas.assert_called_once_with(
            "as65002", added=[], removed=[ROA.parse("10.1.0.0/24-24 => 65002")]
        )

    def test_rpki_roas_prints_a_hosted_ca_refusal_as_an_error(self) -> None:
        self.client.update_roas.side_effect = KrillError(
            400,
            "ca-roa-delta-error",
            "ROA delta rejected",
            {
                "label": "ca-roa-delta-error",
                "delta_error": {
                    "duplicates": [],
                    "notheld": [{"asn": 65003, "prefix": "10.1.0.0/24", "max_length": 24, "comment": None}],
                    "unknowns": [],
                    "invalid_length": [],
                },
            },
        )

        printed = cli_output("rpki_roas", "ca 65003 add 10.1.0.0/24-24 => 65003", net=self.net)

        self.assertEqual(
            printed,
            "Error: Hosted CA AS65003 does not hold 10.1.0.0/24, so it cannot sign a ROA for it\n",
        )

    def test_rpki_roas_prints_an_invalid_roa_as_an_error_and_sends_nothing(self) -> None:
        printed = cli_output("rpki_roas", "ca 65002 add 10.1.0.1/24-24 => 65002", net=self.net)

        self.assertTrue(printed.startswith("Error: '10.1.0.1/24' is not a prefix in CIDR notation"))
        self.client.update_roas.assert_not_called()

    def test_rpki_roas_prints_an_asn_no_hosted_ca_holds_as_an_error(self) -> None:
        self.assertEqual(
            cli_output("rpki_roas", "ca 65009 add 10.1.0.0/24-24 => 65002", net=self.net),
            "Error: No Hosted CA for AS65009. This RPKI CA's Hosted CAs are AS65002, AS65003\n",
        )
        self.client.update_roas.assert_not_called()

    def test_rpki_roas_refuses_an_action_it_does_not_know(self) -> None:
        self.assertEqual(
            cli_output("rpki_roas", "ca 65002 change 10.1.0.0/24 => 65002", net=self.net),
            "Unknown action 'change'. Use 'add' or 'remove'.\n",
        )
        self.client.update_roas.assert_not_called()

    def test_rpki_roas_names_a_node_the_lab_lacks(self) -> None:
        self.assertEqual(
            cli_output("rpki_roas", "nope 65002 add 10.1.0.0/24-24 => 65002", net=self.net),
            "Error: Node not found: nope\n",
        )


@pytest.mark.unit
class RPKIAspasCommandTests(KrillClientStubMixin, unittest.TestCase):
    """rpki_aspas: adding and removing an ASPA at a Hosted CA through the Lab Prompt."""

    USAGE_ADD = "Usage: rpki_aspas <node> <asn> add <customer> => <providers>"
    USAGE_REMOVE = "rpki_aspas <node> <asn> remove <customer> [=> <providers>]"

    def setUp(self) -> None:
        super().setUp()
        self.net = _lab()

    def test_rpki_aspas_without_its_arguments_prints_usage_and_sends_nothing(self) -> None:
        printed = cli_output("rpki_aspas", "", net=self.net)

        self.assertIn(self.USAGE_ADD, printed)
        self.assertIn(self.USAGE_REMOVE, printed)
        self.client.update_aspas.assert_not_called()

    def test_rpki_aspas_add_signs_the_aspa_and_says_so(self) -> None:
        printed = cli_output(
            "rpki_aspas", "ca 65002 add AS65002 => AS65001, AS65005", net=self.net
        )

        self.assertEqual(printed, "Added ASPA AS65002 => AS65001, AS65005 at AS65002 on ca\n")
        self.client.update_aspas.assert_called_once_with(
            "as65002",
            add_or_replace=[ASPA.parse("AS65002 => AS65001, AS65005")],
            remove=[],
        )

    def test_rpki_aspas_remove_withdraws_the_customers_aspa_and_says_so(self) -> None:
        printed = cli_output("rpki_aspas", "ca 65002 remove 65002", net=self.net)

        self.assertEqual(printed, "Removed ASPA for AS65002 at AS65002 on ca\n")
        self.client.update_aspas.assert_called_once_with(
            "as65002", add_or_replace=[], remove=[65002]
        )

    def test_rpki_aspas_remove_takes_the_providers_it_would_have_signed(self) -> None:
        printed = cli_output("rpki_aspas", "ca 65002 remove AS65002 => AS65001", net=self.net)

        self.assertEqual(printed, "Removed ASPA for AS65002 at AS65002 on ca\n")
        self.client.update_aspas.assert_called_once_with(
            "as65002", add_or_replace=[], remove=[65002]
        )

    def test_rpki_aspas_add_without_providers_is_an_error_and_sends_nothing(self) -> None:
        self.assertEqual(
            cli_output("rpki_aspas", "ca 65002 add AS65002", net=self.net),
            "Error: ASPA 'AS65002' must read '<customer> => <provider>[, <provider>...]'\n",
        )
        self.client.update_aspas.assert_not_called()

    def test_rpki_aspas_refuses_an_aspa_for_another_customer(self) -> None:
        self.assertEqual(
            cli_output("rpki_aspas", "ca 65002 add AS65009 => AS65001", net=self.net),
            "Error: Hosted CA AS65002 signs ASPAs only for its own ASN, not AS65009\n",
        )
        self.client.update_aspas.assert_not_called()

    def test_rpki_aspas_refuses_an_action_it_does_not_know(self) -> None:
        self.assertEqual(
            cli_output("rpki_aspas", "ca 65002 sign AS65002 => AS65001", net=self.net),
            "Unknown action 'sign'. Use 'add' or 'remove'.\n",
        )
        self.client.update_aspas.assert_not_called()


def _validator_lab() -> FakeLab:
    """Build a Lab with a Validator, ``rv``, refreshing every 30 seconds, and Router ``r1`` on its link."""
    r1 = FakeNode.with_addresses("r1", "10.0.15.1")
    rv = FakeNode.with_addresses("rv", "10.0.15.2", services=[ValidatorService(refresh=30)])
    return FakeLab(r1, rv)


@pytest.mark.unit
class ShowVRPsCommandTests(RoutinatorClientStubMixin, unittest.TestCase):
    """show_vrps: what a Validator serves, when it refreshed, and which Routers hold a session."""

    def setUp(self) -> None:
        super().setUp()
        self.net = _validator_lab()
        self.client.vrps.return_value = [VRP(prefix="10.1.0.0/24", max_length=24, origin=65002)]
        self.client.aspas.return_value = [{"customer": 65002, "providers": [65001]}]
        self.client.status.return_value = {
            "last_refresh": "2026-11-06T13:36:00.191074841+00:00",
            "rtr_clients": ["10.0.15.1"],
        }

    def test_show_vrps_prints_refresh_times_connected_routers_vrps_and_aspas(self) -> None:
        printed = cli_output("show_vrps", "rv", net=self.net)
        lines = printed.splitlines()

        self.assertEqual(lines[:4], [
            "*** Validator on rv:",
            "    Last refresh: 2026-11-06T13:36:00.191074+00:00",
            "    Next refresh: 2026-11-06T13:36:30.191074+00:00",
            "    Connected Routers: r1",
        ])
        self.assertIn("    VRPs (1):", lines)
        self.assertRegex(printed, r"(?m)^      10\.1\.0\.0/24\s+24\s+AS65002$")
        self.assertIn("    ASPAs (1):", lines)
        self.assertRegex(printed, r"(?m)^      AS65002\s+AS65001$")

    def test_show_vrps_without_a_node_reads_the_labs_one_validator(self) -> None:
        printed = cli_output("show_vrps", "", net=self.net)

        self.assertTrue(printed.startswith("*** Validator on rv:\n"))

    def test_show_vrps_reads_never_before_the_first_run_and_none_for_no_connected_router(self) -> None:
        self.client.status.return_value = {"last_refresh": None, "rtr_clients": []}

        lines = cli_output("show_vrps", "rv", net=self.net).splitlines()

        self.assertEqual(lines[1:4], [
            "    Last refresh: never",
            "    Next refresh: never",
            "    Connected Routers: none",
        ])

    def test_show_vrps_prints_none_for_an_empty_vrp_and_aspa_table(self) -> None:
        self.client.vrps.return_value = []
        self.client.aspas.return_value = []

        lines = cli_output("show_vrps", "rv", net=self.net).splitlines()

        self.assertEqual(lines[4:], [
            "    VRPs (0):",
            "      none",
            "    ASPAs (0):",
            "      none",
        ])

    def test_show_vrps_names_a_node_the_lab_lacks(self) -> None:
        self.assertEqual(
            cli_output("show_vrps", "nope", net=self.net),
            "Error: Node not found: nope\n",
        )
        self.client.status.assert_not_called()

    def test_show_vrps_prints_a_validator_that_is_not_answering_as_an_error(self) -> None:
        self.client.status.side_effect = RoutinatorError(
            "The Validator is not answering: no JSON for /api/v1/status"
        )

        self.assertEqual(
            cli_output("show_vrps", "rv", net=self.net),
            "Error: The Validator is not answering: no JSON for /api/v1/status\n",
        )
