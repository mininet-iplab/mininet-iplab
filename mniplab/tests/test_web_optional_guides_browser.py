"""Optional Guides produce neutral UI and no speculative failed requests."""

from unittest.mock import patch

import pytest

from mniplab.tests.browser import expect
from mniplab.tests.authoring_editors import audit
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver.config import settings
from mniplab.webserver.repositories import GuideRepository

pytestmark = pytest.mark.browser_classroom


class OptionalGuideBrowserTests(MultiLabBrowserCase):
    """Missing Guides, real failures, and direct Guide navigation stay distinct."""

    def setUp(self) -> None:
        super().setUp()
        shipped = GuideRepository.available

        def without_dc_guide(example, *args, **kwargs):
            # Every shipped Example has a Guide now; pretend one has none.
            return False if example == "bgp-dc-lab" else shipped(example, *args, **kwargs)

        patcher = patch.object(GuideRepository, "available", side_effect=without_dc_guide)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_guide_in_single_and_multi_lab_modes_makes_no_guide_request(
        self,
    ) -> None:
        for mode in ["single", "multi"]:
            with self.subTest(mode=mode), patch.object(settings, "lab_mode", mode):
                page = self.signed_in_as("instructor1", "instructor")
                requests = []
                page.on(
                    "request",
                    lambda request: (
                        requests.append(request.url)
                        if "/api/load_guide" in request.url
                        else None
                    ),
                )
                result = page.request.post(
                    self.base_url + "/api/labs",
                    data={"example_id": "bgp-dc-lab", "group": "instructor1"},
                )
                self.assertTrue(result.ok)
                key = result.json()["key"]
                try:
                    page.reload()
                    if mode == "multi":
                        self.select_group(page, "instructor1")
                    expect(page.get_by_role("region", name="Guide", exact=True)).to_be_visible()
                    expect(page.locator("#lab-guide-status")).to_have_text(
                        "No Guide provided for this Lab."
                    )
                    expect(page.locator("#lab-guide-open-btn")).to_be_hidden()
                    self.assertEqual(requests, [])
                    page.keyboard.press("Tab")
                    page.get_by_role("button", name="Open view and layout options").focus()
                    audit(self, page, "optional-guide-" + mode, check_focus=True)
                    if mode == "multi":
                        page.get_by_role("tab", name="Overview", exact=True).click()
                        page.get_by_role(
                            "button", name="Inspect Group instructor1", exact=True
                        ).click()
                        expect(page.locator("a.group-card-lab")).to_have_count(0)
                        expect(page.locator(".group-card-lab")).to_have_text(
                            "BGP Data Center Lab"
                        )
                    page.goto(self.base_url + f"/guide?example=bgp-dc-lab&key={key}")
                    expect(page.locator("#guide-status")).to_have_text(
                        "No Guide provided for this Lab."
                    )
                    self.assertEqual(requests, [])
                finally:
                    self.assertTrue(
                        page.request.post(self.base_url + f"/api/labs/{key}/stop").ok
                    )
                    page.context.close()

    def test_direct_guide_page_skips_a_known_absent_guide(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        requests = []
        page.on(
            "request",
            lambda request: (
                requests.append(request.url)
                if "/api/load_guide" in request.url
                else None
            ),
        )
        page.goto(self.base_url + "/guide?example=bgp-dc-lab")
        expect(page.locator("#guide-status")).to_have_text(
            "No Guide provided for this Lab."
        )
        self.assertEqual(requests, [])

    def test_existing_guide_server_errors_are_visible(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        page.route("**/api/load_guide?*", lambda route: route.fulfill(status=500))
        result = page.request.post(
            self.base_url + "/api/labs", data={"example_id": "static-lab"}
        )
        self.assertTrue(result.ok)
        page.reload()
        self.select_group(page, "instructor1")
        expect(page.get_by_role("region", name="Guide", exact=True)).to_be_visible()
        expect(page.locator("#lab-guide-status")).to_have_text(
            "Failed to load Guide. Please try again."
        )
        expect(page.locator("#lab-guide-open-btn")).to_be_hidden()

    def test_a_guide_that_becomes_blank_has_no_open_guide_link(self) -> None:
        page = self.signed_in_as("instructor1", "instructor")
        page.route(
            "**/api/load_guide?*", lambda route: route.fulfill(status=200, body=" \n")
        )
        result = page.request.post(
            self.base_url + "/api/labs", data={"example_id": "static-lab"}
        )
        self.assertTrue(result.ok)
        page.reload()
        self.select_group(page, "instructor1")
        expect(page.get_by_role("region", name="Guide", exact=True)).to_be_visible()
        expect(page.locator("#lab-guide-status")).to_have_text(
            "No Guide provided for this Lab."
        )
        expect(page.locator("#lab-guide-open-btn")).to_be_hidden()
