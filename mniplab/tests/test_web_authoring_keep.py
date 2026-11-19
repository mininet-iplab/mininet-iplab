"""Keep whole FRR sections through the authenticated authoring HTTP API."""

import httpx2
import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture
from mniplab.tests.test_web_multi_lab import FakeLauncher


@pytest.mark.unit
class KeepAPITests(AuthoringAPIFixture):
    """An Instructor chooses complete configuration sections from a Preview."""

    def test_keep_removes_a_section_without_taking_an_unselected_new_section(
        self,
    ) -> None:
        """Removed blocks stay indivisible and a later Keep places additions in order."""
        self._launch_with("multi", FakeLauncher())
        interface = "interface lo\n description Remove me\nexit\n!\n"
        ospf = "router ospf\n network 10.0.0.0/24 area 0\nexit\n!\n"
        route = "ip route 192.0.2.0/24 10.0.0.2\n"

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(
                client, frr_config=interface + ospf, persistent_config=False
            )
            url = f"/api/authoring/drafts/{draft['id']}"
            self.running_configs["r1"] = interface + ospf
            self.assertEqual(
                (
                    await client.post(
                        f"{url}/preview", json={"revision": draft["revision"]}
                    )
                ).status_code,
                200,
            )
            self.assertFalse(
                (await client.get(f"{url}/keep")).json()["has_kept_changes"]
            )
            self.running_configs["r1"] = route + ospf
            for name, expected in (
                ("interface lo", ospf),
                ("ip route 192.0.2.0/24 10.0.0.2", route + ospf),
            ):
                router = (await client.get(f"{url}/keep")).json()["routers"][0]
                section = next(
                    section for section in router["sections"] if section["name"] == name
                )
                response = await client.post(
                    f"{url}/keep",
                    json={
                        "revision": draft["revision"],
                        "selections": [
                            {
                                "node_id": "r",
                                "digest": router["digest"],
                                "sections": [section["id"]],
                            }
                        ],
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                draft = response.json()
                self.assertEqual(draft["topology"]["nodes"][0]["frr_config"], expected)
                self.assertTrue(
                    (await client.get(f"{url}/keep")).json()["has_kept_changes"]
                )
            review = (await client.get(f"{url}/keep")).json()
            self.assertEqual(review["routers"][0]["sections"], [])
            self.assertEqual(
                (await client.post(f"/api/labs/{review['key']}/stop")).status_code, 200
            )
            self.assertEqual(
                (
                    await client.post(
                        f"{url}/preview", json={"revision": draft["revision"]}
                    )
                ).status_code,
                200,
            )
            self.assertFalse(
                (await client.get(f"{url}/keep")).json()["has_kept_changes"]
            )

        self._as("instructor", exercise)

    def test_keep_offers_no_nat_item_for_router_nat_in_a_running_preview(self) -> None:
        """Router NAT is the Lab's declaration, and Keep never reads a Router's NAT rules."""
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client, persistent_config=False)
            draft["topology"]["links"][0]["ends"][0]["outside"] = True
            draft["topology"]["nodes"][0]["nat"] = [
                {"kind": "static", "inside": "10.0.0.2", "outside": "203.0.113.5"}
            ]
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(
                url, json={"revision": draft["revision"], "document": draft}
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            draft = saved.json()
            self.assertEqual(
                (
                    await client.post(f"{url}/preview", json={"revision": draft["revision"]})
                ).status_code,
                200,
            )
            review = (await client.get(f"{url}/keep")).json()
            self.assertEqual(review["features"], [])
            self.assertEqual(review["routers"][0]["sections"], [])
            self.assertFalse(review["has_kept_changes"])

        self._as("instructor", exercise)

    def test_keep_without_a_tick_refuses_to_change_the_draft(self) -> None:
        """An empty selection never authors the Preview's baseline by accident."""
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client, persistent_config=False)
            url = f"/api/authoring/drafts/{draft['id']}"
            self.assertEqual(
                (
                    await client.post(
                        f"{url}/preview",
                        json={
                            "revision": draft["revision"],
                        },
                    )
                ).status_code,
                200,
            )
            self.running_configs["r1"] = (
                "router ospf\n ospf router-id 1.1.1.1\nexit\n!\n"
            )
            review = (await client.get(f"{url}/keep")).json()["routers"][0]
            for selections in (
                [],
                [{"node_id": "r", "digest": review["digest"], "sections": []}],
            ):
                response = await client.post(
                    f"{url}/keep",
                    json={
                        "revision": draft["revision"],
                        "selections": selections,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)
                stored = (await client.get(url)).json()
                self.assertEqual(stored["revision"], draft["revision"])
                self.assertEqual(stored["topology"], draft["topology"])
                self.assertTrue(stored["preview_valid"])

        self._as("instructor", exercise)

    def test_keep_refuses_a_review_that_changed_or_an_invalid_section(self) -> None:
        """Neither a stale review nor an unknown section can author a Router."""
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(client, persistent_config=False)
            url = f"/api/authoring/drafts/{draft['id']}"
            self.running_configs["r1"] = "router ospf\nexit\n!\n"
            self.assertEqual(
                (
                    await client.post(
                        f"{url}/preview",
                        json={
                            "revision": draft["revision"],
                        },
                    )
                ).status_code,
                200,
            )
            self.running_configs["r1"] = (
                "router ospf\n ospf router-id 1.1.1.1\nexit\n!\n"
            )
            router = (await client.get(f"{url}/keep")).json()["routers"][0]
            for invalid in (99, True, "0"):
                with self.subTest(section=invalid):
                    response = await client.post(
                        f"{url}/keep",
                        json={
                            "revision": draft["revision"],
                            "selections": [
                                {
                                    "node_id": "r",
                                    "digest": router["digest"],
                                    "sections": [invalid],
                                }
                            ],
                        },
                    )
                    self.assertEqual(response.status_code, 422, response.text)
            self.running_configs["r1"] = (
                "router ospf\n ospf router-id 2.2.2.2\nexit\n!\n"
            )
            stale = await client.post(
                f"{url}/keep",
                json={
                    "revision": draft["revision"],
                    "selections": [
                        {
                            "node_id": "r",
                            "digest": router["digest"],
                            "sections": [0],
                        }
                    ],
                },
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertIn("changed since you reviewed", stale.json()["detail"])
            self.assertEqual(
                (await client.get(url)).json()["revision"], draft["revision"]
            )

        self._as("instructor", exercise)

    def test_ambiguous_configurations_are_offered_and_kept_as_a_whole_router(
        self,
    ) -> None:
        """Duplicate blocks, banners, orphan subcommands and reordering stay intact."""
        self._launch_with("multi", FakeLauncher())
        configurations = [
            (
                "interface r1-eth0\n description First\nexit\n!\n"
                "interface r1-eth0\n description Second\nexit\n!\n",
                "interface r1-eth0\n description First\nexit\n!\n"
                "interface r1-eth0\n description Prepared\nexit\n!\n",
            ),
            ("banner motd #\nWelcome\n#\n", "banner motd #\nPrepared Lab\n#\n"),
            (" orphan command\n", " prepared command\n"),
            (
                "ip route 192.0.2.0/24 10.0.0.2\nip route 198.51.100.0/24 10.0.0.2\n",
                "ip route 198.51.100.0/24 10.0.0.2\nip route 192.0.2.0/24 10.0.0.2\n",
            ),
        ]

        async def exercise(client: httpx2.AsyncClient) -> None:
            for before, after in configurations:
                with self.subTest(before=before):
                    draft = await self._router_draft(
                        client, frr_config=before, persistent_config=False
                    )
                    url = f"/api/authoring/drafts/{draft['id']}"
                    self.running_configs["r1"] = before
                    started = await client.post(
                        f"{url}/preview", json={"revision": draft["revision"]}
                    )
                    self.assertEqual(started.status_code, 200, started.text)
                    self.running_configs["r1"] = after
                    router = (await client.get(f"{url}/keep")).json()["routers"][0]
                    self.assertEqual(
                        [section["name"] for section in router["sections"]],
                        ["Whole Router"],
                    )
                    self.assertTrue(router["sections"][0]["whole"])
                    kept = await client.post(
                        f"{url}/keep",
                        json={
                            "revision": draft["revision"],
                            "selections": [
                                {
                                    "node_id": "r",
                                    "digest": router["digest"],
                                    "sections": [0],
                                }
                            ],
                        },
                    )
                    self.assertEqual(kept.status_code, 200, kept.text)
                    self.assertEqual(
                        kept.json()["topology"]["nodes"][0]["frr_config"], after
                    )
                    key = started.json()["key"]
                    self.assertEqual(
                        (await client.post(f"/api/labs/{key}/stop")).status_code, 200
                    )

        self._as("instructor", exercise)

    def test_keep_takes_a_complete_interface_and_leaves_adjacent_bgp_changes(
        self,
    ) -> None:
        """Nearby edits never make a Router's section selection split a block."""
        self._launch_with("multi", FakeLauncher())
        before = (
            "interface r1-eth0\n description Before\n ip ospf cost 10\nexit\n!\n"
            "router bgp 65000\n neighbor 10.0.0.2 remote-as 65001\n"
            " address-family ipv4 unicast\n  neighbor 10.0.0.2 activate\n"
            " exit-address-family\nexit\n!\n"
        )
        after = (
            before.replace("description Before", "description Prepared")
            .replace("ip ospf cost 10", "ip ospf cost 30")
            .replace("remote-as 65001", "remote-as 65002")
        )

        async def exercise(client: httpx2.AsyncClient) -> None:
            draft = await self._router_draft(
                client, frr_config=before, persistent_config=False
            )
            url = f"/api/authoring/drafts/{draft['id']}"
            # FRR's own header is never a section and never kept.
            header = "frr version 10.1\nfrr defaults traditional\nhostname r1\n!\n"
            self.running_configs["r1"] = header + before
            started = await client.post(
                f"{url}/preview", json={"revision": draft["revision"]}
            )
            self.assertEqual(started.status_code, 200, started.text)
            self.running_configs["r1"] = header + after
            response = await client.get(f"{url}/keep")
            self.assertEqual(response.status_code, 200, response.text)
            router = response.json()["routers"][0]
            self.assertEqual(
                [section["name"] for section in router["sections"]],
                ["interface r1-eth0", "router bgp 65000"],
            )
            self.assertEqual((router["added"], router["removed"]), (3, 3))
            section = router["sections"][0]
            self.assertIn("- description Before", section["lines"])
            self.assertIn("+ description Prepared", section["lines"])
            kept = await client.post(
                f"{url}/keep",
                json={
                    "revision": draft["revision"],
                    "selections": [
                        {
                            "node_id": "r",
                            "digest": router["digest"],
                            "sections": [section["id"]],
                        }
                    ],
                },
            )
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertEqual(
                kept.json()["topology"]["nodes"][0]["frr_config"],
                before.replace("description Before", "description Prepared").replace(
                    "ip ospf cost 10", "ip ospf cost 30"
                ),
            )
            self.assertFalse(kept.json()["preview_valid"])
            remaining = (await client.get(f"{url}/keep")).json()["routers"][0][
                "sections"
            ]
            self.assertEqual(
                [section["name"] for section in remaining], ["router bgp 65000"]
            )

        self._as("instructor", exercise)
