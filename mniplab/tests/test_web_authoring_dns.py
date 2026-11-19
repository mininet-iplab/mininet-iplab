"""Primary DNS authoring through the authenticated application boundary."""

import copy
from unittest.mock import patch

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, host_node
from mniplab.tests.labs import authored_dns_test_lab
from mniplab.tests.test_web_multi_lab import FakeLauncher


@pytest.mark.unit
class AuthoringPrimaryDNSAPITests(AuthoringAPIFixture):
    """A saved primary Zone reaches Preview and the published Lab Example."""

    def test_primary_zone_and_host_nameserver_round_trip(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "Authoritative DNS lesson"
            draft["catalog"]["area"] = "DNS"
            primary = host_node("dns", "dns1")
            primary["dns"] = {"zones": [{
                "kind": "primary", "name": "example.com.", "default_ttl": 300,
                "records": [
                    {"name": "@", "type": "NS", "rdata": "ns1.example.com.", "ttl": 300},
                    {"name": "ns1", "type": "A", "rdata": "10.0.0.2", "ttl": 300},
                    {"name": "web", "type": "A", "rdata": "10.0.0.2", "ttl": 300},
                ],
            }]}
            client_host = host_node("client", "h1")
            client_host["nameservers"] = [{"node_id": "dns"}]
            draft["topology"] = {"nodes": [primary, client_host], "links": [{
                "id": "dns-link", "ends": [
                    {"node_id": "dns", "interface_id": "if-000000000001", "port": 0,
                     "ipv4": "10.0.0.2/24", "ipv6": ""},
                    {"node_id": "client", "interface_id": "if-000000000002", "port": 0,
                     "ipv4": "10.0.0.3/24", "ipv6": ""},
                ],
            }]}
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            reopened = (await client.get(url)).json()
            self.assertEqual(reopened["topology"], draft["topology"])
            preview = await client.post(f"{url}/preview", json={"revision": reopened["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(f"{url}/publish", json={"revision": reopened["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            catalog = (await client.get("/api/examples")).json()["examples"]
            self.assertIn(published.json()["example"]["id"], [row["id"] for row in catalog])

            renamed = copy.deepcopy(published.json()["draft"])
            renamed["topology"]["nodes"][0]["name"] = "ns1"
            updated = await client.put(url, json={"revision": renamed["revision"], "document": renamed})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertFalse(updated.json()["preview_valid"])
            self.assertEqual(updated.json()["topology"]["nodes"][1]["nameservers"],
                             [{"node_id": "dns"}])

            altered = copy.deepcopy(updated.json())
            altered["topology"]["nodes"][0]["dns"]["zones"][0]["records"][2]["rdata"] = "bad-address"
            refused = await client.put(url, json={"revision": altered["revision"], "document": altered})
            self.assertEqual(refused.status_code, 422, refused.text)

            altered = copy.deepcopy(updated.json())
            altered["topology"]["nodes"][1]["nameservers"] = [{"node_id": []}]
            refused = await client.put(url, json={"revision": altered["revision"], "document": altered})
            self.assertEqual(refused.status_code, 422, refused.text)

            altered = copy.deepcopy(updated.json())
            altered["topology"]["nodes"][0]["dns"]["zones"][0]["records"][2] = {
                "name": "@", "type": "SOA", "rdata": "incomplete", "ttl": 300,
            }
            refused = await client.put(url, json={"revision": altered["revision"], "document": altered})
            self.assertEqual(refused.status_code, 422, refused.text)

        self._as("instructor", exercise)

    def test_secondary_declares_primary_relationship_without_records(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "Zone Transfer lesson"
            draft["catalog"]["area"] = "DNS"
            ns1 = host_node("primary", "ns1")
            ns1["dns"] = {"zones": [{"kind": "primary", "name": "example.com.",
                                      "default_ttl": 300, "records": [
                                          {"name": "web", "type": "A", "rdata": "10.0.0.2", "ttl": 300}],
                                      "allow_transfer": [{"node_id": "secondary"}]}]}
            ns2 = host_node("secondary", "ns2")
            ns2["dns"] = {"zones": [{"kind": "secondary", "name": "example.com.",
                                      "primary_node_id": "primary"}]}
            draft["topology"] = {"nodes": [ns1, ns2], "links": [{"id": "transfer", "ends": [
                {"node_id": "primary", "interface_id": "if-000000000001", "port": 0,
                 "ipv4": "10.0.0.2/24", "ipv6": ""},
                {"node_id": "secondary", "interface_id": "if-000000000002", "port": 0,
                 "ipv4": "10.0.0.3/24", "ipv6": ""},
            ]}]}
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            preview = await client.post(f"{url}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(f"{url}/publish", json={"revision": saved.json()["revision"]})
            self.assertEqual(published.status_code, 200, published.text)

            invalid = copy.deepcopy(published.json()["draft"])
            invalid["topology"]["nodes"][1]["dns"]["zones"][0]["records"] = [
                {"name": "web", "type": "A", "rdata": "10.0.0.2", "ttl": 300}]
            refused = await client.put(url, json={"revision": invalid["revision"], "document": invalid})
            self.assertEqual(refused.status_code, 422, refused.text)

            refused_transfer = copy.deepcopy(published.json()["draft"])
            refused_transfer["topology"]["nodes"][0]["dns"]["zones"][0]["allow_transfer"] = []
            accepted = await client.put(url, json={"revision": refused_transfer["revision"],
                                                   "document": refused_transfer})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertFalse(accepted.json()["preview_valid"])

            duplicate = copy.deepcopy(accepted.json())
            duplicate["topology"]["nodes"][1]["dns"]["zones"] = [
                {"kind": "primary", "name": "example.com.", "default_ttl": 300, "records": []}]
            refused = await client.put(url, json={"revision": duplicate["revision"], "document": duplicate})
            self.assertEqual(refused.status_code, 422, refused.text)

        self._as("instructor", exercise)

    def test_resolver_references_and_acls_round_trip(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "Iterative Resolver lesson"
            draft["catalog"]["area"] = "DNS"
            draft["topology"] = authored_dns_test_lab()
            resolver = next(node for node in draft["topology"]["nodes"] if node["id"] == "res1")
            resolver["resolver"] = {
                "root_hints": [{"node_id": "root1"}], "forwarders": [],
                "max_ttl": 120, "max_negative_ttl": 30,
                "access_control": [{"subnet": "10.0.0.0/8", "action": "allow"},
                                   {"subnet": "0.0.0.0/0", "action": "refuse"}],
            }
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            preview = await client.post(f"{url}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(f"{url}/publish", json={"revision": saved.json()["revision"]})
            self.assertEqual(published.status_code, 200, published.text)

            renamed = copy.deepcopy(published.json()["draft"])
            next(node for node in renamed["topology"]["nodes"] if node["id"] == "root1")["name"] = "rootns"
            updated = await client.put(url, json={"revision": renamed["revision"], "document": renamed})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertFalse(updated.json()["preview_valid"])
            self.assertEqual(next(node for node in updated.json()["topology"]["nodes"]
                                  if node["id"] == "res1")["resolver"]["root_hints"],
                             [{"node_id": "root1"}])

            invalid = copy.deepcopy(updated.json())
            next(node for node in invalid["topology"]["nodes"]
                 if node["id"] == "res1")["resolver"]["access_control"][0]["action"] = "open"
            refused = await client.put(url, json={"revision": invalid["revision"], "document": invalid})
            self.assertEqual(refused.status_code, 422, refused.text)

        self._as("instructor", exercise)

    def test_dnssec_policy_and_delegation_round_trip(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "Signed hierarchy"
            draft["catalog"]["area"] = "DNS"
            draft["topology"] = authored_dns_test_lab()
            nodes = {node["id"]: node for node in draft["topology"]["nodes"]}
            for node_id in ("root1", "tld1", "auth1"):
                nodes[node_id]["dns"]["zones"][0]["dnssec"] = True
            nodes["root1"]["dns"]["zones"][0]["ds_delegations"] = [
                {"node_id": "tld1", "zone": "com."}]
            nodes["tld1"]["dns"]["zones"][0]["ds_delegations"] = [
                {"node_id": "auth1", "zone": "example.com."}]
            nodes["res1"]["resolver"] = {
                "root_hints": [{"node_id": "root1"}], "forwarders": [],
                "max_ttl": 120, "max_negative_ttl": 30, "access_control": [],
                "trust_anchors": [{"node_id": "root1"}],
            }
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            preview = await client.post(f"{url}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            published = await client.post(f"{url}/publish", json={"revision": saved.json()["revision"]})
            self.assertEqual(published.status_code, 200, published.text)
            invalid = copy.deepcopy(published.json()["draft"])
            next(node for node in invalid["topology"]["nodes"] if node["id"] == "root1")[
                "dns"]["zones"][0]["ds_delegations"] = [
                {"node_id": "tld1", "zone": "unsigned."}]
            refused = await client.put(url, json={"revision": invalid["revision"], "document": invalid})
            self.assertEqual(refused.status_code, 422, refused.text)

        self._as("instructor", exercise)

    def test_selective_keep_keeps_dns_structure_and_resolver_intent(self) -> None:
        self._launch_with("multi", FakeLauncher())
        live_records = [
            {"owner": "web.example.com.", "type": "A", "data": "10.0.0.2", "ttl": 300},
            {"owner": "example.com.", "type": "SOA",
             "data": "dns1.example.com. hostmaster.example.com. 1 3600 60 600 300", "ttl": 3600},
            {"owner": "example.com.", "type": "NS",
             "data": "dns1.example.com.", "ttl": 300},
            {"owner": "web.example.com.", "type": "RRSIG", "data": "signature", "ttl": 300},
        ]
        live_acl = [{"subnet": "0.0.0.0/0", "action": "allow", "family": 4}]
        for mock in (patch("mniplab.labhandle.LocalLabHandle._op_get_dns_records",
                           lambda _handle, _node, _zone: copy.deepcopy(live_records)),
                     patch("mniplab.labhandle.LocalLabHandle._op_get_resolver_access",
                           lambda _handle, _node: copy.deepcopy(live_acl))):
            mock.start()
            self.addCleanup(mock.stop)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "DNS keep"
            draft["catalog"]["area"] = "DNS"
            primary = host_node("dns", "dns1")
            primary["dns"] = {"zones": [{"kind": "primary", "name": "example.com.",
                "default_ttl": 300, "records": [{"name": "web", "type": "A",
                                             "rdata": "10.0.0.2", "ttl": 300}],
                "dnssec": True}]}
            resolver = host_node("res", "res1")
            resolver["resolver"] = {"root_hints": [], "forwarders": [],
                "max_ttl": 120, "max_negative_ttl": 30,
                "access_control": [{"subnet": "0.0.0.0/0", "action": "allow"}]}
            draft["topology"] = {"nodes": [primary, resolver], "links": []}
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            preview = await client.post(f"{url}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual((await client.get(f"{url}/keep")).json()["features"], [])
            live_records[0]["data"] = "10.0.0.8"
            live_records.append({"owner": "example.com.", "type": "NS",
                                 "data": "ns2.example.com.", "ttl": 300})
            live_acl[0]["action"] = "refuse"
            features = (await client.get(f"{url}/keep")).json()["features"]
            self.assertEqual({item["kind"] for item in features}, {"dns_records", "resolver_acl"})
            dns = next(item for item in features if item["kind"] == "dns_records")
            kept = await client.post(f"{url}/keep", json={"revision": saved.json()["revision"],
                "selections": [], "features": [{"id": dns["id"], "digest": dns["digest"]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            nodes = {node["id"]: node for node in kept.json()["topology"]["nodes"]}
            records = nodes["dns"]["dns"]["zones"][0]["records"]
            self.assertEqual(next(item for item in records if item["type"] == "A")["rdata"],
                             "10.0.0.8")
            self.assertEqual(next(item for item in records if item["type"] == "NS")["rdata"],
                             "ns2.example.com.")
            self.assertTrue(nodes["dns"]["dns"]["zones"][0]["dnssec"])
            self.assertEqual(nodes["res"]["resolver"]["access_control"][0]["action"], "allow")
            self.assertFalse(kept.json()["preview_valid"])
            # Rebuilt Zone now has an explicit apex NS, so its old generated
            # default is no longer present in the new Preview.
            live_records[:] = [item for item in live_records if not (
                item["type"] == "NS" and item["data"] == "dns1.example.com.")]
            preview = await client.post(f"{url}/preview", json={
                "revision": kept.json()["revision"],
                "replace_key": preview.json()["key"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            changes = (await client.get(f"{url}/keep")).json()["features"]
            self.assertEqual([item["kind"] for item in changes], ["resolver_acl"])
            stale = await client.post(f"{url}/keep", json={
                "revision": kept.json()["revision"], "selections": [],
                "features": [{"id": changes[0]["id"], "digest": "0" * 64}]})
            self.assertEqual(stale.status_code, 409)
            applied = await client.post(f"{url}/keep", json={
                "revision": kept.json()["revision"], "selections": [],
                "features": [{"id": changes[0]["id"], "digest": changes[0]["digest"]}]})
            self.assertEqual(applied.status_code, 200, applied.text)
            self.assertEqual(applied.json()["topology"]["nodes"][1]["resolver"][
                "access_control"][0]["action"], "refuse")
            self.assertEqual(applied.json()["topology"]["nodes"][1]["resolver"]["root_hints"], [])
            self.assertFalse(applied.json()["preview_valid"])

        self._as("instructor", exercise)

    def test_absolute_record_names_are_not_offered_as_changes_of_an_untouched_zone(self) -> None:
        self._launch_with("multi", FakeLauncher())
        topology = authored_dns_test_lab()
        # What Knot answers for the Test Lab's untouched Zones: every owner absolute.
        answers = {
            ".": [(".", "NS", "root1."), ("root1.", "A", "10.0.1.2"),
                  ("com.", "NS", "ns.com."), ("ns.com.", "A", "10.0.2.2")],
            "com.": [("com.", "NS", "ns.com."), ("ns.com.", "A", "10.0.2.2"),
                     ("example.com.", "NS", "ns.example.com."),
                     ("ns.example.com.", "A", "10.0.3.2")],
            "example.com.": [("example.com.", "NS", "ns.example.com."),
                             ("ns.example.com.", "A", "10.0.3.2"),
                             ("web.example.com.", "A", "10.0.5.2"),
                             ("web.example.com.", "AAAA", "2001:db8:5::2")],
        }

        def live(_handle: object, _node: str, zone: str) -> list:
            return [{"owner": owner, "type": kind, "data": data, "ttl": 300}
                    for owner, kind, data in answers[zone]]

        getter = patch("mniplab.labhandle.LocalLabHandle._op_get_dns_records", live)
        getter.start()
        self.addCleanup(getter.stop)

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "DNS hierarchy"
            draft["catalog"]["area"] = "DNS"
            draft["topology"] = topology
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            preview = await client.post(f"{url}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual((await client.get(f"{url}/keep")).json()["features"], [])
        self._as("instructor", exercise)
