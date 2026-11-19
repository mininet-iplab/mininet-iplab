"""Tests for the DHCP Relay activity query seam."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, mock_open, patch

from mniplab.tests.fakes import cli_output
from mniplab.dhcp.relay import Relay
from mniplab.dhcp.relay_activity import RelayActivityQueryService


class RelayActivityParserTests(unittest.TestCase):
    """Relay activity parsing accepts forwarded packets and ignores other log lines."""

    def test_parses_forwarded_ipv4_and_ipv6_requests(self) -> None:
        log_text = "\n".join(
            [
                "dnsmasq-dhcp: DHCP relay from 10.50.0.1 to 10.0.0.2",
                "dnsmasq-dhcp: DHCP relay at 10.50.0.1 -> 10.0.0.2",
                "dnsmasq-dhcp: DHCP relay at 2001:db8:50::1 -> 2001:db8::2",
            ]
        )

        self.assertEqual(
            RelayActivityQueryService.parse_log(log_text),
            [
                {"source": "10.50.0.1", "server": "10.0.0.2"},
                {"source": "2001:db8:50::1", "server": "2001:db8::2"},
            ],
        )

    def test_ignores_error_and_unrecognised_lines(self) -> None:
        log_text = "\n".join(
            [
                "dnsmasq: failed to start",
                "some unrelated message",
                "dnsmasq-dhcp: DHCP relay from 10.50.0.1 to 10.0.0.2",
            ]
        )

        self.assertEqual(RelayActivityQueryService.parse_log(log_text), [])

    def test_empty_log_has_no_activity(self) -> None:
        self.assertEqual(RelayActivityQueryService.parse_log(""), [])


class RelayActivityQueryTests(unittest.TestCase):
    """The query service reads the relay log and returns its activity."""

    def test_reads_the_attached_node_log(self) -> None:
        node = Mock(name="r1")
        node.name = "r1"
        node.relays = [Relay("10.50.0.0/24", server="10.0.0.2")]
        net = Mock()
        net.get.return_value = node
        log_text = "dnsmasq-dhcp: DHCP relay at 10.50.0.1 -> 10.0.0.2\n"

        with patch("builtins.open", mock_open(read_data=log_text)) as open_file:
            result = RelayActivityQueryService.get_activity(net, "r1")

        self.assertEqual(
            result,
            {
                "success": True,
                "activity": [{"source": "10.50.0.1", "server": "10.0.0.2"}],
            },
        )
        open_file.assert_called_once()

    def test_empty_log_is_successful_with_no_activity(self) -> None:
        node = Mock(name="r1")
        node.name = "r1"
        node.relays = [Relay("10.50.0.0/24", server="10.0.0.2")]
        net = Mock()
        net.get.return_value = node

        with patch("builtins.open", mock_open(read_data="")):
            result = RelayActivityQueryService.get_activity(net, "r1")

        self.assertEqual(result, {"success": True, "activity": []})

    def test_missing_node_is_reported(self) -> None:
        net = Mock()
        net.get.return_value = None

        with self.assertRaises(ValueError) as context:
            RelayActivityQueryService.get_activity(net, "ghost")

        self.assertIn("Node not found", str(context.exception))

    def test_missing_log_reports_that_relay_is_not_running(self) -> None:
        node = Mock(name="r1")
        node.name = "r1"
        node.relays = [Relay("10.50.0.0/24", server="10.0.0.2")]
        net = Mock()
        net.get.return_value = node

        with patch("builtins.open", side_effect=FileNotFoundError):
            with self.assertRaises(ValueError) as context:
                RelayActivityQueryService.get_activity(net, "r1")

        self.assertIn("relay log", str(context.exception))


class ShowRelayActivityCommandTests(unittest.TestCase):
    """The CLI displays activity returned by the shared query service."""

    def test_prints_forwarded_requests(self) -> None:

        with patch(
            "mniplab.dhcp.relay_activity.RelayActivityQueryService.get_activity",
            return_value={
                "success": True,
                "activity": [{"source": "10.50.0.1", "server": "10.0.0.2"}],
            },
        ):
            printed = cli_output("show_relay_activity", "r1")

        self.assertIn("10.50.0.1 -> 10.0.0.2", printed)

    def test_reports_no_activity_without_erroring(self) -> None:

        with patch(
            "mniplab.dhcp.relay_activity.RelayActivityQueryService.get_activity",
            return_value={"success": True, "activity": []},
        ):
            printed = cli_output("show_relay_activity", "r1")

        self.assertIn("No Relay activity", printed)

    def test_requires_a_node_argument(self) -> None:

        self.assertIn("Usage", cli_output("show_relay_activity", ""))


if __name__ == "__main__":
    unittest.main()
