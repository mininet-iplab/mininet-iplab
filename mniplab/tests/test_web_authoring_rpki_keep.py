"""RPKI objects in Keep: a Canvas draft's Hosted CA takes the ROAs and ASPAs an Instructor ticks.

The Preview's live listing is faked at the Lab handle boundary, so each test
reads only what Krill declares, never what it signs. A Python draft's Keep
offers no RPKI objects, even when the Lab's listing has changed.
"""

import copy
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, SourceRecordingLauncher, host_node

PYTHON_CA = (
    "from mniplab import mnIPLab\n"
    "from mniplab.rpki import HostedCA, RPKICAService\n\n\n"
    "def build_network():\n"
    "    net = mnIPLab(topo=None, controller=None)\n"
    "    ca = net.addHost('ca')\n"
    "    ca.addService(RPKICAService([HostedCA(65002, ['10.2.0.0/24'],"
    " roas=['10.2.0.0/24-24 => 65002'])]))\n"
    "    return net\n"
)


def hosted(asn: int, prefixes: List[str], roas: Optional[List[str]] = None,
           aspas: Optional[List[str]] = None) -> Dict[str, Any]:
    """One Hosted CA row in the definition's shape."""
    return {"asn": asn, "prefixes": list(prefixes), "roas": list(roas or []),
            "aspas": list(aspas or [])}


def ca_node(*rows: Dict[str, Any]) -> Dict[str, Any]:
    """A Host carrying an RPKI CA with these Hosted CAs."""
    node = host_node("ca", "ca", ipv4_gateway="10.0.14.1")
    node["rpki_ca"] = {"hostname": None, "hosted_cas": list(rows)}
    return node


def roa(prefix: str, max_length: int, origin: int) -> Dict[str, Any]:
    """A ROA as the Lab handle lists it."""
    return {"prefix": prefix, "max_length": max_length, "origin": origin}


def aspa(customer: int, *providers: int) -> Dict[str, Any]:
    """An ASPA as the Lab handle lists it."""
    return {"customer": customer, "providers": list(providers)}


def listed(asn: int, roas: Optional[List[Dict[str, Any]]] = None,
           aspas: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One Hosted CA as the Lab handle lists it: declarations only."""
    return {"asn": asn, "handle": f"as{asn}", "prefixes": [],
            "roas": [dict(item) for item in roas or []],
            "aspas": [dict(item) for item in aspas or []]}


@pytest.mark.unit
class RPKIObjectsKeepAPITests(AuthoringAPIFixture):
    """A Canvas draft's Hosted CA takes the ROAs and ASPAs the Instructor ticks, and nothing else."""

    def setUp(self) -> None:
        super().setUp()
        self.live: Dict[str, List[Dict[str, Any]]] = {}
        self.launcher = SourceRecordingLauncher()
        self._launch_with("multi", self.launcher)
        listing = patch(
            "mniplab.labhandle.LocalLabHandle._op_list_rpki_cas",
            lambda _handle, node: copy.deepcopy(self.live.get(node, [])),
        )
        listing.start()
        self.addCleanup(listing.stop)

    async def _draft(self, client: httpx2.AsyncClient, *rows: Dict[str, Any]) -> Dict[str, Any]:
        """Save a Canvas draft whose only Node is the RPKI CA with these Hosted CAs."""
        draft = (await client.post("/api/authoring/drafts", json={})).json()
        draft["address_family"] = "ipv4"
        draft["catalog"]["name"] = "RPKI keep"
        draft["catalog"]["area"] = "DNS"
        draft["topology"] = {"nodes": [ca_node(*rows)], "links": []}
        saved = await client.put(f"/api/authoring/drafts/{draft['id']}",
                                 json={"revision": draft["revision"], "document": draft})
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    async def _preview(self, client: httpx2.AsyncClient, draft: Dict[str, Any]) -> str:
        """Start a Preview of the draft and return its Lab key."""
        started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                    json={"revision": draft["revision"]})
        self.assertEqual(started.status_code, 200, started.text)
        return started.json()["key"]

    def test_review_lists_only_hosted_cas_whose_objects_differ(self) -> None:
        """A spelling that normalises equal is not a change; a differing Hosted CA lists both sides."""

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._draft(
                client,
                hosted(65001, ["10.1.0.0/24"], roas=["10.1.0.0/24 => 65001"]),
                hosted(65002, ["10.2.0.0/16"], roas=["10.2.0.0/24-24 => 65002"],
                       aspas=["AS65002 => AS65001"]),
                hosted(65003, ["10.3.0.0/24"]),
            )
            url = f"/api/authoring/drafts/{draft['id']}"
            await self._preview(client, draft)
            # Krill holds every declared Hosted CA, and the spelling of AS65001's ROA differs.
            self.live["ca"] = [
                listed(65001, roas=[roa("10.1.0.0/24", 24, 65001)]),
                listed(65002, roas=[roa("10.2.0.0/24", 24, 65002)], aspas=[aspa(65002, 65001)]),
                listed(65003),
            ]
            self.assertEqual((await client.get(f"{url}/keep")).json()["features"], [])
            self.live["ca"] = [
                listed(65001, roas=[roa("10.1.0.0/24", 24, 65001)]),
                listed(65002, roas=[roa("10.2.1.0/24", 24, 65002)],
                       aspas=[aspa(65002, 65001, 65005)]),
                listed(65003),
            ]
            review = (await client.get(f"{url}/keep")).json()
            self.assertEqual(review["routers"], [])
            self.assertEqual([(item["kind"], item["name"]) for item in review["features"]],
                             [("rpki_objects", "ca · AS65002")])
            item = review["features"][0]
            self.assertEqual(item["node_name"], "ca")
            self.assertEqual(item["before"], {"roas": ["10.2.0.0/24-24 => 65002"],
                                              "aspas": ["AS65002 => AS65001"]})
            self.assertEqual(item["after"], {"roas": ["10.2.1.0/24-24 => 65002"],
                                             "aspas": ["AS65002 => AS65001, AS65005"]})

        self._as("instructor", exercise)

    def test_review_payload_holds_declarations_and_no_signed_objects(self) -> None:
        """Krill's signed objects and certificates in a listing never reach the review."""

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._draft(client, hosted(65002, ["10.2.0.0/24"]))
            await self._preview(client, draft)
            signed = listed(65002, roas=[roa("10.2.0.0/24", 24, 65002)])
            signed.update({"roa_objects": [{"signed": "BEGIN ROA"}],
                           "certificate": "-----BEGIN CERTIFICATE-----"})
            self.live["ca"] = [signed]
            response = await client.get(f"/api/authoring/drafts/{draft['id']}/keep")
            self.assertEqual(response.status_code, 200, response.text)
            for text in ("roa_objects", "certificate", "BEGIN", "signed"):
                self.assertNotIn(text, response.text)
            [item] = response.json()["features"]
            self.assertEqual(set(item), {"id", "kind", "name", "node_id", "node_name",
                                         "before", "after", "digest"})
            self.assertEqual(item["after"], {"roas": ["10.2.0.0/24-24 => 65002"], "aspas": []})

        self._as("instructor", exercise)

    def test_ticked_objects_replace_the_draft_and_the_next_preview_starts_with_them(self) -> None:
        """Only the ticked Hosted CA moves; its objects reach the next script and its Preview."""

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._draft(
                client,
                hosted(65002, ["10.2.0.0/16"], roas=["10.2.0.0/24-24 => 65002"],
                       aspas=["AS65002 => AS65001"]),
                hosted(65003, ["10.3.0.0/24"]),
            )
            url = f"/api/authoring/drafts/{draft['id']}"
            key = await self._preview(client, draft)
            self.live["ca"] = [
                listed(65002, roas=[roa("10.2.1.0/24", 24, 65002)],
                       aspas=[aspa(65002, 65001, 65005)]),
                listed(65003, roas=[roa("10.3.0.0/24", 24, 65003)]),
            ]
            review = (await client.get(f"{url}/keep")).json()
            self.assertEqual([item["name"] for item in review["features"]],
                             ["ca · AS65002", "ca · AS65003"])
            # Nothing moves until ticked: the stored draft is as it was.
            self.assertEqual((await client.get(url)).json()["revision"], draft["revision"])
            tick = next(item for item in review["features"] if item["name"] == "ca · AS65002")
            kept = await client.post(f"{url}/keep", json={
                "revision": draft["revision"], "selections": [],
                "features": [{"id": tick["id"], "digest": tick["digest"]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            rows = {row["asn"]: row for row in next(
                node for node in kept.json()["topology"]["nodes"] if node["id"] == "ca"
            )["rpki_ca"]["hosted_cas"]}
            self.assertEqual(rows[65002]["roas"], ["10.2.1.0/24-24 => 65002"])
            self.assertEqual(rows[65002]["aspas"], ["AS65002 => AS65001, AS65005"])
            self.assertEqual(rows[65003]["roas"], [])
            self.assertFalse(kept.json()["preview_valid"])

            await client.post(f"/api/labs/{key}/stop")
            fresh_key = await self._preview(client, kept.json())
            script = self.launcher.sources[-1]
            self.assertIn(repr(["10.2.1.0/24-24 => 65002"]), script)
            self.assertIn(repr(["AS65002 => AS65001, AS65005"]), script)
            self.assertNotIn("10.2.0.0/24-24 => 65002", script)
            # The new Preview's baseline is the kept declaration, so only AS65003 still differs.
            fresh = (await client.get(f"{url}/keep")).json()
            self.assertEqual([item["name"] for item in fresh["features"]], ["ca · AS65003"])
            await client.post(f"/api/labs/{fresh_key}/stop")

        self._as("instructor", exercise)

    def test_an_unticked_or_empty_keep_leaves_the_draft_as_it_was(self) -> None:
        """Empty selections are refused, and a Keep of another Hosted CA moves nothing here."""

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._draft(
                client,
                hosted(65002, ["10.2.0.0/16"], roas=["10.2.0.0/24-24 => 65002"]),
                hosted(65003, ["10.3.0.0/24"]),
            )
            url = f"/api/authoring/drafts/{draft['id']}"
            await self._preview(client, draft)
            self.live["ca"] = [
                listed(65002, roas=[roa("10.2.1.0/24", 24, 65002)]),
                listed(65003, roas=[roa("10.3.0.0/24", 24, 65003)]),
            ]
            review = (await client.get(f"{url}/keep")).json()
            refused = await client.post(f"{url}/keep", json={
                "revision": draft["revision"], "selections": [], "features": []})
            self.assertEqual(refused.status_code, 422, refused.text)
            other = next(item for item in review["features"] if item["name"] == "ca · AS65003")
            kept = await client.post(f"{url}/keep", json={
                "revision": draft["revision"], "selections": [],
                "features": [{"id": other["id"], "digest": other["digest"]}]})
            self.assertEqual(kept.status_code, 200, kept.text)
            rows = {row["asn"]: row for row in next(
                node for node in kept.json()["topology"]["nodes"] if node["id"] == "ca"
            )["rpki_ca"]["hosted_cas"]}
            self.assertEqual(rows[65002]["roas"], ["10.2.0.0/24-24 => 65002"])
            self.assertEqual(rows[65003]["roas"], ["10.3.0.0/24-24 => 65003"])

        self._as("instructor", exercise)

    def test_a_python_draft_offers_no_rpki_objects_even_when_the_listing_changed(self) -> None:
        """Keep of a Python draft's Preview has no RPKI row, and cannot be told to keep one."""

        async def exercise(client: httpx2.AsyncClient) -> None:
            created = await client.post("/api/authoring/drafts/upload", files={
                "file": ("rpki_lesson.py", PYTHON_CA.encode("utf-8"), "text/x-python")})
            self.assertEqual(created.status_code, 201, created.text)
            draft = created.json()
            url = f"/api/authoring/drafts/{draft['id']}"
            self.live["ca"] = [listed(65002, roas=[roa("10.2.0.0/24", 24, 65002)])]
            key = await self._preview(client, draft)
            self.live["ca"] = [listed(65002, roas=[roa("10.2.0.0/24", 24, 65002),
                                                   roa("10.2.0.0/25", 25, 65002)])]
            review = await client.get(f"{url}/keep")
            self.assertEqual(review.status_code, 200, review.text)
            self.assertNotIn("rpki_objects", [item["kind"] for item in review.json()["features"]])
            forced = await client.post(f"{url}/keep", json={
                "revision": draft["revision"], "selections": [],
                "features": [{"id": "rpki:ca:65002", "digest": "0" * 64}]})
            self.assertEqual(forced.status_code, 422, forced.text)
            self.assertIn("change in your script", forced.json()["detail"])
            await client.post(f"/api/labs/{key}/stop")

        self._as("instructor", exercise)
