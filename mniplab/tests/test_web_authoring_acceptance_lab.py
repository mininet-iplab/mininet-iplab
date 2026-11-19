"""Classroom acceptance of Lab Authoring over real Lab processes.

The authenticated HTTP application starts each Preview through the real
Lab-process launcher, so Keep reads real Knot and Unbound state
through the Lab's handle, and a failed start is cleaned up by the real
process sweep. Each scenario previews one existing Service-family Test Lab.
"""

import os

import httpx2

from mniplab.tests.authoring_fixtures import (
    RealLabAuthoringFixture, host_node, root_interfaces, router_node,
)
from mniplab.tests.labs import authored_dns_test_lab
from mniplab.webserver import registry

EDITABLE_RECORD_TYPES = {"A", "AAAA", "CNAME", "PTR", "TXT", "MX", "NS", "SOA"}


class KeepExclusionTests(RealLabAuthoringFixture):
    """Keep takes authored intent from real Services, never what they generated."""

    def test_signed_hierarchy_keep_offers_only_the_edited_primary_zone(self) -> None:
        self._real_labs("multi")
        topology = authored_dns_test_lab()
        nodes = {node["id"]: node for node in topology["nodes"]}
        for node_id in ("root1", "tld1", "auth1"):
            nodes[node_id]["dns"]["zones"][0]["dnssec"] = True
        nodes["root1"]["dns"]["zones"][0]["ds_delegations"] = [{"node_id": "tld1", "zone": "com."}]
        nodes["tld1"]["dns"]["zones"][0]["ds_delegations"] = [
            {"node_id": "auth1", "zone": "example.com."}]
        nodes["auth1"]["dns"]["zones"][0]["allow_transfer"] = [{"node_id": "ns2"}]
        secondary = {"kind": "secondary", "name": "example.com.", "primary_node_id": "auth1"}
        nodes["ns2"]["dns"] = {"zones": [secondary]}
        nodes["res1"]["resolver"] = {
            "root_hints": [{"node_id": "root1"}], "forwarders": [],
            "max_ttl": 120, "max_negative_ttl": 30,
            "access_control": [{"subnet": "10.0.0.0/8", "action": "allow"}],
            "trust_anchors": [{"node_id": "root1"}],
        }
        # A validated lookup through the Resolver warms its cache at start.
        nodes["client1"]["startup"] = [{"stage": "after-start", "timeout": 10, "may_fail": False,
                                        "command": "dig @10.0.4.2 web.example.com A +dnssec"}]

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._saved(client, topology)
            url = f"/api/authoring/drafts/{draft['id']}"
            preview = await client.post(f"{url}/preview", json={"revision": draft["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            lab = f"/api/labs/{preview.json()['key']}"

            cache = await client.get(f"{lab}/nodes/res1/resolver/cache")
            self.assertEqual(cache.status_code, 200, cache.text)
            self.assertIn("example.com", cache.text)
            self.assertEqual(await self._changes(client, url), [])
            # Generated and replicated state moves: new keys and signatures,
            # and a fresh full transfer to the Secondary.
            for path in ("/dns/auth1/zones/example.com./dnssec/rollover",
                         "/dns/ns2/zones/example.com./retransfer"):
                moved = await client.post(f"{lab}{path}")
                self.assertEqual(moved.status_code, 200, f"{path}: {moved.text}")
            self.assertEqual(await self._changes(client, url), [])

            edited = await client.post(f"{lab}/dns/auth1/zones/example.com./records",
                                       json={"owner": "mail", "type": "A",
                                             "data": "10.0.5.25", "ttl": 300})
            self.assertEqual(edited.status_code, 200, edited.text)
            offered = await self._changes(client, url)
            self.assertEqual([item["id"] for item in offered], ["dns:auth1:example.com."])
            kept = await self._keep(client, url, draft["revision"], offered[0])
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertFalse(kept.json()["preview_valid"])
            after = {node["id"]: node for node in kept.json()["topology"]["nodes"]}
            zone = after["auth1"]["dns"]["zones"][0]
            self.assertTrue(zone["dnssec"])
            self.assertIn({"name": "mail", "type": "A", "rdata": "10.0.5.25", "ttl": 300},
                          zone["records"])
            self.assertLessEqual({record["type"] for record in zone["records"]},
                                 EDITABLE_RECORD_TYPES)
            self.assertEqual(after["ns2"]["dns"]["zones"], [secondary])
            for node_id in ("root1", "tld1", "res1"):
                self.assertEqual(after[node_id], nodes[node_id], node_id)
        self._run(exercise)


class PreviewRecoveryTests(RealLabAuthoringFixture):
    """A Preview whose real Lab fails to start leaves the host as it found it."""

    def test_a_failed_preview_start_leaves_nothing_and_frees_the_shared_slot(self) -> None:
        self._real_labs("single")
        failing = {"stage": "before-daemons", "command": "exit 3", "timeout": 5,
                   "may_fail": False}
        topology = {
            # A persistent Router seeds the Preview's own copy before h1 fails.
            "nodes": [host_node("a", "h1", startup=[failing]),
                      router_node("b", "r1", frr_config="ip route 198.51.100.0/24 192.0.2.1\n"),
                      {"id": "s", "name": "s1", "type": "switch"}],
            "links": [
                {"id": "l1", "ends": [
                    {"node_id": "a", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "192.0.2.1/24", "ipv6": ""},
                    {"node_id": "s", "interface_id": "if-000000000002", "port": 1,
                     "ipv4": "", "ipv6": ""}]},
                {"id": "l2", "ends": [
                    {"node_id": "b", "interface_id": "if-000000000003", "port": 0,
                     "ipv4": "192.0.2.2/24", "ipv6": ""},
                    {"node_id": "s", "interface_id": "if-000000000004", "port": 2,
                     "ipv4": "", "ipv6": ""}]},
            ],
        }
        previews = os.path.join(self.storage.name, "previews", "config")

        async def exercise(client: httpx2.AsyncClient) -> None:
            before = root_interfaces()
            draft = await self._saved(client, topology, persistent_config=True)
            url = f"/api/authoring/drafts/{draft['id']}"
            failed = await client.post(f"{url}/preview", json={"revision": draft["revision"]})
            self.assertEqual(failed.status_code, 500, failed.text)
            self.assertIn("exit 3", failed.json()["detail"])
            self.assertEqual(registry.lab_service.labs(), [])
            self.assertFalse((await client.get("/api/status")).json()["running"])
            self.assertEqual(os.listdir(previews) if os.path.isdir(previews) else [], [])
            self.assertEqual(root_interfaces(), before)
            self.assertFalse((await client.get(f"{url}/preview-status")).json()["current"])

            draft["topology"]["nodes"][0]["startup"] = []
            fixed = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(fixed.status_code, 200, fixed.text)
            started = await client.post(f"{url}/preview",
                                        json={"revision": fixed.json()["revision"]})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(started.json()["preview_valid"])
        self._run(exercise)
