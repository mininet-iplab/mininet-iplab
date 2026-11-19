"""Unit tests for the Routinator client in mniplab/rpki/routinator_client.py.

The bodies below are the replies a live Routinator 0.15.3 gave on its HTTP
endpoint, captured from the RPKI Test Lab. The client is handed a fetch
function that returns them, so it is exercised without a Node or a socket.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict
from unittest.mock import Mock

import pytest

from mniplab.errors import RoutinatorError
from mniplab.rpki.models import VRP
from mniplab.rpki.routinator_client import RoutinatorClient

JSON_BODY = (
    '{"metadata": {"generated": 1793972111, "generatedTime": "2026-11-06T13:35:11Z"},'
    ' "roas": [{"asn": "AS65002", "prefix": "10.1.0.0/24", "maxLength": 24, "ta": "registry"}],'
    ' "aspas": [{"customer": "AS65002", "providers": ["AS65001"], "ta": "registry"}]}'
)

STATUS_BODY = (
    '{"version": "0.15.3", "serial": 1, "now": "2026-11-06T13:36:02Z",'
    ' "lastUpdateStart": "2026-11-06T13:36:00.100000000+00:00",'
    ' "lastUpdateDone": "2026-11-06T13:36:00.191074841+00:00",'
    ' "lastUpdateDuration": 0.09,'
    ' "rtr": {"currentConnections": 1, "bytesRead": 32, "bytesWritten": 116,'
    '   "clients": {"10.0.15.1": {"connections": 1, "serial": 0,'
    '     "updated": "2026-11-06T13:36:00+00:00", "lastReset": "2026-11-06T13:35:56+00:00",'
    '     "resetQueries": 1, "serialQueries": 2, "read": 32, "written": 116}}}}'
)

STATUS_BEFORE_FIRST_RUN = (
    '{"version": "0.15.3", "serial": 0, "now": "2026-11-06T13:35:20Z",'
    ' "lastUpdateStart": null, "lastUpdateDone": null, "rtr": {"currentConnections": 0}}'
)

STATUS_WITH_A_DROPPED_CLIENT = (
    '{"version": "0.15.3", "serial": 2, "now": "2026-11-06T13:40:00Z",'
    ' "lastUpdateDone": "2026-11-06T13:39:50+00:00",'
    ' "rtr": {"currentConnections": 1, "clients": {'
    '   "10.0.15.1": {"connections": 1, "serial": 2},'
    '   "10.0.16.1": {"connections": 0, "serial": 1}}}}'
)


def _client(body: str) -> tuple[RoutinatorClient, Mock]:
    """Build a client whose fetch answers every path with one body.

    Args:
        body: The text every fetch returns.

    Returns:
        The client and the Mock standing in for its fetch function.
    """
    fetch = Mock(return_value=body)
    return RoutinatorClient(fetch), fetch


@pytest.mark.unit
class RoutinatorClientConstructionTests(unittest.TestCase):
    """Constructing the client touches nothing: each read is a request of its own."""

    def test_constructing_the_client_does_not_fetch(self) -> None:
        fetch = Mock(return_value=JSON_BODY)
        RoutinatorClient(fetch)
        self.assertEqual(fetch.mock_calls, [])


@pytest.mark.unit
class RoutinatorClientVRPTests(unittest.TestCase):
    """The VRPs and ASPAs read from Routinator's /json."""

    def test_vrps_are_read_from_the_json_roas(self) -> None:
        client, fetch = _client(JSON_BODY)

        self.assertEqual(client.vrps(), [VRP(prefix="10.1.0.0/24", max_length=24, origin=65002)])
        fetch.assert_called_once_with("/json")

    def test_aspas_are_read_with_their_customer_and_providers_as_integers(self) -> None:
        client, _fetch = _client(JSON_BODY)

        self.assertEqual(client.aspas(), [{"customer": 65002, "providers": [65001]}])

    def test_a_json_body_with_no_roas_or_aspas_reads_as_empty(self) -> None:
        client, _fetch = _client('{"metadata": {}, "roas": [], "aspas": []}')

        self.assertEqual(client.vrps(), [])
        self.assertEqual(client.aspas(), [])


@pytest.mark.unit
class RoutinatorClientStatusTests(unittest.TestCase):
    """The refresh time and the connected RTR clients read from /api/v1/status."""

    def test_status_reads_the_last_refresh_and_the_connected_router_address(self) -> None:
        client, fetch = _client(STATUS_BODY)

        self.assertEqual(client.status(), {
            "last_refresh": "2026-11-06T13:36:00.191074841+00:00",
            "rtr_clients": ["10.0.15.1"],
        })
        fetch.assert_called_once_with("/api/v1/status")

    def test_no_last_refresh_before_the_first_run_completes(self) -> None:
        client, _fetch = _client(STATUS_BEFORE_FIRST_RUN)

        self.assertIsNone(client.status()["last_refresh"])

    def test_a_status_without_last_update_done_has_no_last_refresh(self) -> None:
        client, _fetch = _client('{"version": "0.15.3", "rtr": {"clients": {}}}')

        self.assertIsNone(client.status()["last_refresh"])

    def test_a_client_with_no_open_connection_is_not_reported_as_connected(self) -> None:
        client, _fetch = _client(STATUS_WITH_A_DROPPED_CLIENT)

        self.assertEqual(client.status()["rtr_clients"], ["10.0.15.1"])

    def test_no_rtr_clients_are_listed_without_rtr_client_metrics(self) -> None:
        client, _fetch = _client(
            '{"version": "0.15.3", "lastUpdateDone": "2026-11-06T13:36:00+00:00",'
            ' "rtr": {"currentConnections": 1}}'
        )

        self.assertEqual(client.status()["rtr_clients"], [])


@pytest.mark.unit
class RoutinatorClientNotAnsweringTests(unittest.TestCase):
    """An empty or non-JSON body means the Validator is not answering."""

    def _assert_refused(self, body: str, read: Any) -> None:
        """Check that a read of a body raises RoutinatorError saying the Validator is not answering.

        Args:
            body: The body the fetch returns.
            read: The client method to call.
        """
        client, _fetch = _client(body)
        with self.assertRaises(RoutinatorError) as ctx:
            read(client)
        self.assertIn("not answering", str(ctx.exception))

    def test_an_empty_body_from_json_raises_routinator_error(self) -> None:
        self._assert_refused("", lambda client: client.vrps())

    def test_an_empty_body_from_status_raises_routinator_error(self) -> None:
        self._assert_refused("", lambda client: client.status())

    def test_a_non_json_body_raises_routinator_error(self) -> None:
        self._assert_refused("<html>502 Bad Gateway</html>", lambda client: client.aspas())

    def test_routinator_error_is_a_value_error_so_the_lab_prompt_prints_it(self) -> None:
        self.assertTrue(issubclass(RoutinatorError, ValueError))
