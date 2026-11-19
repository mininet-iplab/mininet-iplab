"""Tests for reading and driving replication from the CLI, the API and the service.

The parser itself is covered in test_dns_transfer_log.py; what is asserted here
is that the CLI and Web API expose the same read-side service, that the two
transfer verbs stay two commands, and that a Record write against a Secondary
Zone is refused with a message naming the Primary.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mniplab.cli import mnIPLabCLI
from mniplab.dns import ARecord, DNSService, SecondaryZone, Zone
from mniplab.dns.query import DNSQueryService
from mniplab.dns.record_service import DNSRecordService
from mniplab.tests.fakes import (
    cli_output,
    FAKE_LAB_KEY,
    FakeLab,
    FakeLabAliveMixin,
    FakeNode,
    FakeTerminalCloserMixin,
    KnotClientStubMixin,
    LoginClientMixin,
)
from mniplab.webserver import registry

APEX = "example.com."

TRANSFER_LOG = """\
2026-09-10T14:58:50+0000 info: [example.com.] AXFR, incoming, remote 10.0.1.2@53, started
2026-09-10T14:58:50+0000 info: [example.com.] AXFR, incoming, remote 10.0.1.2@53, finished, 3.07 seconds, 1 messages, 844 bytes
2026-09-10T14:58:50+0000 info: [example.com.] refresh, remote 10.0.1.2@53, zone updated, 3.07 seconds, serial none -> 1, expires in 600 seconds
"""


def _net(primary: bool = True) -> Any:
    """Build a fake Lab holding one DNS-serving Node.

    Args:
        primary: True for a Node primary for the apex, False for a Secondary.

    Returns:
        The Lab, usable as the `net` argument of the query service.
    """
    zone: Any = (
        Zone(APEX, [ARecord("web", "10.0.0.1")], host_node="ns1")
        if primary
        else SecondaryZone(APEX, primary="10.0.1.2", host_node="ns1")
    )
    return FakeLab(FakeNode("ns1", services=[DNSService(zone)]))


@pytest.mark.unit
class TransferLogQueryServiceTests(unittest.TestCase):
    """DNSQueryService reads the Transfer Log from Knot's own log file."""

    def _with_log(self, text: str, net: Any, zone: Any = APEX) -> dict:
        """Run get_dns_transfers against a temporary log file.

        Args:
            text: Log content to place at the Node's log path.
            net: The fake Lab.
            zone: Zone apex to narrow to, or None.

        Returns:
            The service's result dict.
        """
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "knot.log")
            with open(log_path, "w", encoding="utf-8") as log_file:
                log_file.write(text)
            with patch(
                "mniplab.dns.query.DNSProvisioner.log_host_path", return_value=log_path
            ):
                return DNSQueryService.get_dns_transfers(net, "ns1", zone)

    def test_transfers_are_read_from_the_log_knot_already_writes(self) -> None:
        """No new log target: the Transfer Log comes off the host-visible file."""
        result = self._with_log(TRANSFER_LOG, _net(primary=False))

        self.assertEqual(result["node"], "ns1")
        self.assertEqual(result["role"], "secondary")
        self.assertEqual(result["primary"], "10.0.1.2")
        self.assertEqual(len(result["transfers"]), 1)
        self.assertEqual(result["transfers"][0]["bytes"], 844)
        self.assertEqual(result["state"]["state"], "replicated")

    def test_a_missing_log_is_an_empty_transfer_log(self) -> None:
        """A Node that has transferred nothing is not an error."""
        with patch(
            "mniplab.dns.query.DNSProvisioner.log_host_path",
            return_value="/nonexistent/knot.log",
        ):
            result = DNSQueryService.get_dns_transfers(_net(), "ns1")

        self.assertEqual(result["transfers"], [])
        self.assertEqual(result["state"]["state"], "unknown")

    def test_an_unhosted_zone_is_rejected(self) -> None:
        """Asking for a Zone the Node does not serve names the mistake."""
        with self.assertRaises(ValueError) as ctx:
            DNSQueryService.get_dns_transfers(_net(), "ns1", "other.test.")
        self.assertIn("not hosted", str(ctx.exception))

    def test_show_dns_reports_role_and_primary(self) -> None:
        """Role and Primary belong on the existing command, not a new one."""
        roles = DNSQueryService.zone_role(_net(primary=False).get("ns1").services[0], APEX)
        self.assertEqual(roles, {"role": "secondary", "primary": "10.0.1.2"})

        roles = DNSQueryService.zone_role(_net().get("ns1").services[0], "example.com")
        self.assertEqual(roles, {"role": "primary", "primary": ""})


@pytest.mark.unit
class TransferCommandTests(KnotClientStubMixin, unittest.TestCase):
    """refresh and retransfer are two verbs against a Secondary Zone."""

    def test_refresh_asks_knot_to_check_the_serial(self) -> None:
        """A refresh moves nothing when the Serial has not advanced."""
        result = DNSQueryService.refresh_zone(_net(primary=False), "ns1", APEX)

        self.client.refresh_zone.assert_called_once_with(APEX)
        self.client.retransfer_zone.assert_not_called()
        self.assertEqual(result["action"], "refresh")
        self.assertEqual(result["primary"], "10.0.1.2")

    def test_retransfer_asks_knot_for_a_full_copy(self) -> None:
        """The forced copy is its own Knot verb, not a flag on the refresh."""
        result = DNSQueryService.retransfer_zone(_net(primary=False), "ns1", APEX)

        self.client.retransfer_zone.assert_called_once_with(APEX)
        self.client.refresh_zone.assert_not_called()
        self.assertEqual(result["action"], "retransfer")

    def test_a_primary_zone_cannot_be_transferred_into(self) -> None:
        """Only a Secondary pulls a Zone; a Primary authors it."""
        with self.assertRaises(ValueError) as ctx:
            DNSQueryService.refresh_zone(_net(primary=True), "ns1", APEX)
        self.assertIn("primary for", str(ctx.exception))


@pytest.mark.unit
class SecondaryRecordWriteTests(KnotClientStubMixin, unittest.TestCase):
    """A Record write against a replicated Zone is refused, naming the Primary."""

    def _write(self, net: Any, delete: bool = False) -> None:
        """Attempt a Record mutation against the Lab's only Node.

        Args:
            net: The fake Lab.
            delete: True to attempt a deletion rather than an addition.
        """
        if delete:
            DNSRecordService.unset_record(net, "ns1", APEX, "web")
        else:
            DNSRecordService.set_record(net, "ns1", APEX, "web", "A", "10.0.0.9")

    def test_adding_a_record_to_a_secondary_names_the_primary(self) -> None:
        """Knot's own transaction error says nothing about where the Zone lives."""
        with self.assertRaises(ValueError) as ctx:
            self._write(_net(primary=False))

        message = str(ctx.exception)
        self.assertIn("secondary", message)
        self.assertIn("10.0.1.2", message)

    def test_deleting_a_record_from_a_secondary_is_refused_too(self) -> None:
        """Both mutations go the same way, since both would be overwritten."""
        with self.assertRaises(ValueError) as ctx:
            self._write(_net(primary=False), delete=True)
        self.assertIn("10.0.1.2", str(ctx.exception))

    def test_a_primary_zone_still_accepts_writes(self) -> None:
        """The refusal must not touch the Zone a Learner is meant to edit."""
        result = DNSRecordService.set_record(_net(), "ns1", APEX, "web", "A", "10.0.0.9")

        self.assertEqual(result["owner"], "web")
        self.client.commit_zone.assert_called_once_with(APEX)


@pytest.mark.unit
class ReplicationCLITests(unittest.TestCase):
    """The CLI surfaces: show_dns columns, the Transfer Log, and the two verbs."""

    def test_show_dns_gains_role_and_primary_columns(self) -> None:
        """Role reads off the existing command rather than a new one."""
        dns_info = {
            "node": "ns2",
            "running": True,
            "socket_path": "/tmp/knot-service/ns2/run/knot.sock",
            "socket_state": "active",
            "listeners": ["0.0.0.0@53"],
            "zones": [APEX],
            "roles": {APEX: {"role": "secondary", "primary": "ns1"}},
        }
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_info", return_value=dns_info
        ):
            printed = cli_output("show_dns", "ns2")

        self.assertIn("Role", printed)
        self.assertIn("Primary", printed)
        self.assertIn("secondary", printed)
        self.assertIn("ns1", printed)

    def test_show_dns_transfers_prints_the_transfer_log(self) -> None:
        """Every transfer's type, Serial move and byte count is on the line."""
        result = {
            "node": "ns2",
            "zone": APEX,
            "role": "secondary",
            "primary": "ns1",
            "transfers": [
                {
                    "timestamp": "2026-09-10T14:58:53+0000",
                    "zone": APEX,
                    "direction": "incoming",
                    "transfer_type": "IXFR",
                    "remote": "10.0.1.2@53",
                    "outcome": "finished",
                    "serial_from": 1,
                    "serial_to": 2,
                    "messages": 1,
                    "bytes": 277,
                    "duration": 0.0,
                    "detail": "",
                }
            ],
            "state": {
                "state": "replicated",
                "detail": "zone updated",
                "since": "2026-09-10T14:58:53+0000",
                "next_retry": None,
                "serial": 2,
            },
        }
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_transfers", return_value=result
        ):
            printed = cli_output("show_dns_transfers", "ns2 example.com.")

        self.assertIn("IXFR", printed)
        self.assertIn("1 -> 2", printed)
        self.assertIn("277", printed)
        self.assertIn("replicated", printed)

    def test_show_dns_transfers_reports_an_empty_log(self) -> None:
        """Nothing transferred yet is a sentence, not a blank table."""
        result = {
            "node": "ns2", "zone": "", "role": "", "primary": "",
            "transfers": [],
            "state": {"state": "unknown", "detail": "", "since": "",
                      "next_retry": None, "serial": None},
        }
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_transfers", return_value=result
        ):
            printed = cli_output("show_dns_transfers", "ns2")

        self.assertIn("No zone transfers logged yet", printed)

    def test_a_failed_state_names_its_cause_and_next_retry(self) -> None:
        """Each runtime state carries why it happened and when it retries."""
        result = {
            "node": "ns2", "zone": APEX, "role": "secondary", "primary": "ns1",
            "transfers": [],
            "state": {
                "state": "transfer-refused",
                "detail": "refresh, remote primary, address 10.0.1.2@53, failed (not allowed)",
                "since": "2026-09-10T14:59:52+0000",
                "next_retry": "2026-09-10T15:00:12+0000",
                "serial": None,
            },
        }
        with patch(
            "mniplab.dns.query.DNSQueryService.get_dns_transfers", return_value=result
        ):
            printed = cli_output("show_dns_transfers", "ns2 example.com.")

        self.assertIn("transfer-refused", printed)
        self.assertIn("not allowed", printed)
        self.assertIn("Next retry: 2026-09-10T15:00:12+0000", printed)

    @staticmethod
    def _transfer_result(transfers: list, state: dict) -> dict:
        """Return a get_dns_transfers payload for a Secondary Zone.

        Args:
            transfers: The Transfer Log events to report.
            state: The replication state to report.

        Returns:
            The payload the CLI reads back after a transfer verb.
        """
        return {
            "node": "ns2", "zone": APEX, "role": "secondary", "primary": "ns1",
            "transfers": transfers, "state": state,
        }

    def test_the_two_verbs_are_two_commands(self) -> None:
        """A flag would hide the distinction the Lab is teaching."""
        self.assertTrue(hasattr(mnIPLabCLI, "do_dns_refresh"))
        self.assertTrue(hasattr(mnIPLabCLI, "do_dns_retransfer"))

        net = FakeLab()
        for command, method in (
            ("dns_refresh", "refresh_zone"),
            ("dns_retransfer", "retransfer_zone"),
        ):
            with patch(
                f"mniplab.dns.query.DNSQueryService.{method}",
                return_value={"node": "ns2", "zone": APEX, "action": method,
                              "primary": "ns1"},
            ) as action, patch(
                "mniplab.dns.query.DNSQueryService.get_dns_transfers",
                return_value=self._transfer_result([], {"state": "up-to-date",
                                                        "serial": 2, "detail": ""}),
            ), patch("mniplab.cli.time.sleep"):
                cli_output(command, "ns2 example.com.", net=net)
            action.assert_called_once_with(net, "ns2", "example.com.")

    def test_an_unchanged_zone_reports_up_to_date_and_moves_nothing(self) -> None:
        """The first of the three outcomes must be readable from the verb itself."""
        with patch(
            "mniplab.dns.query.DNSQueryService.refresh_zone",
            return_value={"node": "ns2", "zone": APEX, "action": "refresh",
                          "primary": "ns1"},
        ), patch(
            "mniplab.dns.query.DNSQueryService.get_dns_transfers",
            return_value=self._transfer_result([], {"state": "up-to-date",
                                                    "serial": 2, "detail": ""}),
        ), patch("mniplab.cli.time.sleep"):
            printed = cli_output("dns_refresh", "ns2 example.com.")

        self.assertIn("up to date at serial 2", printed)
        self.assertIn("nothing transferred", printed)

    def test_a_forced_copy_reports_what_crossed_the_wire(self) -> None:
        """A retransfer names its type, its Serial and its size at the prompt."""
        forced = {
            "timestamp": "2026-09-10T14:58:58+0000", "zone": APEX,
            "direction": "incoming", "transfer_type": "AXFR",
            "remote": "10.0.1.2@53", "outcome": "finished",
            "serial_from": 2, "serial_to": 2, "messages": 1, "bytes": 869,
            "duration": 0.0, "detail": "",
        }
        with patch(
            "mniplab.dns.query.DNSQueryService.retransfer_zone",
            return_value={"node": "ns2", "zone": APEX, "action": "retransfer",
                          "primary": "ns1"},
        ), patch(
            "mniplab.dns.query.DNSQueryService.get_dns_transfers",
            side_effect=[
                self._transfer_result([], {"state": "replicated", "serial": 2}),
                self._transfer_result([forced], {"state": "replicated", "serial": 2}),
            ],
        ), patch("mniplab.cli.time.sleep"):
            printed = cli_output("dns_retransfer", "ns2 example.com.")

        self.assertIn("AXFR 2 -> 2", printed)
        self.assertIn("869 bytes", printed)

    def test_the_verbs_print_usage_without_a_zone(self) -> None:
        """Both commands are addressed by Node and Zone."""
        for command, usage in (
            ("dns_refresh", "Usage: dns_refresh <node> <zone>"),
            ("dns_retransfer", "Usage: dns_retransfer <node> <zone>"),
        ):
            self.assertIn(usage, cli_output(command, "ns2"))

    def test_a_refused_transfer_verb_reports_the_error(self) -> None:
        """A Primary Zone named by mistake is explained, not traced."""
        with patch(
            "mniplab.dns.query.DNSQueryService.refresh_zone",
            side_effect=ValueError("ns1 is primary for example.com."),
        ), patch(
            "mniplab.dns.query.DNSQueryService.get_dns_transfers",
            return_value=self._transfer_result([], {"state": "unknown"}),
        ):
            printed = cli_output("dns_refresh", "ns1 example.com.")

        self.assertIn("Error: ns1 is primary for example.com.", printed)


@pytest.mark.unit
class ReplicationRouteTests(LoginClientMixin, FakeLabAliveMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Transfer Log, refresh and retransfer routes under /api/dns."""

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_transfer_log_route_is_addressed_by_node_and_zone(self) -> None:
        """One route serves a whole Node or one of its Zones."""
        payload = {"node": "ns2", "zone": APEX, "transfers": [], "state": {}}
        with patch(
            "mniplab.webserver.registry.get_dns_transfers",
            new=AsyncMock(return_value=payload),
        ) as transfers:
            response = self.client.get("/api/labs/fake/dns/ns2/transfers?zone=example.com.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        transfers.assert_awaited_once_with(FAKE_LAB_KEY, "ns2", "example.com.")

    def test_transfer_log_route_without_a_zone_covers_the_node(self) -> None:
        """An omitted Zone reads every Zone the Node serves."""
        with patch(
            "mniplab.webserver.registry.get_dns_transfers",
            new=AsyncMock(return_value={"node": "ns2", "transfers": []}),
        ) as transfers:
            self.client.get("/api/labs/fake/dns/ns2/transfers")

        transfers.assert_awaited_once_with(FAKE_LAB_KEY, "ns2", None)

    def test_transfer_log_route_passes_include_notify(self) -> None:
        """The include_notify query parameter is passed through to the registry."""
        with patch(
            "mniplab.webserver.registry.get_dns_transfers",
            new=AsyncMock(return_value={"node": "ns2", "transfers": []}),
        ) as transfers:
            self.client.get("/api/labs/fake/dns/ns2/transfers?include_notify=true")

        transfers.assert_awaited_once_with(FAKE_LAB_KEY, "ns2", None, include_notify=True)

    def test_refresh_and_retransfer_are_separate_routes(self) -> None:
        """The two verbs stay distinguishable across the API too."""
        for path, method in (
            ("refresh", "refresh_dns_zone"),
            ("retransfer", "retransfer_dns_zone"),
        ):
            with patch(
                f"mniplab.webserver.registry.{method}",
                new=AsyncMock(return_value={"node": "ns2", "zone": APEX, "action": path}),
            ) as action:
                response = self.client.post(
                    f"/api/labs/fake/dns/ns2/zones/example.com./{path}"
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["action"], path)
            action.assert_awaited_once_with(FAKE_LAB_KEY, "ns2", "example.com.")

    def test_a_primary_zone_refresh_returns_400(self) -> None:
        """Driving a transfer on a Primary is a request error, not a crash."""
        with patch(
            "mniplab.webserver.registry.refresh_dns_zone",
            new=AsyncMock(side_effect=ValueError("ns1 is primary for example.com.")),
        ):
            response = self.client.post("/api/labs/fake/dns/ns1/zones/example.com./refresh")

        self.assertEqual(response.status_code, 400)

    def test_the_routes_require_authentication(self) -> None:
        """Replication is readable and drivable only by a signed-in user."""
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/labs/fake/dns/ns2/transfers").status_code, 401)
        self.assertEqual(
            self.client.post("/api/labs/fake/dns/ns2/zones/example.com./refresh").status_code, 401
        )


@pytest.mark.unit
class ReplicationRegistryTests(unittest.TestCase):
    """The registry delegates each replication call to the typed Lab handle."""

    def setUp(self) -> None:
        self.reg = registry
        self.reg.lab_service.lab = FakeLab()
        self.addCleanup(setattr, self.reg.lab_service, "lab", None)

    def test_delegation_methods_call_lab_handle(self) -> None:
        """Every replication method delegates to the LabHandle method."""
        expected = {"node": "ns2", "zone": APEX}
        for method_name, call, args in (
            ("get_dns_transfers", self.reg.get_dns_transfers, ("ns2", APEX)),
            ("refresh_dns_zone", self.reg.refresh_dns_zone, ("ns2", APEX)),
            ("retransfer_dns_zone", self.reg.retransfer_dns_zone, ("ns2", APEX)),
        ):
            with patch.object(self.reg.lab(FAKE_LAB_KEY).handle, method_name, return_value=expected) as mock_fn:
                self.assertEqual(asyncio.run(call(FAKE_LAB_KEY, *args)), expected)
                if method_name == "get_dns_transfers":
                    mock_fn.assert_called_once_with("ns2", zone=APEX, include_notify=False)
                else:
                    mock_fn.assert_called_once_with(*args)
