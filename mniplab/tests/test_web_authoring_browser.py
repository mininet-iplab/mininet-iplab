"""Keyboard-driven authoring journey against the live Web UI."""

import copy
import os
import re
import zipfile
from typing import Any
from unittest.mock import patch

import pytest

from mniplab.tests.authoring_editors import (
    AuthoringPageCase, add_dhcp_pool, add_primary_zone, catalog_row, draft_menu, labs_explorer,
    open_details, open_from_explorer, open_interface, set_interface, upload_script,
)
from mniplab.tests.browser import FilePayload, Locator, Page, box_of, choose_account_item, expect, screenshots
from mniplab.webserver import registry
from mniplab.webserver.config import settings
from mniplab.tests.authoring_fixtures import RecordingLauncher

pytestmark = pytest.mark.browser_authoring


class AuthoringBrowserTests(AuthoringPageCase):
    """Create, reopen, Preview, and publish a basic lesson using the keyboard."""

    users = "learner1:lab12345:learner,instructor1:lab12345:instructor"

    def setUp(self) -> None:
        super().setUp()
        # The Lab handle boundary: each Router's running configuration by name.
        self.running_configs: dict = {}
        running = patch(
            "mniplab.labhandle.LocalLabHandle._op_get_running_config",
            lambda _handle, node: self.running_configs.get(node, f"hostname {node}\nend\n"),
        )
        running.start()
        self.addCleanup(running.stop)

    def test_secondary_zone_chooses_a_primary_by_keyboard(self) -> None:
        """The Secondary chooser writes a stable Primary Node reference."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            page.locator("#catalog-name").fill("Transfer authoring lesson")
            self._node(page, "h1").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            page.locator("#dns-enabled").check()
            add_primary_zone(page, "example.com.")
            page.keyboard.press("Escape")
            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            self._node(page, "h2").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            page.locator("#dns-enabled").check()
            page.locator("#dns-secondary-source").focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.locator("#dns-add-secondary").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#dns-zones-rows summary").first).to_contain_text("example.com. · Secondary · Primary h1")
            page.keyboard.press("Escape")
            self._settled(page)
            self._node(page, "h2").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            expect(page.locator("#dns-zones-rows summary").first).to_contain_text("example.com. · Secondary · Primary h1")
            page.locator("#dns-zones-rows summary").first.click()
            expect(page.get_by_role("button", name="Add Record to Zone 1")).to_be_hidden()
            saved = self._saved_draft(page)
            primary = next(node for node in saved["topology"]["nodes"] if node["name"] == "h1")
            secondary = next(node for node in saved["topology"]["nodes"] if node["name"] == "h2")
            self.assertEqual(secondary["dns"]["zones"], [{"kind": "secondary", "name": "example.com.",
                                                        "primary_node_id": primary["id"]}])
            screenshots(page, "authoring-06", "secondary", "#dns-add-secondary")

    def test_resolver_settings_are_keyboard_editable(self) -> None:
        """The Resolver editor retains Root Hint identity and Access Rules."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#catalog-name").fill("Resolver authoring lesson")
            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            self._node(page, "h1").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            page.locator("#dns-enabled").check()
            add_primary_zone(page, ".", dnssec=True)
            page.keyboard.press("Escape")
            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            self._node(page, "h2").focus()
            page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…").click()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.keyboard.press("Escape")
            set_interface(page, self._node(page, "h2"), "h1", ipv4="192.0.2.54/24")
            set_interface(page, self._node(page, "h1"), "h2", ipv4="192.0.2.53/24")
            self._node(page, "h2").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            page.locator("#resolver-enabled").focus()
            page.keyboard.press("Space")
            page.locator("#resolver-root-choices input").first.check()
            page.locator("#resolver-trust-choices input").first.check()
            page.locator("#resolver-max-ttl").fill("120")
            page.locator("#resolver-max-negative-ttl").fill("30")
            page.get_by_label("Access rule 1 Subnet").fill("10.0.0.0/8")
            page.get_by_role("button", name="Remove Access rule 2").click()
            page.keyboard.press("Escape")
            self._settled(page)
            self._publish(page)
            expect(page.locator("#status-message")).to_contain_text("Published")
            page.reload()
            self._node(page, "h2").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            expect(page.locator("#resolver-root-choices input").first).to_be_checked()
            expect(page.locator("#resolver-trust-choices input").first).to_be_checked()
            expect(page.locator("#resolver-max-ttl")).to_have_value("120")
            expect(page.get_by_label("Access rule 1 Subnet")).to_have_value("10.0.0.0/8")
            expect(page.get_by_label("Access rule 2 Subnet")).to_have_count(0)
            screenshots(page, "authoring-06", "resolver", "#resolver-access-control")

    def test_primary_record_keep_is_keyboard_selectable(self) -> None:
        """A reviewed live Record can be kept without changing Zone policy."""
        running = [{"owner": "web.example.com.", "type": "A",
                    "data": "10.0.0.2", "ttl": 300}]
        mock = patch("mniplab.labhandle.LocalLabHandle._op_get_dns_records",
                     lambda _handle, _node, _zone: copy.deepcopy(running))
        mock.start()
        self.addCleanup(mock.stop)
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#catalog-name").fill("DNS record keep")
            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            self._node(page, "h1").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            page.locator("#dns-enabled").check()
            add_primary_zone(page, "example.com.", dnssec=True, records=[
                {"name": "web", "type": "A", "rdata": "10.0.0.2", "ttl": 300}])
            page.keyboard.press("Escape")
            self._saved_draft(page)
            page.locator("#preview-draft").click()
            expect(page.locator("#keep-draft")).to_be_visible()
            running[0]["data"] = "10.0.0.8"
            page.locator("#keep-draft").focus()
            page.keyboard.press("Enter")
            box = page.get_by_role("checkbox", name="Keep Primary Zone Records on h1", exact=True)
            expect(box).to_be_focused()
            page.get_by_role("button", name="Show changes on Primary Zone Records on h1").click()
            record = page.get_by_role("region", name="Changes on Primary Zone Records on h1"
                                      ).get_by_role("row", name=re.compile("^web A"))
            expect(record.get_by_role("cell")).to_have_text(["10.0.0.2 (TTL 300)", "Changed 10.0.0.8 (TTL 300)"])
            screenshots(page, "authoring-06", "keep")
            box.focus()
            page.keyboard.press("Space")
            page.locator("#keep-apply").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#keep-dialog")).to_be_hidden()
            expect(page.locator("#status-message")).to_contain_text("Kept 1 change")
            expect(page.locator("#status-draft-state")).to_have_text("Saved")
            self._node(page, "h1").focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            page.locator("#dns-zones-rows summary").first.click()
            expect(page.get_by_label("Zone 1 Record 1 Data")).to_have_value("10.0.0.8")
            expect(page.get_by_role("checkbox", name="Zone 1 DNSSEC", exact=True)).to_be_checked()

    def test_authoring_picker_revises_published_example(self) -> None:
        with self.signed_in_page() as page:
            page.goto(self.base_url + "/authoring")
            catalog_row(page, "Static Routing", "static-lab").get_by_role(
                "button", name="Copy Static Routing Lab", exact=True).click()
            expect(page.locator("#draft-title")).to_have_text("Static Routing Lab")
            # A shipped example's copy can only publish as new.
            self._publish(page, "Publish")
            expect(page.locator("#status-message")).to_contain_text("Published Static Routing Lab")
            example_id = page.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                return (await (await fetch(`/api/authoring/drafts/${id}`)).json()).catalog.id;
            }""")
            page.get_by_role("button", name="Stop Preview").click()
            expect(page.locator("#preview-draft")).to_have_text("Preview")
            authored = catalog_row(page, "Static Routing", example_id)
            expect(authored.locator(".authoring-explorer-details").get_by_role("button")).to_have_text(
                ["Copy", "Revise", "Remove from Catalog"])
            authored.get_by_role("button", name="Revise Static Routing Lab", exact=True).click()
            open_details(page)
            expect(page.locator("#catalog-name")).to_have_value("Static Routing Lab")
            from mniplab.persistent_config import ConfigCopies
            previous_copies = registry.config_copies
            registry.config_copies = ConfigCopies(os.path.join(self.tmp.name, "group-config"))
            self.addCleanup(setattr, registry, "config_copies", previous_copies)
            os.makedirs(registry.config_copies.copy_dir("instructor1", example_id))
            page.get_by_role("tab", name="Guide").click()
            page.locator("#draft-guide").fill("Revised Guide")
            self._settled(page)
            self._publish(page, "Update existing")
            page.wait_for_function("() => document.querySelector('#status-message')?.textContent.includes('Updated')")
            self.assertIn("instructor1", page.locator("#status-message").inner_text())
            self.assertIn("Config Reset", page.locator("#status-message").inner_text())
            screenshots(page, "authoring", "03")
            page.evaluate("() => localStorage.setItem('theme', 'dark')")
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            self.assertEqual(page.locator("html").get_attribute("data-theme"), "dark")

    def test_learner_sees_start_clean_for_incompatible_copy(self) -> None:
        from mniplab.persistent_config import ConfigCopies
        from mniplab.webserver.groups import Groups
        from mniplab.webserver.auth import create_session

        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        previous_groups, previous_copies = registry.groups, registry.config_copies
        registry.groups = Groups(os.path.join(self.tmp.name, "assignments.json"))
        registry.config_copies = ConfigCopies(os.path.join(self.tmp.name, "group-config"))
        self.addCleanup(setattr, registry, "groups", previous_groups)
        self.addCleanup(setattr, registry, "config_copies", previous_copies)

        with self.signed_in_page(wait_for_topology=False) as instructor:
            instructor.goto(self.base_url + "/authoring?adapt=static-lab")
            instructor.wait_for_function(
                "() => document.querySelector('#catalog-name')?.value === 'Static Routing Lab'"
            )
            instructor.locator("#preview-draft").click()
            self._publish(instructor)
            example_id = instructor.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                return (await (await fetch(`/api/authoring/drafts/${id}`)).json()).catalog.id;
            }""")
            registry.groups.assign("learner1", [example_id])
            os.makedirs(registry.config_copies.copy_dir("learner1", example_id))
            instructor.goto(self.base_url + "/")
            instructor.get_by_role("button", name="Inspect Group learner1", exact=True).click()
            card_for_group = instructor.locator(".group-card[aria-label='Group learner1']")
            card_for_group.wait_for()
            self.assertIn("incompatible", card_for_group.inner_text())
            self.assertEqual(card_for_group.locator("[data-action='reset-config']").count(), 1)
            self.assertFalse(card_for_group.locator("[data-action='start']").is_enabled())
            for selector in ("select", "[data-action='start']"):
                control = card_for_group.locator(selector)
                note_id = control.get_attribute("aria-describedby")
                self.assertTrue(note_id)
                self.assertIn("incompatible", card_for_group.locator(f"#{note_id}").inner_text())
            screenshots(instructor, "authoring-03", "instructor", card_for_group)
            instructor.evaluate("() => localStorage.setItem('theme', 'dark')")
            instructor.reload()
            instructor.get_by_role("button", name="Inspect Group learner1", exact=True).click()
            card_for_group.wait_for()
            self.assertEqual(instructor.locator("html").get_attribute("data-theme"), "dark")

            assert self.browser is not None
            context = self.browser.new_context()
            self.addCleanup(context.close)
            context.add_cookies([{
                "name": "session", "value": create_session("learner1", "learner"),
                "url": self.base_url,
            }])
            learner = context.new_page()
            learner.goto(self.base_url + "/")
            card = learner.locator("#start-screen-cards .start-screen-card")
            card.wait_for()
            self.assertEqual(card.locator(".start-screen-start").count(), 0)
            self.assertIn("incompatible", card.inner_text())
            self.assertEqual(card.locator(".start-screen-start-clean").count(), 1)
            screenshots(learner, "authoring-03", "learner")
            learner.evaluate("() => localStorage.setItem('theme', 'dark')")
            learner.reload()
            card.wait_for()
            self.assertEqual(learner.locator("html").get_attribute("data-theme"), "dark")

            # Another session can make the saved copy incompatible while this
            # Learner is already waiting on the start screen.
            import shutil
            copy_dir = registry.config_copies.copy_dir("learner1", example_id)
            shutil.rmtree(copy_dir)
            learner.reload()
            card.locator(".start-screen-start").wait_for()
            os.makedirs(copy_dir)
            card.locator(".start-screen-start").click()
            card.locator(".start-screen-start-clean").wait_for()
            self.assertIn("incompatible", card.inner_text())

    @staticmethod
    def _tab_until_focused(page: Page, selector: str, max_tabs: int = 120) -> None:
        """Reach a visible control through ordinary sequential Tab navigation."""
        target = page.locator(selector)
        for _ in range(max_tabs):
            page.keyboard.press("Tab")
            if target.evaluate("el => el === document.activeElement"):
                return
        raise AssertionError(f"could not reach {selector} by Tab")

    def test_draft_actions_and_sidebar_fields_are_visible_without_page_scroll(self) -> None:
        """At 1280x720 the actions sit above the canvas, the page never scrolls, and
        a focused sidebar field is never obscured (WCAG 2.2 SC 2.4.11)."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            self._new_draft(page)

            actions_box = page.locator(".authoring-actions").bounding_box()
            canvas_box = page.locator("#draft-canvas").bounding_box()
            assert actions_box is not None and canvas_box is not None
            self.assertLessEqual(actions_box["y"] + actions_box["height"], canvas_box["y"])
            self.assertGreaterEqual(actions_box["y"], 0)
            self.assertEqual(
                page.evaluate("() => document.scrollingElement.scrollHeight - window.innerHeight"), 0
            )

            for tab, selector in [("Lesson", "#catalog-name"), ("Lesson", "#catalog-description"),
                                  ("Lesson", "#catalog-protocols"), ("Guide", "#draft-guide")]:
                page.get_by_role("tab", name=tab).click()
                control = page.locator(selector)
                control.focus()
                box = control.bounding_box()
                assert box is not None
                self.assertGreaterEqual(box["y"], 0)
                self.assertLessEqual(box["y"] + min(box["height"], 40), 720)
                on_top = page.evaluate(
                    "([x, y]) => document.elementFromPoint(x, y)?.id",
                    [box["x"] + box["width"] / 2, box["y"] + min(box["height"], 40) / 2],
                )
                self.assertEqual(on_top, selector[1:], f"{selector} is obscured")

    def test_instructor_publishes_a_dual_stack_host_lesson_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            page.wait_for_function(
                "() => document.querySelector('#authoring-link')?.hidden === false"
            )
            self._tab_until_focused(page, "#authoring-link")
            page.keyboard.press("Enter")
            page.wait_for_url("**/authoring")
            page.wait_for_function(
                "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
            )
            page.evaluate("""() => {
                // HTTP pages opened by IP addresses are not secure contexts;
                // those browsers expose getRandomValues but not randomUUID.
                Object.defineProperty(Crypto.prototype, 'randomUUID', {
                    configurable: true,
                    value: undefined,
                });
            }""")
            self.assertEqual(
                page.locator("#authoring-status").inner_text(),
                "Create a Lab draft, upload a script, or copy a lab from the Lab Catalog to begin.",
            )

            self._tab_until_focused(page, "#new-draft")
            page.keyboard.press("Enter")
            page.wait_for_selector("#draft-form:not([hidden])")
            self._tab_until_focused(page, "#sidebar-toggle")
            page.keyboard.press("Enter")

            name = page.locator("#catalog-name")
            self.assertIn(
                "required to publish",
                page.locator('label[for="catalog-name"]').text_content() or "",
            )
            self.assertIn(
                "required to publish",
                (page.locator("#catalog-name-help").text_content() or "").lower(),
            )
            page.get_by_label("Address family", exact=True).select_option("dual")
            name.fill("Dual Stack Basics")
            page.get_by_label("Area", exact=False).select_option("Static")
            page.locator("#catalog-description").fill("Connect two addressed Hosts.")
            page.locator("#catalog-protocols").fill("IPv4, IPv6")

            palette_host = page.locator("#palette-host")
            palette_host.focus()
            page.keyboard.press("Enter")
            expect(self._node(page, "h1")).to_be_focused()
            palette_host.focus()
            page.keyboard.press("Enter")
            self.assertEqual(page.locator("#draft-canvas .node").count(), 2)

            h2 = self._node(page, "h2")
            expect(h2).to_be_focused()
            page.keyboard.press("Enter")
            disclosure = page.locator("#node-position-disclosure")
            if not disclosure.evaluate("el => el.open"):
                disclosure.locator("summary").click()
            page.locator("#host-x").fill("780")
            page.keyboard.press("Escape")
            page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…").click()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            link_dialog = page.locator("#link-dialog")
            self.assertTrue(link_dialog.evaluate("el => el.open"))
            self.assertEqual(page.locator("#link-dialog-title").inner_text(), "Link between h2 and h1")
            page.locator("#link-end-0-port").select_option("3")
            page.locator("#link-end-1-port").select_option("5")
            page.keyboard.press("Escape")
            link = page.locator("#draft-canvas .link").first
            expect(link).to_be_focused()
            expect(link).to_have_attribute("aria-label", re.compile(re.escape("h2-eth3 and h1-eth5")))
            set_interface(page, self._node(page, "h2"), "h1", ipv4="192.0.2.1/24", ipv6="2001:db8::zz/64")
            set_interface(page, self._node(page, "h1"), "h2", ipv4="192.0.2.2/24", ipv6="2001:db8::2/64")

            h1 = self._node(page, "h1")
            h1.focus()
            page.keyboard.press("Enter")
            disclosure = page.locator("#node-position-disclosure")
            if not disclosure.evaluate("el => el.open"):
                disclosure.locator("summary").click()
            page.locator("#host-x").fill("120")
            page.keyboard.press("Escape")
            expect(h1).to_be_focused()
            page.get_by_role("tab", name="Guide").click()
            page.locator("#draft-guide").fill("# Dual Stack Basics\n\nPing both addresses.")

            # The server refuses the address; the status line leads to its field.
            fix = page.locator("#status-draft-fix")
            expect(fix).to_contain_text("valid IPv6 address")
            fix.focus()
            page.keyboard.press("Enter")
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_be_focused()
            summary.get_by_role("link").click()
            first_ipv6 = page.locator("#host-dialog").get_by_label("IPv6 address and prefix")
            expect(first_ipv6).to_have_attribute("aria-invalid", "true")
            self.assertTrue(page.locator("#host-dialog").evaluate("el => el.open"))
            described_by = first_ipv6.get_attribute("aria-describedby")
            assert described_by is not None
            self.assertIn(
                "valid IPv6 address",
                page.locator(f"#{described_by.split()[0]}").inner_text(),
            )
            expect(first_ipv6).to_be_focused()
            first_ipv6.fill("2001:db8::1/64")
            page.keyboard.press("Escape")
            self._settled(page)
            self.assertTrue(page.locator("#preview-draft").is_enabled())

            self._tab_until_focused(page, "#labs-explorer-toggle")
            page.keyboard.press("Enter")
            expect(page.locator("#drafts-list")).to_contain_text("Dual Stack Basics")
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="Lesson").click()
            name.wait_for()
            self.assertEqual(name.input_value(), "Dual Stack Basics")
            link.focus()
            page.keyboard.press("Enter")
            self.assertEqual(
                [page.locator(f"#link-end-{index}-port").input_value() for index in range(2)],
                ["3", "5"],
            )
            page.keyboard.press("Escape")
            host = open_interface(page, self._node(page, "h1"), "h2")
            self.assertEqual(host.get_by_label("IPv6 address and prefix").input_value(), "2001:db8::2/64")
            page.keyboard.press("Escape")
            self.assertIn("Ping both addresses.", page.locator("#draft-guide").input_value())

            preview = page.locator("#preview-draft")
            self.assertTrue(preview.is_enabled())
            preview.focus()
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => document.querySelector('#preview-lab-link')?.hidden === false"
            )
            self.assertIn("Startup succeeded", page.locator("#status-message").inner_text())

            publish = page.locator("#publish-draft")
            publish.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("dialog", name="Publish").get_by_role(
                "button", name="Publish", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => document.querySelector('#status-message').textContent.includes('Published Dual Stack Basics')"
            )
            catalog = page.evaluate("""async () => {
                const response = await fetch('/api/examples');
                return (await response.json()).examples;
            }""")
            example = next(item for item in catalog if item["name"] == "Dual Stack Basics")
            self.assertTrue(example["authored"])
            guide = page.evaluate("""async id => {
                const response = await fetch(`/api/load_guide?example=${encodeURIComponent(id)}`);
                return response.text();
            }""", example["id"])
            self.assertIn("Ping both addresses.", guide)
            layout = page.evaluate("""async id => {
                const response = await fetch(`/api/load_layout?example=${encodeURIComponent(id)}`);
                return (await response.json()).layout;
            }""", example["id"])
            self.assertEqual(set(layout["nodes"]), {"h1", "h2"})
            self.assertEqual(layout["nodes"]["h1"]["x"], 120)
            self.assertEqual(layout["nodes"]["h2"]["x"], 780)

            page.locator("#labs-explorer-toggle").focus()
            page.keyboard.press("Enter")
            page.get_by_role("button", name="More actions for Dual Stack Basics").focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            expect(page.get_by_role("button", name="Copy as new Dual Stack Basics")).to_be_focused()
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => document.querySelector('#catalog-name').value === 'Copy of Dual Stack Basics'"
            )
            # The copy has no Preview of its own yet.
            publish.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("dialog", name="Publish", exact=True)).to_contain_text(
                "Publish starts a check Lab")
            page.keyboard.press("Escape")
            expect(publish).to_be_focused()
            link.focus()
            page.keyboard.press("Enter")
            self.assertEqual(page.locator("#link-end-0-port").input_value(), "3")
            page.locator("#link-remove").focus()
            page.keyboard.press("Enter")
            self.assertEqual(page.locator("#draft-canvas .link").count(), 0)
            expect(self._node(page, "h2")).to_be_focused()
            page.keyboard.press("Enter")
            page.locator("#host-remove").focus()
            page.keyboard.press("Enter")
            self.assertEqual(page.locator("#draft-canvas .node").count(), 1)
            expect(self._node(page, "h1")).to_be_focused()

    @staticmethod
    def _node(page: Page, name: str) -> Locator:
        """The canvas Node whose accessible name starts with ``Host <name> at``."""
        return page.locator(f"#draft-canvas .node[aria-label^='Host {name} at']")

    @staticmethod
    def _dblclick_link(page: Page, link: Locator) -> None:
        """Double-click a Link's midpoint; a horizontal line has no box height."""
        x1, y1, x2, y2 = (float(link.get_attribute(name) or 0) for name in ("x1", "y1", "x2", "y2"))
        point = page.evaluate("""([x, y]) => {
            const target = document.querySelector('#draft-canvas-g') || document.querySelector('#draft-canvas');
            const p = new DOMPoint(x, y).matrixTransform(target.getScreenCTM());
            return [p.x, p.y];
        }""", [(x1 + x2) / 2, (y1 + y2) / 2])
        page.mouse.dblclick(*point)

    def _new_draft(self, page: Page) -> None:
        """Open the authoring page and create an empty draft."""
        page.goto(self.base_url + "/authoring")
        page.wait_for_function(
            "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
        )
        labs_explorer(page).get_by_role("button", name="New draft").click()
        page.wait_for_selector("#draft-form:not([hidden])")
        open_details(page)
        page.get_by_label("Address family", exact=True).select_option("dual")
        page.locator("#catalog-area").select_option("Static")

    @staticmethod
    def _centre(locator: Locator) -> tuple[float, float]:
        """The on-screen centre of a canvas Node."""
        box = locator.locator("circle.node-body").bounding_box()
        assert box is not None
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def _saved_layout(self, page: Page) -> dict:
        """Save the draft and return its stored layout positions."""
        self._settled(page)
        return page.evaluate("""async () => {
            const id = document.querySelector('#draft-id').value;
            const response = await fetch(`/api/authoring/drafts/${encodeURIComponent(id)}`);
            return (await response.json()).layout.nodes;
        }""")

    def test_palette_enter_keeps_placing_hosts_beyond_the_old_canvas_limit(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            palette_host = page.locator("#palette-host")
            for _ in range(43):
                palette_host.focus()
                page.keyboard.press("Enter")
            newest = self._node(page, "h43")
            expect(newest).to_be_focused()
            expect(page.locator("#status-message")).to_have_text(
                re.compile(r"^Added Host h43 at x \d+, y \d+\. Press Escape to undo\.$"))
            expect(newest).to_have_class(re.compile(r"\bselected\b"))
            self.assertEqual(page.locator("#draft-canvas .node.selected").count(), 1)
            positions = list(self._saved_layout(page).values())
            self.assertEqual(len(positions), 43)
            self.assertTrue(any(p["y"] > 2000 for p in positions))
            self.assertEqual(len({(p["x"], p["y"]) for p in positions}), 43)

    def test_drag_adds_moves_and_links_hosts_at_the_pointer_position(self) -> None:
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 1400})
            self._new_draft(page)
            canvas = page.locator("#draft-canvas")
            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            assert box is not None
            drop_x, drop_y = box["width"] * 0.3, box["height"] * 0.5
            page.locator("#palette-host").drag_to(canvas, target_position={"x": drop_x, "y": drop_y})
            h1 = self._node(page, "h1")
            centre = self._centre(h1)
            self.assertAlmostEqual(centre[0], box["x"] + drop_x, delta=2)
            self.assertAlmostEqual(centre[1], box["y"] + drop_y, delta=2)
            expect(h1).to_be_focused()
            page.locator("#palette-host").drag_to(
                canvas, target_position={"x": box["width"] * 0.7, "y": box["height"] * 0.5}
            )
            h2 = self._node(page, "h2")

            handle = h1.locator(".link-handle").bounding_box()
            assert handle is not None
            target = self._centre(h2)
            page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
            page.mouse.down()
            page.mouse.move(*target, steps=8)
            page.mouse.up()
            link = page.locator("#draft-canvas .link")
            self.assertEqual(link.count(), 1)
            expect(link).to_be_focused()
            expect(page.locator("dialog[open]")).to_have_count(0)
            expect(link).to_have_attribute("aria-label", re.compile("^" + re.escape("Link between h1 and h2")))
            self.assertEqual(
                page.locator("#draft-canvas .link-label").all_text_contents(), ["eth0", "eth0"]
            )
            expect(link).to_have_attribute("aria-label", re.compile("Interfaces h1-eth0 and h2-eth0$"))

            start = self._centre(h1)
            corner = canvas.bounding_box()
            assert corner is not None
            expected = canvas.evaluate("""svg => {
                const [x, y] = d3.zoomTransform(svg).invert([5, 5]);
                return {x: Math.round(x), y: Math.round(y)};
            }""")
            page.mouse.move(*start)
            page.mouse.down()
            page.mouse.move(corner["x"] + 5, corner["y"] + 5, steps=8)
            page.mouse.up()
            expect(h1).to_have_attribute("aria-label", f"Host h1 at x {expected['x']}, y {expected['y']}")
            self.assertIn(expected, self._saved_layout(page).values())

    def test_properties_dialog_opens_from_canvas_and_rename_updates_every_label(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-host").click()
            page.locator("#palette-host").click()
            h1 = self._node(page, "h1")
            h1.focus()
            page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…").click()
            page.keyboard.press("Enter")

            h1.dblclick()
            dialog = page.locator("#host-dialog")
            self.assertTrue(dialog.evaluate("el => el.open"))
            expect(page.locator("#node-tab-general")).to_be_focused()
            page.locator("#host-name").fill("alice")
            self.assertEqual(page.locator("#host-dialog-title").inner_text(), "Host alice")
            page.keyboard.press("Escape")
            self.assertFalse(dialog.evaluate("el => el.open"))
            alice = self._node(page, "alice")
            expect(alice).to_be_focused()
            self.assertEqual(alice.locator(".node-label").text_content(), "alice")
            link = page.locator("#draft-canvas .link")
            expect(link).to_have_attribute("aria-label", re.compile("^" + re.escape("Link between alice and h2")))
            expect(link).to_have_attribute("aria-label", re.compile("Interfaces alice-eth0 and h2-eth0$"))

            self._dblclick_link(page, link)
            self.assertEqual(page.locator("#link-dialog-title").inner_text(), "Link between alice and h2")
            expect(page.locator("#link-end-0-heading")).to_contain_text("alice")
            page.keyboard.press("Escape")
            expect(link).to_be_focused()

    @staticmethod
    def _settled(page: Page) -> None:
        """Wait until the draft has saved itself."""
        expect(page.locator("#status-draft-state")).to_have_text("Saved")

    @staticmethod
    def _publish(page: Page, choice: str = "") -> None:
        """Publish, choosing new or update when both are offered; Publish checks the start itself."""
        page.get_by_role("button", name="Publish…").click()
        dialog = page.get_by_role("dialog", name="Publish")
        expect(dialog).to_be_visible()
        if not choice:
            choice = ("Publish as new" if dialog.get_by_role("button", name="Publish as new").is_visible()
                      else "Publish")
        dialog.get_by_role("button", name=choice, exact=True).click()
        expect(dialog).to_be_hidden()

    @staticmethod
    def _follow_refusal(page: Page, reason: str) -> None:
        """Follow the status line's Can't save yet link to the field it names."""
        fix = page.locator("#status-draft-fix")
        expect(fix).to_contain_text(reason)
        fix.focus()
        page.keyboard.press("Enter")

    def _saved_draft(self, page: Page) -> dict:
        """Save the draft and return it as stored."""
        self._settled(page)
        return page.evaluate("""async () => {
            const id = document.querySelector('#draft-id').value;
            return (await fetch(`/api/authoring/drafts/${encodeURIComponent(id)}`)).json();
        }""")

    def test_instructor_authors_a_router_with_startup_commands_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-router").focus()
            page.keyboard.press("Enter")
            router = page.locator("#draft-canvas .node[aria-label^='Router r1 at']")
            expect(router).to_be_focused()
            page.keyboard.press("Enter")
            self.assertEqual(page.locator("#host-dialog-title").inner_text(), "Router r1")
            page.locator("#node-tab-routing").click()
            expect(page.locator("#router-frr-config")).to_be_visible()
            self.assertEqual(page.locator("#host-remove").inner_text(), "Remove Router")

            fast = page.get_by_role("checkbox", name="Fast OSPF timers")
            expect(fast).to_be_hidden()
            ospf = page.get_by_role("checkbox", name="OSPF", exact=True)
            ospf.focus()
            page.keyboard.press("Space")
            expect(fast).to_be_checked()
            expect(fast).to_have_accessible_description(re.compile("hello 1 s and dead 3 s"))
            fast.focus()
            page.keyboard.press("Space")
            expect(fast).not_to_be_checked()
            ospf.focus()
            page.keyboard.press("Space")
            expect(fast).to_be_hidden()
            page.keyboard.press("Space")
            expect(fast).not_to_be_checked()
            page.locator("#router-frr-config").focus()
            page.keyboard.type("router ospf\n network 10.0.0.0/30 area 0")
            page.locator("#node-tab-startup").click()
            page.locator("#node-add-startup").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#startup-0-command")).to_be_focused()
            page.keyboard.type("ip addr add 10.9.9.9/32 dev lo")
            self.assertEqual(page.locator("#startup-0-stage").input_value(), "before-daemons")
            self.assertEqual(page.locator("#startup-0-timeout").input_value(), "5")
            page.locator("#startup-0-timeout").fill("3")
            page.get_by_role("checkbox", name=re.compile("^May fail")).check()
            page.keyboard.press("Escape")
            expect(router).to_be_focused()
            page.get_by_role("checkbox", name="A Learner's saved Router config carries over between sessions").check()

            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            expect(page.locator("#router-protocols")).to_be_hidden()
            page.locator("#node-tab-interfaces").click()
            page.locator("#host-ipv4-gateway").fill("10.0.0.1")
            page.keyboard.press("Escape")
            page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…").click()
            page.keyboard.press("Enter")

            stored = self._saved_draft(page)
            nodes = {node["name"]: node for node in stored["topology"]["nodes"]}
            self.assertEqual(nodes["r1"]["protocols"], ["OSPF"])
            self.assertTrue(stored["persistent_config"])
            self.assertFalse(nodes["r1"]["fast_timers"])
            self.assertEqual(nodes["r1"]["frr_config"], "router ospf\n network 10.0.0.0/30 area 0")
            self.assertEqual(nodes["r1"]["startup"], [{
                "stage": "before-daemons", "command": "ip addr add 10.9.9.9/32 dev lo",
                "timeout": 3, "may_fail": True,
            }])
            self.assertEqual(nodes["h1"]["ipv4_gateway"], "10.0.0.1")
            description = page.locator("#draft-canvas-description").text_content()
            self.assertEqual(description, "1 Host, 1 Router and 1 Link.")

            page.locator("#preview-draft").click()
            expect(page.locator("#status-message")).to_contain_text("Preview Lab started")

    def test_an_empty_startup_command_is_reported_in_its_dialog(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-host").click()
            host = self._node(page, "h1")
            host.focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-startup").click()
            page.locator("#node-add-startup").click()
            page.locator("#node-add-startup").click()
            page.locator("#startup-0-command").fill("true")
            page.locator("#startup-1-command").fill("true")
            page.get_by_role("button", name="Remove Startup command 1").click()
            expect(page.locator("#startup-0-command")).to_be_focused()
            self.assertEqual(page.locator("#node-startup-list li").count(), 1)
            page.locator("#startup-0-command").fill("  ")
            page.keyboard.press("Escape")

            self._follow_refusal(page, "Enter a shell command.")
            self.assertTrue(page.locator("#host-dialog").evaluate("el => el.open"))
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_be_focused()
            summary.locator('a[href="#startup-0-command"]').click()
            command = page.locator("#startup-0-command")
            expect(command).to_be_focused()
            expect(command).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#startup-0-command-error")).to_have_text("Enter a shell command.")

    def test_preview_lists_startup_commands_that_failed_but_may_fail(self) -> None:
        warning = "Startup command on h1 (after start) 'ping -c1 192.0.2.9' failed with exit status 1"
        launcher = patch.object(
            registry.lab_service, "launcher", RecordingLauncher(warnings=[warning])
        )
        launcher.start()
        self.addCleanup(launcher.stop)
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-host").click()
            self._saved_draft(page)
            page.locator("#preview-draft").click()
            feedback = page.locator("#status-message")
            expect(feedback).to_contain_text("1 Startup command failed but may fail")
            expect(feedback).to_contain_text(warning)

    def test_instructor_authors_vlan_trunk_and_subinterface_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-router").focus()
            page.keyboard.press("Enter")
            page.locator("#palette-switch").focus()
            page.keyboard.press("Enter")
            router = page.locator("#draft-canvas .node[aria-label^='Router r1 at']")
            router.focus()
            page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…").click()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.keyboard.press("Escape")
            switch = open_interface(page, page.locator("#draft-canvas .node[aria-label^='Switch s1 at']"), "r1")
            mode = switch.get_by_label("VLAN mode")
            mode.focus()
            page.keyboard.press("End")
            expect(mode).to_have_value("trunk")
            trunks = switch.get_by_label("Allowed VLAN IDs, comma separated")
            trunks.fill("10, 20")
            screenshots(page, "authoring-04", "vlan-link", trunks)
            page.keyboard.press("Escape")
            router.focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-routing").click()
            page.locator("#router-add-subinterface").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#subinterface-0-vlan")).to_be_focused()
            page.locator("#subinterface-0-vlan").fill("10")
            page.locator("#subinterface-0-ipv4").fill("192.168.10.1/24")
            screenshots(page, "authoring-04", "vlan")
            page.keyboard.press("Escape")
            page.locator("#catalog-name").fill("VLAN lesson")
            stored = self._saved_draft(page)
            router = next(node for node in stored["topology"]["nodes"] if node["name"] == "r1")
            self.assertEqual(router["subinterfaces"][0]["vlan"], 10)
            switch_end = next(end for end in stored["topology"]["links"][0]["ends"] if "vlan" in end)
            self.assertEqual(switch_end["vlan"]["trunks"], [10, 20])

    def test_instructor_authors_container_host_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-container").focus()
            page.keyboard.press("Enter")
            container = page.locator("#draft-canvas .node[aria-label^='Container Host d1 at']")
            expect(container).to_be_focused()
            page.keyboard.press("Enter")
            expect(page.get_by_role("textbox", name="Container image")).to_have_value("alpine:3.20")
            page.keyboard.press("Escape")
            stored = self._saved_draft(page)
            self.assertEqual(stored["topology"]["nodes"][0]["image"], "alpine:3.20")

    def test_instructor_authors_hidden_nat_types_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-router").focus()
            page.keyboard.press("Enter")
            page.locator("#palette-nat").focus()
            page.keyboard.press("Enter")
            nat = page.locator("#draft-canvas .node[aria-label^='Internet Gateway nat1 at']")
            nat.focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-nat").click()
            expect(page.locator("#nat-fields")).to_be_visible()
            page.locator("#nat-ip").fill("100.64.0.1/24")
            page.locator("#nat-subnet").fill("100.64.0.0/23")
            screenshots(page, "authoring-04", "nat")
            page.keyboard.press("Escape")
            page.locator("#palette-transit").focus()
            page.keyboard.press("Enter")
            transit = page.locator("#draft-canvas .node[aria-label^='Transit Internet Gateway transit1 at']")
            transit.focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-nat").click()
            page.locator("#nat-ip").fill("100.64.0.1/24")
            page.locator("#nat-subnet").fill("100.64.0.0/23")
            page.locator("#nat-downstream-choices input").focus()
            page.keyboard.press("Space")
            expect(page.locator("#nat-downstream-choices input")).to_be_checked()
            page.keyboard.press("Escape")
            page.locator("#catalog-name").fill("NAT infrastructure")
            stored = self._saved_draft(page)
            self.assertEqual(stored["topology"]["nodes"][1]["type"], "nat")
            self.assertEqual(stored["topology"]["nodes"][2]["downstream_ids"],
                             [stored["topology"]["nodes"][0]["id"]])

    def test_instructor_authors_an_ipv4_dhcp_lesson_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            for item in ("router", "host"):
                page.locator(f"#palette-{item}").focus()
                page.keyboard.press("Enter")
            router = page.locator("#draft-canvas .node[aria-label^='Router r1 at']")
            router.focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            self.assertTrue(page.locator("#dhcp-pools").is_hidden())
            page.locator("#dhcp-enabled").focus()
            page.keyboard.press("Space")
            add_dhcp_pool(page, "10.50.0.0/24", "10.50.0.100", "10.99.0.200", lease="120", router="10.50.0.1")
            end = page.get_by_label("Pool 1 Range end")
            end.press("Tab")
            expect(page.locator("#dhcp-pools-0-end-error")).to_have_text("Outside 10.50.0.0/24.")
            expect(end).to_have_attribute("aria-invalid", "true")
            end.fill("10.50.0.200")
            expect(page.locator("#dhcp-pools-0-end-error")).to_be_hidden()
            screenshots(page, "authoring-05", "dhcp-service", "#dhcp-pools")
            page.keyboard.press("Escape")
            page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…").click()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.keyboard.press("Escape")
            set_interface(page, router, "h1", ipv4="10.50.0.1/24")
            gateway_side = open_interface(page, router, "h1")
            expect(gateway_side.get_by_label("DHCPv4 client")).to_have_count(0)
            page.keyboard.press("Escape")
            host_node = self._node(page, "h1")
            client = open_interface(page, host_node, "r1")
            client.get_by_label("IPv4 address and prefix").fill("10.50.0.9/24")
            client.get_by_label("DHCPv4 client").focus()
            page.keyboard.press("Space")
            expect(client.get_by_label("IPv4 address and prefix")).to_be_disabled()
            expect(client.get_by_label("IPv4 address and prefix")).to_have_value("")
            screenshots(page, "authoring-05", "dhcp-client", client.get_by_label("DHCPv4 client"))
            page.keyboard.press("Escape")
            page.locator("#catalog-name").fill("DHCP lesson")
            stored = self._saved_draft(page)
            self.assertEqual(stored["topology"]["nodes"][0]["dhcp"]["pools"][0]["router"], "10.50.0.1")
            self.assertIs(stored["topology"]["links"][0]["ends"][1]["dhcp4"], True)
            self.assertEqual(stored["topology"]["links"][0]["ends"][1]["ipv4"], "")

    def test_instructor_marks_a_dhcpv6_client_by_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self._new_draft(page)
            for item in ("router", "host"):
                page.locator(f"#palette-{item}").focus()
                page.keyboard.press("Enter")
            router = page.locator("#draft-canvas .node[aria-label^='Router r1 at']")
            router.focus()
            page.keyboard.press("Enter")
            page.locator("#node-tab-routing").click()
            page.locator("#router-frr-config").fill(
                "interface r1-eth0\n ipv6 nd prefix 2001:db8:1::/64 no-autoconfig\n"
                " ipv6 nd managed-config-flag\n no ipv6 nd suppress-ra")
            page.keyboard.press("Escape")
            router.focus()
            page.get_by_role("region", name="Selection").get_by_role("button", name="Connect…").click()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.keyboard.press("Escape")
            set_interface(page, router, "h1", ipv6="2001:db8:1::1/64")
            client = open_interface(page, self._node(page, "h1"), "r1")
            client.get_by_label("DHCPv6 client").focus()
            page.keyboard.press("Space")
            expect(client.get_by_label("IPv6 address and prefix")).to_be_disabled()
            expect(client.get_by_label("IPv6 address and prefix")).to_have_value("")
            expect(client.get_by_label("IPv6 address and prefix")).to_have_accessible_description(
                re.compile("a Learner runs the DHCP client"))
            expect(client.get_by_label("IPv4 address and prefix")).to_be_enabled()
            screenshots(page, "authoring-05", "dhcpv6-client", client.get_by_label("DHCPv6 client"))
            page.keyboard.press("Escape")
            page.locator("#catalog-name").fill("DHCPv6 lesson")
            stored = self._saved_draft(page)
            end = stored["topology"]["links"][0]["ends"][1]
            self.assertIs(end["dhcp6"], True)
            self.assertEqual(end["ipv6"], "")
            self.assertNotIn("dhcp4", end)

    def test_header_parity_and_theme_toggle_on_authoring_page(self) -> None:
        """Verify authoring header parity with main Web UI, breadcrumb, role badge, theme toggle, and logout."""
        with self.signed_in_page() as page:
            page.goto(self.base_url + "/authoring")
            page.wait_for_function(
                "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
            )

            # 1. Header landmark and breadcrumb
            header = page.locator("header[role='banner']")
            expect(header).to_be_visible()

            breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
            expect(breadcrumb).to_be_visible()

            brand_link = page.locator(".header-brand-link")
            expect(brand_link).to_contain_text("Mininet-IPLab")
            expect(brand_link).to_have_attribute("href", "/")

            heading = page.locator("header h1")
            expect(heading).to_have_text("Lab Authoring")

            # 2. Return to Labs link
            return_link = page.locator("#return-labs-link")
            expect(return_link).to_be_visible()
            expect(return_link).to_have_attribute("href", "/")
            expect(return_link).to_have_accessible_name("Return to Labs")

            # 3. User identity and role badge
            username = page.locator("#header-username")
            role_badge = page.locator("#role-badge")
            expect(username).to_have_text("browser-tester")
            expect(role_badge).to_have_text("Instructor")
            expect(role_badge).to_have_attribute("data-role", "instructor")

            # 4. Dark mode, in the account menu, and theme persistence
            account = page.locator("#account-menu-btn")
            expect(account).to_have_attribute("aria-haspopup", "menu")
            account.click()
            theme_toggle = page.get_by_role("menuitemcheckbox", name="Dark mode", exact=True)
            expect(theme_toggle).to_be_visible()
            expect(theme_toggle).to_have_attribute("aria-checked", "false")

            theme_toggle.click()
            page.wait_for_function(
                "() => document.documentElement.getAttribute('data-theme') === 'dark'"
            )
            # A setting: the menu stays open to toggle it back.
            expect(theme_toggle).to_have_attribute("aria-checked", "true")
            self.assertEqual(page.evaluate("() => localStorage.getItem('theme')"), "dark")

            theme_toggle.click()
            page.wait_for_function(
                "() => document.documentElement.getAttribute('data-theme') === 'light'"
            )
            expect(theme_toggle).to_have_attribute("aria-checked", "false")
            page.keyboard.press("Escape")
            expect(account).to_be_focused()
            self.assertEqual(page.evaluate("() => localStorage.getItem('theme')"), "light")

            # 5. Stored theme preference on load
            page.evaluate("() => localStorage.setItem('theme', 'dark')")
            page.reload()
            page.wait_for_function(
                "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
            )
            self.assertEqual(
                page.evaluate("() => document.documentElement.getAttribute('data-theme')"),
                "dark",
            )
            expect(page.locator("#theme-toggle")).to_have_attribute("aria-checked", "true")

            # 6. Log out, the account menu's last item
            choose_account_item(page, "Log out")
            page.wait_for_url(f"{self.base_url}/")
            expect(page.locator("#login-page")).to_be_visible()

    def test_sidebar_width_changes_by_buttons_and_keyboard(self) -> None:
        """The sidebar can grow for reading code without requiring a drag."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            self._new_draft(page)
            sidebar = page.get_by_role("complementary", name="Details")
            initial = sidebar.bounding_box()
            assert initial is not None
            self.assertAlmostEqual(initial["width"], 340, delta=1)

            page.get_by_role("button", name="Wider sidebar").click()
            wider = sidebar.bounding_box()
            assert wider is not None
            self.assertGreater(wider["width"], initial["width"])
            page.get_by_role("button", name="Narrower sidebar").click()
            narrower = sidebar.bounding_box()
            assert narrower is not None
            self.assertAlmostEqual(narrower["width"], initial["width"], delta=1)

            divider = page.get_by_role("separator", name="Sidebar width")
            divider.focus()
            page.keyboard.press("ArrowLeft")
            expect(divider).to_be_focused()
            wider = sidebar.bounding_box()
            assert wider is not None
            self.assertGreater(wider["width"], initial["width"])
            page.keyboard.press("ArrowRight")
            narrower = sidebar.bounding_box()
            assert narrower is not None
            self.assertAlmostEqual(narrower["width"], initial["width"], delta=1)
            page.keyboard.press("End")
            largest = sidebar.bounding_box()
            assert largest is not None
            self.assertLessEqual(largest["width"], 680)
            self.assertGreater(box_of(page.locator("#draft-canvas-drop"))["width"], 400)
            page.keyboard.press("Home")
            smallest = sidebar.bounding_box()
            assert smallest is not None
            self.assertAlmostEqual(smallest["width"], 280, delta=1)
            page.keyboard.press("Tab")
            expect(divider).not_to_be_focused()
            page.get_by_role("button", name="Details", exact=True).click()
            expect(divider).to_be_hidden()
            page.get_by_role("button", name="Details", exact=True).click()
            reopened = sidebar.bounding_box()
            assert reopened is not None
            self.assertAlmostEqual(reopened["width"], 280, delta=1)

    def test_sidebar_separator_reports_available_width(self) -> None:
        """The resize handle announces the limit of the current viewport."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1024, "height": 720})
            self._new_draft(page)
            divider = page.get_by_role("separator", name="Sidebar width")
            expect(divider).to_have_attribute("aria-valuemax", "513")
            divider.focus()
            page.keyboard.press("End")
            expect(divider).to_have_attribute("aria-valuenow", "513")

    def test_sidebar_drag_width_survives_stacking_and_the_script_pane(self) -> None:
        """Dragging gives code room while the narrow layout keeps its full width."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            self._new_draft(page)
            page.get_by_role("button", name="Add Host", exact=True).click()
            sidebar = page.get_by_role("complementary", name="Details")
            divider = page.get_by_role("separator", name="Sidebar width")
            handle = divider.bounding_box()
            assert handle is not None
            page.mouse.move(handle["x"] + 12, handle["y"] + handle["height"] / 2)
            page.mouse.down()
            page.mouse.move(handle["x"] - 188, handle["y"] + handle["height"] / 2, steps=5)
            page.mouse.up()
            expanded = sidebar.bounding_box()
            assert expanded is not None
            self.assertGreater(expanded["width"], 500)
            self.assertGreater(box_of(page.locator("#draft-canvas-drop"))["width"], 350)

            screenshots(page, "redesign-08", "sidebar-expanded")

            page.set_viewport_size({"width": 900, "height": 720})
            expect(page.locator("#sidebar-resize")).to_be_hidden()
            expect(page.get_by_role("button", name="Wider sidebar")).to_be_hidden()
            stacked = sidebar.bounding_box()
            assert stacked is not None
            card = page.locator("#draft-form").evaluate("el => el.clientWidth")
            self.assertAlmostEqual(stacked["width"], card, delta=1)
            page.set_viewport_size({"width": 1280, "height": 720})
            expect(divider).to_have_attribute("aria-valuenow", str(round(expanded["width"])))
            restored = sidebar.bounding_box()
            assert restored is not None
            self.assertAlmostEqual(restored["width"], expanded["width"], delta=1)

            # The Script pane folds the sidebar to its rail; closing it brings that width back.
            script = page.get_by_role("button", name="Script", exact=True)
            script.click()
            expect(page.get_by_role("textbox", name="Lab script", exact=True)).to_be_visible()
            self.assertLess(box_of(sidebar)["width"], 60)
            script.click()
            returned = sidebar.bounding_box()
            assert returned is not None
            self.assertAlmostEqual(returned["width"], expanded["width"], delta=1)

    def test_auto_arrange_spreads_nodes_without_clamping_the_layout(self) -> None:
        """Auto-Arrange asks first, then moves every Node apart and saves the Layout."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 3500, -500), ("h2", 3500, -500), ("h3", 3500, -500), ("h4", 3500, -500)],
                       [("h1", "h2"), ("h2", "h3"), ("h3", "h4")])
            stacked = self._saved_layout(page)
            arrange = page.get_by_role("button", name="Auto-Arrange Nodes")
            dialog = page.get_by_role("alertdialog", name="Auto-Arrange Nodes")

            arrange.click()
            dialog.get_by_role("button", name="Cancel").click()
            expect(dialog).to_be_hidden()
            self.assertEqual(self._saved_layout(page), stacked)

            arrange.click()
            dialog.get_by_role("button", name="Re-arrange").click()
            expect(dialog).to_be_hidden()
            positions = list(self._saved_layout(page).values())
            self.assertEqual(len(positions), 4)
            self.assertAlmostEqual(sum(p["x"] for p in positions) / 4, 3500, delta=1)
            self.assertAlmostEqual(sum(p["y"] for p in positions) / 4, -500, delta=1)
            for index, first in enumerate(positions):
                for second in positions[index + 1:]:
                    distance = ((first["x"] - second["x"]) ** 2 + (first["y"] - second["y"]) ** 2) ** 0.5
                    self.assertGreaterEqual(distance, 100, (first, second))

    def test_canvas_container_fills_the_workspace_with_pan_and_zoom(self) -> None:
        """The canvas fills the space between palette and sidebar, and pans and zooms."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            self._new_draft(page)
            canvas_box = page.locator("#draft-canvas").bounding_box()
            container_box = page.locator(".authoring-canvas").bounding_box()
            palette_box = page.locator("#node-palette").bounding_box()
            sidebar_box = page.get_by_role("complementary", name="Details").bounding_box()
            assert canvas_box is not None
            assert container_box is not None
            assert palette_box is not None and sidebar_box is not None

            # Fills the space between the palette rail and the sidebar
            self.assertAlmostEqual(container_box["x"], palette_box["x"] + palette_box["width"], delta=2)
            self.assertAlmostEqual(container_box["x"] + container_box["width"], sidebar_box["x"], delta=2)
            self.assertAlmostEqual(canvas_box["width"], container_box["width"], delta=2)
            self.assertAlmostEqual(canvas_box["height"], container_box["height"], delta=2)

            # Zoom controls work
            initial_transform = page.locator("#draft-canvas-g").get_attribute("transform") or ""
            page.locator("#canvas-zoom-in").click()
            page.wait_for_timeout(300)
            zoomed_transform = page.locator("#draft-canvas-g").get_attribute("transform") or ""
            self.assertNotEqual(initial_transform, zoomed_transform)

            def get_scale(t: str) -> float:
                m = re.search(r"scale\(([\d.]+)\)", t)
                return float(m.group(1)) if m else 0.0

            self.assertGreater(get_scale(zoomed_transform), get_scale(initial_transform))

            # Fit view resets
            page.locator("#canvas-zoom-fit").click()
            page.wait_for_timeout(300)
            fit_transform = page.locator("#draft-canvas-g").get_attribute("transform") or ""
            self.assertAlmostEqual(get_scale(fit_transform), get_scale(initial_transform), delta=0.01)

            # Pan by dragging empty canvas space
            page.mouse.move(canvas_box["x"] + 20, canvas_box["y"] + 20)
            page.mouse.down()
            page.mouse.move(canvas_box["x"] + 80, canvas_box["y"] + 80, steps=5)
            page.mouse.up()
            panned_transform = page.locator("#draft-canvas-g").get_attribute("transform") or ""
            self.assertNotEqual(fit_transform, panned_transform)

    def test_escape_restores_open_preview_link(self) -> None:
        """Undoing a placement keeps the link to an existing Preview."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-host").click()
            self._settled(page)
            page.locator("#preview-draft").click()
            page.wait_for_function(
                "() => document.querySelector('#preview-lab-link')?.hidden === false"
            )
            page.locator("#palette-router").click()
            page.keyboard.press("Escape")
            expect(page.locator("#status-draft-state")).to_have_text("Saved")
            expect(page.locator("#preview-lab-link")).to_be_visible()

    def test_cancel_link_stretching_with_escape(self) -> None:
        """Pressing Escape while stretching a link cancels the pending link."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.locator("#palette-host").click()
            page.locator("#palette-router").click()
            h1 = self._node(page, "h1")
            r1 = page.locator("#draft-canvas .node[aria-label^='Router r1 at']")

            handle = h1.locator(".link-handle").bounding_box()
            assert handle is not None
            r1_centre = self._centre(r1)

            page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
            page.mouse.down()
            page.mouse.move(*r1_centre, steps=4)

            # Pending link visible while stretching
            self.assertEqual(page.locator("#draft-canvas .link-pending").count(), 1)

            # Escape cancels
            page.keyboard.press("Escape")
            self.assertEqual(page.locator("#draft-canvas .link-pending").count(), 0)

            # Releasing mouse does not create a link
            page.mouse.up()
            self.assertEqual(page.locator("#draft-canvas .link").count(), 0)

    def test_the_script_is_read_copied_and_downloaded_from_the_pane_by_keyboard(self) -> None:
        """The saved draft's script can be read, copied, and downloaded without a pointer."""
        with self.signed_in_page() as page:
            page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                           origin=self.base_url)
            self._new_draft(page)
            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            page.locator("#catalog-name").fill("Python export lesson")
            self._settled(page)

            self._tab_until_focused(page, "#script-toggle")
            page.keyboard.press("Enter")
            pane = page.get_by_role("region", name="Script", exact=True)
            script = pane.get_by_role("textbox", name="Lab script", exact=True)
            expect(script).to_contain_text("h1 = net.addHost('h1')")
            expect(script).to_contain_text("run_lab(")
            self._tab_until_focused(page, "#script-source")
            page.keyboard.type("h1.cmd('true')")
            self._settled(page)
            draft_id = page.locator("#draft-id").input_value()
            source = page.request.get(
                f"{self.base_url}/api/authoring/drafts/{draft_id}/python"
            ).json()["source"]
            page.keyboard.press("Control+Home")
            page.keyboard.type("changed")
            expect(script).not_to_contain_text("changed")
            page.keyboard.press("Control+a")
            page.keyboard.press("Control+c")
            self.assertEqual(page.evaluate("() => navigator.clipboard.readText()"), source)
            page.keyboard.press("Tab")
            expect(script).not_to_be_focused()

            download = pane.get_by_role("button", name="Download .zip", exact=True)
            download.focus()
            with page.expect_download() as download_info:
                page.keyboard.press("Enter")
            package_download = download_info.value
            self.assertEqual(package_download.suggested_filename, "python-export-lesson.zip")
            with zipfile.ZipFile(package_download.path()) as package:
                self.assertEqual(
                    package.read("python-export-lesson/python-export-lesson.py").decode(), source)
                self.assertIn("python-export-lesson/README.md", package.namelist())
            expect(page.locator("#status-message")).to_contain_text("Downloaded")

            screenshots(page, "redesign-08", "generated-python")

            # An edit not yet saved is saved first: the download is the renamed draft.
            page.get_by_role("button", name="Details", exact=True).click()
            page.get_by_role("tab", name="Lesson").click()
            page.locator("#catalog-name").fill("Renamed before saving")
            with page.expect_download() as download_info:
                download.click()
            self.assertEqual(download_info.value.suggested_filename, "renamed-before-saving.zip")

    def _code_section_draft(self, page: Page, name: str, code: str) -> str:
        """Save a one-Host Canvas draft with this Code section; return its id."""
        self._new_draft(page)
        page.get_by_role("button", name="Add Host", exact=True).click()
        page.get_by_label("Catalog name").fill(name)
        self._settled(page)
        draft_id = page.locator("#draft-id").input_value()
        page.evaluate("""async ([id, code]) => {
            const draft = await (await fetch(`/api/authoring/drafts/${id}`)).json();
            const response = await fetch(`/api/authoring/drafts/${id}`, {method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({revision: draft.revision, document: {...draft, code_section: code}})});
            if (!response.ok) throw new Error(await response.text());
        }""", [draft_id, code])
        return draft_id

    def test_switching_drafts_keeps_the_pane_open_on_each_drafts_own_code_section(self) -> None:
        """Unfolding the explorer keeps the open draft; opening another shows its own script."""
        with self.signed_in_page() as page:
            self._code_section_draft(page, "First lesson", "h1.cmd('first')\n")
            self._code_section_draft(page, "Second lesson", "h1.cmd('second')\n")
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            toggle = page.get_by_role("button", name="Script", exact=True)
            toggle.click()
            band = page.locator(".cm-code-band")
            expect(band).to_have_text("    h1.cmd('second')")
            labs = page.get_by_role("button", name="Labs", exact=True)
            labs.focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            expect(labs).to_be_focused()
            expect(band).to_have_text("    h1.cmd('second')")

            open_from_explorer(page, "First lesson")
            expect(page.locator("#draft-title")).to_have_text("First lesson")
            expect(toggle).to_have_attribute("aria-pressed", "true")
            expect(band).to_have_text("    h1.cmd('first')")
            expect(page.locator("#code-section-band")).to_contain_text("is yours")

    def test_the_code_section_is_editable_while_the_script_loads(self) -> None:
        """Typing in the Code section never waits for the script around it."""
        with self.signed_in_page() as page:
            self._code_section_draft(page, "Loading lesson", "")
            # Hold the script until the Code section has been typed and saved.
            held: list = []
            holding = [True]
            page.route("**/python", lambda route: held.append(route) if holding[0] else route.continue_())
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            page.get_by_role("button", name="Script", exact=True).click()
            script = page.get_by_role("textbox", name="Lab script", exact=True)
            script.focus()
            page.keyboard.type("h1.cmd('early')")
            page.keyboard.press("Tab")
            expect(script).not_to_be_focused()
            expect(page.locator("#status-draft-state")).to_have_text("Saved")
            holding[0] = False
            for route in held:
                route.continue_()
            expect(script).to_contain_text("h1 = net.addHost('h1')")
            expect(page.locator(".cm-code-band")).to_have_text("    h1.cmd('early')")
            self.assertEqual(self._saved_draft(page)["code_section"], "h1.cmd('early')")

    def test_a_script_that_loads_late_never_replaces_a_later_drafts_script(self) -> None:
        """Opening another draft while a script loads keeps that draft's script."""
        with self.signed_in_page() as page:
            earlier = self._code_section_draft(page, "Earlier choice", "h1.cmd('earlier')\n")
            self._code_section_draft(page, "Later choice", "h1.cmd('later')\n")
            # Hold the earlier script before opening its draft, which asks for it.
            pending = []
            page.route(f"**/{earlier}/python", lambda route: pending.append(route))
            with page.expect_request(f"**/{earlier}/python"):
                open_from_explorer(page, "Earlier choice")
            page.get_by_role("button", name="Script", exact=True).click()
            open_from_explorer(page, "Later choice")
            script = page.get_by_role("textbox", name="Lab script", exact=True)
            expect(page.locator(".cm-code-band")).to_have_text("    h1.cmd('later')")
            expect(script).to_contain_text("h1 = net.addHost('h1')")
            with page.expect_response(f"**/{earlier}/python"):
                for route in pending:
                    route.continue_()
            expect(page.locator("#draft-title")).to_have_text("Later choice")
            expect(page.locator(".cm-code-band")).to_have_text("    h1.cmd('later')")
            expect(page.locator("#script-editor .cm-line").filter(
                has_text="# Code section")).to_have_count(1)

    def test_the_script_pane_opens_at_the_code_section_in_view(self) -> None:
        """A long generated script opens scrolled to the lines the Instructor writes."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            # Enough Hosts that the script before the Code section scrolls.
            for _ in range(12):
                page.get_by_role("button", name="Add Host", exact=True).click()
            page.get_by_label("Catalog name").fill("Viewed line lesson")
            self._settled(page)
            page.get_by_role("button", name="Script", exact=True).click()
            script = page.get_by_role("textbox", name="Lab script", exact=True)
            expect(script).to_contain_text("return net")
            scroller = page.locator("#script-editor .cm-scroller")
            expect(scroller).not_to_have_js_property("scrollTop", 0)
            expect(page.locator(".cm-code-band")).to_be_in_viewport()
            script.focus()
            page.keyboard.type("h1.cmd('viewed')")
            self.assertEqual(self._saved_draft(page)["code_section"], "h1.cmd('viewed')")

    UPLOADED = ("from mniplab import mnIPLab\n\n\ndef build_network():\n"
                "    net = mnIPLab(topo=None, controller=None)\n"
                "    r1 = net.addRouter('r1', proto='OSPF')\n"
                "    h1 = net.addHost('h1', ip=None)\n"
                "    net.addLink(r1, h1)\n"
                "    return net\n")

    def _open_upload(self, page: Page, python: str = UPLOADED, name: str = "lesson.py") -> Locator:
        """Upload a script as a new Python draft, open it and return its script."""
        page.goto(self.base_url + "/authoring")
        page.wait_for_function(
            "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'")
        upload_script(page, name, python)
        page.reload()
        page.wait_for_selector("#python-editor:not([hidden])")
        return page.get_by_role("textbox", name="Lab script", exact=True)

    def test_an_uploaded_script_is_editable_and_tab_leaves_it(self) -> None:
        """An uploaded script remains editable and saved in the same Python draft."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1280, "height": 720})
            source = self._open_upload(page)
            draft_id = page.locator("#draft-id").input_value()
            expect(source).to_have_attribute("contenteditable", "true")
            expect(source).to_have_accessible_description(
                "Ctrl+F searches · F1 or Show API help describes the method at the "
                "cursor (F1 may be taken by the browser) · Tab leaves the editor")
            expect(page.locator("#python-editor-help")).to_contain_text(
                "Changes save to this draft")
            expect(page.get_by_role("toolbar", name="Add a Node")).to_be_hidden()
            expect(page.get_by_role("button", name="Keep changes…")).to_be_hidden()
            expect(page.get_by_role("button", name="Preview", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Publish…", exact=True)).to_be_visible()
            # The script sits in the Script pane, between the drawing and the sidebar's rail.
            source_box = page.locator("#python-code-editor").bounding_box()
            canvas_box = page.locator("#draft-canvas-drop").bounding_box()
            sidebar_box = page.get_by_role("complementary", name="Details").bounding_box()
            assert source_box is not None and canvas_box is not None and sidebar_box is not None
            self.assertGreater(source_box["x"], canvas_box["x"] + canvas_box["width"] - 1)
            self.assertLessEqual(source_box["x"] + source_box["width"], sidebar_box["x"])

            source.focus()
            page.keyboard.press("Control+End")
            page.keyboard.type("\n# saved edit")
            expect(source).to_contain_text("# saved edit")
            expect(page.locator("#status-draft-state")).to_have_text("Saved")
            self.assertEqual(self._saved_draft(page)["python"], self.UPLOADED + "\n# saved edit")

            page.keyboard.press("Control+f")
            find = page.get_by_role("textbox", name="Find", exact=True)
            expect(find).to_be_focused()
            find.fill("addRouter")
            expect(page.get_by_role("button", name="next", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(source).to_be_focused()
            page.keyboard.press("Tab")
            expect(source).not_to_be_focused()
            expect(page.get_by_role("button", name="Details", exact=True)).to_be_focused()

            page.get_by_role("button", name="Close draft", exact=True).click()
            open_from_explorer(page, "lesson, Python")
            expect(page.get_by_role("textbox", name="Lab script", exact=True)).to_contain_text(
                "# saved edit")
            self.assertEqual(page.locator("#draft-id").input_value(), draft_id)
            self.assertEqual(self._saved_draft(page)["python"], self.UPLOADED + "\n# saved edit")

    def test_a_script_is_uploaded_previewed_and_published_by_keyboard(self) -> None:
        """Upload a script… makes a new Python draft, previewed and published without a pointer."""
        from mniplab.tests.authoring_fixtures import PackageRecordingLauncher
        launcher = PackageRecordingLauncher()
        swap = patch.object(registry.lab_service, "launcher", launcher)
        swap.start()
        self.addCleanup(swap.stop)
        with self.signed_in_page() as page:
            # File choosers are intercepted from the start, so none opens unseen.
            page.on("filechooser", lambda _chooser: None)
            self._new_draft(page)
            page.locator("#palette-host").focus()
            page.keyboard.press("Enter")
            self._settled(page)
            visual_id = page.locator("#draft-id").input_value()

            page.locator("#labs-explorer-toggle").focus()
            page.keyboard.press("Enter")
            drafts = page.get_by_role("complementary", name="Labs").get_by_role("region", name="Drafts")
            upload = drafts.get_by_role("button", name="Upload a script…")
            expect(upload).to_have_accessible_description(
                "Makes a separate Python draft from one .py file, with no canvas to edit.")
            upload.focus()
            with page.expect_file_chooser() as chooser:
                page.keyboard.press("Enter")
            notes: FilePayload = {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"}
            chooser.value.set_files(files=[notes])
            expect(drafts.get_by_role("alert")).to_contain_text(
                "notes.txt is not a Python script: upload one .py file")
            expect(drafts).to_be_visible()
            expect(upload).to_be_focused()
            screenshots(page, "rework-05b", "drafts-upload-refused", "#upload-script")
            upload.focus()

            with page.expect_file_chooser() as chooser:
                page.keyboard.press("Enter")
            lesson: FilePayload = {"name": "lesson.py", "mimeType": "text/x-python",
                                   "buffer": self.UPLOADED.encode()}
            chooser.value.set_files(files=[lesson])
            expect(drafts).to_be_hidden()
            source = page.get_by_role("textbox", name="Lab script", exact=True)
            expect(source).to_be_focused()
            expect(page.locator("#status-message")).to_contain_text(
                "Uploaded lesson.py as a new Python draft.")
            expect(page.locator("#draft-title")).to_have_text("lesson")
            expect(page.get_by_role("button", name=re.compile(r"^Router r1, read-only"))).to_be_visible()
            self.assertNotEqual(page.locator("#draft-id").input_value(), visual_id)
            self.assertEqual(page.evaluate(
                "async () => (await (await fetch('/api/authoring/drafts')).json()).drafts.length"), 2)
            screenshots(page, "rework-05b", "python-read-only", fit=True)

            page.locator("#preview-draft").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#status-preview-state")).to_have_text("running")
            self.assertEqual((launcher.runs[-1]["source"], launcher.runs[-1]["seeds"]),
                             (self.UPLOADED, {}))
            expect(page.locator("#keep-draft")).to_be_visible()
            open_details(page)
            page.get_by_role("tab", name="Lesson", exact=True).click()
            page.locator("#catalog-area").select_option("Static")
            self._settled(page)
            page.locator("#publish-draft").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("dialog", name="Publish").get_by_role(
                "button", name="Publish", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            expect(page.locator("#status-message")).to_contain_text("Published lesson")

    def test_a_python_lab_example_offers_revise_and_independent_copy(self) -> None:
        """A published Python Lab Example supports revision and an independent copy."""
        from mniplab.tests.authoring_fixtures import PackageRecordingLauncher
        swap = patch.object(registry.lab_service, "launcher", PackageRecordingLauncher())
        swap.start()
        self.addCleanup(swap.stop)
        with self.signed_in_page() as page:
            self._open_upload(page)
            open_details(page)
            page.get_by_role("tab", name="Lesson", exact=True).click()
            page.locator("#catalog-area").select_option("Static")
            self._settled(page)
            page.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                const post = (url, body) => fetch(url, {method: 'POST',
                    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                const draft = await (await fetch(`/api/authoring/drafts/${id}`)).json();
                const preview = await (await post(`/api/authoring/drafts/${id}/preview`,
                    {revision: draft.revision})).json();
                await post(`/api/labs/${preview.key}/stop`, {});
                const published = await post(`/api/authoring/drafts/${id}/publish`,
                    {revision: draft.revision});
                if (!published.ok) throw new Error(await published.text());
            }""")
            labs_explorer(page).get_by_role("searchbox", name="Search labs").fill("lesson")
            row = page.locator("#example-source-list li").filter(has_text="lesson")
            summary = row.get_by_role("button", name="lesson", exact=True)
            expect(summary).to_have_accessible_description("2 Nodes · Authored · Python")
            summary.click()
            expect(row.locator(".authoring-explorer-details").get_by_role("button")).to_have_text(
                ["Copy", "Revise", "Remove from Catalog"])
            original_id = page.locator("#draft-id").input_value()
            row.get_by_role("button", name="Revise lesson", exact=True).click()
            expect(page.locator("#draft-id")).not_to_have_value(original_id)
            expect(page.locator("#draft-title")).to_have_text("lesson")
            draft = self._saved_draft(page)
            self.assertEqual(draft["kind"], "python")
            self.assertIsNotNone(draft["catalog"]["id"])
            labs_explorer(page)
            row.get_by_role("button", name="Copy lesson", exact=True).click()
            expect(page.locator("#draft-id")).not_to_have_value(draft["id"])
            copied = self._saved_draft(page)
            self.assertNotEqual(copied["id"], draft["id"])
            self.assertIsNone(copied["catalog"]["id"])
            self.assertEqual(copied["python"], draft["python"])
            self.assertEqual(copied["files"], draft["files"])

    def test_close_draft_saves_it_and_stays_closed_after_a_reload(self) -> None:
        """Close draft leaves no draft open, across reloads, until one is opened again."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.get_by_role("button", name="Add Host", exact=True).click()
            page.get_by_label("Catalog name").fill("Closed lesson")
            draft_id = page.locator("#draft-id").input_value()

            close = page.get_by_role("group", name="Draft actions").get_by_role(
                "button", name="Close draft")
            close.focus()
            page.keyboard.press("Enter")
            expect(page.locator("#draft-form")).to_be_hidden()
            expect(page.locator("#draft-actions")).to_be_hidden()
            status = page.locator("#authoring-status")
            expect(status).to_have_text(
                "Closed Closed lesson. Open a Lab draft from Drafts, or create or upload a new one.")
            expect(page.get_by_role("button", name="Open Closed lesson")).to_be_focused()
            stored = page.request.get(f"{self.base_url}/api/authoring/drafts/{draft_id}").json()
            self.assertEqual(stored["catalog"]["name"], "Closed lesson")
            self.assertEqual(len(stored["topology"]["nodes"]), 1)

            page.reload()
            expect(status).to_have_text("Open a Lab draft from Drafts, or create or upload a new one.")
            expect(page.locator("#draft-form")).to_be_hidden()
            open_from_explorer(page, "Closed lesson")
            expect(page.locator("#draft-form")).to_be_visible()
            page.reload()
            expect(page.locator("#draft-title")).to_have_text("Closed lesson")

    def test_remove_from_catalog_is_confirmed_and_refused_while_a_lab_of_it_runs(self) -> None:
        """Remove from Catalog asks once, naming Config Copies, and waits for its Labs to stop."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.get_by_role("button", name="Add Host", exact=True).click()
            page.get_by_label("Catalog name").fill("Removable lesson")
            self._settled(page)
            example_id = page.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                const post = (url, body) => fetch(url, {method: 'POST',
                    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                const draft = await (await fetch(`/api/authoring/drafts/${id}`)).json();
                const preview = await (await post(`/api/authoring/drafts/${id}/preview`,
                    {revision: draft.revision})).json();
                await post(`/api/labs/${preview.key}/stop`, {});
                const published = await (await post(`/api/authoring/drafts/${id}/publish`,
                    {revision: draft.revision})).json();
                return published.example.id;
            }""")
            started = page.request.post(f"{self.base_url}/api/labs", data={"example_id": example_id})
            self.assertEqual(started.status, 200, started.text())
            # Publishing moved the draft's revision: open it as it is now.
            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")

            search = labs_explorer(page).get_by_role("searchbox", name="Search labs")
            shipped = catalog_row(page, "Static Routing Lab", "static-lab")
            expect(shipped.get_by_role("button", name=re.compile("Remove"))).to_have_count(0)
            row = catalog_row(page, "Removable", example_id)
            expect(row.locator(".authoring-explorer-details").get_by_role("button")).to_have_text(
                ["Copy", "Revise", "Remove from Catalog"])
            remove = row.get_by_role("button", name="Remove Removable lesson from the Catalog")

            remove.focus()
            page.keyboard.press("Enter")
            confirm = page.get_by_role("alertdialog", name="Remove from Catalog")
            expect(confirm).to_have_accessible_description(
                "Remove Removable lesson from the Lab Catalog for every Group? No Group holds a "
                "Config Copy of it. Drafts revising it publish as a new lab. "
                "This cannot be undone.")
            expect(confirm.get_by_role("button", name="Cancel")).to_be_focused()
            page.keyboard.press("Enter")
            expect(confirm).to_be_hidden()
            expect(remove).to_be_focused()
            expect(row).to_be_visible()

            page.keyboard.press("Enter")
            confirm.get_by_role("button", name="Remove from Catalog").click()
            expect(confirm.get_by_role("alert")).to_contain_text(
                "Removable lesson cannot leave the Catalog: Group")
            expect(confirm.get_by_role("button", name="Remove from Catalog")).to_be_hidden()
            close = confirm.get_by_role("button", name="Close")
            expect(close).to_be_focused()
            screenshots(page, "rework-05b", "remove-from-catalog-refused", "#confirm-dialog")
            page.keyboard.press("Enter")
            expect(confirm).to_be_hidden()
            expect(remove).to_be_focused()
            expect(row).to_be_visible()

            page.request.post(f"{self.base_url}/api/labs/{started.json()['key']}/stop")
            page.keyboard.press("Enter")
            confirm.get_by_role("button", name="Remove from Catalog").click()
            expect(confirm).to_be_hidden()
            expect(row).to_have_count(0)
            expect(search).to_be_focused()
            expect(page.locator("#status-message")).to_have_text(
                "Removed Removable lesson from the Lab Catalog.")
            catalog = page.request.get(f"{self.base_url}/api/examples").json()["examples"]
            self.assertNotIn(example_id, [item["id"] for item in catalog])

            # The draft that published it now publishes a new Lab Example.
            open_details(page)
            page.get_by_label("Catalog name").fill("Removable lesson, again")
            self._settled(page)
            page.locator("#preview-draft").click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            page.get_by_role("button", name="Publish…").click()
            publish = page.get_by_role("dialog", name="Publish")
            expect(publish.get_by_role("button", name="Publish", exact=True)).to_be_visible()
            expect(publish.get_by_role("button", name="Update existing")).to_be_hidden()

    def test_start_from_a_lab_example_offers_copy_and_revise_only(self) -> None:
        """A shipped example offers exact Copy and a separate Canvas start."""
        with self.signed_in_page() as page:
            self._new_draft(page)
            shipped = catalog_row(page, "Static Routing Lab", "static-lab")
            expect(shipped.locator(".authoring-explorer-details").get_by_role("button")).to_have_text(
                ["Copy", "Start from Canvas version"]
            )
            row = catalog_row(page, "Data Center", "bgp-dc-lab")
            expect(row.get_by_text(re.compile("leaf-spine topology"))).to_be_visible()
            row.get_by_role(
                "button", name="Start from Canvas version of BGP Data Center Lab", exact=True
            ).focus()
            page.keyboard.press("Enter")
            expect(page.locator("#draft-title")).to_have_text("BGP Data Center Lab")
            expect(page.locator("#draft-canvas .node-label", has_text=re.compile("^rs1$"))).to_be_visible()
            draft_id = page.locator("#draft-id").input_value()
            draft = page.request.get(f"{self.base_url}/api/authoring/drafts/{draft_id}").json()
            self.assertNotIn("kind", draft)
            spine = next(node for node in draft["topology"]["nodes"] if node["name"] == "rs1")
            self.assertEqual(spine["vrfs"], [{"name": "vrf-ext", "table": 10}])

    def test_save_layout_is_offered_only_for_a_shipped_lab_example(self) -> None:
        """An authored Lab's layout is edited in its Draft, so its running view cannot save one."""
        display = "() => document.querySelector('#save-layout-btn').style.display"
        with self.signed_in_page() as page:
            self._new_draft(page)
            page.get_by_role("button", name="Add Host", exact=True).click()
            self._settled(page)
            preview = page.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                const draft = await (await fetch(`/api/authoring/drafts/${id}`)).json();
                const response = await fetch(`/api/authoring/drafts/${id}/preview`, {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({revision: draft.revision})});
                return (await response.json()).key;
            }""")
            page.goto(f"{self.base_url}/labs/{preview}")
            expect(page.locator("#lab-name")).to_have_text("Untitled lab")
            expect(page.locator("#lab-name")).to_have_attribute("data-example", re.compile("^preview-"))
            self.assertEqual(page.evaluate(display), "none")
            page.request.post(f"{self.base_url}/api/labs/{preview}/stop")

            started = page.request.post(f"{self.base_url}/api/labs", data={"example_id": "static-lab"})
            self.assertEqual(started.status, 200, started.text())
            page.goto(f"{self.base_url}/labs/{started.json()['key']}")
            expect(page.locator("#lab-name")).to_have_text("Static Routing Lab")
            page.wait_for_function(f"() => ({display})() === ''")

            # A refused save says why rather than only its status code.
            page.route("**/api/save_layout", lambda route: route.fulfill(
                status=409, json={"detail": "Layout refused for a reason"}))
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.simulation = appState.simulation
                    || {nodes: () => [{id: 'h1', fx: 1, fy: 2}]};
                document.querySelector('#save-layout-btn').click();
            }""")
            expect(page.locator("#layout-status")).to_have_text("Layout refused for a reason")
            page.request.post(f"{self.base_url}/api/labs/{started.json()['key']}/stop")

    def test_expired_session_signs_in_without_losing_unsaved_edits(self) -> None:
        """A refused save keeps edits while the Instructor signs in again."""
        from mniplab.webserver.auth import active_sessions, create_session

        with self.signed_in_page() as page:
            token = create_session("instructor1", "instructor")
            page.context.add_cookies([
                {"name": "session", "value": token, "url": self.base_url},
            ])
            self._new_draft(page)
            page.locator("#catalog-name").fill("Before expiry")
            self.assertEqual(self._saved_draft(page)["catalog"]["name"], "Before expiry")
            active_sessions.pop(token)
            page.locator("#catalog-name").fill("Retained edit")
            dialog = page.get_by_role("dialog", name="Sign in again")
            expect(dialog).to_be_visible()
            expect(dialog.get_by_label("Username")).to_have_value("instructor1")
            expect(dialog.get_by_label("Password")).to_be_focused()
            dialog.get_by_label("Password").fill("wrong-password")
            dialog.get_by_role("button", name="Sign in", exact=True).click()
            expect(dialog.get_by_role("alert")).not_to_be_empty()
            expect(dialog.get_by_label("Password")).to_have_attribute("aria-invalid", "true")
            expect(dialog).to_be_visible()
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(page.locator("#session-recovery")).to_be_visible()
            page.get_by_role("button", name="Sign in again", exact=True).click()
            expect(dialog).to_be_visible()
            dialog.get_by_label("Password").fill("lab12345")
            expect(dialog.get_by_label("Password")).not_to_have_attribute("aria-invalid", "true")
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            expect(page.locator("#catalog-name")).to_have_value("Retained edit")
            self.assertEqual(self._saved_draft(page)["catalog"]["name"], "Retained edit")
            page.reload()
            expect(page.locator("#catalog-name")).to_have_value("Retained edit")

    def test_reauthentication_preserves_another_instructors_revision(self) -> None:
        """Retrying a refused save still respects the revision conflict guard."""
        from mniplab.webserver.auth import active_sessions, create_session

        with self.signed_in_page() as page:
            token = create_session("instructor1", "instructor")
            page.context.add_cookies([
                {"name": "session", "value": token, "url": self.base_url},
            ])
            self._new_draft(page)
            stored = self._saved_draft(page)
            stored["catalog"]["name"] = "Other Instructor's revision"
            response = page.request.put(
                f"{self.base_url}/api/authoring/drafts/{stored['id']}",
                data={"revision": stored["revision"], "document": stored},
            )
            self.assertEqual(response.status, 200)
            active_sessions.pop(token)
            page.locator("#catalog-name").fill("My unsaved edit")
            dialog = page.get_by_role("dialog", name="Sign in again")
            expect(dialog).to_be_visible()
            dialog.get_by_label("Password").fill("lab12345")
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            expect(page.locator("#status-draft-state")).to_contain_text("Someone else changed this draft")
            expect(page.locator("#catalog-name")).to_have_value("My unsaved edit")
            saved = page.request.get(f"{self.base_url}/api/authoring/drafts/{stored['id']}").json()
            self.assertEqual(saved["catalog"]["name"], "Other Instructor's revision")

    def test_expired_session_download_offers_sign_in(self) -> None:
        """The binary download path also recovers an expired Session."""
        from mniplab.webserver.auth import active_sessions, create_session

        with self.signed_in_page() as page:
            token = create_session("instructor1", "instructor")
            page.context.add_cookies([
                {"name": "session", "value": token, "url": self.base_url},
            ])
            self._new_draft(page)
            page.get_by_role("button", name="Add Host", exact=True).click()
            self._saved_draft(page)
            active_sessions.pop(token)
            page.get_by_role("button", name="Script", exact=True).click()
            page.locator("#script-download").click()
            expect(page.get_by_role("dialog", name="Sign in again")).to_be_visible()

    def test_code_only_config_copy_offers_learner_start_clean(self) -> None:
        """A saved copy needs recovery even when the Canvas persistence flag is off."""
        from mniplab.persistent_config import ConfigCopies
        from mniplab.tests.authoring_fixtures import SeedingLauncher
        from mniplab.webserver.auth import create_session
        from mniplab.webserver.groups import Groups

        previous_groups, previous_copies = registry.groups, registry.config_copies
        registry.groups = Groups(os.path.join(self.tmp.name, "assignments.json"))
        registry.config_copies = ConfigCopies(os.path.join(self.tmp.name, "group-config"))
        self.addCleanup(setattr, registry, "groups", previous_groups)
        self.addCleanup(setattr, registry, "config_copies", previous_copies)
        with patch.object(settings, "lab_mode", "multi"), \
                patch.object(registry.lab_service, "launcher", SeedingLauncher()), \
                self.signed_in_page(wait_for_topology=False) as instructor:
            example_id = instructor.evaluate("""async () => {
                async function api(path, method, body) {
                    const response = await fetch(path, {method,
                        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                    if (!response.ok) throw new Error(await response.text());
                    return response.json();
                }
                let draft = await api('/api/authoring/drafts', 'POST', {});
                draft.catalog.name = 'Code-only Router lesson';
                draft.catalog.area = 'Static';
                draft.topology.nodes = [{id: 'h', name: 'h1', type: 'host',
                    ipv4_gateway: '', ipv6_gateway: '', startup: []}];
                draft.layout.nodes = {h: {x: 200, y: 200}};
                draft.code_section = "net.addRouter('r1', frr_dir='frr-config/r1')";
                const path = `/api/authoring/drafts/${draft.id}`;
                draft = await api(path, 'PUT', {revision: draft.revision, document: draft});
                const preview = await api(`${path}/preview`, 'POST', {revision: draft.revision});
                const first = await api(`${path}/publish`, 'POST', {revision: draft.revision});
                await api(`/api/labs/${preview.key}/stop`, 'POST', {});
                return first.example.id;
            }""")
            registry.groups.assign("learner1", [example_id])
            instructor.evaluate("""async example => {
                const response = await fetch('/api/labs', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({example_id: example, group: 'learner1'})});
                if (!response.ok) throw new Error(await response.text());
                const lab = await response.json();
                await fetch(`/api/labs/${lab.key}/stop`, {method: 'POST'});
            }""", example_id)
            copy_dir = registry.config_copies.copy_dir("learner1", example_id)
            marker = os.path.join(copy_dir, "group-work.txt")
            with open(marker, "w", encoding="utf-8") as output:
                output.write("Group work")
            instructor.evaluate("""async () => {
                const drafts = (await (await fetch('/api/authoring/drafts')).json()).drafts;
                let draft = drafts[0];
                draft.code_section = "net.addRouter('r2', frr_dir='frr-config/r2')";
                const path = `/api/authoring/drafts/${draft.id}`;
                const saved = await fetch(path, {method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({revision: draft.revision, document: draft})});
                if (!saved.ok) throw new Error(await saved.text());
                draft = await saved.json();
                const options = {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({revision: draft.revision, mode: 'update_existing'})};
                const preview = await fetch(`${path}/preview`, options);
                if (!preview.ok) throw new Error(await preview.text());
                const lab = await preview.json();
                const published = await fetch(`${path}/publish`, options);
                if (!published.ok) throw new Error(await published.text());
                await fetch(`/api/labs/${lab.key}/stop`, {method: 'POST'});
            }""")
            assert self.browser is not None
            context = self.browser.new_context()
            self.addCleanup(context.close)
            context.add_cookies([{"name": "session", "value": create_session("learner1", "learner"),
                                 "url": self.base_url}])
            learner = context.new_page()
            learner.goto(self.base_url + "/")
            card = learner.locator("#start-screen-cards .start-screen-card")
            expect(card).to_contain_text("structurally incompatible")
            expect(card.locator(".start-screen-start")).to_have_count(0)
            clean = card.get_by_role("button", name="Start Code-only Router lesson clean, from the seed")
            expect(clean).to_be_visible()
            self.assertTrue(os.path.isfile(marker))
            clean.click()
            dialog = learner.locator("#confirm-modal")
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            self.assertTrue(os.path.isfile(marker))
            clean.click()
            dialog.get_by_role("button", name="Start clean", exact=True).click()
            expect(learner.locator("#lab-name")).to_have_text("Code-only Router lesson")
            expect(learner.locator("#lab-name")).to_have_attribute("data-example", example_id)
            self.assertFalse(os.path.exists(marker))
