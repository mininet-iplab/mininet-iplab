"""Speaker generators through the authenticated authoring HTTP API."""

import copy
from pathlib import Path

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture
from mniplab.tests.authoring_speakers import announcements


def generator() -> dict:
    """A small repeatable generator for an authored Speaker."""
    return {"count": 3, "sub_prefixes": 2, "prefix_seed": 42, "as_path_seed": 88}


@pytest.mark.unit
class SpeakerGeneratorAPITests(AuthoringAPIFixture):
    """Generators and crafted Routes coexist without keeping runtime announcements."""

    def test_neighbor_generator_inherits_hand_written_global_routes(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (
                await client.post(
                    "/api/authoring/drafts", json={"example_id": "bgp-isp-lab"}
                )
            ).json()
            speaker = next(
                node for node in draft["topology"]["nodes"] if node["type"] == "speaker"
            )
            speaker.pop("route_generator", None)
            speaker["routes"] = [
                {
                    "prefix": "203.0.113.0/24",
                    "next_hop": speaker["neighbors"][0]["local_address"],
                }
            ]
            speaker["neighbors"][0]["route_generator"] = generator()
            speaker["neighbors"][0]["routes"] = []
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            source = (await client.get(url + "/python")).json()["source"]
            observed = announcements(source, Path(self.storage.name) / "lab.py")
            key = f"{speaker['name']}->{speaker['neighbors'][0]['peer']}"
            self.assertIn("203.0.113.0/24", {row["prefix"] for row in observed[key]})
            self.assertGreater(len(observed[key]), 1)

        self._as("instructor", exercise)

    def test_global_and_neighbor_generators_render_the_shipped_peer_helpers(
        self,
    ) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            for family in ("ipv4", "ipv6"):
                draft = (
                    await client.post(
                        "/api/authoring/drafts", json={"example_id": "bgp-isp-lab"}
                    )
                ).json()
                speaker = next(
                    node
                    for node in draft["topology"]["nodes"]
                    if node["type"] == "speaker"
                )
                if family == "ipv6":
                    # Build an IPv6-only draft; no shipped sibling is needed.
                    draft["address_family"] = "ipv6"
                    draft["persistent_config"] = False
                    draft["topology"] = {"nodes": [speaker], "links": []}
                    draft["layout"] = {"version": 1, "nodes": {}}
                    speaker["neighbors"][0]["peer"] = "2001:db8:1::1"
                    speaker["neighbors"][0]["local_address"] = "2001:db8:1::2"
                speaker["route_generator"] = generator()
                neighbor = speaker["neighbors"][0]
                neighbor["route_generator"] = generator()
                neighbor["routes"] = [
                    {
                        "prefix": (
                            "2001:db8:ffff::/64"
                            if family == "ipv6"
                            else "198.51.100.0/24"
                        ),
                        "next_hop": neighbor["local_address"],
                    }
                ]
                url = f"/api/authoring/drafts/{draft['id']}"
                saved = await client.put(
                    url, json={"revision": draft["revision"], "document": draft}
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                source = (await client.get(url + "/python")).json()["source"]
                self.assertIn(
                    (
                        "make_v6_peer_routes"
                        if family == "ipv6"
                        else "make_v4_peer_routes"
                    ),
                    source,
                )
                self.assertIn("num_prefixes=3", source)
                self.assertIn("num_sub_prefixes=2", source)
                self.assertIn("prefix_seed=42", source)
                self.assertIn("as_path_seed=88", source)
                self.assertIn(f"next_hop={neighbor['local_address']!r}", source)
                self.assertIn("BGPRoute(", source)
                self.assertEqual(
                    (await client.get(url)).json()["topology"], draft["topology"]
                )

        self._as("instructor", exercise)

    def test_generator_refuses_bad_numbers_and_unknown_fields(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (
                await client.post(
                    "/api/authoring/drafts", json={"example_id": "bgp-isp-lab"}
                )
            ).json()
            url = f"/api/authoring/drafts/{draft['id']}"
            for field, value in (
                ("count", 0),
                ("count", True),
                ("count", 2001),
                ("sub_prefixes", -1),
                ("prefix_seed", None),
                ("as_path_seed", 1.5),
                ("family", "ipv6"),
            ):
                changed = copy.deepcopy(draft)
                speaker = next(
                    node
                    for node in changed["topology"]["nodes"]
                    if node["type"] == "speaker"
                )
                speaker["route_generator"] = {**generator(), field: value}
                refused = await client.put(
                    url, json={"revision": changed["revision"], "document": changed}
                )
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertIn("generator", refused.json()["detail"].lower())

        self._as("instructor", exercise)
