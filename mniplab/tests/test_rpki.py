"""Unit tests for the RPKI CA Service.

Covers the ROA, ASPA and Hosted CA models and their declaration checks, the
RPKI CA Service, the start-time diagnostics, the hosts-file rewrite that keeps
a declared hostname, and the Krill client against a stub unix-socket server.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import tempfile
import threading
import tomllib
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from mniplab.errors import ConfigurationError, KrillError
from mniplab.rpki.diagnostics import RPKIDiagnostics
from mniplab.rpki.krill_client import KrillClient
from mniplab.rpki.models import ASPA, ROA, HostedCA
from mniplab.rpki.service import (
    RPKICAService,
    ValidatorService,
    rpki_ca_services,
    validator_services,
)
from mniplab.rpki.validator_provisioner import ValidatorProvisioner
from mniplab.service import Service
from mniplab.tests.fakes import FakeIntf, FakeLab, FakeNode
from mniplab.tests.labs import parse_rpki_prefix_count, parse_rpki_states
from mniplab.utils import update_hosts_file


def _attached(node: FakeNode, *services: Service) -> FakeNode:
    """Attach RPKI Services to a fake Node the way ``addService`` does.

    Args:
        node: The Node to attach to.
        *services: The Services to attach.

    Returns:
        The same Node.
    """
    for service in services:
        service.node = node
        node.services.append(service)
    return node


@pytest.mark.unit
class ROATests(unittest.TestCase):
    """Parsing and validating a Route Origin Authorisation."""

    def test_parses_an_ipv4_roa_with_a_maximum_length(self) -> None:
        roa = ROA.parse("10.1.0.0/24-25 => 65002")
        self.assertEqual((roa.prefix, roa.max_length, roa.origin), ("10.1.0.0/24", 25, 65002))
        self.assertEqual(ROA.parse(str(roa)), roa)

    def test_parses_an_ipv6_roa_with_an_as_prefixed_origin(self) -> None:
        roa = ROA.parse("2001:db8::/32-48 => AS65002")
        self.assertEqual(roa.origin, 65002)
        self.assertEqual(str(roa), "2001:db8::/32-48 => 65002")
        self.assertEqual(ROA.parse(str(roa)), roa)

    def test_maximum_length_defaults_to_the_prefix_length(self) -> None:
        roa = ROA.parse("10.1.0.0/24 => 65002")
        self.assertEqual(roa.max_length, 24)
        self.assertEqual(str(roa), "10.1.0.0/24-24 => 65002")

    def test_refuses_a_prefix_with_host_bits_set(self) -> None:
        with self.assertRaises(ValueError):
            ROA.parse("10.1.0.1/24-24 => 65002")

    def test_refuses_a_maximum_length_shorter_than_the_prefix(self) -> None:
        with self.assertRaisesRegex(ValueError, "maxLength 23"):
            ROA.parse("10.1.0.0/24-23 => 65002")

    def test_refuses_a_maximum_length_longer_than_the_address_family(self) -> None:
        with self.assertRaisesRegex(ValueError, "maxLength 33"):
            ROA.parse("10.1.0.0/24-33 => 65002")
        with self.assertRaisesRegex(ValueError, "maxLength 129"):
            ROA.parse("2001:db8::/32-129 => 65002")

    def test_refuses_an_asn_of_zero_or_above_the_32_bit_range(self) -> None:
        with self.assertRaises(ValueError):
            ROA.parse("10.1.0.0/24-24 => AS0")
        with self.assertRaises(ValueError):
            ROA.parse("10.1.0.0/24-24 => 4294967296")

    def test_refuses_text_without_the_arrow(self) -> None:
        with self.assertRaises(ValueError):
            ROA.parse("10.1.0.0/24-24 65002")


@pytest.mark.unit
class ASPATests(unittest.TestCase):
    """Parsing and validating an Autonomous System Provider Authorisation."""

    def test_parses_a_customer_and_its_providers(self) -> None:
        aspa = ASPA.parse("AS65002 => AS65001, AS65005")
        self.assertEqual(aspa.customer, 65002)
        self.assertEqual(aspa.providers, [65001, 65005])
        self.assertEqual(str(aspa), "AS65002 => AS65001, AS65005")
        self.assertEqual(ASPA.parse(str(aspa)), aspa)

    def test_refuses_an_aspa_without_a_provider(self) -> None:
        with self.assertRaises(ValueError):
            ASPA.parse("AS65002 =>")

    def test_refuses_a_customer_that_is_not_an_asn(self) -> None:
        with self.assertRaises(ValueError):
            ASPA.parse("ASx => AS65001")


@pytest.mark.unit
class HostedCATests(unittest.TestCase):
    """A Hosted CA's resources and the starting objects checked against them."""

    def test_refuses_a_starting_roa_outside_its_resources(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            HostedCA(65002, ["10.1.0.0/24"], roas=["10.9.0.0/24-24 => 65002"])
        message = str(ctx.exception)
        self.assertIn("Hosted CA AS65002", message)
        self.assertIn("10.9.0.0/24", message)

    def test_accepts_a_starting_roa_inside_a_covering_prefix(self) -> None:
        hosted = HostedCA(65002, ["10.0.0.0/8"], roas=["10.1.0.0/24-24 => 65002"])
        self.assertEqual(hosted.roas, [ROA.parse("10.1.0.0/24-24 => 65002")])

    def test_refuses_an_ipv4_roa_under_only_ipv6_resources(self) -> None:
        with self.assertRaises(ValueError):
            HostedCA(65002, ["2001:db8::/32"], roas=["10.1.0.0/24-24 => 65002"])

    def test_refuses_a_starting_aspa_for_another_customer(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            HostedCA(65002, ["10.1.0.0/24"], aspas=["AS65009 => AS65001"])
        self.assertIn("AS65009", str(ctx.exception))

    def test_refuses_an_empty_prefix_list(self) -> None:
        with self.assertRaises(ValueError):
            HostedCA(65002, [])

    def test_refuses_a_prefix_with_host_bits_set(self) -> None:
        with self.assertRaises(ValueError):
            HostedCA(65002, ["10.1.0.1/24"])

    def test_handle_is_the_krill_name_of_its_asn(self) -> None:
        self.assertEqual(HostedCA(65002, ["10.1.0.0/24"]).handle, "as65002")


@pytest.mark.unit
class RPKICAServiceTests(unittest.TestCase):
    """The RPKI CA Service's declaration and hostname."""

    def test_refuses_no_hosted_ca(self) -> None:
        with self.assertRaises(ValueError):
            RPKICAService([])

    def test_refuses_an_asn_declared_twice(self) -> None:
        with self.assertRaisesRegex(ValueError, "AS65002"):
            RPKICAService([
                HostedCA(65002, ["10.1.0.0/24"]),
                HostedCA(65002, ["10.2.0.0/24"]),
            ])

    def test_refuses_a_hostname_that_is_not_a_dns_name(self) -> None:
        with self.assertRaises(ValueError):
            RPKICAService([HostedCA(65002, ["10.1.0.0/24"])], hostname="bad_name")

    def test_a_declared_hostname_is_the_resolved_one(self) -> None:
        service = RPKICAService([HostedCA(65002, ["10.1.0.0/24"])], hostname="rpki.lab")
        _attached(FakeNode("ca"), service)
        self.assertEqual(service.resolved_hostname, "rpki.lab")

    def test_the_node_name_is_the_resolved_hostname_by_default(self) -> None:
        service = RPKICAService([HostedCA(65002, ["10.1.0.0/24"])])
        _attached(FakeNode("ca"), service)
        self.assertEqual(service.resolved_hostname, "ca")

    def test_an_unattached_service_has_no_resolved_hostname(self) -> None:
        service = RPKICAService([HostedCA(65002, ["10.1.0.0/24"])])
        with self.assertRaises(ValueError):
            service.resolved_hostname  # pylint: disable=pointless-statement

    def test_hosts_lines_name_the_node_by_its_ipv4_addresses_only(self) -> None:
        node = FakeNode("ca", intfs=[FakeIntf("ca-eth0", ip="10.0.14.2", ip6="2001:db8:14::2")])
        service = RPKICAService([HostedCA(65002, ["10.1.0.0/24"])], hostname="rpki.lab")
        _attached(node, service)
        self.assertEqual(service.hosts_lines(), ["10.0.14.2 rpki.lab"])

    def test_rpki_ca_services_lists_only_the_rpki_services(self) -> None:
        service = RPKICAService([HostedCA(65002, ["10.1.0.0/24"])])
        node = _attached(FakeNode("ca"), service)
        self.assertEqual(rpki_ca_services(node), [service])
        self.assertEqual(rpki_ca_services(FakeNode("r1")), [])


@pytest.mark.unit
class ValidatorServiceTests(unittest.TestCase):
    """The Validator Service's refresh interval and its attachment."""

    def test_refresh_defaults_to_ten_seconds(self) -> None:
        self.assertEqual(ValidatorService().refresh, 10)

    def test_accepts_a_positive_whole_number_of_seconds(self) -> None:
        self.assertEqual(ValidatorService(refresh=30).refresh, 30)

    def test_refuses_a_refresh_of_zero_or_less(self) -> None:
        for refresh in (0, -5):
            with self.subTest(refresh=refresh):
                with self.assertRaisesRegex(ValueError, "greater than zero"):
                    ValidatorService(refresh=refresh)

    def test_refuses_a_refresh_that_is_not_a_whole_number(self) -> None:
        for refresh in ("10", 10.5, True):
            with self.subTest(refresh=refresh):
                with self.assertRaisesRegex(ValueError, "whole number"):
                    ValidatorService(refresh=refresh)  # type: ignore[arg-type]

    def test_is_named_validator_and_serves_ipv4_only(self) -> None:
        service = ValidatorService()
        self.assertEqual(service.name, "validator")
        self.assertEqual(service.badge_text, "Validator")
        self.assertEqual(service.families, [4])

    def test_validator_services_lists_only_the_validator_services(self) -> None:
        service = ValidatorService()
        node = _attached(FakeNode("rv"), service)
        self.assertEqual(validator_services(node), [service])
        self.assertEqual(validator_services(FakeNode("r1")), [])


@pytest.mark.unit
class RPKIDiagnosticsTests(unittest.TestCase):
    """Start-time diagnostics for the RPKI CA Service."""

    def test_refuses_a_second_rpki_ca_naming_both_nodes(self) -> None:
        first = _attached(FakeNode("ca1"), RPKICAService([HostedCA(65002, ["10.1.0.0/24"])]))
        second = _attached(FakeNode("ca2"), RPKICAService([HostedCA(65003, ["10.3.0.0/24"])]))
        with self.assertRaises(ConfigurationError) as ctx:
            RPKIDiagnostics.validate_network(FakeLab(first, second))
        self.assertIn("ca1", str(ctx.exception))
        self.assertIn("ca2", str(ctx.exception))

    def test_accepts_one_rpki_ca(self) -> None:
        node = _attached(FakeNode("ca"), RPKICAService([HostedCA(65002, ["10.1.0.0/24"])]))
        RPKIDiagnostics.validate_network(FakeLab(node))

    def test_refuses_a_node_name_that_is_not_a_dns_name(self) -> None:
        node = _attached(FakeNode("my_ca"), RPKICAService([HostedCA(65002, ["10.1.0.0/24"])]))
        with self.assertRaisesRegex(ConfigurationError, "my_ca"):
            RPKIDiagnostics.validate_network(FakeLab(node))

    def test_refuses_a_validator_in_a_lab_without_an_rpki_ca_naming_its_node(self) -> None:
        validator_node = _attached(FakeNode("rv"), ValidatorService())
        with self.assertRaises(ConfigurationError) as ctx:
            RPKIDiagnostics.validate_network(FakeLab(validator_node))
        message = str(ctx.exception)
        self.assertIn("'rv'", message)
        self.assertIn("RPKI CA", message)

    def test_accepts_a_validator_beside_an_rpki_ca(self) -> None:
        ca = _attached(FakeNode("ca"), RPKICAService([HostedCA(65002, ["10.1.0.0/24"])]))
        rv = _attached(FakeNode("rv"), ValidatorService())
        RPKIDiagnostics.validate_network(FakeLab(ca, rv))


@pytest.mark.unit
class UpdateHostsFileTests(unittest.TestCase):
    """The hosts-file rewrite keeps an RPKI CA's declared hostname."""

    def test_keeps_a_declared_hostname_beside_the_node_name(self) -> None:
        node = _attached(
            FakeNode("ca", intfs=[FakeIntf("ca-eth0", ip="10.0.14.2")]),
            RPKICAService([HostedCA(65002, ["10.1.0.0/24"])], hostname="rpki.lab"),
        )
        lab = FakeLab(node)
        hosts_file = os.path.join(lab.lab_dir, "etc", "hosts")
        os.makedirs(os.path.dirname(hosts_file), exist_ok=True)
        update_hosts_file(lab, hosts_file=hosts_file)
        with open(hosts_file, encoding="utf-8") as handle:
            self.assertIn("10.0.14.2 rpki.lab", handle.read().splitlines())


SHOW_BGP_TWO_ORIGINS = """BGP routing table entry for 10.1.0.0/24, version 1
Paths: (2 available, best #1, table default)
  Advertised to peers:
  10.0.12.2 10.0.13.2
  65003
    10.0.13.2 from 10.0.13.2 (1.1.1.1)
      Origin IGP, valid, external, best (Older Path), rpki validation-state: invalid
      Last update: Fri Nov  6 08:08:44 2026
  65002
    10.0.12.2 from 10.0.12.2 (2.2.2.2)
      Origin IGP, metric 0, valid, external, rpki validation-state: valid
      Last update: Fri Nov  6 08:08:44 2026
"""

SHOW_BGP_NOT_FOUND = """BGP routing table entry for 10.1.0.0/24, version 1
Paths: (1 available, best #1, table default)
  Not advertised to any peer
  65003 65002
    10.0.13.2 from 10.0.13.2 (1.1.1.1)
      Origin IGP, valid, external, best (First path received), rpki validation-state: not found
"""

PREFIX_TABLE_EMPTY = """RPKI/RTR prefix table
Prefix                                   Prefix Length  Origin-AS
Number of IPv4 Prefixes: 0
Number of IPv6 Prefixes: 0
"""

PREFIX_TABLE_ONE = """RPKI/RTR prefix table
Prefix                                   Prefix Length  Origin-AS
10.1.0.0                                    24 -  24   65002
Number of IPv4 Prefixes: 1
Number of IPv6 Prefixes: 0
"""


@pytest.mark.unit
class RPKIVtyshParsingTests(unittest.TestCase):
    """Reading validation states and VRP counts from FRR's own output."""

    def test_a_path_reads_its_origin_as_the_last_as_and_its_validation_state(self) -> None:
        self.assertEqual(
            parse_rpki_states(SHOW_BGP_TWO_ORIGINS), {65003: "invalid", 65002: "valid"}
        )

    def test_a_path_reads_not_found_as_a_two_word_state(self) -> None:
        self.assertEqual(parse_rpki_states(SHOW_BGP_NOT_FOUND), {65002: "not found"})

    def test_a_prefix_table_with_no_vrps_counts_zero(self) -> None:
        self.assertEqual(parse_rpki_prefix_count(PREFIX_TABLE_EMPTY), 0)

    def test_a_prefix_table_counts_its_ipv4_vrps(self) -> None:
        self.assertEqual(parse_rpki_prefix_count(PREFIX_TABLE_ONE), 1)

    def test_no_connection_to_the_cache_counts_zero_vrps(self) -> None:
        self.assertEqual(parse_rpki_prefix_count("No connection to RPKI cache server.\n"), 0)

    def test_output_without_a_prefix_count_is_refused_not_read_as_zero(self) -> None:
        with self.assertRaises(ValueError):
            parse_rpki_prefix_count("% Unknown command: show rpki prefix-table\n")


def _tools_present_except(*missing: str) -> Any:
    """Return a stand-in for ``shutil.which`` that finds every tool but the named ones.

    Args:
        *missing: Command names to report as absent from PATH.

    Returns:
        A function with the signature of ``shutil.which`` for the first argument.
    """
    def which(name: str, *args: Any, **kwargs: Any) -> Optional[str]:
        del args, kwargs
        return None if name in missing else f"/usr/bin/{name}"

    return which


@pytest.mark.unit
class ValidatorPreflightTests(unittest.TestCase):
    """The Validator's preflight: FRR's RPKI module, Routinator and Krill must be installed."""

    def test_passes_when_the_module_and_both_binaries_are_installed(self) -> None:
        validator = _attached(FakeNode("rv"), ValidatorService())
        with patch("mniplab.frrouter.config.rpki_module_installed", return_value=True), \
                patch("shutil.which", side_effect=_tools_present_except()):
            ValidatorProvisioner.ensure_available([validator])

    def test_ignores_a_lab_without_a_validator(self) -> None:
        with patch("mniplab.frrouter.config.rpki_module_installed", return_value=False), \
                patch("shutil.which", side_effect=_tools_present_except("routinator", "krill")):
            ValidatorProvisioner.ensure_available([FakeNode("r1")])

    def test_refuses_a_missing_rpki_module_and_points_to_the_install_docs(self) -> None:
        validator = _attached(FakeNode("rv"), ValidatorService())
        with patch("mniplab.frrouter.config.rpki_module_installed", return_value=False), \
                patch("shutil.which", side_effect=_tools_present_except()):
            with self.assertRaises(ConfigurationError) as ctx:
                ValidatorProvisioner.ensure_available([validator])
        message = str(ctx.exception)
        self.assertIn("'rv'", message)
        self.assertIn("bgpd_rpki.so", message)
        self.assertIn("docs/install.md", message)

    def test_refuses_a_missing_routinator_and_points_to_the_install_docs(self) -> None:
        validator = _attached(FakeNode("rv"), ValidatorService())
        with patch("mniplab.frrouter.config.rpki_module_installed", return_value=True), \
                patch("shutil.which", side_effect=_tools_present_except("routinator")):
            with self.assertRaises(ConfigurationError) as ctx:
                ValidatorProvisioner.ensure_available([validator])
        message = str(ctx.exception)
        self.assertIn("routinator", message)
        self.assertIn("docs/install.md", message)

    def test_refuses_a_missing_krill_and_points_to_the_install_docs(self) -> None:
        validator = _attached(FakeNode("rv"), ValidatorService())
        with patch("mniplab.frrouter.config.rpki_module_installed", return_value=True), \
                patch("shutil.which", side_effect=_tools_present_except("krill")):
            with self.assertRaises(ConfigurationError) as ctx:
                ValidatorProvisioner.ensure_available([validator])
        message = str(ctx.exception)
        self.assertIn("krill", message)
        self.assertIn("docs/install.md", message)


@pytest.mark.unit
class ValidatorStartTests(unittest.TestCase):
    """The Validator's start: its TAL comes from the Lab's RPKI CA, and Routinator must answer."""

    def _lab(self) -> Tuple[FakeLab, FakeNode, FakeNode]:
        """Build a Lab with an RPKI CA on ``ca`` and a Validator on ``rv``.

        Returns:
            The Lab, the RPKI CA Node and the Validator Node.
        """
        ca = _attached(FakeNode("ca"), RPKICAService([HostedCA(65002, ["10.1.0.0/24"])]))
        rv = _attached(FakeNode("rv"), ValidatorService())
        return FakeLab(ca, rv), ca, rv

    def test_a_validator_without_an_rpki_ca_fails_the_start_naming_its_node(self) -> None:
        rv = _attached(FakeNode("rv"), ValidatorService())
        with self.assertRaises(ConfigurationError) as ctx:
            ValidatorProvisioner.start_node_services(rv, FakeLab(rv))
        self.assertIn("'rv'", str(ctx.exception))
        self.assertIn("RPKI CA", str(ctx.exception))

    def test_a_failed_tal_fetch_fails_the_start_naming_the_rpki_ca(self) -> None:
        lab, _ca, rv = self._lab()
        with patch.object(KrillClient, "tal", side_effect=KrillError(None, "unreachable", "no answer")):
            with self.assertRaises(ConfigurationError) as ctx:
                ValidatorProvisioner.start_node_services(rv, lab)
        message = str(ctx.exception)
        self.assertIn("'rv'", message)
        self.assertIn("'ca'", message)

    def test_routinator_lists_rtr_clients_only_when_started_with_rtr_client_metrics(self) -> None:
        lab, _ca, rv = self._lab()
        rv.recorder.respond_to("curl", '{"version": "0.15.3"}')
        with patch.object(KrillClient, "tal", return_value="tal"):
            ValidatorProvisioner.start_node_services(rv, lab)
        launched = rv.recorder.issued("routinator")
        self.assertEqual(len(launched), 1)
        _, _, server_options = launched[0].partition(" server ")
        self.assertIn("--rtr-client-metrics", server_options)

    def test_the_tal_is_written_into_the_validators_lab_directory(self) -> None:
        lab, _ca, rv = self._lab()
        rv.recorder.respond_to("curl", '{"version": "0.15.3"}')
        locator = "rsync://ca/ta/ta.cer\n\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n"
        with patch.object(KrillClient, "tal", return_value=locator):
            ValidatorProvisioner.start_node_services(rv, lab)
        tal_path = os.path.join(lab.validator_dir("rv", "etc"), "tals", "registry.tal")
        with open(tal_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), locator)

    def test_routinator_config_in_the_node_home_carries_the_server_values(self) -> None:
        lab, ca, _rv = self._lab()
        ValidatorProvisioner.write_config("rv", lab, ca)
        config_path = os.path.join(lab.node_home_dir("rv"), ".routinator.conf")
        with open(config_path, encoding="utf-8") as handle:
            config = tomllib.loads(handle.read())
        self.assertEqual(config, {
            "repository-dir": lab.validator_dir("rv", "lib"),
            "extra-tals-dir": os.path.join(lab.validator_dir("rv", "etc"), "tals"),
            "no-rir-tals": True,
            "rrdp-root-certs": [lab.rpki_ca_root_cert_path("ca")],
            "disable-rsync": True,
            "allow-dubious-hosts": True,
        })


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Answers each request from the server's table of (method, path) answers."""

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Answer a GET request."""
        self._answer()

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Read a POST body and answer it."""
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._answer()

    def _answer(self) -> None:
        """Send the configured status and JSON body, or a 404 for an unknown path."""
        status, body = self.server.answers.get(  # type: ignore[attr-defined]
            (self.command, self.path),
            (404, {"label": "not-found", "msg": self.path}),
        )
        if body is None:
            payload = b""
        elif isinstance(body, bytes):
            payload = body
        else:
            payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        """Silence the request log."""


class _UnixStubServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """A unix-socket HTTP server holding the answers the test configures."""

    daemon_threads = True

    def __init__(self, socket_path: str) -> None:
        """Bind the server to a socket path with no answers configured.

        Args:
            socket_path: Where to listen.
        """
        super().__init__(socket_path, _StubHandler)
        self.answers: Dict[Tuple[str, str], Tuple[int, Any]] = {}


class _FakeTime:
    """A clock and sleep for pacing tests, moved only by a sleep or by the test."""

    def __init__(self) -> None:
        """Start the clock at a fixed reading, with no sleeps recorded."""
        self.now = 100.0
        self.slept: List[float] = []

    def clock(self) -> float:
        """Return the fake clock's reading.

        Returns:
            The seconds on the fake clock.
        """
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record a sleep and advance the clock by it.

        Args:
            seconds: How long the caller asked to sleep.
        """
        self.slept.append(seconds)
        self.now += seconds


@pytest.mark.unit
class KrillClientTests(unittest.TestCase):
    """The Krill client over a unix socket, against a stub server."""

    API = "/api/v1"

    def setUp(self) -> None:
        self.runtime = tempfile.mkdtemp(prefix="krill-stub-")
        self.addCleanup(shutil.rmtree, self.runtime, True)
        self.socket_path = os.path.join(self.runtime, "krill.sock")
        self.server = _UnixStubServer(self.socket_path)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop, thread)

    def _stop(self, thread: threading.Thread) -> None:
        """Stop the stub server and wait for its loop to end."""
        self.server.shutdown()
        thread.join(timeout=5)

    def answer(self, method: str, path: str, status: int, body: Any) -> None:
        """Configure the stub's answer to one request.

        Args:
            method: The HTTP method.
            path: The full request path, including ``/api/v1``.
            status: The status to answer with.
            body: The JSON body to answer with, or None for an empty one.
        """
        self.server.answers[(method, path)] = (status, body)

    def test_constructing_does_not_connect(self) -> None:
        client = KrillClient(os.path.join(self.runtime, "missing.sock"))
        self.assertEqual(client.socket_path, os.path.join(self.runtime, "missing.sock"))

    def test_a_missing_socket_is_not_running(self) -> None:
        self.assertFalse(KrillClient(os.path.join(self.runtime, "missing.sock")).is_running())

    def test_running_when_krill_lists_its_cas(self) -> None:
        self.answer("GET", f"{self.API}/cas", 200, {"cas": [{"handle": "testbed"}]})
        client = KrillClient(self.socket_path)
        self.assertTrue(client.is_running())
        self.assertEqual(client.list_cas(), ["testbed"])

    def test_an_error_body_becomes_a_krill_error_with_its_label(self) -> None:
        self.answer("POST", f"{self.API}/cas/as65003/routes", 400, {
            "label": "ca-roa-delta-error",
            "msg": "Cannot add the following ROAs with prefixes not on any of your certificates",
            "args": {"ca": "as65003"},
            "delta_error": {
                "duplicates": [], "unknowns": [], "invalid_length": [],
                "notheld": [{"asn": 65003, "prefix": "10.1.0.0/24",
                             "max_length": 24, "comment": None}],
            },
        })
        client = KrillClient(self.socket_path)
        with self.assertRaises(KrillError) as ctx:
            client.update_roas("as65003", added=[ROA.parse("10.1.0.0/24-24 => 65003")], removed=[])
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.label, "ca-roa-delta-error")
        body = ctx.exception.body
        assert isinstance(body, dict)
        self.assertEqual(body["delta_error"]["notheld"][0]["prefix"], "10.1.0.0/24")

    def test_tal_reads_the_locator_at_the_root_without_the_api_prefix(self) -> None:
        locator = b"rsync://ca/ta/ta.cer\n\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n"
        self.answer("GET", "/ta/ta.tal", 200, locator)
        self.assertEqual(KrillClient(self.socket_path).tal(), locator.decode("utf-8"))

    def test_list_roas_drops_the_signed_objects(self) -> None:
        self.answer("GET", f"{self.API}/cas/as65002/routes", 200, [{
            "asn": 65002, "prefix": "10.1.0.0/24", "max_length": 24, "comment": None,
            "roa_objects": [{"signed": "object"}],
        }])
        roas = KrillClient(self.socket_path).list_roas("as65002")
        self.assertEqual(roas, [ROA.parse("10.1.0.0/24-24 => 65002")])

    def test_a_second_publish_at_one_ca_waits_a_second_after_the_first_returned(self) -> None:
        self.answer("POST", f"{self.API}/cas/as65002/routes", 200, None)
        fake = _FakeTime()
        client = KrillClient(self.socket_path, clock=fake.clock, sleep=fake.sleep)
        client.update_roas("as65002", added=[], removed=[])
        self.assertEqual(fake.slept, [])
        client.update_roas("as65002", added=[], removed=[])
        self.assertEqual(fake.slept, [1.0])

    def test_publishes_at_different_hosted_cas_do_not_wait_for_each_other(self) -> None:
        self.answer("POST", f"{self.API}/cas/as65002/routes", 200, None)
        self.answer("POST", f"{self.API}/cas/as65003/routes", 200, None)
        fake = _FakeTime()
        client = KrillClient(self.socket_path, clock=fake.clock, sleep=fake.sleep)
        client.update_roas("as65002", added=[], removed=[])
        client.update_roas("as65003", added=[], removed=[])
        self.assertEqual(fake.slept, [])

    def test_a_publish_more_than_a_second_after_the_last_does_not_wait(self) -> None:
        self.answer("POST", f"{self.API}/cas/as65002/routes", 200, None)
        fake = _FakeTime()
        client = KrillClient(self.socket_path, clock=fake.clock, sleep=fake.sleep)
        client.update_roas("as65002", added=[], removed=[])
        fake.now += 1.5
        client.update_roas("as65002", added=[], removed=[])
        self.assertEqual(fake.slept, [])

    def test_publishes_on_different_sockets_do_not_wait_for_each_other(self) -> None:
        other_path = os.path.join(self.runtime, "other.sock")
        other = _UnixStubServer(other_path)
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        other.answers[("POST", f"{self.API}/cas/as65002/routes")] = (200, None)
        self.answer("POST", f"{self.API}/cas/as65002/routes", 200, None)
        fake = _FakeTime()
        first = KrillClient(self.socket_path, clock=fake.clock, sleep=fake.sleep)
        second = KrillClient(other_path, clock=fake.clock, sleep=fake.sleep)
        first.update_roas("as65002", added=[], removed=[])
        second.update_roas("as65002", added=[], removed=[])
        self.assertEqual(fake.slept, [])

    def test_adding_a_parent_does_not_pace_configuring_the_repository(self) -> None:
        # A Hosted CA with no repository publishes nothing, so its parent
        # needs no second's gap before the repository is configured.
        self.answer("POST", f"{self.API}/cas/as65002/parents", 200, None)
        self.answer("POST", f"{self.API}/cas/as65002/repo", 200, None)
        self.answer("POST", f"{self.API}/cas/as65002/routes", 200, None)
        fake = _FakeTime()
        client = KrillClient(self.socket_path, clock=fake.clock, sleep=fake.sleep)
        client.add_parent("as65002", "testbed", {})
        client.configure_repo("as65002", {})
        self.assertEqual(fake.slept, [])
        client.update_roas("as65002", added=[], removed=[])
        self.assertEqual(fake.slept, [1.0])

    def test_a_refused_publish_still_paces_the_next_one(self) -> None:
        self.answer("POST", f"{self.API}/cas/as65002/routes", 400, {
            "label": "ca-roa-delta-error", "msg": "refused",
        })
        fake = _FakeTime()
        client = KrillClient(self.socket_path, clock=fake.clock, sleep=fake.sleep)
        with self.assertRaises(KrillError):
            client.update_roas("as65002", added=[], removed=[])
        self.answer("POST", f"{self.API}/cas/as65002/routes", 200, None)
        client.update_roas("as65002", added=[], removed=[])
        self.assertEqual(fake.slept, [1.0])


class _RecordingKrill:
    """A Krill client that records each call and activates a CA on its second look."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str]] = []
        self._looks: Dict[str, int] = {}

    def __getattr__(self, name: str) -> Any:
        def call(handle: str, *args: Any, **kwargs: Any) -> Any:
            if name == "add_child":
                handle = args[0]
            self.calls.append((name, handle))
            if name == "child_request":
                return {"id_cert": "cert"}
            if name == "show_ca":
                self._looks[handle] = self._looks.get(handle, 0) + 1
                held = self._looks[handle] > 1
                return {"resources": {"asn": "AS1", "ipv4": "10.0.0.0/24"} if held else {}}
            return {}
        return call


@pytest.mark.unit
class HostedCAProvisioningTests(unittest.TestCase):
    """The Hosted CAs are all set up before the start waits for any certificate."""

    def setUp(self) -> None:
        from mniplab.rpki.provisioner import RPKICAProvisioner
        self.provisioner = RPKICAProvisioner
        self.hosted = [
            HostedCA(65001, ["10.1.0.0/24"], roas=["10.1.0.0/24-24 => 65001"]),
            HostedCA(65002, ["10.2.0.0/24"], aspas=["AS65002 => AS65001"]),
        ]

    def test_every_hosted_ca_is_set_up_before_the_first_wait(self) -> None:
        client = _RecordingKrill()
        self.provisioner.provision_hosted_cas(client, "ca", self.hosted)  # type: ignore[arg-type]

        names = [name for name, _ in client.calls]
        self.assertLess(
            max(i for i, n in enumerate(names) if n == "configure_repo"),
            names.index("show_ca"),
        )

    def test_starting_objects_are_signed_only_once_their_ca_holds_its_resources(self) -> None:
        client = _RecordingKrill()
        self.provisioner.provision_hosted_cas(client, "ca", self.hosted)  # type: ignore[arg-type]

        after_wait = client.calls[[n for n, _ in client.calls].index("show_ca"):]
        self.assertIn(("update_roas", "as65001"), after_wait)
        self.assertIn(("update_aspas", "as65002"), after_wait)
        for signed, handle in (("update_roas", "as65001"), ("update_aspas", "as65002")):
            looks = [i for i, c in enumerate(client.calls) if c == ("show_ca", handle)]
            self.assertLess(looks[1], client.calls.index((signed, handle)))
