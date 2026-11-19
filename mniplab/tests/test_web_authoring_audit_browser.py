"""Rendered WCAG checks across the combined authoring workflow."""

import copy
import re
import time
from typing import Any

import pytest

from mniplab.tests.authoring_editors import (
    RESOLVER,
    ClassroomBrowserFixture,
    SeededDraftMixin,
    activate,
    audit,
    enter_text,
    reach,
)
from mniplab.tests.browser import Route, expect

pytestmark = pytest.mark.browser_authoring


class AuthoringWorkflowAuditTests(SeededDraftMixin, ClassroomBrowserFixture):
    """Check browser observations at desktop, tablet, phone and 320 CSS pixels."""

    def test_combined_editors_reflow_and_keep_readable_unobscured_focus(self) -> None:
        """Inspect the shell, dialogs, structured editors, Guide and Python views."""
        page = self.session_page("instructor1", "instructor")
        self._seed(
            page,
            [
                (
                    "r1",
                    300,
                    300,
                    "router",
                    {
                        "dhcp": {
                            "pools": [
                                {
                                    "cidr": "10.50.0.0/24",
                                    "start": "10.50.0.100",
                                    "end": "10.50.0.200",
                                    "lease_time": 120,
                                    "preferred_lifetime": None,
                                    "router": "10.50.0.1",
                                    "dns_server": "",
                                    "reservations": [],
                                }
                            ]
                        },
                        "dns": {
                            "zones": [
                                {
                                    "kind": "primary",
                                    "name": "example.com.",
                                    "default_ttl": 300,
                                    "records": [
                                        {
                                            "name": "web",
                                            "type": "A",
                                            "rdata": "10.50.0.2",
                                            "ttl": 300,
                                        }
                                    ],
                                    "dnssec": True,
                                }
                            ]
                        },
                        "resolver": copy.deepcopy(RESOLVER),
                        "relay": {
                            "subnets": ["10.20.0.0/24"],
                            "servers": ["10.50.0.2"],
                        },
                    },
                ),
                ("h1", 600, 300, "host", {}),
                ("ex1", 600, 600, "speaker", {}),
            ],
            [("r1", "h1", {"ipv4": "10.50.0.1/24"}, {"ipv4": "10.50.0.2/24"})],
        )
        reach(page, page.get_by_label("Catalog name"))
        audit(self, page, "shell", check_focus=True)
        activate(page, page.get_by_role("button", name="Labs", exact=True))
        audit(self, page, "labs-explorer", check_focus=True)
        activate(page, page.get_by_role("button", name="More actions for Untitled lab"))
        audit(self, page, "draft-menu", check_focus=True)
        page.keyboard.press("Escape")
        enter_text(page, page.get_by_label("Search labs"), "Static Routing Lab")
        activate(page, page.get_by_role("button", name="Static Routing Lab", exact=True))
        audit(self, page, "catalog-row", check_focus=True)
        activate(page, page.get_by_role("button", name="Labs", exact=True))
        reach(page, page.get_by_role("button", name=re.compile("Router r1 at")))
        page.keyboard.press("Enter")
        activate(page, page.get_by_role("tab", name="Services", exact=True))
        activate(page, page.locator("#dhcp-pools-rows summary").first)
        reach(page, page.get_by_label("Pool 1 Subnet"))
        audit(self, page, "dhcp-rows", check_focus=True)
        activate(page, page.locator("#dns-zones-rows summary").first)
        reach(page, page.get_by_label("Zone 1 Record 1 Owner"))
        audit(self, page, "dns-rows", check_focus=True)
        reach(page, page.get_by_label("Access rule 1 Subnet"))
        audit(self, page, "resolver-rows", check_focus=True)
        tip = page.get_by_role("button", name="About Relayed Subnets", exact=True)
        activate(page, tip)
        expect(tip).to_have_attribute("aria-expanded", "true")
        audit(self, page, "relay-tip", check_focus=True)
        page.keyboard.press("Escape")
        # The remaining Node and Link tabs share the same dialog frame.
        activate(
            page, page.get_by_role("button", name=re.compile("BGP Speaker ex1 at"))
        )
        activate(page, page.get_by_role("tab", name="Speaker", exact=True))
        activate(page, page.locator("#speaker-neighbors-rows summary").first)
        reach(page, page.get_by_label("Neighbor 1 Peer", exact=True))
        audit(self, page, "speaker-rows", check_focus=True)
        page.keyboard.press("Escape")
        reach(page, page.get_by_role("button", name=re.compile("Router r1 at")))
        selection = page.get_by_role("region", name="Selection")
        activate(page, selection.get_by_role("button", name="Connect…"))
        audit(self, page, "connect")
        page.keyboard.press("Escape")
        reach(page, page.get_by_role("button", name=re.compile("Router r1 at")))
        page.keyboard.press("Enter")
        activate(page, page.get_by_role("tab", name="Interfaces", exact=True))
        reach(page, page.get_by_label("IPv4 address and prefix").first)
        audit(self, page, "interfaces", check_focus=True)
        page.keyboard.press("Escape")
        activate(page, selection.get_by_role("button", name="Addresses…"))
        popover = page.get_by_role("dialog", name=re.compile("Addresses of"))
        reach(page, popover.get_by_label("IPv4 address and prefix").first)
        audit(self, page, "addresses", check_focus=True)
        page.keyboard.press("Escape")
        reach(page, page.get_by_role("button", name=re.compile("Router r1 at")))
        activate(page, selection.get_by_role("button", name=re.compile("r1-eth0")))
        page.keyboard.press("Enter")
        reach(page, page.get_by_role("combobox", name="Interface").first)
        audit(self, page, "link", check_focus=True)
        page.keyboard.press("Escape")
        activate(page, page.get_by_role("button", name="Publish…"))
        expect(page.get_by_role("dialog", name="Publish", exact=True)).to_contain_text(
            "Publish starts a check Lab"
        )
        audit(self, page, "publish-readiness", check_focus=True)
        page.keyboard.press("Escape")
        activate(page, page.get_by_role("tab", name="Guide", exact=True))
        activate(page, page.get_by_role("button", name="Expand", exact=True))
        reach(page, page.get_by_label("Guide Markdown"))
        audit(self, page, "guide", check_focus=True)
        page.keyboard.press("Escape")
        activate(page, page.get_by_role("button", name="Script", exact=True))
        script = page.get_by_role("textbox", name="Lab script", exact=True)
        reach(page, script)
        audit(self, page, "script-pane", check_focus=True)
        page.keyboard.type("net.addRouter")
        activate(page, page.get_by_role("button", name="Show API help", exact=True))
        expect(
            page.get_by_role("region", name="Mininet-IPLab API help")
        ).to_be_visible()
        audit(self, page, "python-api-help", check_focus=True)

    def test_keep_publication_assignment_and_learner_surfaces(self) -> None:
        """Audit the action dialogs and the published Learner view at every size/theme."""
        page = self.session_page("instructor1", "instructor")
        self._seed(
            page,
            [("r1", 300, 300, "router", {}), ("h1", 600, 300, "host", {})],
            [("r1", "h1", {"ipv4": "10.50.0.1/24"}, {"ipv4": "10.50.0.2/24"})],
        )
        enter_text(page, page.get_by_label("Catalog name"), "Audit classroom")
        activate(page, page.get_by_role("tab", name="Guide", exact=True))
        enter_text(
            page,
            page.get_by_label("Guide (Markdown)"),
            "# Readable lesson\nUse the Topology and Tools.",
        )
        self.saved(page)
        self.preview(page)
        self.conditions[:] = [
            {
                "node1": "r1",
                "node2": "h1",
                "status": "up",
                "delay": "5ms",
                "jitter": None,
                "loss": None,
                "bandwidth": None,
                "scheduler": "hfsc",
            }
        ]
        activate(page, page.get_by_role("button", name="Keep changes…"))
        dialog = page.get_by_role("dialog", name="Keep changes")
        reach(page, dialog.get_by_role("checkbox", name=re.compile("^Keep Link")))
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Enter")
        expect(dialog.get_by_role("columnheader")).to_contain_text(
            ["Field", "Before", "After"]
        )
        audit(self, page, "keep", check_focus=True)
        page.keyboard.press("Escape")
        activate(page, page.get_by_role("button", name="Stop Preview"))
        dialog = page.get_by_role("alertdialog", name="Stop Preview")
        expect(dialog).to_have_accessible_description(
            re.compile("1 change not yet kept will be lost")
        )
        audit(self, page, "keep-loss")
        activate(page, dialog.get_by_role("button", name="Discard and continue"))
        self.conditions.clear()
        self.preview(page)
        activate(page, page.get_by_role("button", name="Publish…"))
        publish = page.get_by_role("dialog", name="Publish", exact=True)
        reach(page, publish.get_by_role("button", name="Publish", exact=True))
        audit(self, page, "publish", check_focus=True)
        activate(page, publish.get_by_role("button", name="Publish", exact=True))
        expect(publish).to_be_hidden()
        activate(page, page.get_by_role("button", name="Stop Preview"))
        expect(page.get_by_role("region", name="Status")).to_contain_text(
            "Preview: not running"
        )
        activate(page, page.get_by_role("link", name="Return to Labs"))
        activate(page, page.get_by_role("button", name="Inspect Group learner1", exact=True))
        reach(
            page,
            page.get_by_role("button", name="Edit Assignment of learner1", exact=True),
        )
        audit(self, page, "instructor-groups", check_focus=True)
        activate(
            page,
            page.get_by_role("button", name="Edit Assignment of learner1", exact=True),
        )
        assignment = page.get_by_role("dialog", name="Assignment for learner1")
        choice = assignment.get_by_role("checkbox", name=re.compile("Audit classroom"))
        reach(page, choice)
        audit(self, page, "assignment", check_focus=True)
        page.keyboard.press("Space")
        activate(page, assignment.get_by_role("button", name="Save Assignment"))
        learner = self.session_page("learner1", "learner")
        reach(
            learner,
            learner.get_by_role("button", name="Start Audit classroom", exact=True),
        )
        audit(self, learner, "learner-assignment", check_focus=True)
        activate(
            learner,
            learner.get_by_role("button", name="Start Audit classroom", exact=True),
        )
        learner.wait_for_selector("#stop-lab-btn:visible")
        expect(learner.get_by_role("region", name="Guide", exact=True)).to_be_visible()
        expect(
            learner.get_by_role("article", name="Lab guide content")
        ).to_contain_text("Readable lesson")
        expect(learner.get_by_role("button").get_by_role("img")).to_have_count(0)
        learner.get_by_role("button", name="Open in New Tab (lab guide)").focus()
        audit(self, learner, "learner-guide", check_focus=True)

    def test_pending_save_preview_and_shared_draft_conflict_are_announced(self) -> None:
        """Audit delayed-save busy state and the alert/Reopen route for a stale edit."""
        page = self.session_page("instructor1", "instructor")
        self._seed(page, [("r1", 300, 300, "router", {})])
        before = self.saved(page)
        url = f"{self.base_url}/api/authoring/drafts/{before['id']}"
        held: list[tuple[Route, Any]] = []

        def hold_save(route: Route) -> None:
            if route.request.method == "PUT":
                held.append((route, route.fetch()))
            else:
                route.continue_()

        page.route(url, hold_save)
        enter_text(page, page.get_by_label("Catalog name"), "Delayed classroom")
        deadline = time.monotonic() + 30
        while page.request.get(url).json()["revision"] <= before["revision"]:
            self.assertLess(time.monotonic(), deadline, "save did not persist")
            page.wait_for_timeout(50)
        activate(page, page.get_by_role("button", name="Preview", exact=True))
        toggle = page.get_by_role("button", name="Preview", exact=True)
        expect(toggle).to_have_attribute("aria-disabled", "true")
        expect(page.get_by_role("region", name="Status")).to_contain_text(
            "Draft: Saving…"
        )
        expect(page.get_by_role("region", name="Status")).to_contain_text(
            "Preview: starting…"
        )
        audit(self, page, "save-preview-pending", check_focus=True)
        self.assertEqual(len(held), 1)
        route, response = held.pop()
        route.fulfill(response=response)
        page.unroute(url, hold_save)
        expect(page.get_by_role("region", name="Status")).to_contain_text(
            "Preview: running"
        )
        current = self.saved(page)
        newer = copy.deepcopy(current)
        newer["guide"] = "Another Instructor's saved lesson"
        saved = page.request.put(
            url, data={"revision": current["revision"], "document": newer}
        )
        self.assertEqual(saved.status, 200, saved.text())
        enter_text(page, page.get_by_label("Catalog name"), "Stale local edit")
        alert = page.get_by_role("region", name="Status").get_by_role("alert")
        expect(alert).to_contain_text("Someone else changed this draft")
        reopen = alert.get_by_role("button", name="Reopen", exact=True)
        reach(page, reopen)
        audit(self, page, "conflict", check_focus=True)
        activate(page, reopen)
        reopened = self.saved(page)
        self.assertEqual(reopened["guide"], newer["guide"])
        self.assertEqual(reopened["catalog"]["name"], "Delayed classroom")
