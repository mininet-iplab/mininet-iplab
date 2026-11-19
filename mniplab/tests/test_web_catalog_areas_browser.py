"""Areas group the Catalog and a Group's Assignment, under search."""

import os
from unittest.mock import patch

import pytest

from mniplab.tests.browser import expect
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import registry
from mniplab.webserver.repositories import load_labs_manifest
from mniplab.webserver.config import settings

pytestmark = pytest.mark.browser_classroom


class CatalogAreasTests(MultiLabBrowserCase):
    """Both Labs lists use headed Areas in teaching order on one server."""

    def setUp(self) -> None:
        super().setUp()
        storage = patch.dict(os.environ, {
            "MNIPLAB_AUTHORED_LABS_DIR": os.path.join(self.tmp.name, "authoring"),
        })
        storage.start()
        self.addCleanup(storage.stop)

    def test_areas_group_the_sidebar_and_only_the_groups_assignment(self) -> None:
        settings.lab_mode = "single"
        self.addCleanup(setattr, settings, "lab_mode", "multi")
        page = self.signed_in_as("instructor1", "instructor")
        sidebar = page.locator("#sidebar-lab-list")
        expect(sidebar.get_by_role("heading").first).to_have_text("Static")
        self.assertEqual(sidebar.get_by_role("heading").all_inner_texts(), [
            "Static", "Intra-domain", "Inter-domain", "Data Center", "Switching", "NAT", "DNS", "DHCP",
        ])
        # BGP spans Inter-domain and Data Center; searching hides every other Area.
        page.get_by_role("searchbox", name="Search labs").fill("BGP")
        expect(sidebar.get_by_role("heading")).to_have_text(["Inter-domain", "Data Center"])
        expect(sidebar.get_by_role("group", name="Inter-domain", exact=True)).to_be_visible()
        expect(sidebar.get_by_role("group", name="Data Center", exact=True)).to_be_visible()

        legacy = {"id": "authored-" + "a" * 32, "name": "Earlier lesson", "authored": True}
        with patch("mniplab.webserver.api.load_labs_manifest", return_value=load_labs_manifest() + [legacy]):
            page.reload()
            expect(sidebar.get_by_role("heading").last).to_have_text("Other")
            expect(sidebar.get_by_role("group", name="Other", exact=True)).to_contain_text("Earlier lesson")

        page.goto(self.base_url + "/authoring?adapt=static-lab")
        page.get_by_role("button", name="Details", exact=True).click()
        area = page.get_by_role("combobox", name="Area", exact=False)
        expect(area).to_have_value("Static")
        choices = area.locator("option").all_text_contents()
        self.assertNotIn("Other", choices)
        self.assertIn("SP Transport", choices)
        self.assertIn("Multicast", choices)
        with page.expect_response(lambda response: response.request.method == "PUT"
                                  and "/api/authoring/drafts/" in response.url):
            area.select_option("DNS")
        expect(page.locator("#status-draft-state")).to_have_text("Saved")
        page.reload()
        page.get_by_role("button", name="Details", exact=True).click()
        expect(page.get_by_role("combobox", name="Area", exact=False)).to_have_value("DNS")

        settings.lab_mode = "multi"
        registry.groups.assign("learner1", ["dns-lab", "static-lab-nat", "static-lab", "ospf-lab"])
        learner = self.signed_in_as("learner1", "learner")
        cards = learner.locator("#start-screen-cards")
        expect(cards.get_by_role("heading", name="Static", exact=True)).to_be_visible()
        self.assertEqual(cards.locator(".catalog-area-heading").all_inner_texts(), [
            "Static", "Intra-domain", "DNS",
        ])
        # Assignment order does not reorder the Catalog within an Area.
        catalog = learner.request.get(self.base_url + "/api/examples").json()["examples"]
        expected_names = [row["name"] for row in catalog if row["area"] == "Static"]
        self.assertEqual(cards.get_by_role("group", name="Static", exact=True).locator(
            ".catalog-card-name"
        ).all_inner_texts(), expected_names)
        self.assertEqual(cards.locator(".start-screen-card").count(), 4)
