"""Real Knot execution of the DNS Test Lab described as an authored definition."""

import os
import tempfile

from mniplab.tests.labs import LabCase, authored_dns_test_lab, load_builder
from mniplab.dns.query import DNSQueryService
from mniplab.dns.record_service import DNSRecordService
from mniplab.resolver.access import ResolverAccessService
from mniplab.resolver.query import ResolverQueryService
from mniplab.utils import wait_until
from mniplab.webserver.authoring import AuthoringRepository, dns_keep_records
from mniplab.webserver.authoring_keep import keep_feature_baseline


class AuthoredPrimaryDNSLabTests(LabCase):
    """Primary authoring builds a real authoritative Service and private resolver."""

    def test_dns_test_lab_answers_from_authored_primary(self) -> None:
        topology = authored_dns_test_lab()
        auth1 = next(node for node in topology["nodes"] if node["id"] == "auth1")
        res1 = next(node for node in topology["nodes"] if node["id"] == "res1")
        ns2 = next(node for node in topology["nodes"] if node["id"] == "ns2")
        # res1 knows no Root Hints: only its forwarder can answer for it.
        res1["resolver"] = {
            "root_hints": [], "forwarders": [{"node_id": "auth1"}],
            "max_ttl": 120, "max_negative_ttl": 30,
            "access_control": [{"subnet": "10.0.0.0/8", "action": "allow"}],
        }
        # ns2 is named by address in the Primary's allow list, and names res1 by address.
        auth1["dns"]["zones"][0]["allow_transfer"] = [{"address": "10.0.6.2"}]
        ns2["dns"] = {"zones": [{"kind": "secondary", "name": "example.com.",
                                   "primary_node_id": "auth1"}]}
        ns2["nameservers"] = [{"address": "10.0.4.2"}]
        auth1["dns"]["zones"].append({
            "kind": "primary", "name": "5.0.10.in-addr.arpa.", "default_ttl": 300,
            "records": [{"name": "@", "type": "NS", "rdata": "ns.example.com.", "ttl": 300},
                        {"name": "2", "type": "PTR", "rdata": "web.example.com.", "ttl": 300}]})
        with tempfile.TemporaryDirectory() as root:
            repository = AuthoringRepository(root)
            draft = repository.create()
            draft["topology"] = topology
            saved = repository.save(draft["id"], draft["revision"], draft)
            path = os.path.join(root, "lab.py")
            repository.write_builder(saved, path)
            network = self.start(load_builder(path).build_network())

            answer = network.get("client1").cmd("dig @10.0.3.2 web.example.com A +short")
            self.assertIn("10.0.5.2", answer)
            reverse = network.get("client1").cmd("dig @10.0.3.2 -x 10.0.5.2 +short")
            self.assertIn("web.example.com.", reverse)
            private_resolver = network.get("client1").cmd("cat /etc/resolv.conf")
            self.assertIn("nameserver 10.0.4.2", private_resolver)
            self.assertEqual(network.get("res1").services[0].forwarders, ["10.0.3.2"])
            self.assertIn("web.example.com has address 10.0.5.2",
                          network.get("client1").cmd("host web.example.com"))
            self.assertTrue(wait_until(
                lambda: "10.0.5.2" in network.get("client1").cmd(
                    "dig @10.0.6.2 web.example.com A +short"), 5.0),
                "Secondary did not answer after a Zone Transfer its address was allowed")
            # dig prints only "Transfer failed" for a refusal and for a timeout alike, so
            # show that auth1 is reachable over TCP and hands the Zone to the listed address.
            self.assertIn("status: NOERROR", network.get("client1").cmd(
                "dig @10.0.3.2 example.com SOA +tcp +time=2 +tries=1"))
            self.assertIn("web.example.com.", network.get("ns2").cmd(
                "dig @10.0.3.2 example.com AXFR +time=2 +tries=1"))
            transfer = network.get("client1").cmd("dig @10.0.3.2 example.com AXFR +time=2 +tries=1")
            self.assertIn("Transfer failed", transfer)
            self.assertNotIn("web.example.com.", transfer)
            # ns2 serves example.com. itself, so only a name outside it shows which
            # name server it asks: the reverse Zone is answered by res1, never by ns2.
            self.assertIn("nameserver 10.0.4.2", network.get("ns2").cmd("cat /etc/resolv.conf"))
            self.assertIn("web.example.com.", network.get("ns2").cmd("host 10.0.5.2"))
            baseline = keep_feature_baseline(saved)["dns:auth1:example.com."]
            live = DNSQueryService.get_dns_records(network, "auth1", "example.com.")
            self.assertEqual(dns_keep_records(live, "example.com.", baseline["value"], "auth1"),
                             baseline["value"])
            DNSRecordService.set_record(network, "auth1", "example.com.",
                                        "ns2", "A", "10.0.6.2", ttl=300)
            DNSRecordService.set_record(network, "auth1", "example.com.",
                                        "@", "NS", "ns2.example.com.", ttl=300)
            changed = dns_keep_records(DNSQueryService.get_dns_records(
                network, "auth1", "example.com."), "example.com.", baseline["value"], "auth1")
            self.assertIn({"name": "@", "type": "NS", "rdata": "ns2.example.com.",
                           "ttl": 300}, changed)

    def test_secondary_transfers_the_authored_primary_zone(self) -> None:
        topology = authored_dns_test_lab()
        auth1 = next(node for node in topology["nodes"] if node["id"] == "auth1")
        ns2 = next(node for node in topology["nodes"] if node["id"] == "ns2")
        auth1["dns"]["zones"][0]["allow_transfer"] = [{"node_id": "ns2"}]
        ns2["dns"] = {"zones": [{"kind": "secondary", "name": "example.com.",
                                   "primary_node_id": "auth1"}]}

        network = self.start_draft(topology, address_family="ipv4")

        self.assertEqual(network.get("ns2").services[0].secondary_zones[
            "example.com."].records, [])
        self.assertTrue(wait_until(
            lambda: "10.0.5.2" in network.get("client1").cmd(
                "dig @10.0.6.2 web.example.com A +short"), 5.0),
            "Secondary did not answer after its initial Zone Transfer",
        )

        # Shorten the Zone's expiry, then cut the Secondary off from it.
        soa = next(item for item in DNSQueryService.get_dns_records(
            network, "auth1", "example.com.") if item["type"] == "SOA")
        fields = soa["data"].split()
        fields[2] = str(int(fields[2]) + 1)
        fields[3:6] = ["2", "2", "12"]
        DNSRecordService.set_record(
            network, "auth1", "example.com.", "@", "SOA", " ".join(fields),
            ttl=soa["ttl"], old_owner="@", old_type="SOA", old_data=soa["data"])
        self.assertTrue(wait_until(lambda: any(
            item["type"] == "SOA" and item["data"].split()[5] == "12"
            for item in DNSQueryService.get_dns_records(network, "ns2", "example.com.")), 10))
        client1 = network.get("client1")
        query = "dig @10.0.6.2 web.example.com A +time=1 +tries=1"
        # Down only r1's side, so auth1 keeps its default route for the return.
        r1 = network.get("r1")
        r1.cmd("ip link set r1-eth2 down")
        try:
            expired = wait_until(lambda: "status: SERVFAIL" in client1.cmd(query), 20)
        finally:
            r1.cmd("ip link set r1-eth2 up")
        self.assertTrue(expired, "Secondary kept answering past the Zone's expiry")
        DNSQueryService.refresh_zone(network, "ns2", "example.com.")
        self.assertTrue(wait_until(lambda: "10.0.5.2" in client1.cmd(query + " +short"), 10),
                        "Secondary did not answer again after a refresh")

    def test_authored_resolver_walks_the_in_lab_hierarchy(self) -> None:
        topology = authored_dns_test_lab()
        resolver = next(node for node in topology["nodes"] if node["id"] == "res1")
        resolver["resolver"] = {
            "root_hints": [{"node_id": "root1"}], "forwarders": [],
            "max_ttl": 120, "max_negative_ttl": 30,
            "access_control": [{"subnet": "10.0.0.0/8", "action": "allow"},
                               {"subnet": "0.0.0.0/0", "action": "refuse"}],
        }
        network = self.start_draft(topology, address_family="ipv4")
        service = network.get("res1").services[0]
        self.assertEqual(service.root_hints[0].label, "root1")
        self.assertEqual(service.max_ttl, 120)
        self.assertIn("10.0.5.2", network.get("client1").cmd(
            "dig @10.0.4.2 web.example.com A +short"))
        self.assertEqual(
            [{"subnet": rule["subnet"], "action": rule["action"]}
             for rule in ResolverAccessService.list_rules(network, "res1")],
            resolver["resolver"]["access_control"],
        )

    def test_signed_delegations_validate_from_authored_root_anchor(self) -> None:
        topology = authored_dns_test_lab()
        nodes = {node["id"]: node for node in topology["nodes"]}
        for node_id in ("root1", "tld1", "auth1"):
            nodes[node_id]["dns"]["zones"][0]["dnssec"] = True
        nodes["auth1"]["dns"]["zones"][0].update(nsec3=True, nsec3_salt="aabbccdd")
        nodes["tld1"]["dns"]["zones"][0].update(nsec3=True, nsec3_salt="")
        nodes["root1"]["dns"]["zones"][0]["nsec3"] = True
        nodes["root1"]["dns"]["zones"][0]["ds_delegations"] = [
            {"node_id": "tld1", "zone": "com."}]
        nodes["tld1"]["dns"]["zones"][0]["ds_delegations"] = [
            {"node_id": "auth1", "zone": "example.com."}]
        nodes["res1"]["resolver"] = {
            "root_hints": [{"node_id": "root1"}], "forwarders": [],
            "max_ttl": 120, "max_negative_ttl": 30,
            "access_control": [{"subnet": "10.0.0.0/8", "action": "allow"}],
            "trust_anchors": [{"node_id": "root1"}],
        }
        nodes["auth1"]["dns"]["zones"][0]["allow_transfer"] = [{"node_id": "ns2"}]
        nodes["ns2"]["dns"] = {"zones": [{"kind": "secondary", "name": "example.com.",
                                            "primary_node_id": "auth1"}]}
        network = self.start_draft(topology, address_family="ipv4")
        client1 = network.get("client1")
        answer = client1.cmd("dig @10.0.4.2 web.example.com A +dnssec")
        self.assertIn("10.0.5.2", answer)
        self.assertIn(" ad;", answer)
        self.assertIn("RRSIG", answer)

        # delv checks the chain itself, from the Root's DS as its only anchor.
        root_ds = network.get("root1").services[0].primary_zones["."].ds_record(digest_type=2)
        with tempfile.NamedTemporaryFile("w", suffix=".anchor") as anchor:
            anchor.write(f'trust-anchors {{ . static-ds {root_ds.key_tag} {root_ds.algorithm} '
                         f'{root_ds.digest_type} "{root_ds.digest}"; }};\n')
            anchor.flush()
            delv = client1.cmd(f"delv @10.0.4.2 -a {anchor.name} web.example.com")
        self.assertIn("; fully validated", delv)

        # A Secondary of a signed Zone serves it signed, and records the transfer.
        self.assertTrue(wait_until(lambda: "RRSIG" in client1.cmd(
            "dig @10.0.6.2 web.example.com A +dnssec +norecurse"), 5.0),
            "Secondary did not answer the signed Zone after its transfer")
        transfers = DNSQueryService.get_dns_transfers(network, "ns2", "example.com.")
        self.assertGreaterEqual(len(transfers["transfers"]), 1, transfers)

        # Denial of existence is proved with hashed names, not plain NSEC.
        missing = network.get("client1").cmd("dig @10.0.4.2 missing.example.com A +dnssec")
        self.assertIn("status: NXDOMAIN", missing)
        self.assertIn(" NSEC3 ", missing)
        self.assertNotIn(" NSEC ", missing)
        self.assertIn("AABBCCDD", network.get("client1").cmd(
            "dig @10.0.3.2 example.com NSEC3PARAM +short").upper())
        # An empty salt is a salt of no octets, not a request for a random one.
        params = network.get("client1").cmd("dig @10.0.2.2 com. NSEC3PARAM +short").split()
        self.assertEqual(params[3:4], ["-"], params)

        # The DNSSEC details report the salt each Zone is signed with, including
        # the random one Knot chose for the Zone that declared none.
        root_params = network.get("client1").cmd("dig @10.0.1.2 . NSEC3PARAM +short").split()
        self.assertEqual(len(root_params[3]), 16, root_params)
        for node, zone, salt in (("root1", ".", root_params[3].lower()),
                                 ("tld1", "com.", None),
                                 ("auth1", "example.com.", "aabbccdd")):
            details = DNSQueryService.get_zone_dnssec(network, node, zone)
            self.assertEqual((details["nsec3"], details["nsec3_salt"]), (True, salt), zone)

        # A stale DS breaks the chain of trust; restoring it repairs it.
        ds = next(item for item in DNSQueryService.get_dns_records(network, "tld1", "com.")
                  if item["type"] == "DS" and item["owner"] == "example.com.")
        tag, algorithm, digest_type, _digest = ds["data"].split()
        stale = f"{tag} {algorithm} {digest_type} {'0' * 64}"
        query = "dig @10.0.4.2 web.example.com A +dnssec +time=1 +tries=1"
        for old, new, expected in ((ds["data"], stale, "status: SERVFAIL"),
                                   (stale, ds["data"], " ad;")):
            DNSRecordService.set_record(
                network, "tld1", "com.", "example.com.", "DS", new, ttl=ds["ttl"],
                old_owner="example.com.", old_type="DS", old_data=old)
            ResolverQueryService.flush_cache(network, "res1")
            self.assertTrue(wait_until(lambda: expected in client1.cmd(query), 10), expected)
