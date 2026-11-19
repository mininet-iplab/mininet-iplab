"""Autosave, the status line and the draft actions, driven as an Instructor in a real browser."""

import asyncio
import copy
import re
import threading
import time
from typing import Any, Optional
from unittest.mock import patch

import pytest

from mniplab.labprocess import LabStartError
from mniplab.tests.authoring_editors import (
    AuthoringPageCase, add_dhcp_pool, draft_menu, open_details, open_from_explorer, upload_script,
)
from mniplab.tests.browser import Locator, Page, Route, expect, screenshots
from mniplab.tests.test_web_multi_lab import FakeLabChild, FakeLauncher
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_authoring

# Comfortably past the autosave pause after the last edit.
AFTER_PAUSE_MS = 2000


class HeldLauncher(FakeLauncher):
    """Hold each Lab's startup until the test releases it.

    Attributes:
        release: Set to let a held startup finish.
    """

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    async def __call__(
        self,
        example_id: str,
        lab_file: str,
        config_copy: Optional[str] = None,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
    ) -> FakeLabChild:
        await asyncio.get_running_loop().run_in_executor(None, self.release.wait)
        return await super().__call__(example_id, lab_file, config_copy, timeout, cwd)


class AuthoringActionsBrowserTests(AuthoringPageCase):
    """A Lab Example Draft saves itself and reports its state in one status line."""

    def _open_new_draft(self, page: Page) -> None:
        """Open Lab Authoring on a fresh empty draft, with the page's timers under test control."""
        page.clock.install()
        page.goto(self.base_url + "/authoring")
        page.wait_for_function(
            "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
        )
        page.evaluate("""async () => {
            const draft = await (await fetch('/api/authoring/drafts', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
            draft.catalog.area = 'Static';
            await fetch(`/api/authoring/drafts/${draft.id}`, {method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({revision: draft.revision, document: draft})});
        }""")
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")
        open_details(page)

    @staticmethod
    def _status_line(page: Page) -> Locator:
        return page.get_by_role("region", name="Status")

    @staticmethod
    def _stored(page: Page) -> dict:
        """The open draft as the server holds it."""
        return page.evaluate("""async () => {
            const id = document.querySelector('#draft-id').value;
            return (await fetch(`/api/authoring/drafts/${encodeURIComponent(id)}`)).json();
        }""")

    def test_an_edit_saves_itself_as_the_next_revision(self) -> None:
        """About a second after the last edit the draft is saved, with no Save button to press."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            status = self._status_line(page)
            expect(status).to_contain_text("Draft: Saved")
            expect(page.get_by_role("button", name="Save draft")).to_have_count(0)
            revision = self._stored(page)["revision"]
            requests: list[str] = []
            page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))

            page.get_by_label("Catalog name").fill("Autosaved lesson")
            expect(status).to_contain_text("Draft: Saving…")
            page.clock.run_for(AFTER_PAUSE_MS)

            expect(status).to_contain_text("Draft: Saved")
            stored = self._stored(page)
            self.assertEqual(stored["revision"], revision + 1)
            self.assertEqual(stored["catalog"]["name"], "Autosaved lesson")
            # Saving never reloads the whole draft list.
            self.assertNotIn(f"GET {self.base_url}/api/authoring/drafts", requests)

    @staticmethod
    def _place(page: Page, kind: str) -> Locator:
        """Place a Node from the palette by keyboard; it is left focused."""
        page.locator(f"#palette-{kind}").focus()
        page.keyboard.press("Enter")
        return page.locator("#draft-canvas .node.selected")

    def test_closing_a_dialog_saves_without_waiting(self) -> None:
        """Edits made in a Node's dialog are saved as soon as it closes."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            page.clock.pause_at(page.evaluate("Date.now()") + 1)
            revision = self._stored(page)["revision"]
            self._place(page, "host")
            page.keyboard.press("Enter")
            page.locator("#host-name").fill("alice")
            page.keyboard.press("Escape")

            expect(self._status_line(page)).to_contain_text("Draft: Saved")
            stored = self._stored(page)
            self.assertEqual(stored["revision"], revision + 1)
            self.assertEqual([node["name"] for node in stored["topology"]["nodes"]], ["alice"])

    def test_undoing_a_placement_that_already_saved_saves_its_removal(self) -> None:
        """Escape still undoes a placement after it saved; the undo is saved too."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            revision = self._stored(page)["revision"]
            self._place(page, "host")
            page.clock.run_for(AFTER_PAUSE_MS)
            expect(self._status_line(page)).to_contain_text("Draft: Saved")
            self.assertEqual(len(self._stored(page)["topology"]["nodes"]), 1)

            page.keyboard.press("Escape")
            expect(page.locator("#draft-canvas .node")).to_have_count(0)
            page.clock.run_for(AFTER_PAUSE_MS)
            expect(self._status_line(page)).to_contain_text("Draft: Saved")
            stored = self._stored(page)
            self.assertEqual(stored["topology"]["nodes"], [])
            self.assertEqual(stored["revision"], revision + 2)

    def test_an_unsaveable_node_stays_in_memory_and_links_to_its_field(self) -> None:
        """A Transit Internet Gateway without downstream Routers cannot be saved yet, and says why."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            revision = self._stored(page)["revision"]
            self._place(page, "transit")
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="NAT", exact=True).click()
            page.locator("#nat-ip").fill("100.64.0.1/24")
            page.locator("#nat-subnet").fill("100.64.0.0/24")
            page.keyboard.press("Escape")
            page.clock.run_for(AFTER_PAUSE_MS)

            status = self._status_line(page)
            expect(status).to_contain_text(
                "Draft: Can't save yet: Transit Internet Gateway transit1: Choose at least one downstream Router.")
            expect(status.get_by_role("status")).to_have_text(
                "Can't save yet: Transit Internet Gateway transit1: Choose at least one downstream Router.")
            expect(status.get_by_role("alert")).to_have_count(0)
            self.assertEqual(self._stored(page)["revision"], revision)
            expect(page.locator("#draft-canvas .node", has_text="transit1")).to_have_count(1)

            # Another edit retries the save; the link keeps the Instructor's focus meanwhile.
            fix = status.get_by_role("button", name="Transit Internet Gateway transit1: Choose at least one downstream Router.")
            page.get_by_label("Catalog name").fill("Still refused")
            fix.focus()
            page.clock.run_for(AFTER_PAUSE_MS)
            expect(fix).to_be_focused()
            page.keyboard.press("Enter")
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_be_focused()
            summary.locator('a[href="#nat-downstream-fields"]').click()
            downstream = page.locator("#nat-downstream-fields")
            expect(downstream).to_be_focused()
            expect(downstream).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#nat-downstream-fields-error")).to_have_text("Choose at least one downstream Router.")

    def test_a_refusal_from_the_server_links_to_its_field(self) -> None:
        """What only the server checks is refused the same way, next to the field it names."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            revision = self._stored(page)["revision"]
            self._place(page, "host")
            page.keyboard.press("Enter")
            page.locator("#node-tab-interfaces").click()
            page.locator("#host-ipv4-gateway").fill("not-an-address")
            page.keyboard.press("Escape")

            status = self._status_line(page)
            expect(status).to_contain_text(
                "Draft: Can't save yet: Host h1: Enter a valid IPv4 default gateway address")
            self.assertEqual(self._stored(page)["revision"], revision)
            status.get_by_role("button", name="Host h1: Enter a valid IPv4 default gateway address",
                               exact=False).click()
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_be_focused()
            summary.locator('a[href="#host-ipv4-gateway"]').click()
            gateway = page.locator("#host-ipv4-gateway")
            expect(gateway).to_be_focused()
            expect(gateway).to_have_attribute("aria-invalid", "true")

            gateway.fill("192.0.2.1")
            page.keyboard.press("Escape")
            expect(status).to_contain_text("Draft: Saved")
            self.assertEqual(self._stored(page)["revision"], revision + 1)

    def test_a_newer_revision_from_elsewhere_is_a_conflict_to_reopen(self) -> None:
        """Nothing is merged: the Instructor is alerted and reopens the latest revision."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            expect(self._status_line(page)).to_contain_text("Draft: Saved")
            # Another Instructor saves this draft first.
            page.evaluate("""async () => {
                const id = document.querySelector('#draft-id').value;
                const url = `/api/authoring/drafts/${encodeURIComponent(id)}`;
                const draft = await (await fetch(url)).json();
                draft.catalog.name = 'Their lesson';
                await fetch(url, {method: 'PUT', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({revision: draft.revision, document: draft})});
            }""")
            page.get_by_label("Catalog name").fill("My lesson")
            page.clock.run_for(AFTER_PAUSE_MS)

            alert = self._status_line(page).get_by_role("alert")
            expect(alert).to_have_text("Someone else changed this draft · Reopen")
            self.assertEqual(self._stored(page)["catalog"]["name"], "Their lesson")
            # Further edits never overwrite theirs.
            page.get_by_label("Catalog name").fill("My lesson, again")
            page.clock.run_for(AFTER_PAUSE_MS)
            self.assertEqual(self._stored(page)["catalog"]["name"], "Their lesson")
            dialog = self._publish_dialog(page)
            expect(dialog.get_by_role("list", name="Publishing needs").get_by_role("listitem").first
                   ).to_have_text("Someone else changed this draft Reopen")
            page.keyboard.press("Escape")

            alert.get_by_role("button", name="Reopen").click()
            expect(page.get_by_label("Catalog name")).to_have_value("Their lesson")
            expect(self._status_line(page)).to_contain_text("Draft: Saved")
            expect(page.locator("#canvas-region")).to_be_focused()

    def _preview_host_draft(self, page: Page) -> None:
        """A new draft with one Host, with its Preview running."""
        self._open_new_draft(page)
        self._place(page, "host")
        page.get_by_role("button", name="Preview", exact=True).click()
        expect(self._status_line(page)).to_contain_text("Preview: running")

    def test_open_lab_remains_after_a_reload(self) -> None:
        """The Preview's state is read from the server, so a reload keeps Open Lab."""
        with self.signed_in_page() as page:
            self._preview_host_draft(page)
            status = self._status_line(page)
            open_lab = status.get_by_role("link", name="Open Lab")
            expect(open_lab).to_have_attribute("href", f"/labs/{self.launcher.children[0].key}")
            expect(page.get_by_role("button", name="Stop Preview")).to_be_visible()

            page.reload()
            page.wait_for_selector("#draft-form:not([hidden])")
            expect(status).to_contain_text("Preview: running")
            expect(open_lab).to_have_attribute("href", f"/labs/{self.launcher.children[0].key}")
            expect(status.get_by_role("button", name="Keep changes…")).to_be_visible()
            expect(page.get_by_role("button", name="Stop Preview")).to_be_visible()

    def test_a_network_edit_puts_the_running_preview_out_of_date(self) -> None:
        """A Guide edit leaves the Preview current; renaming a Node does not."""
        with self.signed_in_page() as page:
            self._preview_host_draft(page)
            status = self._status_line(page)
            page.get_by_role("tab", name="Guide").click()
            page.get_by_label("Guide (Markdown)").fill("# Ping h1")
            page.clock.run_for(AFTER_PAUSE_MS)
            expect(status).to_contain_text("Draft: Saved")
            expect(status).to_contain_text("Preview: running")

            page.locator("#draft-canvas .node").first.focus()
            page.keyboard.press("Enter")
            page.locator("#host-name").fill("alice")
            page.keyboard.press("Escape")
            expect(status).to_contain_text("Preview: out of date")
            expect(status.get_by_role("link", name="Open Lab")).to_be_visible()
            expect(status.get_by_role("button", name="Keep changes…")).to_be_visible()
            self.assertFalse(self._stored(page)["preview_valid"])
            screenshots(page, "redesign-03", "status-preview-out-of-date")

    def test_nothing_saves_while_a_preview_is_starting(self) -> None:
        """Edits made during startup wait for it, so the server does not stop the new Preview."""
        held = HeldLauncher()
        self.addCleanup(held.release.set)
        swap = patch.object(registry.lab_service, "launcher", held)
        swap.start()
        self.addCleanup(swap.stop)
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "host")
            page.clock.run_for(AFTER_PAUSE_MS)
            status = self._status_line(page)
            expect(status).to_contain_text("Draft: Saved")
            revision = self._stored(page)["revision"]
            toggle = page.get_by_role("button", name="Preview", exact=True)
            toggle.click()
            expect(status).to_contain_text("Preview: starting…")
            expect(toggle).to_have_attribute("aria-disabled", "true")

            page.get_by_label("Catalog name").fill("Edited during startup")
            page.clock.run_for(AFTER_PAUSE_MS)
            page.locator("#draft-canvas .node").first.focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Escape")
            expect(status).to_contain_text("Draft: Saving…")
            self.assertEqual(self._stored(page)["revision"], revision)

            held.release.set()
            expect(status).to_contain_text("Preview: running")
            expect(status).to_contain_text("Draft: Saved")
            stored = self._stored(page)
            self.assertEqual(stored["revision"], revision + 1)
            self.assertEqual(stored["catalog"]["name"], "Edited during startup")
            self.assertTrue(stored["preview_valid"])

    def test_a_preview_that_fails_to_start_is_an_alert(self) -> None:
        """A failed start is the Preview segment's alert, and Preview can be tried again."""
        async def failing(*_args: Any, **_kwargs: Any) -> None:
            raise LabStartError("Node h1 did not start")

        swap = patch.object(registry.lab_service, "launcher", failing)
        swap.start()
        self.addCleanup(swap.stop)
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "host")
            toggle = page.get_by_role("button", name="Preview", exact=True)
            toggle.click()
            alert = self._status_line(page).get_by_role("alert")
            expect(alert).to_have_text("failed: Node h1 did not start")
            expect(self._status_line(page)).to_contain_text("Preview: failed: Node h1 did not start")
            expect(self._status_line(page).get_by_role("link", name="Open Lab")).to_have_count(0)
            expect(toggle).to_have_attribute("aria-disabled", "false")

    def test_stop_preview_stops_it_and_keeps_focus_on_the_toggle(self) -> None:
        """With nothing not yet kept, Stop Preview stops at once and reads Preview again."""
        with self.signed_in_page() as page:
            self._preview_host_draft(page)
            page.get_by_role("button", name="Stop Preview").click()
            status = self._status_line(page)
            expect(status).to_contain_text("Preview: not running")
            expect(status.get_by_role("link", name="Open Lab")).to_have_count(0)
            expect(status.get_by_role("button", name="Keep changes…")).to_have_count(0)
            expect(page.get_by_role("button", name="Preview", exact=True)).to_be_focused()
            expect(status.get_by_role("status")).to_have_text("Preview Lab stopped.")
            self.assertTrue(self.launcher.children[0].stopped)

    @staticmethod
    def _publish_dialog(page: Page) -> Locator:
        page.get_by_role("button", name="Publish…").click()
        dialog = page.get_by_role("dialog", name="Publish")
        expect(dialog).to_be_visible()
        return dialog

    def test_publish_says_a_catalog_name_is_missing_and_opens_the_lesson_tab(self) -> None:
        """Publish… is always enabled; it lists what is missing, each with its fix."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "host")
            page.get_by_label("Catalog name").fill("")
            page.get_by_role("tab", name="Guide").click()
            expect(page.get_by_role("button", name="Publish…")).to_be_enabled()

            dialog = self._publish_dialog(page)
            missing = dialog.get_by_role("list", name="Publishing needs")
            expect(missing.get_by_role("listitem")).to_have_text([
                "Needs a Catalog name Open Lesson tab",
            ])
            fix = missing.get_by_role("button", name="Open Lesson tab")
            expect(fix).to_be_focused()
            expect(dialog.get_by_role("button", name="Publish", exact=True)).to_have_count(0)
            screenshots(page, "redesign-03", "publish-missing")
            fix.focus()
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            expect(page.get_by_role("tab", name="Lesson")).to_have_attribute("aria-selected", "true")
            expect(page.get_by_label("Catalog name")).to_be_focused()

    def test_publish_says_an_empty_draft_needs_a_node_and_goes_to_the_palette(self) -> None:
        """An empty draft cannot publish; the fix leads to the palette."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            dialog = self._publish_dialog(page)
            missing = dialog.get_by_role("list", name="Publishing needs")
            expect(missing.get_by_role("listitem").first).to_have_text("Needs at least one Node Add a Node")
            missing.get_by_role("button", name="Add a Node").click()
            expect(page.get_by_role("toolbar", name="Add a Node").get_by_role("button", name="Add Host")
                   ).to_be_focused()

    def test_publish_says_a_python_draft_needs_a_script_that_compiles(self) -> None:
        """An uploaded script that does not compile is still a draft; Publish… leads to its line."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            upload_script(page, "broken.py", "def build_network(:\n    pass\n")
            open_from_explorer(page, "broken, Python")
            source = page.locator("#python-source")
            expect(source).to_have_attribute("aria-invalid", "true")
            expect(page.get_by_text("⚠ 1", exact=True)).to_be_visible()
            error = page.locator("#python-source-error")
            expect(error).to_contain_text("Line 1")
            expect(error).to_contain_text("Fix the script here, then Preview it.")
            expect(source).to_have_accessible_description(re.compile("Line 1"))

            dialog = self._publish_dialog(page)
            missing = dialog.get_by_role("list", name="Publishing needs")
            expect(missing.get_by_role("listitem").first).to_have_text(
                "Needs a script that compiles Show the error")
            missing.get_by_role("button", name="Show the error").click()
            expect(source).to_be_focused()

    def test_publish_checks_startup_and_publishes_the_latest_edit_in_one_action(self) -> None:
        """Publish runs its own check; an edit made just before is saved and published."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "host")
            status = self._status_line(page)
            publish = page.get_by_role("button", name="Publish…")
            page.get_by_label("Catalog name").fill("Ping lesson")
            dialog = self._publish_dialog(page)
            expect(dialog).to_have_accessible_description(
                "Publish Ping lesson to the Lab Catalog as a new lab?")
            expect(dialog.get_by_role("button", name="Update existing")).to_have_count(0)
            button = dialog.get_by_role("button", name="Publish", exact=True)
            expect(button).to_be_focused()
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            expect(publish).to_be_focused()
            expect(status.get_by_role("status")).to_have_text("Published Ping lesson in the Lab Catalog.")
            expect(status).to_contain_text("Preview: running")
            examples = page.evaluate("async () => (await (await fetch('/api/examples')).json()).examples")
            self.assertIn("Ping lesson", [example["name"] for example in examples])

            dialog = self._publish_dialog(page)
            expect(dialog).to_have_accessible_description(
                "This revision is already published. Edit the draft to publish it again.")
            expect(dialog.get_by_role("button", name="Close")).to_be_focused()
            page.keyboard.press("Escape")
            expect(publish).to_be_focused()

    def test_publish_asks_new_or_update_once_the_draft_has_published(self) -> None:
        """A draft that published a Lab Example may publish another or update that one."""
        with self.signed_in_page() as page:
            self._preview_host_draft(page)
            page.get_by_label("Catalog name").fill("Ping lesson")
            dialog = self._publish_dialog(page)
            dialog.get_by_role("button", name="Publish", exact=True).click()
            expect(dialog).to_be_hidden()

            page.get_by_role("tab", name="Guide").click()
            page.get_by_label("Guide (Markdown)").fill("# Ping h1 again")
            dialog = self._publish_dialog(page)
            expect(dialog).to_have_accessible_description(
                "Publish Ping lesson as a new lab, or update the one this draft came from?")
            expect(dialog.get_by_role("button", name="Publish as new")).to_be_focused()
            dialog.get_by_role("button", name="Update existing").click()
            expect(dialog).to_be_hidden()
            expect(self._status_line(page).get_by_role("status")).to_have_text(
                "Updated Ping lesson in the Lab Catalog.")
            examples = page.evaluate("async () => (await (await fetch('/api/examples')).json()).examples")
            self.assertEqual([example["name"] for example in examples].count("Ping lesson"), 1)

    def test_publish_lists_a_draft_that_cannot_be_saved_yet(self) -> None:
        """Can't save yet is one of the things publishing needs, with the way to the field."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "transit")
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="NAT", exact=True).click()
            page.locator("#nat-ip").fill("100.64.0.1/24")
            page.locator("#nat-subnet").fill("100.64.0.0/24")
            page.keyboard.press("Escape")
            dialog = self._publish_dialog(page)
            item = dialog.get_by_role("list", name="Publishing needs").get_by_role("listitem").first
            expect(item).to_have_text("Can't save yet: Transit Internet Gateway transit1: "
                                      "Choose at least one downstream Router. Show the field")
            item.get_by_role("button", name="Show the field").click()
            summary = page.locator("#node-dialog-error-summary")
            expect(summary).to_be_focused()
            summary.locator('a[href="#nat-downstream-fields"]').click()
            expect(page.locator("#nat-downstream-fields")).to_be_focused()

    def test_keep_review_shows_a_pool_change_as_a_field_table(self) -> None:
        """A DHCP Pool change reads field · before · after, with what changed said in words."""
        running: dict[str, Any] = {"success": True, "pools": [
            {"family": 4, "cidr": "10.50.0.0/24", "start": "10.50.0.100", "end": "10.50.0.200",
             "interface": "r1-eth0", "lease_time": None, "router": "", "dns_server": "",
             "reservations": []}]}
        pools = patch("mniplab.labhandle.LocalLabHandle._op_get_node_pools",
                      lambda _handle, _node: copy.deepcopy(running))
        pools.start()
        self.addCleanup(pools.stop)
        configs = patch("mniplab.labhandle.LocalLabHandle._op_get_running_config",
                        lambda _handle, node: f"hostname {node}\nend\n")
        configs.start()
        self.addCleanup(configs.stop)
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "router")
            page.keyboard.press("Enter")
            page.locator("#node-tab-services").click()
            page.locator("#dhcp-enabled").check()
            add_dhcp_pool(page, "10.50.0.0/24", "10.50.0.100", "10.50.0.200")
            page.keyboard.press("Escape")
            page.get_by_role("button", name="Preview", exact=True).click()
            status = self._status_line(page)
            expect(status).to_contain_text("Preview: running")

            running["pools"][0].update(end="10.50.0.150", reservations=[
                {"mac": "aa:bb:cc:dd:ee:01", "duid": "", "ip": "10.50.0.50", "hostname": "h1"}])
            # An edit made just before is saved before the review opens.
            page.get_by_label("Catalog name").fill("Pool lesson")
            keep = status.get_by_role("button", name="Keep changes…")
            keep.click()
            dialog = page.get_by_role("dialog", name="Keep changes")
            expect(dialog).to_have_accessible_description("Nothing is kept unless you tick it.")
            box = dialog.get_by_role("checkbox", name="Keep DHCP on r1", exact=True)
            expect(box).to_be_focused()
            dialog.get_by_role("button", name="Show changes on DHCP on r1").click()
            table = dialog.get_by_role("region", name="Changes on DHCP on r1"
                                       ).get_by_role("table")
            expect(table.get_by_role("columnheader")).to_have_text(["Field", "Before", "After"])
            expect(table.get_by_role("rowheader")).to_have_text([
                "Subnet", "Range start", "Range end", "Lease time", "Router", "DNS server",
                "Reservation aa:bb:cc:dd:ee:01"])
            rows = table.get_by_role("row")
            expect(rows.filter(has_text="Range end").get_by_role("cell")).to_have_text(
                ["10.50.0.200", "Changed 10.50.0.150"])
            expect(rows.filter(has_text="Range start").get_by_role("cell")).to_have_text(
                ["10.50.0.100", "10.50.0.100"])
            expect(rows.filter(has_text="Reservation").get_by_role("cell")).to_have_text(
                ["none", "Added 10.50.0.50 · h1"])
            screenshots(page, "redesign-03", "keep-pool")

            box.check()
            dialog.get_by_role("button", name="Keep selected").click()
            expect(dialog).to_be_hidden()
            expect(keep).to_be_focused()
            stored = self._stored(page)
            self.assertEqual(stored["catalog"]["name"], "Pool lesson")
            self.assertEqual(stored["topology"]["nodes"][0]["dhcp"]["pools"][0]["end"], "10.50.0.150")

    def test_copying_a_draft_that_cannot_be_saved_returns_focus_to_its_menu(self) -> None:
        """Copy as new needs the open draft saved; when it cannot be, focus is not lost."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "transit")
            draft_menu(page, "Untitled lab").get_by_role(
                "button", name="Copy as new Untitled lab").click()
            expect(page.get_by_role("button", name="More actions for Untitled lab")).to_be_focused()
            expect(self._status_line(page).get_by_role("status")).to_contain_text("Can't save yet")
            self.assertEqual(len(page.evaluate(
                "async () => (await (await fetch('/api/authoring/drafts')).json()).drafts")), 1)

    def test_a_refused_rebuild_keeps_the_running_preview(self) -> None:
        """When a new Preview is refused, the one still running keeps Open Lab and Stop Preview."""
        with self.signed_in_page() as page:
            self._preview_host_draft(page)
            page.locator("#draft-canvas .node").first.focus()
            page.keyboard.press("Delete")
            dialog = self._publish_dialog(page)
            expect(dialog).to_contain_text("Needs at least one Node")
            expect(dialog.get_by_role("button", name="Publish", exact=True)).to_be_hidden()
            dialog.get_by_role("button", name="Close", exact=True).click()
            status = self._status_line(page)
            expect(status.get_by_role("link", name="Open Lab")).to_have_attribute(
                "href", f"/labs/{self.launcher.children[0].key}")
            expect(page.get_by_role("button", name="Stop Preview")).to_be_visible()
            self.assertFalse(self.launcher.children[0].stopped)

    def test_preview_waits_for_an_inflight_save_response(self) -> None:
        """Repeated Preview activation during a delayed save starts one current Lab."""
        with self.signed_in_page() as page:
            self._open_new_draft(page)
            self._place(page, "host")
            page.clock.run_for(AFTER_PAUSE_MS)
            status = self._status_line(page)
            expect(status).to_contain_text("Draft: Saved")
            before = self._stored(page)
            held: list[tuple[Route, Any]] = []
            previews: list[dict[str, Any]] = []

            def hold_save(route: Route) -> None:
                if route.request.method == "PUT":
                    held.append((route, route.fetch()))
                else:
                    route.continue_()

            def record_preview(request: Any) -> None:
                if request.url.endswith("/preview"):
                    previews.append(request.post_data_json)

            page.route(f"**/api/authoring/drafts/{before['id']}", hold_save)
            page.on("request", record_preview)
            page.get_by_label("Catalog name").fill("Delayed save lesson")
            page.clock.run_for(AFTER_PAUSE_MS)
            deadline = time.monotonic() + 30
            url = f"{self.base_url}/api/authoring/drafts/{before['id']}"
            while page.request.get(url).json()["revision"] != before["revision"] + 1:
                self.assertLess(time.monotonic(), deadline, "save did not persist")
                page.wait_for_timeout(50)
            expect(status).to_contain_text("Draft: Saving…")
            self.assertEqual(len(held), 1)
            self.assertEqual(held[0][1].status, 200)

            toggle = page.get_by_role("button", name="Preview", exact=True)
            toggle.focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.clock.run_for(AFTER_PAUSE_MS)
            expect(status).to_contain_text("Preview: starting…")
            expect(toggle).to_have_attribute("aria-disabled", "true")
            self.assertEqual(previews, [])

            route, response = held.pop()
            route.fulfill(response=response)
            page.unroute(f"**/api/authoring/drafts/{before['id']}", hold_save)
            expect(status).to_contain_text("Preview: running")
            expect(status).to_contain_text("Draft: Saved")
            self.assertEqual(previews, [{"revision": before["revision"] + 1}])
            self.assertTrue(self._stored(page)["preview_valid"])
            expect(page.get_by_role("button", name="Stop Preview")).to_be_focused()

    def test_signing_in_during_preview_startup_keeps_saves_paused(self) -> None:
        """Authentication releases its own hold while Preview keeps its hold."""
        from mniplab.webserver.auth import active_sessions, create_session

        held = HeldLauncher()
        self.addCleanup(held.release.set)
        swap = patch.object(registry.lab_service, "launcher", held)
        swap.start()
        self.addCleanup(swap.stop)
        with self.signed_in_page() as page:
            token = create_session("instructor1", "instructor")
            page.context.add_cookies([
                {"name": "session", "value": token, "url": self.base_url},
            ])
            self._open_new_draft(page)
            self._place(page, "host")
            page.clock.run_for(AFTER_PAUSE_MS)
            expect(self._status_line(page)).to_contain_text("Draft: Saved")
            revision = self._stored(page)["revision"]
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(self._status_line(page)).to_contain_text("Preview: starting…")
            page.get_by_label("Catalog name").fill("Held edit during sign-in")
            active_sessions.pop(token)
            page.locator("#labs-explorer-toggle").click()
            dialog = page.get_by_role("dialog", name="Sign in again")
            expect(dialog).to_be_visible()
            dialog.get_by_label("Password").fill("lab12345")
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            page.clock.run_for(AFTER_PAUSE_MS)
            self.assertEqual(self._stored(page)["revision"], revision)
            expect(self._status_line(page)).to_contain_text("Preview: starting…")
            held.release.set()
            expect(self._status_line(page)).to_contain_text("Preview: running")
            expect(self._status_line(page)).to_contain_text("Draft: Saved")
            self.assertEqual(self._stored(page)["catalog"]["name"], "Held edit during sign-in")
