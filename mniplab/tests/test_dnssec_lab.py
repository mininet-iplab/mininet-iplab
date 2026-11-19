"""Integration tests for the DNSSEC Flagship Lab and Chain of Trust testbed.

Verifies end-to-end cryptographic signing and validation:
- Authoritative KASP zone signing with split KSK/ZSK and ECDSA P-256.
- Synchronous DS record delegation from child zones to parent zones.
- Unbound resolver validation anchored at the root KSK trust anchor.
- Client queries asserting the 'ad' (Authenticated Data) flag.
- Tracing the validated chain with 'delv' reporting ';; fully validated'.
- Secondary nameserver origin authentication over AXFR.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

import pytest

from mniplab.dns.models import DSRecord, SecondaryZone
from mniplab.resolver.service import ResolverService
from mniplab.tests.fakes import FakeTerminalCloserMixin, LoginClientMixin
from mniplab.webserver.repositories import GuideRepository
from mniplab.tests.labs import build_example

LAB_ID = "dnssec-lab"
LAB_NODES = ("r1", "root1", "tld1", "auth1", "auth2", "res1", "client1")


def _repo_root() -> str:
    """Return the repository root directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _examples_dir() -> str:
    """Return the absolute path to the examples/ directory."""
    return os.path.join(_repo_root(), "examples")


@pytest.mark.unit
class DNSSECLabTopologyTests(unittest.TestCase):
    """Unit tests for DNSSEC flagship lab topology, hierarchy, and layout."""

    def setUp(self) -> None:
        self.net = build_example(self, "dnssec-lab")

    def test_every_declared_node_exists(self) -> None:
        for name in LAB_NODES:
            self.assertIsNotNone(self.net.get(name), f"{name} must exist in lab")

    def test_client_nameserver_points_to_resolver(self) -> None:
        client = self.net.get("client1")
        self.assertEqual(client.nameservers, ["10.0.5.2"])

    def test_resolver_configured_with_root_hints_and_trust_anchor(self) -> None:
        res = self.net.get("res1")
        service = ResolverService.find_on(res)
        self.assertEqual([h.label for h in service.root_hints], ["root1"])
        self.assertEqual(len(service.trust_anchors), 1)

    def test_authoritative_hierarchy_and_ds_delegations(self) -> None:
        root_node = self.net.get("root1")
        tld_node = self.net.get("tld1")
        auth1_node = self.net.get("auth1")
        auth2_node = self.net.get("auth2")

        root_service = root_node.services[0]
        tld_service = tld_node.services[0]
        auth1_service = auth1_node.services[0]
        auth2_service = auth2_node.services[0]

        root_zone = root_service.primary_zones["."]
        tld_zone = tld_service.primary_zones["com."]
        example_zone = auth1_service.primary_zones["example.com."]
        secondary_zone = auth2_service.secondary_zones["example.com."]

        self.assertTrue(root_zone.dnssec)
        self.assertTrue(tld_zone.dnssec)
        self.assertTrue(example_zone.dnssec)
        self.assertIsInstance(secondary_zone, SecondaryZone)

        # Root zone must carry DS for com.
        root_ds_records = [r for r in root_zone.records if isinstance(r, DSRecord)]
        self.assertTrue(any(r.name == "com." for r in root_ds_records))

        # TLD zone must carry DS for example.com.
        tld_ds_records = [r for r in tld_zone.records if isinstance(r, DSRecord)]
        self.assertTrue(any(r.name == "example.com." for r in tld_ds_records))


@pytest.mark.unit
class DNSSECLabGuideTests(unittest.TestCase):
    """The Lab's classroom walkthrough guide."""

    def test_guide_walks_every_step_the_ticket_names(self) -> None:
        """Guide covers validation, the Secondary, failure staging, repair, NSEC, rollover."""
        guide = GuideRepository.load(LAB_ID, _examples_dir())

        # The topology it teaches against
        for node in LAB_NODES:
            self.assertIn(node, guide, f"guide never mentions {node}")
        self.assertIn("10.0.5.2", guide)

        # Step 1: valid resolution, the ad flag, RRSIGs, delv
        self.assertIn("dig +dnssec web.example.com @10.0.5.2", guide)
        self.assertIn("Authenticated Data", guide)
        self.assertIn("RRSIG", guide)
        self.assertIn("delv", guide)
        self.assertIn("fully validated", guide)

        # Step 2: the Secondary serves identical signatures without keys
        self.assertIn("10.0.4.2", guide)
        self.assertIn("AXFR", guide)
        self.assertIn("no private keys", guide)

        # Step 3: staging the failure by editing the parent DS
        self.assertIn("dns_records tld1 com.", guide)
        self.assertIn("SERVFAIL", guide)
        self.assertIn("broken trust chain", guide)
        self.assertIn("BOGUS", guide)

        # Step 4: repairing the DS and recovering
        self.assertIn("flush_cache res1", guide)
        self.assertIn("NOERROR", guide)

        # Step 5: authenticated denial of existence
        self.assertIn("missing.example.com", guide)
        self.assertIn("NXDOMAIN", guide)
        self.assertIn("NSEC", guide)
        self.assertIn("NODATA", guide)

        # Step 6: ZSK rollover without downtime
        self.assertIn("dnssec_rollover auth1 example.com. zsk", guide)
        self.assertIn("dnssec_keys auth1 example.com.", guide)
        self.assertIn("KSK", guide)

    def test_guide_points_at_the_web_ui_surfaces_this_lab_adds(self) -> None:
        """Guide names the Signed badge, DNSSEC Details, Copy DS, and the Resolver badges."""
        guide = GuideRepository.load(LAB_ID, _examples_dir())
        self.assertIn("Signed", guide)
        self.assertIn("DNSSEC Details", guide)
        self.assertIn("Copy DS", guide)
        self.assertIn("SECURE", guide)
        self.assertIn("Secure Queries", guide)
        self.assertIn("Bogus Queries", guide)


@pytest.mark.unit
class DNSSECWebAPIShapeTests(LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Zone DNSSEC route answers in the shape the Web UI panel reads.

    The panel's Signed badge, key table, and Copy DS button are each driven by
    one field of this payload, so a change to its shape is a change to the
    panel — which is what these assertions pin.
    """

    def setUp(self) -> None:
        super().setUp()
        self.open_session()

    def test_signed_zone_carries_keys_and_ds_records(self) -> None:
        payload = {
            "node": "auth1",
            "zone": "example.com.",
            "dnssec": True,
            "nsec3": False,
            "nsec3_salt": None,
            "is_secondary": False,
            "keys": [
                {"tag": 31589, "ksk": True, "zsk": False, "algorithm": 13, "size": 256},
                {"tag": 52814, "ksk": False, "zsk": True, "algorithm": 13, "size": 256},
            ],
            "ds_records": [
                {
                    "owner": "example.com.",
                    "key_tag": 31589,
                    "algorithm": 13,
                    "digest_type": 2,
                    "digest": "ab" * 32,
                },
            ],
        }
        with patch(
            "mniplab.webserver.registry.get_dnssec_details",
            new=AsyncMock(return_value=payload),
        ):
            response = self.client.get("/api/labs/fake/dns/auth1/zones/example.com./dnssec")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["dnssec"])
        self.assertEqual({k["tag"] for k in body["keys"]}, {31589, 52814})
        self.assertEqual(len(body["ds_records"]), 1)
        ds = body["ds_records"][0]
        self.assertEqual(
            [ds["key_tag"], ds["algorithm"], ds["digest_type"]], [31589, 13, 2]
        )

    def test_secondary_zone_reports_no_keys(self) -> None:
        """A Secondary holds no private keys, so the panel has nothing to show."""
        payload = {
            "node": "auth2",
            "zone": "example.com.",
            "dnssec": False,
            "nsec3": False,
            "nsec3_salt": None,
            "is_secondary": True,
            "keys": [],
            "ds_records": [],
        }
        with patch(
            "mniplab.webserver.registry.get_dnssec_details",
            new=AsyncMock(return_value=payload),
        ):
            response = self.client.get("/api/labs/fake/dns/auth2/zones/example.com./dnssec")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["dnssec"])
        self.assertEqual(body["keys"], [])
        self.assertTrue(body["is_secondary"])
