"""A keyboard classroom journey, with fake and real Preview/Group Labs."""

import re
import subprocess
import time

import pytest

from mniplab.tests.authoring_editors import (
    ClassroomBrowserFixture, ClassroomFixture, activate, enter_text, reach,
)
from mniplab.tests.browser import Page, expect


class ClassroomJourney(ClassroomFixture):
    """Classroom actions at the browser seam; the classes below choose the Labs."""

    __test__ = False

    def test_instructor_journey_keeps_a_learners_published_lesson_running(self) -> None:
        """Create/adapt/reopen/Keep/Publish/assign/revise using only keys."""
        instructor = self.session_page("instructor1", "instructor")
        activate(
            instructor, instructor.get_by_role("link", name="Lab Authoring", exact=True)
        )
        instructor.wait_for_url("**/authoring")
        activate(
            instructor, instructor.get_by_role("button", name="New draft", exact=True)
        )
        activate(instructor, instructor.get_by_role("button", name="Details", exact=True))
        activate(
            instructor, instructor.get_by_role("button", name="Add Host", exact=True)
        )
        activate(
            instructor, instructor.get_by_role("button", name="Add Router", exact=True)
        )
        selection = instructor.get_by_role("region", name="Selection")
        activate(instructor, selection.get_by_role("button", name="Connect…"))
        instructor.keyboard.press("Enter")
        expect(selection).to_contain_text("no address yet")
        for node, address in (("Router r1 at", "192.0.2.1/24"), ("Host h1 at", "192.0.2.2/24")):
            activate(instructor, instructor.get_by_role("button", name=re.compile(node)))
            activate(instructor, instructor.get_by_role("tab", name="Interfaces", exact=True))
            enter_text(
                instructor,
                instructor.get_by_label("IPv4 address and prefix").first,
                address,
            )
            instructor.keyboard.press("Escape")
        enter_text(
            instructor, instructor.get_by_label("Catalog name"), "New classroom lesson"
        )
        new_draft = self.saved(instructor)
        self.assertEqual(len(new_draft["topology"]["nodes"]), 2)
        self.assertEqual(len(new_draft["topology"]["links"]), 1)
        # Close the page, then reopen through the shared Labs explorer.
        instructor.close()
        instructor = self.session_page("instructor1", "instructor")
        activate(
            instructor, instructor.get_by_role("link", name="Lab Authoring", exact=True)
        )
        expect(instructor.get_by_role("region", name="Status")).to_contain_text(
            "Draft: Saved"
        )
        activate(instructor, instructor.get_by_role("button", name="Labs", exact=True))
        activate(
            instructor,
            instructor.get_by_role(
                "button", name="Open New classroom lesson", exact=True
            ),
        )
        self.assertEqual(self.saved(instructor)["topology"], new_draft["topology"])
        activate(instructor, instructor.get_by_role("button", name="Labs", exact=True))
        enter_text(
            instructor, instructor.get_by_label("Search labs"), "Static Routing"
        )
        activate(
            instructor,
            instructor.get_by_role("button", name="Static Routing Lab", exact=True),
        )
        activate(
            instructor,
            instructor.get_by_role(
                "button",
                name="Start from Canvas version of Static Routing Lab",
                exact=True,
            ),
        )
        activate(instructor, instructor.get_by_role("button", name="Details", exact=True))
        enter_text(
            instructor, instructor.get_by_label("Catalog name"), "Classroom routing"
        )
        # Make the classroom's route intent explicit through the Seed editor.
        for name, route in (
            ("r1", "ip route 192.168.2.0/24 10.10.1.2"),
            ("r2", "ip route 192.168.1.0/24 10.10.1.1"),
        ):
            activate(
                instructor,
                instructor.get_by_role("button", name=re.compile(f"Router {name} at")),
            )
            activate(
                instructor, instructor.get_by_role("tab", name="Routing", exact=True)
            )
            config = instructor.get_by_label("FRR configuration", exact=True)
            enter_text(
                instructor,
                config,
                "\n".join(
                    line
                    for line in config.input_value().splitlines()
                    if line.strip() != "end"
                )
                + "\n"
                + route
                + "\n",
            )
            instructor.keyboard.press("Escape")
        activate(instructor, instructor.get_by_role("tab", name="Guide", exact=True))
        enter_text(
            instructor,
            instructor.get_by_label("Guide (Markdown)"),
            "# First classroom sitting\nPing h2 from h1.",
        )
        self.saved(instructor)
        self.preview(instructor)
        self.change_preview(instructor)
        activate(instructor, instructor.get_by_role("button", name="Keep changes…"))
        keep = instructor.get_by_role("dialog", name="Keep changes")
        boxes = keep.get_by_role("checkbox", name=re.compile("^Keep Link"))
        expect(boxes).to_have_count(2)
        expect(boxes.nth(0)).not_to_be_checked()
        expect(boxes.nth(1)).not_to_be_checked()
        reach(instructor, boxes.nth(0))
        instructor.keyboard.press("Space")
        activate(instructor, keep.get_by_role("button", name="Keep selected"))
        expect(keep).to_be_hidden()
        after = self.saved(instructor)
        links = {link["id"]: link for link in after["topology"]["links"]}
        self.assertEqual(links["h1-r1"]["condition"]["delay"], "5ms")
        self.assertNotIn("condition", links["h2-r2"])
        expect(instructor.get_by_role("region", name="Status")).to_contain_text(
            "Preview: out of date"
        )
        # Stop with the remaining change's loss warning, then Preview the recipe.
        activate(instructor, instructor.get_by_role("button", name="Stop Preview"))
        keep = instructor.get_by_role("alertdialog", name="Stop Preview")
        expect(keep).to_have_accessible_description(
            re.compile("1 change not yet kept will be lost")
        )
        activate(instructor, keep.get_by_role("button", name="Discard and continue"))
        self.conditions.clear()
        if not self.real_labs:
            self.conditions.append(
                {"node1": "h1", "node2": "r1", **links["h1-r1"]["condition"]}
            )
        self.preview(instructor)
        self.publish(instructor)
        published = self.saved(instructor)
        example_id = published["catalog"]["id"]
        activate(instructor, instructor.get_by_role("button", name="Stop Preview"))
        instructor.wait_for_selector("#status-preview-state:text-is('not running')")
        activate(instructor, instructor.get_by_role("link", name="Return to Labs"))
        activate(instructor, instructor.get_by_role("button", name="Inspect Group learner1", exact=True))
        group = instructor.get_by_role("article", name="Group learner1", exact=True)
        activate(
            instructor, group.get_by_role("button", name="Edit Assignment of learner1")
        )
        assignment = instructor.get_by_role("dialog", name="Assignment for learner1")
        box = assignment.get_by_role("checkbox", name=re.compile("Classroom routing"))
        reach(instructor, box)
        instructor.keyboard.press("Space")
        activate(instructor, assignment.get_by_role("button", name="Save Assignment"))
        learner = self.session_page("learner1", "learner")
        expect(
            learner.get_by_role("link", name="Lab Authoring", exact=True)
        ).to_have_count(0)
        activate(
            learner,
            learner.get_by_role("button", name="Start Classroom routing", exact=True),
        )
        learner.wait_for_selector("#stop-lab-btn:visible")
        group_url = learner.url
        self.use_lesson(learner)
        activate(
            instructor, instructor.get_by_role("link", name="Lab Authoring", exact=True)
        )
        # Wait for the restored draft before using the page's bound actions.
        expect(instructor.get_by_label("Catalog name")).to_have_value("Classroom routing")
        activate(instructor, instructor.get_by_role("button", name="Labs", exact=True))
        enter_text(
            instructor,
            instructor.get_by_label("Search labs"),
            "Classroom routing",
        )
        activate(
            instructor,
            instructor.get_by_role("button", name="Classroom routing", exact=True),
        )
        activate(
            instructor,
            instructor.get_by_role(
                "button",
                name="Revise Classroom routing",
                exact=True,
            ),
        )
        activate(instructor, instructor.get_by_role("button", name="Details", exact=True))
        activate(instructor, instructor.get_by_role("tab", name="Guide", exact=True))
        enter_text(
            instructor,
            instructor.get_by_label("Guide (Markdown)"),
            "# Second classroom sitting\nA revised lesson.",
        )
        self.saved(instructor)
        self.preview(instructor)
        self.publish(instructor, update=True)
        updated = self.saved(instructor)
        self.assertEqual(updated["catalog"]["id"], example_id)
        current = instructor.request.get(
            f"{self.base_url}/api/load_guide?example={example_id}"
        )
        self.assertIn("Second classroom sitting", current.text())
        self.assertEqual(learner.url, group_url)
        learner.reload()
        learner.wait_for_selector("#stop-lab-btn:visible")
        self.use_lesson(learner)
        self.assertEqual(learner.url, group_url)
        expect(
            learner.get_by_role("article", name="Lab guide content")
        ).not_to_contain_text("Second classroom sitting")


class ClassroomJourneyTests(ClassroomJourney, ClassroomBrowserFixture):
    """The keyboard journey with fake Lab processes, in the browser tier."""

    pytestmark = [pytest.mark.browser_authoring]

    __test__ = True


class RealClassroomJourneyTests(ClassroomJourney):
    """The same keyboard journey, with actual isolated Preview and Group Labs.

    Built on the unmarked fixture: it starts real Labs, so it belongs to the
    privileged tier alone.
    """

    pytestmark = [
        pytest.mark.requires_browser,
        pytest.mark.integration,
        pytest.mark.requires_root,
    ]
    __test__ = True
    real_labs = True

    def change_preview(self, page: Page) -> None:
        """Edit two live Link Conditions through the Preview's browser page.

        Args:
            page: Authoring page linking to its real Preview Lab.
        """
        tabs = len(page.context.pages)
        activate(page, page.get_by_role("link", name="Open Lab", exact=True))
        page.wait_for_url("**/labs/*")
        self.assertEqual(len(page.context.pages), tabs)
        running = page
        for host, router, delay in (("h1", "r1", "5"), ("h2", "r2", "7")):
            activate(
                running,
                running.get_by_role(
                    "button",
                    name=re.compile(
                        f"Link between {host} and {router}|Link between {router} and {host}"
                    ),
                ),
            )
            activate(
                running,
                running.get_by_role("menuitem", name="Configure link", exact=True),
            )
            dialog = running.get_by_role("dialog", name="Configure Link", exact=True)
            enter_text(running, dialog.get_by_label("Delay (ms):"), delay)
            activate(running, dialog.get_by_role("button", name="Apply Configuration"))
            expect(dialog).to_be_hidden()
        activate(running, running.get_by_role("link", name="Lab Authoring", exact=True))
        page.wait_for_url("**/authoring")
        expect(page.get_by_role("region", name="Status")).to_contain_text("Preview: running")
        details = page.get_by_role("button", name="Details", exact=True)
        if details.get_attribute("aria-expanded") == "false":
            activate(page, details)

    def use_lesson(self, page: Page) -> None:
        """Read the immutable Guide and verify real routed host connectivity.

        Args:
            page: Learner page running the published real Lab.
        """
        super().use_lesson(page)
        # Poll resolved HTTP results: async wait_for_function predicates return
        # a truthy Promise before their boolean result is available.
        key = page.url.rsplit("/", 1)[-1]
        deadline = time.monotonic() + 30
        while True:
            routes = [
                page.request.get(
                    f"{self.base_url}/api/labs/{key}/routing/routes/{node}"
                ).json()
                for node in ("r1", "r2")
            ]
            if all(
                result.get("success")
                and any(
                    line.startswith(prefix + " ") and " via " in line
                    for line in result["output"].splitlines()
                )
                for result, prefix in zip(routes, ("192.168.2.0/24", "192.168.1.0/24"))
            ):
                break
            self.assertLess(time.monotonic(), deadline, routes)
            page.wait_for_timeout(100)
        activate(page, page.get_by_role("button", name="Tools", exact=True))
        dialog = page.get_by_role("dialog", name="Network Tools")
        source = dialog.get_by_role("tabpanel", name="Ping", exact=True).get_by_label(
            "Source:", exact=True
        )
        reach(page, source)
        page.keyboard.press("Home")
        for _ in range(8):
            if source.input_value() == "h1":
                break
            page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        dest = dialog.get_by_role("tabpanel", name="Ping", exact=True).get_by_label(
            "Destination:", exact=True
        )
        reach(page, dest)
        page.keyboard.press("Home")
        for _ in range(8):
            if dest.input_value() == "h2":
                break
            page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        self.assertEqual(source.input_value(), "h1")
        self.assertEqual(dest.input_value(), "h2")
        enter_text(page, dialog.get_by_label("Packet Count:"), "1")
        activate(page, dialog.get_by_role("button", name="Run Ping"))
        page.wait_for_selector("#ping-output:text-matches('packet loss')")
        expect(page.locator("#ping-output")).to_contain_text("1 received")
        page.keyboard.press("Escape")

    def test_exact_shipped_copy_publishes_an_empty_routing_exercise(self) -> None:
        """Clear actual shipped Seeds in the browser and configure the Group's FRR."""
        from pathlib import Path
        from mniplab.tests.labs import REPO_ROOT
        from mniplab.webserver import registry

        instructor = self.session_page("instructor1", "instructor")
        activate(instructor, instructor.get_by_role("link", name="Lab Authoring", exact=True))
        instructor.wait_for_url("**/authoring")
        enter_text(instructor, instructor.get_by_label("Search labs"), "Static Routing")
        activate(instructor, instructor.get_by_role("button", name="Static Routing Lab", exact=True))
        activate(instructor, instructor.get_by_role("button", name="Copy Static Routing Lab", exact=True))
        original = self.saved(instructor)
        self.assertEqual(original["kind"], "python")
        self.assertEqual(original["python"], Path(REPO_ROOT, "examples/static-lab.py").read_text())
        for name in ("r1", "r2"):
            path = f"frr-config/static-lab/{name}/frr.conf"
            activate(instructor, instructor.get_by_role("tab", name=f"static-lab/{name}/frr.conf", exact=True))
            empty = "\n".join(line for line in original["files"][path].splitlines()
                              if not line.startswith("ip route ")) + "\n"
            enter_text(instructor, instructor.get_by_role("textbox", name=f"Seed file {path}"), empty)
            self.saved(instructor)
        activate(instructor, instructor.get_by_role("button", name="Details", exact=True))
        enter_text(instructor, instructor.get_by_label("Catalog name"), "Empty classroom exercise")
        saved = self.saved(instructor)
        self.assertEqual(saved["python"], original["python"])
        self.preview(instructor)
        self.publish(instructor)
        example = self.saved(instructor)["catalog"]["id"]
        self.assertNotEqual(example, "static-lab")
        activate(instructor, instructor.get_by_role("button", name="Stop Preview"))
        instructor.wait_for_selector("#status-preview-state:text-is('not running')")
        assigned = instructor.request.put(f"{self.base_url}/api/admin/assignments/learner1",
                                         data={"example_ids": [example]})
        self.assertEqual(assigned.status, 200, assigned.text())
        learner = self.session_page("learner1", "learner")
        activate(learner, learner.get_by_role("button", name="Start Empty classroom exercise", exact=True))
        learner.wait_for_selector("#stop-lab-btn:visible")
        key = learner.url.rsplit("/", 1)[-1]
        routes = learner.request.get(f"{self.base_url}/api/labs/{key}/routing/routes/r1").json()
        self.assertIn("192.168.1.0/24", routes["output"])
        self.assertNotIn("192.168.2.0/24", routes["output"])
        self.assertNotIn("ip route 192.168.2.0/24",
                         registry.lab_service.get(key).handle.get_running_config("r1"))
        attachment = registry.lab_service.get(key).handle.node_attachment("r1")
        edited = subprocess.run(["mnexec", "-a", str(attachment.pid), "vtysh", "-c",
                                 "configure terminal", "-c", "ip route 192.168.2.0/24 10.10.1.2"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        self.assertIn("ip route 192.168.2.0/24 10.10.1.2",
                      registry.lab_service.get(key).handle.get_running_config("r1"))
        refused = learner.request.get(f"{self.base_url}/api/authoring/drafts")
        self.assertEqual(refused.status, 403)
        instructor.screenshot(path="/tmp/authoring14-empty-copy-published.png", full_page=True)
        learner.screenshot(path="/tmp/authoring14-empty-copy-group.png", full_page=True)
