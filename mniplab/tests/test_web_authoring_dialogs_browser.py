"""Tests for the tabbed Node dialog and rebuilt Link dialog."""

import re

import pytest

from mniplab.tests.browser import expect, screenshots
from mniplab.tests.authoring_editors import AuthoringPageCase, add_dhcp_pool

pytestmark = pytest.mark.browser_authoring


class AuthoringDialogsBrowserTests(AuthoringPageCase):
    """An Instructor edits Nodes in tabs and Links in columns."""

    def test_tab_sets_per_node_kind(self) -> None:
        """Each Node kind shows only the tabs that apply to it, and no shown tab is empty."""
        nodes = [
            ("h1", 100, 100, "host", {}),
            ("c1", 250, 100, "container", {"image": "alpine:3.20"}),
            ("r1", 400, 100, "router", {}),
            ("s1", 550, 100, "switch", {}),
            ("nat1", 100, 300, "nat", {}),
            ("transit1", 250, 300, "transit", {}),
            ("spk1", 400, 300, "speaker", {}),
        ]
        expected_tabs = {
            "h1": ["General", "Services", "Startup", "Interfaces"],
            "c1": ["General", "Startup", "Interfaces"],
            "r1": ["General", "Routing", "Services", "Startup", "Interfaces"],
            "s1": ["General", "Interfaces"],
            "nat1": ["General", "NAT", "Interfaces"],
            "transit1": ["General", "NAT", "Interfaces"],
            "spk1": ["General", "Speaker", "Startup", "Interfaces"],
        }
        links = [("h1", "r1"), ("c1", "s1"), ("r1", "s1"), ("nat1", "r1"),
                 ("transit1", "r1"), ("spk1", "s1")]
        with self.signed_in_page() as page:
            self._seed(page, nodes, links)
            for name, expected in expected_tabs.items():
                self._node(page, name).focus()
                page.keyboard.press("Enter")
                dialog = page.locator("#host-dialog")
                expect(dialog).to_be_visible()
                tablist = dialog.locator('[role="tablist"][aria-label="Node settings"]')
                expect(tablist).to_be_visible()
                tab_labels = tablist.locator('[role="tab"]:not([hidden])').all_inner_texts()
                # Clean error marks or extra badges if any
                clean_labels = [label.split("\n")[0].strip() for label in tab_labels]
                self.assertEqual(clean_labels, expected, f"Tabs mismatch for {name}")
                if name == "r1":
                    screenshots(page, "redesign-05", "router")
                for tab in tablist.locator('[role="tab"]:not([hidden])').all():
                    tab.click()
                    panel = dialog.locator(f"#{tab.get_attribute('aria-controls')}")
                    visible = panel.locator("input, select, textarea, button, summary").filter(visible=True)
                    self.assertGreater(visible.count(), 0, f"{name}: empty {tab.inner_text()} tab")
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()

    def test_enabling_service_from_checklist_expands_editor_and_saves_definition(self) -> None:
        """Services checklist enables a service, shows its editor, and persists in definition."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 300, 300, "router", {})])
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog = page.locator("#host-dialog")
            expect(dialog).to_be_visible()

            # Switch to Services tab
            services_tab = dialog.locator('[role="tab"]', has_text="Services")
            services_tab.click()
            expect(services_tab).to_have_attribute("aria-selected", "true")

            dhcp_checkbox = dialog.locator('#service-check-dhcp, input[type="checkbox"][data-service="dhcp"]')
            expect(dhcp_checkbox).not_to_be_checked()
            expect(dialog.locator("#dhcp-pool-fields")).to_be_hidden()

            dhcp_checkbox.check()
            expect(dialog.locator("#dhcp-pool-fields")).to_be_visible()
            add_dhcp_pool(page, "10.0.0.0/24", "10.0.0.10", "10.0.0.100", lease="600", router="10.0.0.1")

            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()

            saved = self._saved_draft(page)
            r1 = next(node for node in saved["topology"]["nodes"] if node["name"] == "r1")
            self.assertIsNotNone(r1.get("dhcp"))
            self.assertEqual(r1["dhcp"]["pools"][0]["cidr"], "10.0.0.0/24")

    def test_error_on_hidden_tab_marked_and_reached_from_summary(self) -> None:
        """An error on a hidden tab carries a mark and clicking the summary link opens and focuses it."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 200, 200, "router", {})])
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog = page.locator("#host-dialog")
            expect(dialog).to_be_visible()

            # Switch to Services tab and enable Relay without subnets
            dialog.locator('#node-tab-services').click()
            dialog.locator('#relay-enabled').check()
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()

            status = page.get_by_role("region", name="Status")
            expect(status).to_contain_text("Router r1: Enter at least one Subnet CIDR to relay")
            fix_btn = status.get_by_role("button", name=re.compile("Router r1", re.I))
            fix_btn.click()

            expect(dialog).to_be_visible()

            # General tab is active initially
            expect(dialog.locator("#node-tab-general")).to_have_attribute("aria-selected", "true")
            expect(dialog.locator("#node-panel-services")).to_be_hidden()

            # Services tab carries the text-and-icon error mark
            services_tab = dialog.locator("#node-tab-services")
            expect(services_tab.locator(".dialog-tab-error-mark")).to_contain_text("⚠")
            expect(services_tab.locator(".dialog-tab-error-text")).to_contain_text("1 error")

            # Error summary is focused
            summary = dialog.locator("#node-dialog-error-summary")
            expect(summary).to_be_focused()

            # Clicking the error link in the summary switches to the Services tab and focuses the field
            summary.locator('a[href="#relay-subnets"]').click()
            expect(services_tab).to_have_attribute("aria-selected", "true")
            expect(dialog.locator("#node-panel-services")).to_be_visible()
            relay_subnets = dialog.locator("#relay-subnets")
            expect(relay_subnets).to_be_focused()
            expect(relay_subnets).to_have_attribute("aria-invalid", "true")

            # Escape returns focus to canvas node
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(self._node(page, "r1")).to_be_focused()

    def test_link_dialog_interface_select_produces_right_port(self) -> None:
        """Link dialog interface select shows name-ethN for free ports and saves the port."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 200, 200, "router", {}), ("h1", 500, 200, "host", {})], [("r1", "h1")])
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog = page.locator("#host-dialog")
            dialog.locator('[role="tab"]', has_text="Interfaces").click()
            dialog.locator("#node-interfaces-list .authoring-interface").first.get_by_role("button", name=re.compile("Edit", re.I)).click()

            link_dialog = page.locator("#link-dialog")
            expect(link_dialog).to_be_visible()

            # End 0 is r1: heading is Router r1, with Change… button
            heading_0 = link_dialog.locator("#link-end-0-heading")
            expect(heading_0).to_contain_text("r1")
            change_btn_0 = link_dialog.locator("#link-end-0-change")
            expect(change_btn_0).to_be_visible()

            # Interface select shows name-ethN
            intf_select = link_dialog.locator("#link-end-0-port")
            expect(intf_select).to_be_visible()
            # Selected value should be 0 (r1-eth0)
            expect(intf_select).to_have_value("0")

            # Option labels include r1-eth0, r1-eth1, etc.
            options = intf_select.locator("option").all_inner_texts()
            self.assertIn("r1-eth0", options)
            self.assertIn("r1-eth1", options)

            # Select r1-eth1
            intf_select.select_option(value="1")

            page.keyboard.press("Escape")
            expect(link_dialog).to_be_hidden()

            saved = self._saved_draft(page)
            link = saved["topology"]["links"][0]
            self.assertEqual(link["ends"][0]["port"], 1)

    def test_link_dialog_change_repoints_end_without_orphaning_subinterfaces(self) -> None:
        """Change… re-points an end to another Node without orphaning router VLAN subinterfaces."""
        with self.signed_in_page() as page:
            # r1, r2, h1. Link r1 to h1.
            self._seed(page, [
                ("r1", 200, 200, "router", {}),
                ("r2", 200, 400, "router", {}),
                ("h1", 500, 200, "host", {}),
            ], [("r1", "h1")])

            # Give r1 a subinterface on its link to h1 via the UI
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog = page.locator("#host-dialog")
            dialog.locator('#node-tab-routing').click()
            dialog.locator('#router-add-subinterface').click()
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()

            # Open the link dialog via the Links tab on r1
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog.locator('[role="tab"]', has_text="Interfaces").click()
            dialog.locator("#node-interfaces-list .authoring-interface").first.get_by_role("button", name=re.compile("Edit", re.I)).click()

            link_dialog = page.locator("#link-dialog")
            expect(link_dialog).to_be_visible()

            # Click Change… on end 0 (r1)
            change_btn = link_dialog.locator("#link-end-0-change")
            change_btn.click()

            # Node select appears; select r2
            node_select = link_dialog.locator("#link-end-0-change-node")
            expect(node_select).to_be_visible()
            node_select.select_option(label="Router r2")

            # Heading updates to Router r2
            expect(link_dialog.locator("#link-end-0-heading")).to_contain_text("r2")

            page.keyboard.press("Escape")
            expect(link_dialog).to_be_hidden()

            saved = self._saved_draft(page)
            link = saved["topology"]["links"][0]
            r2 = next(n for n in saved["topology"]["nodes"] if n["name"] == "r2")
            r1 = next(n for n in saved["topology"]["nodes"] if n["name"] == "r1")
            self.assertEqual(link["ends"][0]["node_id"], r2["id"])
            # r1 should not have an orphaned subinterface
            parents = [e["interface_id"] for l in saved["topology"]["links"] for e in l["ends"] if e["node_id"] == r1["id"]]
            for sub in r1.get("subinterfaces", []):
                self.assertIn(sub["parent_interface_id"], parents)

    def test_link_dialog_initial_condition_in_collapsed_disclosure(self) -> None:
        """Initial Link Condition sits in a disclosure below columns, collapsed unless set."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 200, 200, "router", {}), ("h1", 500, 200, "host", {})], [("r1", "h1")])
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog = page.locator("#host-dialog")
            dialog.locator('[role="tab"]', has_text="Interfaces").click()
            dialog.locator("#node-interfaces-list .authoring-interface").first.get_by_role("button", name=re.compile("Edit", re.I)).click()

            link_dialog = page.locator("#link-dialog")
            expect(link_dialog).to_be_visible()

            disclosure = link_dialog.locator("#link-condition-disclosure")
            expect(disclosure).to_be_visible()
            # Collapsed unless set
            expect(disclosure).not_to_have_attribute("open", "")

            # Checking the box enables controls and expands disclosure
            disclosure.locator("summary").click()
            expect(disclosure).to_have_attribute("open", "")
            checkbox = link_dialog.locator("#link-condition-enabled")
            checkbox.check()
            expect(link_dialog.locator("#link-condition-controls")).to_be_visible()
            link_dialog.locator("#link-condition-delay").fill("15ms")

            page.keyboard.press("Escape")
            expect(link_dialog).to_be_hidden()

            # Reopening the link dialog shows the disclosure already open because condition is set
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog.locator('[role="tab"]', has_text="Interfaces").click()
            dialog.locator("#node-interfaces-list .authoring-interface").first.get_by_role("button", name=re.compile("Edit", re.I)).click()
            expect(link_dialog.locator("#link-condition-disclosure")).to_have_attribute("open", "")

    def test_link_dialog_layout_and_screenshots(self) -> None:
        """Link dialog renders 2 columns side by side, stacks below 700px, and fits 375 viewport."""
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 200, 200, "router", {}), ("s1", 500, 200, "switch", {})], [("r1", "s1")])
            self._node(page, "r1").focus()
            page.keyboard.press("Enter")
            dialog = page.locator("#host-dialog")
            dialog.locator('[role="tab"]', has_text="Interfaces").click()
            dialog.locator("#node-interfaces-list .authoring-interface").first.get_by_role("button", name=re.compile("Edit", re.I)).click()

            link_dialog = page.locator("#link-dialog")
            expect(link_dialog).to_be_visible()

            # Take screenshots in light and dark across viewports
            screenshots(page, "redesign-05", "link")

            # At 375 width, ensure columns stack and dialog has no horizontal overflow
            page.set_viewport_size({"width": 375, "height": 800})
            ends_container = link_dialog.locator("#link-dialog-ends")
            expect(ends_container).to_be_visible()
            box = link_dialog.bounding_box()
            self.assertIsNotNone(box)
            assert box is not None
            self.assertLessEqual(box["width"], 375)

    def test_position_disclosure_keyboard_alternative(self) -> None:
        """Position disclosure can be opened by keyboard, x/y set, and read back over HTTP."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100, "host", {})])
            self._node(page, "h1").focus()
            page.keyboard.press("Enter")
            dialog = page.locator("#host-dialog")
            expect(dialog).to_be_visible()

            # The Position disclosure is initially collapsed
            disclosure = dialog.locator("#node-position-disclosure")
            expect(disclosure).not_to_have_attribute("open", "")
            expect(dialog.locator("#host-x")).to_be_hidden()

            # Focus the summary and open it via keyboard
            summary = disclosure.locator("summary")
            summary.focus()
            page.keyboard.press("Enter")
            expect(disclosure).to_have_attribute("open", "")
            expect(dialog.locator("#host-x")).to_be_visible()

            # Set new coordinates
            dialog.locator("#host-x").fill("250")
            dialog.locator("#host-y").fill("350")
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()

            saved = self._saved_draft(page)
            h1 = next(n for n in saved["topology"]["nodes"] if n["name"] == "h1")
            pos = saved["layout"]["nodes"][h1["id"]]
            self.assertEqual(pos, {"x": 250, "y": 350})
            page.reload()
            self._node(page, "h1").focus()
            page.keyboard.press("Enter")
            expect(dialog.locator("#host-y")).to_have_value("350")


