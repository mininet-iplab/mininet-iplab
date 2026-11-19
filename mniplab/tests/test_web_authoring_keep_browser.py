"""The Instructor reviews and keeps configuration using the keyboard."""

import copy
from typing import Any
from unittest.mock import patch

import pytest

from mniplab.tests.browser import expect, screenshots
from mniplab.tests.authoring_editors import AuthoringPageCase, upload_script
from mniplab.tests.authoring_fixtures import PackageRecordingLauncher, dhcp_pool
from mniplab.tests.test_web_multi_lab import FakeLabChild
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_authoring


@pytest.mark.browser
class KeepBrowserTests(AuthoringPageCase):
    """Keep presents whole sections in a folded review with one row per Router."""

    def test_empty_review_remembers_kept_changes_and_offers_close(self) -> None:
        """Empty reviews distinguish startup from Keep, including after reload."""
        running = {"r1": "interface lo\n description Before\nexit\n"}
        read = patch(
            "mniplab.labhandle.LocalLabHandle._op_get_running_config",
            lambda _handle, node: running[node],
        )
        read.start()
        self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 200, "router", {})], [])
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            trigger = page.get_by_role("button", name="Keep changes…", exact=True)
            trigger.click()
            dialog = page.get_by_role("dialog", name="Keep changes", exact=True)
            expect(dialog).to_contain_text("No supported changes to Keep yet.")
            expect(
                dialog.get_by_text("Nothing is kept unless you tick it.")
            ).to_be_hidden()
            close = dialog.get_by_role("button", name="Close", exact=True)
            expect(close).to_be_focused()
            screenshots(page, "rework-06", "keep-empty", "#keep-routers")
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            expect(trigger).to_be_focused()

            running["r1"] = running["r1"].replace("Before", "Prepared")
            trigger.click()
            expect(
                dialog.get_by_text("Nothing is kept unless you tick it.")
            ).to_be_visible()
            expect(
                dialog.get_by_role("button", name="Cancel", exact=True)
            ).to_be_visible()
            dialog.get_by_role("checkbox", name="Keep Router r1", exact=True).check()
            dialog.get_by_role("button", name="Keep selected", exact=True).click()
            expect(dialog).to_be_hidden()
            page.reload()
            expect(page.locator("#status-preview-state")).to_have_text("out of date")
            page.get_by_role("button", name="Keep changes…", exact=True).click()
            expect(dialog).to_contain_text("Your changes are kept in the draft.")
            expect(dialog).to_contain_text("No new supported changes to Keep.")
            expect(dialog).not_to_contain_text("since this Preview started")
            expect(
                dialog.get_by_text("Nothing is kept unless you tick it.")
            ).to_be_hidden()
            expect(
                dialog.get_by_role("button", name="Close", exact=True)
            ).to_be_focused()
            screenshots(page, "rework-06", "keep-already-kept", "#keep-routers")
            page.keyboard.press("Escape")

            running["r1"] = running["r1"].replace("Prepared", "Refined")
            page.get_by_role("button", name="Keep changes…", exact=True).click()
            expect(
                dialog.get_by_text("Nothing is kept unless you tick it.")
            ).to_be_visible()
            expect(
                dialog.get_by_role("checkbox", name="Keep Router r1", exact=True)
            ).not_to_be_checked()
            expect(
                dialog.get_by_role("button", name="Cancel", exact=True)
            ).to_be_visible()
            dialog.get_by_role("checkbox", name="Keep Router r1", exact=True).check()
            dialog.get_by_role("button", name="Keep selected", exact=True).click()
            expect(dialog).to_be_hidden()
            self.assertIn(
                "description Refined",
                self._saved_draft(page)["topology"]["nodes"][0]["frr_config"],
            )

    def test_python_keep_fills_a_seed_and_explains_an_unkeepable_router(self) -> None:
        launcher = patch.object(
            registry.lab_service, "launcher", PackageRecordingLauncher()
        )
        launcher.start()
        self.addCleanup(launcher.stop)
        running = {"r1": "end\n", "r2": "end\n"}
        read = patch(
            "mniplab.labhandle.LocalLabHandle._op_get_running_config",
            lambda _handle, node: running[node],
        )
        read.start()
        self.addCleanup(read.stop)
        source = (
            "from mniplab import mnIPLab\n"
            "def build_network():\n"
            "    net = mnIPLab(topo=None, controller=None)\n"
            "    net.addRouter('r1', frr_dir='./frr-config/lesson/%(name)s')\n"
            "    net.addRouter('r2')\n"
            "    return net\n"
        )
        with self.signed_in_page() as page:
            page.goto(self.base_url + "/authoring")
            upload_script(page, "lesson.py", source)
            page.reload()
            page.wait_for_selector("#python-editor:not([hidden])")
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            running["r1"] = "ip route 192.0.2.0/24 Null0\n"
            running["r2"] = "ip route 198.51.100.0/24 Null0\n"
            keep = page.get_by_role("button", name="Keep changes…", exact=True)
            expect(keep).to_be_visible()
            keep.click()
            dialog = page.get_by_role("dialog", name="Keep changes", exact=True)
            expect(
                dialog.get_by_role("checkbox", name="Keep Router r1", exact=True)
            ).not_to_be_checked()
            expect(
                dialog.get_by_role("checkbox", name="Keep Router r2", exact=True)
            ).to_be_disabled()
            expect(dialog).to_contain_text(
                "No Seed folder; name one with frr_dir in your script."
            )
            screenshots(page, "rework-06", "python-keep", "#keep-routers")
            dialog.get_by_role("checkbox", name="Select all", exact=True).check()
            dialog.get_by_role("button", name="Keep selected", exact=True).click()
            expect(dialog).to_be_hidden()
            saved = self._saved_draft(page)
            self.assertEqual(
                saved["files"], {"frr-config/lesson/r1/frr.conf": running["r1"]}
            )
            self.assertEqual(saved["python"], source)

    def test_selection_and_folded_status_offer_the_same_node_changes_and_frr_addresses(
        self,
    ) -> None:
        running = {"r1": "end\n", "r2": "end\n"}
        pool = dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")
        reads: list[Any] = [
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_running_config",
                lambda _handle, node: running[node],
            ),
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_node_pools",
                lambda _handle, _node: {"pools": [copy.deepcopy(pool)]},
            ),
        ]
        for read in reads:
            read.start()
            self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(
                page,
                [
                    ("r1", 300, 200, "router", {"dhcp": {"pools": [pool]}}),
                    ("r2", 700, 200, "router", {}),
                ],
                [("r1", "r2")],
            )
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            running["r1"] = (
                "interface r1-eth0\n ip address 192.0.2.9/24\n ipv6 address 2001:db8::9/64\nexit\n"
            )
            running["r2"] = "ip route 198.51.100.0/24 Null0\n"
            pool["lease_time"] = 300
            self._node(page, "r1").click()
            selection = page.get_by_role("region", name="Selection", exact=True)
            expect(selection).to_contain_text("2 changes in the Preview")
            expect(selection).to_contain_text("192.0.2.9/24 (from r1's FRR config)")
            expect(selection).to_contain_text("2001:db8::9/64 (from r1's FRR config)")
            screenshots(page, "rework-06", "keep-selection", "#selection")
            selection.get_by_role("button", name="Addresses…", exact=True).click()
            addresses = page.get_by_role("dialog", name="Addresses of r1", exact=True)
            expect(addresses).to_contain_text("192.0.2.9/24 (from r1's FRR config)")
            screenshots(page, "rework-06", "frr-addresses", "#addresses-list")
            page.keyboard.press("Escape")
            selection.get_by_role("button", name="Keep…", exact=True).click()
            dialog = page.get_by_role("dialog", name="Keep changes", exact=True)
            expect(
                dialog.get_by_role("checkbox", name="Keep Router r1", exact=True)
            ).to_be_checked()
            expect(
                dialog.get_by_role("checkbox", name="Keep DHCP on r1", exact=True)
            ).to_be_checked()
            expect(
                dialog.get_by_role("checkbox", name="Keep Router r2", exact=True)
            ).to_have_count(0)
            dialog.get_by_role("button", name="Keep selected", exact=True).click()
            expect(dialog).to_be_hidden()
            saved = self._saved_draft(page)
            self.assertIn(
                "ip address 192.0.2.9/24", saved["topology"]["nodes"][0]["frr_config"]
            )
            self.assertEqual(saved["topology"]["links"][0]["ends"][0]["ipv4"], "")
            page.get_by_role("button", name="Script", exact=True).click()
            expect(
                page.get_by_role("button", name="Details", exact=True)
            ).to_have_attribute("aria-expanded", "false")
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            status = page.get_by_role("region", name="Status", exact=True)
            expect(status).to_contain_text("1 change not yet kept")
            expect(
                status.get_by_role("button", name="Keep…", exact=True)
            ).to_be_visible()
            screenshots(page, "rework-06", "keep-status", "#status-line")

    def test_stop_preview_uses_the_pages_warning_with_the_same_selectable_rows(
        self,
    ) -> None:
        running = {"r1": "end\n"}
        pool = dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")
        reads: list[Any] = [
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_running_config",
                lambda _handle, node: running[node],
            ),
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_node_pools",
                lambda _handle, _node: {"pools": [copy.deepcopy(pool)]},
            ),
        ]
        for read in reads:
            read.start()
            self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 200, "router", {"dhcp": {"pools": [pool]}})])
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            running["r1"] = "ip route 192.0.2.0/24 Null0\n"
            pool["lease_time"] = 300
            page.get_by_role("button", name="Stop Preview", exact=True).click()
            dialog = page.get_by_role("alertdialog", name="Stop Preview", exact=True)
            expect(dialog).to_be_visible()
            expect(dialog).to_contain_text("2 changes not yet kept")
            router = dialog.get_by_role("checkbox", name="Keep Router r1", exact=True)
            expect(router).not_to_be_checked()
            expect(
                dialog.get_by_role("checkbox", name="Keep DHCP on r1", exact=True)
            ).not_to_be_checked()
            expect(dialog.get_by_role("region", name="Changes on r1")).to_be_hidden()
            screenshots(page, "rework-06", "stop-keep", "#confirm-dialog-choices")
            page.keyboard.press("Escape")
            expect(page.locator("#status-preview-state")).to_have_text("running")
            expect(
                page.get_by_role("button", name="Stop Preview", exact=True)
            ).to_be_focused()
            page.get_by_role("button", name="Stop Preview", exact=True).click()
            router.check()
            dialog.get_by_role("button", name="Keep selected", exact=True).click()
            expect(dialog).to_be_hidden()
            expect(page.locator("#status-preview-state")).to_have_text("not running")
            saved = self._saved_draft(page)
            self.assertIn(
                "ip route 192.0.2.0/24 Null0",
                saved["topology"]["nodes"][0]["frr_config"],
            )
            self.assertIsNone(
                saved["topology"]["nodes"][0]["dhcp"]["pools"][0]["lease_time"]
            )

    def test_publish_lists_the_same_rows_and_keeps_only_ticked_changes_before_its_check(
        self,
    ) -> None:
        running = {"r1": "end\n"}
        pool = dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200")
        reads: list[Any] = [
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_running_config",
                lambda _handle, node: running[node],
            ),
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_node_pools",
                lambda _handle, _node: {"pools": [copy.deepcopy(pool)]},
            ),
        ]
        for read in reads:
            read.start()
            self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 200, "router", {"dhcp": {"pools": [pool]}})])
            page.get_by_label("Catalog name").fill("Kept lesson")
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            running["r1"] = "ip route 192.0.2.0/24 Null0\n"
            pool["lease_time"] = 300
            page.get_by_role("button", name="Publish…", exact=True).click()
            dialog = page.get_by_role("dialog", name="Publish", exact=True)
            expect(
                dialog.get_by_role("heading", name="Changes not yet kept")
            ).to_be_visible()
            expect(
                dialog.get_by_role("checkbox", name="Select all", exact=True)
            ).not_to_be_checked()
            router = dialog.get_by_role("checkbox", name="Keep Router r1", exact=True)
            expect(router).not_to_be_checked()
            expect(
                dialog.get_by_role("checkbox", name="Keep DHCP on r1", exact=True)
            ).not_to_be_checked()
            expect(dialog.get_by_role("region", name="Changes on r1")).to_be_hidden()
            screenshots(page, "rework-06", "publish-keep", "#publish-keep-choices")
            router.focus()
            page.keyboard.press("Space")
            page.keyboard.press("Tab")
            expect(
                dialog.get_by_role("checkbox", name="Keep DHCP on r1", exact=True)
            ).to_be_focused()
            page.keyboard.press("Tab")
            expect(
                dialog.get_by_role("button", name="Publish", exact=True)
            ).to_be_focused()
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            expect(page.locator("#status-message")).to_have_text(
                "Published Kept lesson in the Lab Catalog."
            )
            saved = self._saved_draft(page)
            self.assertIn(
                "ip route 192.0.2.0/24 Null0",
                saved["topology"]["nodes"][0]["frr_config"],
            )
            self.assertIsNone(
                saved["topology"]["nodes"][0]["dhcp"]["pools"][0]["lease_time"]
            )
            self.assertTrue(saved["preview_valid"])

    def test_publish_shows_a_failed_start_check_without_publishing(self) -> None:
        from mniplab.labprocess import LabStartError
        from mniplab.tests.test_web_multi_lab import FakeLauncher

        class FailingLauncher(FakeLauncher):
            """Refuse startup at the Lab process boundary."""

            async def __call__(self, *args: Any, **kwargs: Any) -> FakeLabChild:
                raise LabStartError("Router r1 failed to start")

        launcher = patch.object(registry.lab_service, "launcher", FailingLauncher())
        launcher.start()
        self.addCleanup(launcher.stop)
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 300, 200, "host", {})])
            page.get_by_label("Catalog name").fill("Failed lesson")
            page.get_by_role("button", name="Publish…", exact=True).click()
            dialog = page.get_by_role("dialog", name="Publish", exact=True)
            screenshots(page, "rework-06", "publish-check", "#publish-check-note")
            dialog.get_by_role("button", name="Publish", exact=True).click()
            expect(dialog.get_by_role("alert")).to_contain_text(
                "Router r1 failed to start"
            )
            expect(dialog).to_be_visible()
            expect(
                dialog.get_by_role("button", name="Cancel", exact=True)
            ).to_be_enabled()
            screenshots(page, "rework-06", "publish-failed", "#publish-feedback")
            self.assertIsNone(self._saved_draft(page)["catalog"]["id"])

    def test_keep_help_is_folded_behind_the_information_button(self) -> None:
        """The compact review reveals its keyboard help and boundary on request."""
        read = patch(
            "mniplab.labhandle.LocalLabHandle._op_get_running_config",
            return_value="end\n",
        )
        read.start()
        self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 200, "router", {})], [])
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            page.get_by_role("button", name="Keep changes…").click()
            dialog = page.get_by_role("dialog", name="Keep changes")
            info = dialog.get_by_role("button", name="About Keep", exact=True)
            expect(info).to_have_attribute("aria-expanded", "false")
            expect(dialog.get_by_text("Keep never takes", exact=False)).to_be_hidden()
            info.focus()
            page.keyboard.press("Enter")
            expect(info).to_have_attribute("aria-expanded", "true")
            expect(
                dialog.get_by_text("Use arrow keys within a row", exact=False)
            ).to_be_visible()
            expect(dialog.get_by_text("Keep never takes", exact=False)).to_be_visible()
            screenshots(page, "rework-06", "keep-help", "#keep-help")
            page.keyboard.press("Escape")
            page.get_by_role("button", name="Keep changes…").click()
            expect(info).to_have_attribute("aria-expanded", "false")
            expect(dialog.get_by_text("Keep never takes", exact=False)).to_be_hidden()

    def test_a_router_row_opens_its_sections_and_keeps_only_the_chosen_section(
        self,
    ) -> None:
        """Tab visits a row once; arrows reach its diff and section controls."""
        running = {
            "r1": (
                "interface r1-eth0\n description Before\nexit\n!\n"
                "router ospf\n network 10.0.0.0/24 area 0\nexit\n!\n"
            )
        }
        read = patch(
            "mniplab.labhandle.LocalLabHandle._op_get_running_config",
            lambda _handle, node: running[node],
        )
        read.start()
        self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 200, "router", {})], [])
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            running["r1"] = (
                running["r1"]
                .replace("description Before", "description Prepared")
                .replace("area 0", "area 1")
            )
            page.get_by_role("button", name="Keep changes…").click()
            dialog = page.get_by_role("dialog", name="Keep changes")
            row = dialog.get_by_role("checkbox", name="Keep Router r1", exact=True)
            expect(row).to_be_focused()
            expect(row).not_to_be_checked()
            expect(
                dialog.get_by_role("checkbox", name="Select all", exact=True)
            ).not_to_be_checked()
            expect(dialog).to_contain_text("interface r1-eth0, router ospf")
            expect(dialog).to_contain_text("2 added · 2 removed")
            expect(dialog.get_by_role("region", name="Changes on r1")).to_be_hidden()
            screenshots(page, "rework-06", "keep-review", "#keep-routers")
            row.focus()
            page.keyboard.press("Tab")
            expect(
                dialog.get_by_role("button", name="Keep selected", exact=True)
            ).to_be_focused()
            page.keyboard.press("Shift+Tab")
            page.keyboard.press("ArrowRight")
            expect(
                dialog.get_by_role("button", name="Show changes on r1")
            ).to_be_focused()
            page.keyboard.press("Enter")
            expect(dialog.get_by_role("region", name="Changes on r1")).to_be_visible()
            screenshots(page, "rework-06", "keep-sections", "#keep-routers")
            dialog.get_by_role("button", name="Show changes on r1").focus()
            page.keyboard.press("ArrowRight")
            interface = dialog.get_by_role(
                "checkbox", name="Keep interface r1-eth0 on r1", exact=True
            )
            expect(interface).to_be_focused()
            page.keyboard.press("Space")
            expect(row).to_have_attribute("aria-checked", "mixed")
            page.keyboard.press("Tab")
            expect(
                dialog.get_by_role("button", name="Keep selected", exact=True)
            ).to_be_focused()
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            stored = self._saved_draft(page)
            config = stored["topology"]["nodes"][0]["frr_config"]
            self.assertIn("description Prepared", config)
            self.assertIn("area 0", config)
            self.assertNotIn("area 1", config)

    def test_select_all_keeps_two_changed_pools_from_one_dhcp_service_row(self) -> None:
        """One Service checkbox covers its Pools and Reservations together."""
        pools = [
            dhcp_pool("10.50.0.0/24", "10.50.0.100", "10.50.0.200"),
            dhcp_pool("10.51.0.0/24", "10.51.0.100", "10.51.0.200"),
        ]
        running = copy.deepcopy(pools)
        reads: list[Any] = [
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_running_config",
                return_value="end\n",
            ),
            patch(
                "mniplab.labhandle.LocalLabHandle._op_get_node_pools",
                lambda _handle, _node: {
                    "success": True,
                    "pools": copy.deepcopy(running),
                },
            ),
        ]
        for read in reads:
            read.start()
            self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(
                page, [("r1", 300, 200, "router", {"dhcp": {"pools": pools}})], []
            )
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            for pool in running:
                pool["lease_time"] = 300
            running[0]["reservations"] = [
                {
                    "mac": "aa:bb:cc:dd:ee:01",
                    "duid": "",
                    "ip": "10.50.0.50",
                    "hostname": "h1",
                }
            ]
            page.get_by_role("button", name="Keep changes…").click()
            dialog = page.get_by_role("dialog", name="Keep changes")
            row = dialog.get_by_role("checkbox", name="Keep DHCP on r1", exact=True)
            expect(row).to_be_focused()
            expect(row).not_to_be_checked()
            expect(dialog.get_by_role("checkbox")).to_have_count(2)
            page.keyboard.press("ArrowRight")
            page.keyboard.press("Enter")
            changes = dialog.get_by_role("region", name="Changes on DHCP on r1")
            expect(changes).to_contain_text("10.50.0.0/24")
            expect(changes).to_contain_text("10.51.0.0/24")
            expect(changes).to_contain_text("Reservation aa:bb:cc:dd:ee:01")
            screenshots(page, "rework-06", "keep-services", "#keep-routers")
            all_rows = dialog.get_by_role("checkbox", name="Select all", exact=True)
            all_rows.focus()
            page.keyboard.press("Space")
            expect(row).to_be_checked()
            page.keyboard.press("Space")
            expect(row).not_to_be_checked()
            page.keyboard.press("Space")
            dialog.get_by_role("button", name="Keep selected", exact=True).click()
            expect(dialog).to_be_hidden()
            kept = self._saved_draft(page)["topology"]["nodes"][0]["dhcp"]["pools"]
            self.assertEqual([pool["lease_time"] for pool in kept], [300, 300])
            self.assertEqual(kept[0]["reservations"], running[0]["reservations"])
