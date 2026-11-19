"""Tests for the Authoritative DNS Service: models, zone generation, DNSService, and lifecycle.

Unit coverage (unprivileged) covers Record validation, default SOA and apex NS
generation, Zone dictionary management, DNSService snapshot restoration, and
Knot configuration rendering.

Privileged integration tests verify that a client node querying knotd with dig
resolves A, AAAA, CNAME, PTR, MX, and TXT records, and receives NXDOMAIN for
unknown names in hosted zones.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import tempfile
from typing import Any, Optional
import unittest
from unittest.mock import Mock, patch

import pytest
from mniplab.dns.models import (
    Record,
    ARecord,
    AAAARecord,
    CNAMERecord,
    PTRRecord,
    TXTRecord,
    MXRecord,
    NSRecord,
    SOARecord,
    DSRecord,
    RECORD_TYPES,
    SecondaryZone,
    Zone,
)
from mniplab.errors import ConfigurationError
from mniplab.dns.diagnostics import DNSDiagnostics
from mniplab.dns.service import DNSService
from mniplab.dns.provisioner import DNSProvisioner
from mniplab.service import Service, ServiceMixin
from mniplab.tests.fakes import FakeLab, FakeNode, cli_output
from mniplab.tests.labs import LabCase, build_dns_test_lab, dns_example_zone, unstarted_lab


@pytest.mark.unit
class RecordModelTests(unittest.TestCase):
    """Tests for Record base class and typed subclasses in mniplab/dns/models.py."""

    def test_arecord_valid(self) -> None:
        rec = ARecord("web", "10.0.0.1", ttl=300)
        self.assertEqual(rec.name, "web")
        self.assertEqual(rec.rtype, "A")
        self.assertEqual(rec.address, "10.0.0.1")
        self.assertEqual(rec.rdata, "10.0.0.1")
        self.assertEqual(rec.ttl, 300)
        self.assertEqual(rec.to_dict()["type"], "A")
        self.assertEqual(rec.to_dict()["address"], "10.0.0.1")

    def test_arecord_invalid_ip_raises(self) -> None:
        with self.assertRaises(ValueError):
            ARecord("web", "not-an-ip")
        with self.assertRaises(ValueError):
            ARecord("web", "2001:db8::1")  # IPv6 in A record

    def test_aaaarecord_valid(self) -> None:
        rec = AAAARecord("ipv6web", "2001:db8::1", ttl=600)
        self.assertEqual(rec.name, "ipv6web")
        self.assertEqual(rec.rtype, "AAAA")
        self.assertEqual(rec.address, "2001:db8::1")
        self.assertEqual(rec.rdata, "2001:db8::1")
        self.assertEqual(rec.ttl, 600)
        self.assertEqual(rec.to_dict()["type"], "AAAA")

    def test_aaaarecord_invalid_ip_raises(self) -> None:
        with self.assertRaises(ValueError):
            AAAARecord("web", "10.0.0.1")  # IPv4 in AAAA record
        with self.assertRaises(ValueError):
            AAAARecord("web", "invalid-ipv6")

    def test_cnamerecord_valid(self) -> None:
        rec = CNAMERecord("alias", "web.example.com.", ttl=300)
        self.assertEqual(rec.name, "alias")
        self.assertEqual(rec.rtype, "CNAME")
        self.assertEqual(rec.target, "web.example.com.")
        self.assertEqual(rec.rdata, "web.example.com.")
        self.assertEqual(rec.to_dict()["target"], "web.example.com.")

    def test_cnamerecord_empty_target_raises(self) -> None:
        with self.assertRaises(ValueError):
            CNAMERecord("alias", "")

    def test_ptrrecord_valid(self) -> None:
        rec = PTRRecord("1", "host1.example.com.", ttl=300)
        self.assertEqual(rec.name, "1")
        self.assertEqual(rec.rtype, "PTR")
        self.assertEqual(rec.ptrdname, "host1.example.com.")
        self.assertEqual(rec.rdata, "host1.example.com.")
        self.assertEqual(rec.to_dict()["ptrdname"], "host1.example.com.")

    def test_ptrrecord_empty_target_raises(self) -> None:
        with self.assertRaises(ValueError):
            PTRRecord("1", "")

    def test_txtrecord_formatting(self) -> None:
        rec = TXTRecord("info", "sample text", ttl=300)
        self.assertEqual(rec.name, "info")
        self.assertEqual(rec.rtype, "TXT")
        self.assertEqual(rec.text, "sample text")
        self.assertEqual(rec.rdata, '"sample text"')

        # Already quoted
        rec2 = TXTRecord("info", '"already quoted"')
        self.assertEqual(rec2.text, "already quoted")
        self.assertEqual(rec2.rdata, '"already quoted"')

    def test_mxrecord_valid(self) -> None:
        rec = MXRecord("@", "mail.example.com.", preference=10, ttl=300)
        self.assertEqual(rec.name, "@")
        self.assertEqual(rec.rtype, "MX")
        self.assertEqual(rec.exchange, "mail.example.com.")
        self.assertEqual(rec.preference, 10)
        self.assertEqual(rec.rdata, "10 mail.example.com.")

    def test_mxrecord_invalid_preference_raises(self) -> None:
        with self.assertRaises(ValueError):
            MXRecord("@", "mail.example.com.", preference=-1)
        with self.assertRaises(ValueError):
            MXRecord("@", "mail.example.com.", preference=70000)

    def test_nsrecord_valid(self) -> None:
        rec = NSRecord("@", "ns1.example.com.", ttl=300)
        self.assertEqual(rec.name, "@")
        self.assertEqual(rec.rtype, "NS")
        self.assertEqual(rec.nameserver, "ns1.example.com.")
        self.assertEqual(rec.rdata, "ns1.example.com.")

    def test_nsrecord_empty_nameserver_raises(self) -> None:
        with self.assertRaises(ValueError):
            NSRecord("@", "")

    def test_soarecord_defaults(self) -> None:
        soa = SOARecord("ns1.example.com.", "hostmaster.example.com.")
        self.assertEqual(soa.name, "@")
        self.assertEqual(soa.rtype, "SOA")
        self.assertEqual(soa.mname, "ns1.example.com.")
        self.assertEqual(soa.rname, "hostmaster.example.com.")
        self.assertEqual(soa.serial, 1)
        self.assertEqual(soa.refresh, 3600)
        self.assertEqual(soa.retry, 900)
        self.assertEqual(soa.expire, 604800)
        self.assertEqual(soa.minimum, 300)
        self.assertEqual(soa.ttl, 3600)
        self.assertEqual(
            soa.rdata,
            "ns1.example.com. hostmaster.example.com. 1 3600 900 604800 300",
        )

    def test_dsrecord_valid(self) -> None:
        rec = DSRecord("example.com.", 17782, 13, 2, "7ab44252b42a2d2462f5b4c0e681393b04dc64d7a3a2c638d8642c1e510d014f", ttl=300)
        self.assertEqual(rec.name, "example.com.")
        self.assertEqual(rec.rtype, "DS")
        self.assertEqual(rec.key_tag, 17782)
        self.assertEqual(rec.algorithm, 13)
        self.assertEqual(rec.digest_type, 2)
        self.assertEqual(rec.digest, "7ab44252b42a2d2462f5b4c0e681393b04dc64d7a3a2c638d8642c1e510d014f")
        self.assertEqual(
            rec.rdata,
            "17782 13 2 7ab44252b42a2d2462f5b4c0e681393b04dc64d7a3a2c638d8642c1e510d014f",
        )
        self.assertEqual(rec.ttl, 300)
        d = rec.to_dict()
        self.assertEqual(d["type"], "DS")
        self.assertEqual(d["key_tag"], 17782)
        self.assertEqual(d["algorithm"], 13)
        self.assertEqual(d["digest_type"], 2)
        self.assertEqual(d["digest"], "7ab44252b42a2d2462f5b4c0e681393b04dc64d7a3a2c638d8642c1e510d014f")

    def test_dsrecord_invalid_fields_raise(self) -> None:
        valid_digest = "7ab44252b42a2d2462f5b4c0e681393b04dc64d7a3a2c638d8642c1e510d014f"
        with self.assertRaises(ValueError):
            DSRecord("example.com.", -1, 13, 2, valid_digest)
        with self.assertRaises(ValueError):
            DSRecord("example.com.", 65536, 13, 2, valid_digest)
        with self.assertRaises(ValueError):
            DSRecord("example.com.", 17782, -1, 2, valid_digest)
        with self.assertRaises(ValueError):
            DSRecord("example.com.", 17782, 256, 2, valid_digest)
        with self.assertRaises(ValueError):
            DSRecord("example.com.", 17782, 13, -1, valid_digest)
        with self.assertRaises(ValueError):
            DSRecord("example.com.", 17782, 13, 256, valid_digest)
        with self.assertRaises(ValueError):
            DSRecord("example.com.", 17782, 13, 2, "not-hex-digest-zz")
        with self.assertRaises(ValueError):
            DSRecord("example.com.", 17782, 13, 2, "")

    def test_record_types_registered(self) -> None:
        self.assertIn("DS", RECORD_TYPES)
        self.assertIs(RECORD_TYPES["DS"], DSRecord)
        self.assertIn("A", RECORD_TYPES)
        self.assertIn("SOA", RECORD_TYPES)

    def test_records_are_frozen_and_hashable(self) -> None:
        rec1 = ARecord("web", "10.0.0.1", ttl=300)
        rec2 = ARecord("web", "10.0.0.1", ttl=300)
        rec3 = ARecord("web", "10.0.0.2", ttl=300)
        self.assertEqual(rec1, rec2)
        self.assertNotEqual(rec1, rec3)
        s = {rec1, rec2, rec3}
        self.assertEqual(len(s), 2)
        with self.assertRaises(Exception):
            setattr(rec1, "ttl", 100)  # Frozen dataclass


@pytest.mark.unit
class ZoneModelTests(unittest.TestCase):
    """Tests for Zone class in mniplab/dns/models.py."""

    def test_zone_name_must_end_with_dot(self) -> None:
        with self.assertRaises(ValueError):
            Zone("example.com")  # Missing trailing dot

        zone = Zone("example.com.")
        self.assertEqual(zone.name, "example.com.")

    def test_zone_dnssec_attributes(self) -> None:
        zone = Zone("example.com.")
        self.assertFalse(zone.dnssec)
        self.assertFalse(zone.nsec3)
        self.assertIsNone(zone.nsec3_salt)

        signed_zone = Zone("example.com.", dnssec=True, nsec3=True, nsec3_salt="AABBCCDD")
        self.assertTrue(signed_zone.dnssec)
        self.assertTrue(signed_zone.nsec3)
        self.assertEqual(signed_zone.nsec3_salt, "AABBCCDD")

    def test_zone_ds_record_disabled_raises(self) -> None:
        zone = Zone("example.com.", dnssec=False)
        with self.assertRaises(ValueError):
            zone.ds_record()

    def test_zone_ds_record_generation(self) -> None:
        zone = Zone("example.com.", dnssec=True)
        ds = zone.ds_record(digest_type=2)
        self.assertIsInstance(ds, DSRecord)
        self.assertEqual(ds.name, "example.com.")
        self.assertEqual(ds.algorithm, 13)
        self.assertEqual(ds.digest_type, 2)
        self.assertGreater(ds.key_tag, 0)
        self.assertEqual(len(ds.digest), 64)

    def test_auto_generates_soa_and_apex_ns_when_omitted(self) -> None:
        zone = Zone("example.com.", [ARecord("web", "10.0.0.1")])
        self.assertIsNotNone(zone.soa_record)
        assert zone.soa_record is not None
        self.assertEqual(zone.soa_record.mname, "ns1.example.com.")
        self.assertEqual(zone.soa_record.rname, "hostmaster.example.com.")

        apex_ns = zone.ns_records
        self.assertEqual(len(apex_ns), 1)
        self.assertEqual(apex_ns[0].nameserver, "ns1.example.com.")
        self.assertEqual(apex_ns[0].name, "@")

        # Verify records list contains SOA, NS, and A
        self.assertEqual(len(zone.records), 3)

    def test_auto_generates_with_host_node(self) -> None:
        zone = Zone("example.com.", [ARecord("web", "10.0.0.1")], host_node="dns1")
        self.assertIsNotNone(zone.soa_record)
        assert zone.soa_record is not None
        self.assertEqual(zone.soa_record.mname, "dns1.example.com.")
        self.assertEqual(zone.ns_records[0].nameserver, "dns1.example.com.")

    def test_preserves_explicit_soa_and_ns(self) -> None:
        custom_soa = SOARecord("master.example.com.", "admin.example.com.", serial=42)
        custom_ns = NSRecord("@", "master.example.com.")
        zone = Zone("example.com.", [custom_soa, custom_ns, ARecord("web", "10.0.0.1")])
        self.assertEqual(zone.soa_record, custom_soa)
        assert zone.soa_record is not None
        self.assertEqual(zone.soa_record.serial, 42)
        self.assertEqual(len(zone.ns_records), 1)
        self.assertEqual(zone.ns_records[0], custom_ns)
        self.assertEqual(len(zone.records), 3)

    def test_bind_host_node_updates_only_auto_generated(self) -> None:
        # Auto-generated SOA/NS updates when host node bound
        zone = Zone("example.com.", [ARecord("web", "10.0.0.1")])
        zone.bind_host_node("r1")
        assert zone.soa_record is not None
        self.assertEqual(zone.soa_record.mname, "r1.example.com.")
        self.assertEqual(zone.ns_records[0].nameserver, "r1.example.com.")

        # Explicit SOA/NS does NOT change on bind
        custom_soa = SOARecord("master.example.com.", "admin.example.com.")
        zone_custom = Zone("example.com.", [custom_soa])
        zone_custom.bind_host_node("r1")
        assert zone_custom.soa_record is not None
        self.assertEqual(zone_custom.soa_record.mname, "master.example.com.")

    def test_reverse_zone(self) -> None:
        zone = Zone("0.0.10.in-addr.arpa.", [PTRRecord("1", "host1.example.com.")])
        self.assertEqual(zone.name, "0.0.10.in-addr.arpa.")
        self.assertIsNotNone(zone.soa_record)
        ptr_records = zone.get_records("PTR")
        self.assertEqual(len(ptr_records), 1)
        self.assertEqual(ptr_records[0].rdata, "host1.example.com.")

    def test_reverse_zone_ipv6(self) -> None:
        zone = Zone(
            "0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa.",
            [
                PTRRecord(
                    "1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0",
                    "dns1.example.com.",
                )
            ],
            host_node="dns1",
        )
        self.assertEqual(zone.name, "0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa.")
        self.assertIsNotNone(zone.soa_record)
        assert zone.soa_record is not None
        self.assertEqual(zone.soa_record.mname, "dns1.")
        self.assertEqual(zone.ns_records[0].nameserver, "dns1.")
        ptr_records = zone.get_records("PTR")
        self.assertEqual(len(ptr_records), 1)
        self.assertIsInstance(ptr_records[0], PTRRecord)
        assert isinstance(ptr_records[0], PTRRecord)
        self.assertEqual(ptr_records[0].ptrdname, "dns1.example.com.")

    def test_clone_creates_independent_copy(self) -> None:
        zone = Zone("example.com.", [ARecord("web", "10.0.0.1")])
        clone = zone.clone()
        self.assertEqual(clone.name, zone.name)
        self.assertEqual(len(clone.records), len(zone.records))

        # Mutate clone
        clone.add_record(ARecord("web2", "10.0.0.2"))
        self.assertNotEqual(len(clone.records), len(zone.records))


@pytest.mark.unit
class DNSServiceTests(unittest.TestCase):
    """Tests for DNSService in mniplab/dns/service.py."""

    def test_subclasses_service_and_carries_name_and_badge(self) -> None:
        srv = DNSService(Zone("example.com."))
        self.assertIsInstance(srv, Service)
        self.assertEqual(srv.name, "dns")
        self.assertEqual(srv.badge_text, "DNS")
        self.assertEqual(srv.families, [4, 6])

    def test_badge_text_distinguishes_secondary_zones(self) -> None:
        sec_srv = DNSService(SecondaryZone("example.com.", primary="10.0.0.1"))
        self.assertEqual(sec_srv.badge_text, "DNS · SECONDARY")

        dual_srv = DNSService(
            Zone("primary.example.com."),
            SecondaryZone("example.com.", primary="10.0.0.1"),
        )
        self.assertEqual(dual_srv.badge_text, "DNS · PRIMARY · SECONDARY")

        prim_srv = DNSService(Zone("example.com."))
        self.assertEqual(prim_srv.badge_text, "DNS")

    def test_holds_zones_keyed_by_apex(self) -> None:
        z1 = Zone("example.com.")
        z2 = Zone("0.0.10.in-addr.arpa.")
        srv = DNSService(z1, z2)
        self.assertIn("example.com.", srv.zones)
        self.assertIn("0.0.10.in-addr.arpa.", srv.zones)
        self.assertEqual(len(srv.zones), 2)

    def test_reset_zones_restores_declared_state(self) -> None:
        z1 = Zone("example.com.", [ARecord("web", "10.0.0.1")])
        srv = DNSService(z1)
        initial_records_count = len(srv.zones["example.com."].records)

        # Mutate zone by adding a record
        srv.primary_zones["example.com."].add_record(ARecord("attacker", "192.168.1.1"))
        self.assertEqual(len(srv.zones["example.com."].records), initial_records_count + 1)

        # Reset zones
        srv.reset_zones()
        self.assertEqual(len(srv.zones["example.com."].records), initial_records_count)
        self.assertFalse(
            any(r.name == "attacker" for r in srv.zones["example.com."].records)
        )

    def test_find_on_returns_service_or_raises(self) -> None:
        class FakeNode(ServiceMixin):
            def __init__(self, name: str) -> None:
                self.name = name

        node = FakeNode("dns1")
        with self.assertRaises(ValueError):
            DNSService.find_on(node)

        srv = DNSService(Zone("example.com."))
        node.addService(srv)
        self.assertEqual(DNSService.find_on(node), srv)


@pytest.mark.unit
class SecondaryZoneModelTests(unittest.TestCase):
    """Tests for the SecondaryZone type: declaration, rejection, and resolution."""

    def test_carries_apex_and_primary_and_nothing_else(self) -> None:
        ns1 = FakeNode.with_addresses("ns1", "10.0.1.2")
        secondary = SecondaryZone("example.com.", primary=ns1)

        self.assertEqual(secondary.name, "example.com.")
        self.assertIs(secondary.primary, ns1)
        self.assertEqual(secondary.records, [])
        self.assertEqual(secondary.primary_label, "ns1")

    def test_generates_no_soa_and_no_apex_ns(self) -> None:
        secondary = SecondaryZone("example.com.", primary="10.0.1.2")

        rtypes = {r.rtype for r in secondary.records}
        self.assertNotIn("SOA", rtypes)
        self.assertNotIn("NS", rtypes)

    def test_rejects_seeded_records(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            SecondaryZone(
                "example.com.", primary="10.0.1.2", records=[ARecord("web", "10.0.0.1")]
            )
        self.assertIn("Zone Transfer", str(ctx.exception))

    def test_requires_an_apex_fqdn_and_a_primary(self) -> None:
        with self.assertRaises(ValueError):
            SecondaryZone("example.com", primary="10.0.1.2")
        with self.assertRaises(ValueError):
            SecondaryZone("example.com.", primary="")

    def test_primary_named_by_node_resolves_to_its_addresses(self) -> None:
        ns1 = FakeNode.with_addresses("ns1", "10.0.1.2", "10.0.9.2")
        secondary = SecondaryZone("example.com.", primary=ns1)

        self.assertEqual(secondary.primary_addresses(), ["10.0.1.2", "10.0.9.2"])

    def test_primary_named_by_address_string_resolves_to_itself(self) -> None:
        secondary = SecondaryZone("example.com.", primary="192.0.2.53")

        self.assertEqual(secondary.primary_addresses(), ["192.0.2.53"])
        self.assertEqual(secondary.primary_label, "192.0.2.53")

    def test_soa_record_answers_none_rather_than_missing(self) -> None:
        """Read-side callers reach for soa_record across both Zone types."""
        self.assertIsNone(SecondaryZone("example.com.", primary="10.0.1.2").soa_record)

    def test_clone_is_independent_and_keeps_the_primary(self) -> None:
        ns1 = FakeNode.with_addresses("ns1", "10.0.1.2")
        secondary = SecondaryZone("example.com.", primary=ns1, host_node="ns2")
        copy = secondary.clone()

        self.assertIsNot(copy, secondary)
        self.assertEqual(copy.name, secondary.name)
        self.assertIs(copy.primary, ns1)
        self.assertEqual(copy.host_node, "ns2")


@pytest.mark.unit
class ClassroomSOATimerTests(unittest.TestCase):
    """Tests for the classroom SOA timer defaults."""

    def test_auto_generated_soa_lowers_retry_and_expire_but_not_refresh(self) -> None:
        soa = Zone("example.com.", host_node="ns1").soa_record

        assert soa is not None
        self.assertEqual(soa.refresh, 3600)
        self.assertEqual(soa.retry, 60)
        self.assertEqual(soa.expire, 600)
        self.assertEqual(soa.minimum, 300)

    def test_an_instructor_can_override_every_timer(self) -> None:
        declared = SOARecord(
            mname="ns1.example.com.",
            rname="hostmaster.example.com.",
            refresh=7200,
            retry=900,
            expire=604800,
            minimum=60,
        )
        zone = Zone("example.com.", [declared], host_node="ns1")

        soa = zone.soa_record
        assert soa is not None
        self.assertEqual(
            (soa.refresh, soa.retry, soa.expire, soa.minimum), (7200, 900, 604800, 60)
        )


@pytest.mark.unit
class DNSServiceReplicationTests(unittest.TestCase):
    """Tests for a Service holding both Primary and Secondary Zones."""

    def test_one_node_is_primary_for_one_apex_and_secondary_for_another(self) -> None:
        other = FakeNode.with_addresses("ns2", "10.0.2.2")
        service = DNSService(
            Zone("example.com.", host_node="ns1"),
            SecondaryZone("example.net.", primary=other),
        )

        self.assertEqual(set(service.zones), {"example.com.", "example.net."})
        self.assertEqual(set(service.primary_zones), {"example.com."})
        self.assertEqual(set(service.secondary_zones), {"example.net."})

    def test_reset_zones_restores_declared_secondaries(self) -> None:
        service = DNSService(SecondaryZone("example.com.", primary="10.0.1.2"))
        service.bind_node("ns2")
        del service.zones["example.com."]

        service.reset_zones()

        self.assertIn("example.com.", service.secondary_zones)


@pytest.mark.unit
class DNSProvisionerUnitTests(unittest.TestCase):
    """Tests for DNSProvisioner configuration rendering and path management."""

    def test_paths(self) -> None:
        net = FakeLab()
        self.assertTrue(DNSProvisioner.host_dir("dns1", net=net).endswith("nodes/dns1"))
        self.assertTrue(
            DNSProvisioner.control_socket_host_path("dns1", net=net).endswith(
                "nodes/dns1/run/knot/knot.sock"
            )
        )

    def test_ensure_available_names_the_node_missing_knotd(self) -> None:
        """A served Node with no knotd in PATH fails the preflight by name."""
        node = FakeNode("dns1", services=[DNSService(Zone("example.com."))])

        with patch("shutil.which", return_value=None):
            with self.assertRaises(ConfigurationError) as ctx:
                DNSProvisioner.ensure_available([node])
            DNSProvisioner.ensure_available([FakeNode("h1")])

        err = str(ctx.exception)
        self.assertIn("dns1", err)
        self.assertIn("knotd", err)

    def test_render_zone_file(self) -> None:
        zone = Zone(
            "example.com.",
            [
                ARecord("web", "10.0.0.1"),
                AAAARecord("ipv6web", "2001:db8::1"),
                CNAMERecord("alias", "web.example.com."),
                TXTRecord("txt", "hello world"),
                MXRecord("@", "mail.example.com.", preference=10),
            ],
            host_node="dns1",
        )
        content = DNSProvisioner.render_zone_file(zone)
        self.assertIn("$ORIGIN example.com.", content)
        self.assertIn("$TTL 300", content)
        self.assertIn("IN SOA dns1.example.com. hostmaster.example.com.", content)
        self.assertIn("IN NS dns1.example.com.", content)
        self.assertIn("web 300 IN A 10.0.0.1", content)
        self.assertIn("ipv6web 300 IN AAAA 2001:db8::1", content)
        self.assertIn("alias 300 IN CNAME web.example.com.", content)
        self.assertIn('txt 300 IN TXT "hello world"', content)
        self.assertIn("IN MX 10 mail.example.com.", content)

    def test_render_config(self) -> None:
        net = FakeLab()
        srv = DNSService(Zone("example.com.", [ARecord("web", "10.0.0.1")]))
        conf_path = DNSProvisioner.render_config("dns1", srv, net=net)
        self.assertTrue(os.path.exists(conf_path))
        with open(conf_path, "r", encoding="utf-8") as f:
            conf_content = f.read()

        self.assertIn("0.0.0.0@53", conf_content)
        self.assertIn("::@53", conf_content)
        self.assertIn("mod-stats", conf_content)
        self.assertIn("domain: example.com.", conf_content)

        # Verify zone file was also created in lib
        lib_dir = DNSProvisioner.host_dir("dns1", "lib", net=net)
        zone_file = os.path.join(lib_dir, "example.com.zone")
        self.assertTrue(os.path.exists(zone_file))

    def test_render_config_dnssec_signed_zone(self) -> None:
        net = FakeLab()
        srv = DNSService(Zone("example.com.", [ARecord("web", "10.0.0.1")], dnssec=True))
        conf_path = DNSProvisioner.render_config("dns1", srv, net=net)
        self.assertTrue(os.path.exists(conf_path))
        with open(conf_path, "r", encoding="utf-8") as f:
            conf_content = f.read()

        self.assertIn("policy:", conf_content)
        self.assertIn("id: default", conf_content)
        self.assertIn("algorithm: ecdsap256sha256", conf_content)
        self.assertIn("dnssec-signing: on", conf_content)
        self.assertIn("dnssec-policy: default", conf_content)

        # Verify keys were pre-initialized in lib/keys
        keys_dir = os.path.join(DNSProvisioner.host_dir("dns1", "lib", net=net), "keys")
        self.assertTrue(os.path.exists(keys_dir))
        self.assertTrue(os.path.exists(os.path.join(keys_dir, "data.mdb")))

    def test_render_config_secondary_zone_no_kasp(self) -> None:
        net = FakeLab()
        srv = DNSService(SecondaryZone("example.com.", primary="10.0.1.2"))
        conf_path = DNSProvisioner.render_config("dns2", srv, net=net)
        with open(conf_path, "r", encoding="utf-8") as f:
            conf_content = f.read()

        self.assertNotIn("policy:", conf_content)
        self.assertNotIn("dnssec-signing: on", conf_content)
        self.assertIn("template: secondary", conf_content)

    def test_render_config_nsec3(self) -> None:
        net = FakeLab()
        srv = DNSService(Zone("example.com.", dnssec=True, nsec3=True))
        conf_path = DNSProvisioner.render_config("dns1", srv, net=net)
        with open(conf_path, "r", encoding="utf-8") as f:
            conf_content = f.read()

        self.assertIn("nsec3: on", conf_content)

    def _policy_of(self, conf_content: str, zone: str) -> str:
        """The ``dnssec-policy`` a rendered config gives one Zone."""
        block = conf_content.split(f"  - domain: {zone}\n", 1)[1].split("\n  - ", 1)[0]
        return next(line.split(": ")[1] for line in block.splitlines()
                    if "dnssec-policy" in line)

    def test_a_declared_nsec3_salt_sets_its_own_length_and_never_rotates(self) -> None:
        """Knot replaces a salt of any other length, and re-salts after 30 days."""
        net = FakeLab()
        srv = DNSService(
            Zone("short.example.", dnssec=True, nsec3=True, nsec3_salt="aabb"),
            Zone("long.example.", dnssec=True, nsec3=True, nsec3_salt="aabbccddeeff0011"),
            Zone("bare.example.", dnssec=True, nsec3=True, nsec3_salt=""),
            Zone("random.example.", dnssec=True, nsec3=True),
            Zone("tens.example.", dnssec=True, nsec3=True, nsec3_salt="aa" * 10),
        )
        conf_path = DNSProvisioner.render_config("dns1", srv, net=net)
        with open(conf_path, "r", encoding="utf-8") as f:
            conf_content = f.read()

        section = conf_content.split("policy:\n", 1)[1].split("\n\n", 1)[0]
        policies = {block.splitlines()[0]: block for block in section.split("  - id: ")[1:]}
        for zone, octets in (("short.example.", 2), ("long.example.", 8), ("bare.example.", 0)):
            policy = self._policy_of(conf_content, zone)
            self.assertIn(f"nsec3-salt-length: {octets}", policies[policy])
            self.assertIn("nsec3-salt-lifetime: 0", policies[policy])
            self.assertIn("nsec3: on", policies[policy])
        self.assertNotIn("nsec3-salt-length", policies[self._policy_of(conf_content, "random.example.")])
        self.assertIn("nsec3-salt-length: 10", policies[self._policy_of(conf_content, "tens.example.")])
        # Policies are written by salt length, shortest first, not by the order of their ids.
        salted = [policy for policy in policies if policy.startswith("nsec3-salt-")]
        self.assertEqual(salted, ["nsec3-salt-0", "nsec3-salt-2", "nsec3-salt-8", "nsec3-salt-10"])
        self.assertNotEqual(self._policy_of(conf_content, "short.example."),
                            self._policy_of(conf_content, "bare.example."))

    def _keymgr_calls(self, zone: Zone, salt_result: Mock) -> list:
        """The keymgr commands ``ensure_zone_keys`` issues, with ``salt_result`` answering the salt."""
        calls: list = []

        def run(cmd, *args, **kwargs):
            calls.append(cmd)
            if "nsec3-salt" in cmd:
                return salt_result
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=run):
            DNSProvisioner.ensure_zone_keys(zone, "dns1", net=FakeLab())
        return calls

    def test_ensure_zone_keys_gives_keymgr_an_empty_salt_as_an_empty_argument(self) -> None:
        zone = Zone("bare.example.", dnssec=True, nsec3=True, nsec3_salt="")

        calls = self._keymgr_calls(zone, Mock(returncode=0, stdout="", stderr=""))

        salt_calls = [cmd for cmd in calls if "nsec3-salt" in cmd]
        self.assertEqual([cmd[-3:] for cmd in salt_calls], [["bare.example.", "nsec3-salt", ""]])

    def test_ensure_zone_keys_leaves_the_salt_alone_unless_nsec3_declares_one(self) -> None:
        for zone in (Zone("random.example.", dnssec=True, nsec3=True),
                     Zone("plain.example.", dnssec=True, nsec3_salt="aabb")):
            calls = self._keymgr_calls(zone, Mock(returncode=0, stdout="", stderr=""))

            self.assertFalse(any("nsec3-salt" in cmd for cmd in calls), zone.name)

    def test_ensure_zone_keys_fails_the_build_when_keymgr_refuses_the_salt(self) -> None:
        zone = Zone("odd.example.", dnssec=True, nsec3=True, nsec3_salt="abc")

        with self.assertRaises(RuntimeError) as raised:
            self._keymgr_calls(zone, Mock(returncode=1, stdout="", stderr="error: malformed data\n"))

        self.assertIn("odd.example.", str(raised.exception))
        self.assertIn("malformed data", str(raised.exception))


@pytest.mark.unit
class DNSServiceReadinessTests(unittest.TestCase):
    """start_dns_service returns only once knotd answers, or fails the start."""

    def _serving_node(self) -> FakeNode:
        zone = Zone("example.com.", [ARecord("web", "10.0.0.1")], host_node="dns1")
        return FakeNode.with_addresses("dns1", "10.0.0.2", services=[DNSService(zone)])

    def test_start_waits_for_knot_to_answer_on_its_control_socket(self) -> None:
        dns1 = self._serving_node()
        net = FakeLab(dns1)
        dns1.net = net
        probed: list = []

        def is_running(client: Any) -> bool:
            probed.append(client.socket_path)
            return True

        with patch("mniplab.dns.provisioner.KnotControlClient.is_running", is_running):
            DNSProvisioner.start_dns_service(dns1, dns1.services[0], net=net)
            expected = DNSProvisioner.control_socket_host_path("dns1", net=net)

        self.assertEqual(probed, [expected])

    def test_a_server_that_never_answers_fails_the_start_naming_its_log(self) -> None:
        dns1 = self._serving_node()
        net = FakeLab(dns1)
        dns1.net = net

        with patch.object(DNSProvisioner, "READY_TIMEOUT", 0), \
                patch("mniplab.dns.provisioner.KnotControlClient.is_running", return_value=False):
            with self.assertRaises(ConfigurationError) as ctx:
                DNSProvisioner.start_dns_service(dns1, dns1.services[0], net=net)

        message = str(ctx.exception)
        self.assertIn("dns1", message)
        self.assertIn("knotd", message)
        self.assertIn("knot.log", message)

    def _resolv_conf_after_start(self, dns1: FakeNode, seeded: str) -> str:
        net = FakeLab(dns1)
        dns1.net = net
        path = net.resolv_conf_file("dns1")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(seeded)

        with patch("mniplab.dns.provisioner.KnotControlClient.is_running", return_value=True):
            DNSProvisioner.start_dns_service(dns1, dns1.services[0], net=net)

        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_a_server_resolves_through_itself_not_the_hosts_nameservers(self) -> None:
        # knotd looks up its own hostname as it starts; through a host
        # nameserver the Lab cannot reach, that waits out a 5 s timeout.
        resolv = self._resolv_conf_after_start(self._serving_node(), "nameserver 192.0.2.53\n")

        self.assertEqual(resolv, "nameserver 127.0.0.1\n")

    def test_a_server_keeps_the_nameservers_its_script_declared(self) -> None:
        dns1 = self._serving_node()
        dns1.params["nameservers"] = ["10.0.5.2"]

        resolv = self._resolv_conf_after_start(dns1, "nameserver 10.0.5.2\n")

        self.assertEqual(resolv, "nameserver 10.0.5.2\n")


class DNSProvisionerReplicationTests(unittest.TestCase):
    """Tests for rendering a Primary's notify and ACL and a Secondary's master."""

    APEX = "example.com."

    def _render(self, node_name: str, service: DNSService, nodes: list) -> str:
        """Render one Node's knot.conf against a Lab and return its content."""
        net = FakeLab(*nodes)
        secondaries = DNSProvisioner.collect_secondaries(nodes)
        conf_path = DNSProvisioner.render_config(node_name, service, secondaries, net=net)
        with open(conf_path, "r", encoding="utf-8") as f:
            return f.read()

    def _lab(self, primary_zone: Optional[Zone] = None) -> tuple:
        """Build a two-Node Lab: ns1 Primary and ns2 Secondary for one apex."""
        zone = primary_zone or Zone(self.APEX, host_node="ns1")
        ns1 = FakeNode.with_addresses("ns1", "10.0.1.2", services=[DNSService(zone)])
        ns2 = FakeNode.with_addresses(
            "ns2",
            "10.0.2.2",
            services=[DNSService(SecondaryZone(self.APEX, primary=ns1))],
        )
        net = FakeLab(ns1, ns2)
        ns1.net = net
        ns2.net = net
        return ns1, ns2

    def test_primary_derives_its_transfer_acl_and_notify_list(self) -> None:
        ns1, ns2 = self._lab()
        conf = self._render("ns1", ns1.services[0], [ns1, ns2])

        self.assertIn("action: transfer", conf)
        self.assertIn("10.0.2.2", conf)
        self.assertIn("notify:", conf)

    def test_explicit_allow_transfer_replaces_the_derivation_outright(self) -> None:
        staged = Zone(self.APEX, host_node="ns1", allow_transfer=["192.0.2.99"])
        ns1, ns2 = self._lab(staged)
        conf = self._render("ns1", ns1.services[0], [ns1, ns2])

        transfer_acl = conf.split("acl:")[1].split("action: transfer")[0]
        self.assertIn("192.0.2.99", transfer_acl)
        self.assertNotIn("10.0.2.2", transfer_acl)

    def test_primary_zone_is_rendered_with_the_journal_enabled(self) -> None:
        ns1, ns2 = self._lab()
        conf = self._render("ns1", ns1.services[0], [ns1, ns2])

        self.assertIn("journal-content: changes", conf)

    def test_secondary_renders_a_master_and_a_notify_acl(self) -> None:
        ns1, ns2 = self._lab()
        conf = self._render("ns2", ns2.services[0], [ns1, ns2])

        self.assertIn("master:", conf)
        self.assertIn("action: notify", conf)
        self.assertIn("10.0.1.2", conf)

    def test_secondary_is_rendered_without_a_zone_file(self) -> None:
        ns1, ns2 = self._lab()
        conf = self._render("ns2", ns2.services[0], [ns1, ns2])
        zone_file = os.path.join(
            DNSProvisioner.host_dir("ns2", "lib", net=ns2.net), "example.com.zone"
        )
        self.assertFalse(os.path.exists(zone_file))

        self.assertIn("zonefile-load: none", conf)

    def test_start_orders_primaries_ahead_of_secondaries(self) -> None:
        ns1, ns2 = self._lab()
        started: list = []
        net = FakeLab(ns1, ns2)

        with patch.object(
                    DNSProvisioner, "start_node_services",
                    side_effect=lambda node, *a, **k: started.append(node.name),
                ), \
                patch.object(DNSProvisioner, "nudge_secondaries"):
            DNSProvisioner.start_all(net, [ns2, ns1])

        self.assertEqual(started, ["ns1", "ns2"])

    def test_a_node_serving_both_roles_starts_with_the_primaries(self) -> None:
        """The mutual-backup Node holds a Primary others replicate, so it goes first."""
        pure_secondary = FakeNode.with_addresses(
            "ns3",
            "10.0.3.2",
            services=[DNSService(SecondaryZone("example.net.", primary="10.0.1.2"))],
        )
        both = FakeNode.with_addresses(
            "ns2",
            "10.0.2.2",
            services=[
                DNSService(
                    Zone("example.net.", host_node="ns2"),
                    SecondaryZone("example.com.", primary="10.0.1.2"),
                )
            ],
        )
        started: list = []
        net = FakeLab(pure_secondary, both)

        with patch.object(
            DNSProvisioner, "start_node_services",
            side_effect=lambda node, *a, **k: started.append(node.name),
        ), patch.object(DNSProvisioner, "nudge_secondaries"):
            DNSProvisioner.start_all(net, [pure_secondary, both])

        self.assertEqual(started, ["ns2", "ns3"])

    def test_stop_discards_a_secondarys_replicated_contents(self) -> None:
        """A Secondary's Records live in Knot's storage, cleared with the rest."""
        ns1, ns2 = self._lab()
        net = ns2.net
        ns2.cmd = Mock()

        DNSProvisioner.render_config(
            "ns2", ns2.services[0], DNSProvisioner.collect_secondaries([ns1, ns2]), net=net
        )
        host_dir = DNSProvisioner.host_dir("ns2", net=net)
        self.assertTrue(os.path.exists(host_dir))

        DNSProvisioner.stop_dns_service(ns2)

        self.assertFalse(os.path.exists(host_dir))

    def test_start_nudges_secondaries_without_blocking_on_a_transfer(self) -> None:
        """An unreachable Primary must not stop the Lab from finishing start."""
        ns2 = FakeNode.with_addresses(
            "ns2",
            "10.0.2.2",
            services=[DNSService(SecondaryZone(self.APEX, primary="192.0.2.99"))],
        )

        FakeLab(ns2)

        with patch(
            "mniplab.dns.control_client.KnotControlClient.connect",
            side_effect=OSError("no such socket"),
        ):
            DNSProvisioner.nudge_secondaries([ns2])


@pytest.mark.unit
class DNSDiagnosticsTests(unittest.TestCase):
    """Tests for DNSDiagnostics: fatal start-time errors and teachable warnings."""

    def _make_node(self, name: str, zones: list) -> FakeNode:
        """Create a Node hosting a DNSService with the given zones."""
        return FakeNode(name, services=[DNSService(*zones)])

    def _make_net(self, *nodes: Any) -> FakeLab:
        """Create a Lab holding these Nodes."""
        return FakeLab(*nodes)

    def test_normalize_nameservers_valid_and_invalid(self) -> None:
        """normalize_nameservers validates IP format and raises ValueError on invalid addresses."""
        from mniplab.dns.diagnostics import normalize_nameservers

        self.assertEqual(
            normalize_nameservers(["10.0.0.1", " 2001:db8::1 "]),
            ["10.0.0.1", "2001:db8::1"],
        )
        self.assertEqual(normalize_nameservers("10.0.0.1"), ["10.0.0.1"])
        self.assertEqual(normalize_nameservers(None), [])
        with self.assertRaises(ValueError):
            normalize_nameservers("not-an-ip")
        with self.assertRaises(ValueError):
            normalize_nameservers(["10.0.0.1", "bad-ip"])

    def test_fatal_non_fqdn_zone_name_raises(self) -> None:
        """Zone name without trailing dot raises ConfigurationError naming node and zone."""
        zone = Zone("example.com.")
        zone.name = "example.com"
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        with self.assertRaises(ConfigurationError) as ctx:
            DNSDiagnostics.validate_network(net)

        err = str(ctx.exception)
        self.assertIn("dns1", err)
        self.assertIn("example.com", err)
        self.assertIn("trailing dot", err.lower())

    def test_fatal_invalid_dns_characters_in_zone_apex_raises(self) -> None:
        """Zone apex with invalid DNS characters raises ConfigurationError naming node and zone."""
        zone = Zone("example.com.")
        zone.name = "bad name.com."
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        with self.assertRaises(ConfigurationError) as ctx:
            DNSDiagnostics.validate_network(net)

        err = str(ctx.exception)
        self.assertIn("dns1", err)
        self.assertIn("bad name.com.", err)
        self.assertIn("invalid", err.lower())

    def test_fatal_invalid_dns_characters_in_record_name_raises(self) -> None:
        """Record owner name with invalid DNS characters raises ConfigurationError naming node, zone, and record."""
        zone = Zone("example.com.", [Record("web site", "A", "10.0.0.1", 300)])
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        with self.assertRaises(ConfigurationError) as ctx:
            DNSDiagnostics.validate_network(net)

        err = str(ctx.exception)
        self.assertIn("dns1", err)
        self.assertIn("example.com.", err)
        self.assertIn("web site", err)

    def test_fatal_duplicate_zone_apex_raises(self) -> None:
        """Duplicate zone apex across multiple nodes raises ConfigurationError naming zone and nodes."""
        node1 = self._make_node("dns1", [Zone("example.com.")])
        node2 = self._make_node("dns2", [Zone("example.com.")])
        net = self._make_net(node1, node2)

        with self.assertRaises(ConfigurationError) as ctx:
            DNSDiagnostics.validate_network(net)

        err = str(ctx.exception)
        self.assertIn("example.com.", err)
        self.assertIn("dns1", err)
        self.assertIn("dns2", err)

    def test_primary_and_its_secondary_may_share_an_apex(self) -> None:
        """Replication is the feature, so a shared apex is accepted."""
        primary = self._make_node("ns1", [Zone("example.com.")])
        secondary = self._make_node(
            "ns2", [SecondaryZone("example.com.", primary="10.0.1.2")]
        )
        net = self._make_net(primary, secondary)

        warnings = DNSDiagnostics.validate_network(net, warn_fn=lambda _: None)

        self.assertEqual(warnings, [])

    def test_warning_secondary_whose_apex_no_node_serves_as_primary(self) -> None:
        """An orphaned Secondary warns rather than failing: it may be staged."""
        secondary = self._make_node(
            "ns2", [SecondaryZone("example.com.", primary="192.0.2.99")]
        )
        net = self._make_net(secondary)

        warnings = DNSDiagnostics.validate_network(net, warn_fn=lambda _: None)

        self.assertEqual(len(warnings), 1)
        self.assertIn("ns2", warnings[0])
        self.assertIn("example.com.", warnings[0])
        self.assertIn("192.0.2.99", warnings[0])

    def test_warning_delegation_ns_without_glue(self) -> None:
        """Child delegation NS without matching in-bailiwick A/AAAA glue produces teachable warning naming record and directive."""
        zone = Zone("example.com.", [
            NSRecord("sub", "ns1.sub.example.com."),
        ])
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertIn("sub", w)
        self.assertIn("ns1.sub.example.com.", w)
        self.assertIn("glue", w.lower())
        self.assertIn("ARecord", w)

    def test_delegation_ns_with_glue_produces_no_warning(self) -> None:
        """Child delegation NS with matching A glue produces no warning."""
        zone = Zone("example.com.", [
            NSRecord("sub", "ns1.sub.example.com."),
            ARecord("ns1.sub", "10.0.0.50"),
        ])
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(warnings, [])

    def test_out_of_bailiwick_delegation_ns_produces_no_warning(self) -> None:
        """Out-of-bailiwick delegation NS produces no glue warning."""
        zone = Zone("example.com.", [
            NSRecord("sub", "ns1.otherdomain.org."),
        ])
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(warnings, [])

    def test_warning_dangling_cname(self) -> None:
        """CNAME pointing to non-existent target produces teachable warning naming record and directive."""
        zone = Zone("example.com.", [
            CNAMERecord("portal", "missing.example.com."),
        ])
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertIn("portal", w)
        self.assertIn("missing.example.com.", w)
        self.assertIn("ARecord", w)

    def test_valid_cname_produces_no_warning(self) -> None:
        """CNAME pointing to existing A record produces no warning."""
        zone = Zone("example.com.", [
            ARecord("web", "10.0.0.1"),
            CNAMERecord("portal", "web.example.com."),
        ])
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(warnings, [])

    def test_warning_missing_reverse_ptr_in_hosted_reverse_zone(self) -> None:
        """Forward A record with no reverse PTR in hosted reverse zone produces warning naming record and directive."""
        fwd_zone = Zone("example.com.", [
            ARecord("web", "10.0.0.1"),
        ])
        rev_zone = Zone("0.0.10.in-addr.arpa.")
        node = self._make_node("dns1", [fwd_zone, rev_zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertIn("10.0.0.1", w)
        self.assertIn("0.0.10.in-addr.arpa.", w)
        self.assertIn("PTRRecord", w)

    def test_valid_reverse_ptr_produces_no_warning(self) -> None:
        """Forward A record with matching reverse PTR in hosted reverse zone produces no warning."""
        fwd_zone = Zone("example.com.", [
            ARecord("web", "10.0.0.1"),
        ])
        rev_zone = Zone("0.0.10.in-addr.arpa.", [
            PTRRecord("1", "web.example.com."),
        ])
        node = self._make_node("dns1", [fwd_zone, rev_zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(warnings, [])

    def test_unhosted_reverse_zone_produces_no_warning(self) -> None:
        """Forward A record with no hosted reverse zone produces no warning."""
        fwd_zone = Zone("example.com.", [
            ARecord("web", "192.168.1.1"),
        ])
        rev_zone = Zone("0.0.10.in-addr.arpa.")
        node = self._make_node("dns1", [fwd_zone, rev_zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(warnings, [])

    def test_reverse_ptr_target_mismatch_produces_warning(self) -> None:
        """PTR record pointing to a different hostname does not satisfy forward-reverse parity."""
        fwd_zone = Zone("example.com.", [
            ARecord("web", "10.0.0.1"),
        ])
        rev_zone = Zone("0.0.10.in-addr.arpa.", [
            PTRRecord("1", "other.example.com."),
        ])
        node = self._make_node("dns1", [fwd_zone, rev_zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(len(warnings), 1)
        self.assertIn("PTRRecord", warnings[0])

    def test_reverse_zone_boundary_no_false_match(self) -> None:
        """IP 10.100.0.1 should not falsely match reverse zone 0.10.in-addr.arpa."""
        fwd_zone = Zone("example.com.", [
            ARecord("web", "10.100.0.1"),
        ])
        rev_zone = Zone("0.10.in-addr.arpa.")
        node = self._make_node("dns1", [fwd_zone, rev_zone])
        net = self._make_net(node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertEqual(warnings, [])

    def test_warning_dnssec_ds_digest_mismatch(self) -> None:
        """Parent DS record mismatch with child zone KSK digest emits actionable warning."""
        child_zone = Zone("example.com.", dnssec=True)
        # Corrupt DS record with wrong digest
        corrupt_ds = DSRecord("example.com.", key_tag=9999, algorithm=13, digest_type=2, digest="deadbeef0000")
        parent_zone = Zone("com.", dnssec=True, records=[corrupt_ds])

        p_node = self._make_node("tld1", [parent_zone])
        c_node = self._make_node("auth1", [child_zone])
        net = self._make_net(p_node, c_node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertTrue(any("SERVFAIL" in w and "example.com." in w and "mismatch" in w.lower() for w in warnings))

    def test_warning_dnssec_ds_points_to_unsigned_zone(self) -> None:
        """Parent DS record pointing to an unsigned child zone emits actionable warning."""
        child_zone = Zone("example.com.", dnssec=False)
        ds = DSRecord("example.com.", key_tag=1234, algorithm=13, digest_type=2, digest="abcdef123456")
        parent_zone = Zone("com.", dnssec=True, records=[ds])

        p_node = self._make_node("tld1", [parent_zone])
        c_node = self._make_node("auth1", [child_zone])
        net = self._make_net(p_node, c_node)

        warnings = DNSDiagnostics.validate_network(net)

        self.assertTrue(any("unsigned" in w.lower() and "example.com." in w for w in warnings))

    def test_valid_dnssec_ds_delegation_no_warning(self) -> None:
        """Parent DS record matching child KSK produces no DNSSEC delegation warnings."""
        child_zone = Zone("example.com.", dnssec=True)
        valid_ds = child_zone.ds_record(digest_type=2)
        parent_zone = Zone("com.", dnssec=True, records=[valid_ds])

        p_node = self._make_node("tld1", [parent_zone])
        c_node = self._make_node("auth1", [child_zone])
        net = self._make_net(p_node, c_node)

        warnings = DNSDiagnostics.validate_network(net)

        dnssec_warnings = [w for w in warnings if "DS record" in w]
        self.assertEqual(dnssec_warnings, [])

    def test_validate_network_emits_mininet_warnings(self) -> None:
        """validate_network emits mininet warnings when warn_fn is provided or via mininet.log.warn."""
        zone = Zone("example.com.", [
            CNAMERecord("portal", "missing.example.com."),
        ])
        node = self._make_node("dns1", [zone])
        net = self._make_net(node)

        with patch("mniplab.dns.diagnostics.warn") as mock_warn:
            warnings = DNSDiagnostics.validate_network(net)
            self.assertEqual(len(warnings), 1)
            self.assertTrue(mock_warn.called)
            warn_call_text = mock_warn.call_args[0][0]
            self.assertIn("portal", warn_call_text)

    def test_mniplab_start_validates_dns_prior_to_starting_daemons(self) -> None:
        """mnIPLab.start() validates DNS configuration before starting daemons."""
        h1 = FakeNode("h1", services=[DNSService(Zone("example.com."))])
        h2 = FakeNode("h2", services=[DNSService(Zone("example.com."))])

        with unstarted_lab() as net:
            net.hosts = [h1, h2]
            with patch.object(net._lifecycle, "start_all") as mock_start_all:
                with self.assertRaises(ConfigurationError):
                    net.start()
                mock_start_all.assert_not_called()


@pytest.mark.unit
class RootZoneTests(unittest.TestCase):
    """Tests for hosting the root zone, which an in-lab Iterative Walk starts from."""

    def test_root_apex_qualifies_defaults_without_a_double_dot(self) -> None:
        """A Zone('.') is its own trailing dot, so defaults append nothing to it."""
        zone = Zone(".", [], host_node="root1")

        soa = zone.soa_record
        self.assertIsNotNone(soa)
        assert soa is not None
        self.assertEqual(soa.mname, "root1.")
        self.assertEqual(soa.rname, "hostmaster.")
        self.assertEqual(zone.ns_records[0].nameserver, "root1.")

    def test_root_zone_file_renders_with_root_origin(self) -> None:
        """The rendered master file is rooted at '.' and carries its delegations."""
        zone = Zone(
            ".",
            [NSRecord("com", "ns.com."), ARecord("ns.com.", "10.0.2.2")],
            host_node="root1",
        )

        rendered = zone.to_zone_file()

        self.assertIn("$ORIGIN .", rendered)
        self.assertIn("com 300 IN NS ns.com.", rendered)
        self.assertIn("ns.com. 300 IN A 10.0.2.2", rendered)

    def test_root_apex_passes_syntax_checks(self) -> None:
        """'.' is a valid apex; its records are still checked for DNS characters."""
        DNSDiagnostics.check_zone_syntax(
            Zone(".", [ARecord("root1", "10.0.1.2")], host_node="root1"),
            node_name="root1",
        )

        with self.assertRaises(ConfigurationError):
            DNSDiagnostics.check_zone_syntax(
                Zone(".", [ARecord("bad name", "10.0.1.2")], host_node="root1"),
                node_name="root1",
            )


if __name__ == "__main__":
    unittest.main()

