"""Lab-wide settings through the authenticated authoring API."""

import copy

import pytest
import httpx2

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, dhcp_pool, host_node


@pytest.mark.unit
class LabSettingsAPITests(AuthoringAPIFixture):
    """An Instructor answers the address and persistence questions once."""

    def test_new_canvas_draft_defaults_to_ipv4_and_discarded_router_config(
        self,
    ) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            self.assertEqual(draft["address_family"], "ipv4")
            self.assertFalse(draft["persistent_config"])

        self._as("instructor", exercise)

    def test_service_addresses_and_dhcp_clients_follow_the_family(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            host = host_node("h", "h1")
            host["nameservers"] = [{"address": "2001:db8::53"}]
            host["dhcp"] = {
                "pools": [
                    dhcp_pool("2001:db8:1::/64", "2001:db8:1::10", "2001:db8:1::20")
                ]
            }
            host["relay"] = {
                "subnets": ["2001:db8:2::/64"],
                "servers": ["2001:db8::54"],
            }
            host["resolver"] = {
                "root_hints": [{"address": "2001:db8::55"}],
                "forwarders": [{"address": "2001:db8::56"}],
                "max_ttl": 86400,
                "max_negative_ttl": 3600,
                "access_control": [{"subnet": "::/0", "action": "allow"}],
            }
            draft["topology"] = {
                "nodes": [host, host_node("b", "h2")],
                "links": [
                    {
                        "id": "client",
                        "ends": [
                            {
                                "node_id": "h",
                                "interface_id": "if-000000000001",
                                "port": 0,
                                "ipv4": "",
                                "ipv6": "",
                                "dhcp6": True,
                            },
                            {
                                "node_id": "b",
                                "interface_id": "if-000000000002",
                                "port": 0,
                                "ipv4": "",
                                "ipv6": "",
                            },
                        ],
                    }
                ],
            }
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            draft["address_family"] = "ipv4"
            refused = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(refused.status_code, 422, refused.text)
            for field in (
                "nameservers",
                "dhcp",
                "relay",
                "root_hints",
                "forwarders",
                "dhcp6",
            ):
                self.assertIn(field, refused.json()["detail"])

        self._as("instructor", exercise)

    def test_policy_subnets_dns_records_and_frr_text_never_block_family_switching(
        self,
    ) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            host = host_node("h", "h1")
            host["resolver"] = {
                "root_hints": [],
                "forwarders": [],
                "max_ttl": 86400,
                "max_negative_ttl": 3600,
                "access_control": [
                    {"subnet": "::/0", "action": "allow"},
                    {"subnet": "0.0.0.0/0", "action": "allow"},
                ],
            }
            host["dns"] = {
                "zones": [
                    {
                        "kind": "primary",
                        "name": "example.com.",
                        "default_ttl": 300,
                        "records": [
                            {
                                "name": "v6",
                                "type": "AAAA",
                                "rdata": "2001:db8::1",
                                "ttl": 300,
                            },
                            {
                                "name": "v4",
                                "type": "A",
                                "rdata": "192.0.2.1",
                                "ttl": 300,
                            },
                        ],
                    }
                ]
            }
            router = {
                "id": "r",
                "name": "r1",
                "type": "router",
                "protocols": [],
                "frr_config": "interface lo\n ip address 192.0.2.1/32\n ipv6 address 2001:db8::1/128\n",
                "fast_timers": False,
                "startup": [],
            }
            draft["topology"]["nodes"] = [host, router]
            original = copy.deepcopy(draft["topology"])
            url = f"/api/authoring/drafts/{draft['id']}"
            for family in ("ipv4", "ipv6", "dual"):
                draft["address_family"] = family
                saved = await client.put(
                    url, json={"revision": draft["revision"], "document": draft}
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                draft = saved.json()
                self.assertEqual(draft["topology"], original)

        self._as("instructor", exercise)

    def test_persistence_gives_every_router_a_seed_and_can_be_switched_off(
        self,
    ) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["persistent_config"] = True
            draft["topology"]["nodes"] = [
                {
                    "id": name,
                    "name": name,
                    "type": "router",
                    "protocols": [],
                    "frr_config": "",
                    "fast_timers": False,
                    "startup": [],
                }
                for name in ("r1", "r2")
            ]
            url = f"/api/authoring/drafts/{draft['id']}"
            for persistent in (True, False):
                draft["persistent_config"] = persistent
                saved = await client.put(
                    url, json={"revision": draft["revision"], "document": draft}
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                draft = saved.json()
                generated = (await client.get(url + "/python")).json()["source"]
                self.assertEqual(generated.count("frr_dir="), 2 if persistent else 0)
                self.assertNotIn("config_mode", str(draft["topology"]))

        self._as("instructor", exercise)

    def test_family_change_names_every_conflicting_field_and_preserves_the_draft(
        self,
    ) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["address_family"] = "dual"
            draft["topology"] = {
                "nodes": [
                    host_node("a", "h1", ipv6_gateway="2001:db8::1"),
                    host_node("b", "h2"),
                ],
                "links": [
                    {
                        "id": "link-a",
                        "ends": [
                            {
                                "node_id": "a",
                                "interface_id": "if-000000000001",
                                "port": 0,
                                "ipv4": "",
                                "ipv6": "2001:db8::2/64",
                            },
                            {
                                "node_id": "b",
                                "interface_id": "if-000000000002",
                                "port": 0,
                                "ipv4": "",
                                "ipv6": "",
                            },
                        ],
                    }
                ],
            }
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            draft["address_family"] = "ipv4"
            refused = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertIn("h1", refused.json()["detail"])
            self.assertIn("ipv6_gateway", refused.json()["detail"])
            self.assertIn("h1-eth0", refused.json()["detail"])
            self.assertIn("ipv6", refused.json()["detail"])
            reopened = (await client.get(url)).json()
            self.assertEqual(reopened["address_family"], "dual")
            self.assertEqual(reopened["topology"], draft["topology"])

        self._as("instructor", exercise)

    def test_speaker_neighbors_and_hand_written_routes_block_a_family_switch(
        self,
    ) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            copied = await client.post(
                "/api/authoring/drafts", json={"example_id": "bgp-ispixp-lab"}
            )
            self.assertEqual(copied.status_code, 201, copied.text)
            draft = copied.json()
            speaker = next(
                node for node in draft["topology"]["nodes"] if node["type"] == "speaker"
            )
            # Keep IPv6-only authoring coverage in a draft built by the test.
            draft["address_family"] = "ipv6"
            draft["persistent_config"] = False
            draft["topology"] = {"nodes": [speaker], "links": []}
            draft["layout"] = {"version": 1, "nodes": {}}
            for index, neighbor in enumerate(speaker["neighbors"], 1):
                neighbor["peer"] = f"2001:db8:{index}::1"
                neighbor["local_address"] = f"2001:db8:{index}::2"
            speaker["routes"] = [
                {
                    "prefix": "2001:db8:ffff::/64",
                    "next_hop": speaker["neighbors"][0]["local_address"],
                }
            ]
            speaker["neighbors"][0]["routes"] = copy.deepcopy(speaker["routes"])
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            draft["address_family"] = "ipv4"
            refused = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(refused.status_code, 422, refused.text)
            for field in (
                "neighbors[1].peer",
                "neighbors[1].local_address",
                "routes[1].prefix",
                "routes[1].next_hop",
                "neighbors[1].routes[1].prefix",
                "neighbors[1].routes[1].next_hop",
            ):
                self.assertIn(field, refused.json()["detail"])

        self._as("instructor", exercise)

    def test_settings_reject_unknown_families_and_non_boolean_persistence(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            url = f"/api/authoring/drafts/{draft['id']}"
            for field, value in (
                ("address_family", "automatic"),
                ("persistent_config", "yes"),
                ("persistent_config", 1),
            ):
                changed = {**draft, field: value}
                refused = await client.put(
                    url, json={"revision": draft["revision"], "document": changed}
                )
                self.assertEqual(refused.status_code, 422, refused.text)

        self._as("instructor", exercise)

    def test_dns_transfer_addresses_follow_the_family_and_block_switching(self) -> None:
        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            host = host_node("h", "h1")
            host["dns"] = {
                "zones": [
                    {
                        "kind": "primary",
                        "name": "example.com.",
                        "default_ttl": 300,
                        "records": [],
                        "allow_transfer": [{"address": "2001:db8::53"}],
                    }
                ]
            }
            draft["topology"]["nodes"] = [host]
            url = f"/api/authoring/drafts/{draft['id']}"
            refused = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertIn("allow_transfer[1].address", refused.json()["detail"])
            draft["address_family"] = "ipv6"
            saved = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            draft["address_family"] = "ipv4"
            refused = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertEqual(
                (await client.get(url)).json()["topology"], draft["topology"]
            )

        self._as("instructor", exercise)
