"""Unit tests for the Lab handle over its RPC socket.

A real unix socket, a real server thread, and the fake Lab behind a
``LocalLabHandle``: what crosses the boundary is what a browser would see —
the same answers, the same error classes, and a diagnostic naming the Lab
once the other side is gone.
"""

import os
import tempfile
import unittest
from typing import List

import pytest

from mniplab.errors import (
    DNSValidationError,
    KrillError,
    LabUnavailableError,
    LinkError,
    RoutinatorError,
    RPKINotHeldError,
    RPKIObjectError,
)
from mniplab.exabgp.query import SpeakerNotFoundError
from mniplab.labhandle import LocalLabHandle, NodeAttachment
from mniplab.labrpc import LabRPCServer, RemoteLabHandle, decode_error, encode_error
from mniplab.tests.fakes import FakeIntf, FakeLab, FakeLink, FakeNode


def _lab() -> FakeLab:
    h1 = FakeNode.with_addresses("h1", "10.0.0.1/24")
    h2 = FakeNode.with_addresses("h2", "10.0.0.2/24")
    h1.pid = 4321
    h2.pid = 4322
    link = FakeLink(FakeIntf("h1-eth0", h1, ip="10.0.0.1"), FakeIntf("h2-eth0", h2, ip="10.0.0.2"))
    return FakeLab(h1, h2, links=[link])


@pytest.mark.unit
class RemoteHandleTests(unittest.TestCase):
    """The remote handle answers as the local one does, over the socket."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.socket_path = os.path.join(self.tmp.name, "k1.sock")
        self.local = LocalLabHandle(_lab(), example="tiny-lab", key="k1")
        self.server = LabRPCServer(self.local, self.socket_path)
        self.server.start()
        self.addCleanup(self.server.close)
        self.remote = RemoteLabHandle("k1", "tiny-lab", socket_path=self.socket_path, timeout=5)

    def test_reads_cross_the_socket_unchanged(self) -> None:
        self.assertEqual(self.remote.status(), self.local.status())
        self.assertEqual(self.remote.topology(), self.local.topology())
        self.assertEqual(
            self.remote.node_attachment("h1"),
            NodeAttachment("h1", 4321, self.local.net.node_home_dir("h1")),
        )
        self.assertEqual(self.remote.list_interfaces("h1"), self.local.list_interfaces("h1"))

    def test_a_value_error_arrives_as_a_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.remote.node_attachment("h9")
        self.assertEqual(str(ctx.exception), "Node not found: h9")

    def test_a_structured_error_keeps_its_class_and_fields(self) -> None:
        raised = DNSValidationError(
            "CNAME collides", command="zone-set", error_code="semantic", zone="example.com.",
        )
        rebuilt = decode_error(encode_error(raised))
        self.assertIsInstance(rebuilt, DNSValidationError)
        assert isinstance(rebuilt, DNSValidationError)
        self.assertEqual(
            (rebuilt.message, rebuilt.command, rebuilt.error_code, rebuilt.zone),
            ("CNAME collides", "zone-set", "semantic", "example.com."),
        )

    def test_a_not_found_speaker_keeps_its_class(self) -> None:
        with self.assertRaises(SpeakerNotFoundError):
            self.remote.get_speaker("h1")

    def test_an_unknown_error_class_falls_back_to_its_nearest_base(self) -> None:
        class OddLinkError(LinkError):
            pass

        rebuilt = decode_error(encode_error(OddLinkError("no such link")))
        self.assertIs(type(rebuilt), LinkError)
        self.assertEqual(str(rebuilt), "no such link")

    def test_an_operation_outside_the_interface_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.remote._invoke("net")
        self.assertIn("unknown Lab operation", str(ctx.exception))

    def test_a_gone_lab_answers_with_a_diagnostic_naming_it(self) -> None:
        self.server.close()

        with self.assertRaises(LabUnavailableError) as ctx:
            self.remote.status()

        self.assertIn("Lab 'tiny-lab' (k1)", str(ctx.exception))
        self.assertIn("not running", str(ctx.exception))

    def test_stop_reaches_the_lab_and_tells_the_process(self) -> None:
        stopped: List[object] = []
        self.server.on_stopped = lambda: stopped.append(True)
        self.local.net.stop = lambda: stopped.append("net")

        self.remote.stop()

        self.assertEqual(stopped, ["net", True])


@pytest.mark.unit
class RPKIErrorTransportTests(unittest.TestCase):
    """RPKI refusals and Krill failures keep their classes and fields across the socket.

    A refusal must reach the web API as a ValueError (400), an unreachable
    Krill as a KrillError (503) and a Validator that is not answering as a
    RoutinatorError (503); a class the socket does not know arrives as the
    nearest listed base, which here is mnIPLabError, a 500.
    """

    def test_a_not_held_refusal_keeps_its_class_and_fields(self) -> None:
        raised = RPKINotHeldError(
            "Hosted CA AS65003 does not hold 10.9.0.0/24, so it cannot sign a ROA for it",
            hosted_ca=65003,
            prefixes=["10.9.0.0/24"],
        )
        rebuilt = decode_error(encode_error(raised))
        self.assertIsInstance(rebuilt, RPKINotHeldError)
        assert isinstance(rebuilt, RPKINotHeldError)
        self.assertEqual(str(rebuilt), str(raised))
        self.assertEqual(rebuilt.hosted_ca, 65003)
        self.assertEqual(rebuilt.prefixes, ["10.9.0.0/24"])
        self.assertIsInstance(rebuilt, ValueError)

    def test_an_object_refusal_stays_an_rpki_object_error(self) -> None:
        rebuilt = decode_error(encode_error(RPKIObjectError("Hosted CA AS65002 refused the ROA change")))
        self.assertIs(type(rebuilt), RPKIObjectError)
        self.assertEqual(str(rebuilt), "Hosted CA AS65002 refused the ROA change")

    def test_a_krill_refusal_keeps_its_status_label_and_body(self) -> None:
        body = {"label": "ca-roa-delta-error", "msg": "rejected", "delta_error": {"notheld": []}}
        raised = KrillError(400, "ca-roa-delta-error", "rejected", body)
        rebuilt = decode_error(encode_error(raised))
        self.assertIsInstance(rebuilt, KrillError)
        assert isinstance(rebuilt, KrillError)
        self.assertEqual((rebuilt.status, rebuilt.label, rebuilt.message), (400, "ca-roa-delta-error", "rejected"))
        self.assertEqual(rebuilt.body, body)
        self.assertEqual(str(rebuilt), "ca-roa-delta-error: rejected")

    def test_a_validator_that_is_not_answering_arrives_as_a_routinator_error(self) -> None:
        raised = RoutinatorError("The Validator is not answering: no JSON for /json")
        rebuilt = decode_error(encode_error(raised))
        self.assertIs(type(rebuilt), RoutinatorError)
        self.assertEqual(str(rebuilt), str(raised))

    def test_an_unreachable_krill_arrives_as_a_krill_error_with_no_status(self) -> None:
        rebuilt = decode_error(encode_error(KrillError(None, "", "no answer on the socket")))
        self.assertIsInstance(rebuilt, KrillError)
        assert isinstance(rebuilt, KrillError)
        self.assertIsNone(rebuilt.status)
        self.assertEqual(str(rebuilt), "no answer on the socket")
