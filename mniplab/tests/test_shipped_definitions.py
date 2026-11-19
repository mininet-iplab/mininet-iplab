"""Shipped Lab Examples prepared as editable authoring definitions."""

import os
import tempfile
import unittest

import pytest

from mniplab.tests.authoring_fixtures import PREPARED, draft_from_shipped
from mniplab.tests.labs import EXAMPLES_DIR, catalogue
from mniplab.webserver.authoring import AuthoringRepository, DraftNotFoundError, shipped_definition


@pytest.mark.unit
class ShippedDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.repository = AuthoringRepository(storage.name)

    def test_each_prepared_example_is_a_valid_buildable_draft(self) -> None:
        for example_id in PREPARED:
            with self.subTest(example_id):
                draft = draft_from_shipped(self.repository, example_id)
                self.repository.validate_for_build(draft)
                names = {node["name"] for node in draft["topology"]["nodes"]}
                self.assertEqual(set(draft["layout"]["nodes"]), names)

    def test_routers_take_their_frr_configuration_from_the_shipped_seeds(self) -> None:
        static = draft_from_shipped(self.repository, "static-lab")
        r1 = next(node for node in static["topology"]["nodes"] if node["name"] == "r1")
        self.assertTrue(static["persistent_config"])
        self.assertIn("ip route 192.168.2.0/24 10.10.1.2", r1["frr_config"])
        self.assertNotIn("hostname", r1["frr_config"])
        self.assertIn("Static", static["guide"])

        ospf = draft_from_shipped(self.repository, "ospf-lab")
        r11 = next(node for node in ospf["topology"]["nodes"] if node["name"] == "r11")
        self.assertEqual(r11["protocols"], ["OSPF"])
        self.assertIn("passive-interface r11-eth1", r11["frr_config"])
        self.assertEqual(
            sorted(node["name"] for node in ospf["topology"]["nodes"] if node["type"] == "switch"),
            ["s1", "s2", "s3"],
        )

        legacy_bgp = draft_from_shipped(self.repository, "bgp-lab")
        r11 = next(node for node in legacy_bgp["topology"]["nodes"] if node["name"] == "r11")
        self.assertIn("no bgp enforce-first-as", r11["frr_config"])
        self.assertFalse(r11["fast_timers"])

        bgp_nat = draft_from_shipped(self.repository, "bgp-isp-lab")
        r1 = next(node for node in bgp_nat["topology"]["nodes"] if node["name"] == "r1")
        self.assertIn("BFD", r1["protocols"])

    def test_dhcp_lesson_keeps_its_service_relay_and_router_advertisements(self) -> None:
        def node(draft, name):
            return next(item for item in draft["topology"]["nodes"] if item["name"] == name)

        def end(draft, name):
            return next(item for link in draft["topology"]["links"] for item in link["ends"]
                        if item["node_id"] == node(draft, name)["id"])

        dhcp = draft_from_shipped(self.repository, "dhcp-lab")
        r1 = node(dhcp, "r1")
        pools = r1["dhcp"]["pools"]
        self.assertEqual(
            [pool["cidr"] for pool in pools],
            ["10.10.0.0/24", "2001:db8:10::/64", "10.20.0.0/24", "2001:db8:20::/64",
             "10.51.0.0/24", "2001:db8:61::/64", "2001:db8:62::/64"],
        )
        self.assertEqual(pools[0]["reservations"][0]["mac"], end(dhcp, "h1")["mac"])
        reservations = [pool["reservations"] for pool in pools[2:4]]
        self.assertEqual({item["mac"] for group in reservations for item in group},
                         {end(dhcp, "h2")["mac"]})
        self.assertEqual(node(dhcp, "r2")["relay"]["servers"], ["10.0.0.1", "2001:db8:0::1"])
        # M set, O only and neither: the Router Advertisement is the lesson.
        self.assertIn("interface r1-eth3\n ipv6 nd prefix 2001:db8:61::/64 no-autoconfig\n"
                      " ipv6 nd managed-config-flag", r1["frr_config"])
        self.assertIn("interface r1-eth4\n ipv6 nd other-config-flag", r1["frr_config"])
        self.assertIn("interface r1-eth5\n no ipv6 nd suppress-ra", r1["frr_config"])
        # The rogue Host runs no DHCP server until its Guide step starts one.
        self.assertNotIn("dhcp", node(dhcp, "rogue"))

    def test_unprepared_or_unknown_examples_are_not_found(self) -> None:
        for example_id in ("../labs", "missing"):
            with self.subTest(example_id), self.assertRaises(DraftNotFoundError):
                shipped_definition(example_id, EXAMPLES_DIR)

    def test_dns_lessons_keep_zone_roles_and_validation_chain(self) -> None:
        def node(draft, name):
            return next(item for item in draft["topology"]["nodes"] if item["name"] == name)

        unsigned = draft_from_shipped(self.repository, "dns-lab")
        self.assertEqual([zone["name"] for zone in node(unsigned, "auth1")["dns"]["zones"]],
                         ["example.com.", "10.in-addr.arpa."])
        self.assertEqual(node(unsigned, "auth2")["dns"]["zones"],
                         [{"kind": "secondary", "name": "example.com.", "primary_node_id": "auth1"},
                          {"kind": "secondary", "name": "10.in-addr.arpa.", "primary_node_id": "auth1"}])
        self.assertEqual(node(unsigned, "client1")["nameservers"], [{"node_id": "res1"}])
        self.assertEqual(node(unsigned, "res1")["resolver"]["root_hints"],
                         [{"node_id": "root1"}])
        self.assertEqual(node(unsigned, "res1")["resolver"]["max_ttl"], 120)
        self.assertEqual(node(unsigned, "res1")["resolver"]["trust_anchors"], [])

        signed = draft_from_shipped(self.repository, "dnssec-lab")
        self.assertEqual(node(signed, "root1")["dns"]["zones"][0]["ds_delegations"],
                         [{"node_id": "tld1", "zone": "com."}])
        self.assertEqual(node(signed, "res1")["resolver"]["trust_anchors"],
                         [{"node_id": "root1"}])

    def test_every_definition_file_is_a_catalogued_example(self) -> None:
        files = sorted(os.listdir(os.path.join(EXAMPLES_DIR, "definitions")))
        self.assertEqual(files, sorted(f"{example_id}.json" for example_id in PREPARED))

    def test_every_catalogued_example_is_prepared(self) -> None:
        self.assertEqual(sorted(entry["id"] for entry in catalogue()), sorted(PREPARED))

    def test_the_vrf_lesson_keeps_its_vrfs_and_its_loopbacks_in_frr_text(self) -> None:
        draft = draft_from_shipped(self.repository, "bgp-dc-lab")
        nodes = {node["name"]: node for node in draft["topology"]["nodes"]}
        for spine in ("rs1", "rs2"):
            with self.subTest(spine):
                self.assertEqual(nodes[spine]["vrfs"], [{"name": "vrf-ext", "table": 10}])
                in_vrf = sorted(end["port"] for link in draft["topology"]["links"]
                                for end in link["ends"]
                                if end["node_id"] == spine and end.get("vrf") == "vrf-ext")
                self.assertEqual(in_vrf, [0, 1])
        self.assertIn("interface lo\n ip address 1.1.1.1/32\n ip address 8.8.8.8/32\n",
                      nodes["isp1"]["frr_config"])
        with open(os.path.join(EXAMPLES_DIR, "bgp-dc-lab.py"), encoding="utf-8") as script:
            self.assertNotIn("dev lo", script.read())
