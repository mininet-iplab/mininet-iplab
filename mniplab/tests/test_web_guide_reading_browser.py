"""Guide reading reaches the final content through real browser input."""

import pytest

from mniplab.tests.browser import expect
from mniplab.tests.test_web_multi_lab_browser import MultiLabBrowserCase
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_classroom


class GuideReadingTests(MultiLabBrowserCase):
    """Standalone and running-Lab Guides remain readable across responsive layouts."""

    def test_new_tab_guide_scrolls_to_its_final_section(self) -> None:
        page = self.signed_in_as("learner1", "learner")
        for width in (1280, 768, 600):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 720})
                page.goto(self.base_url + "/guide?example=static-lab")
                header = page.locator(".guide-page-header")
                expect(page.locator("#guide-content")).to_contain_text(
                    "What you will learn"
                )
                page.mouse.move(width / 2, 400)
                page.mouse.wheel(0, 1000)
                page.wait_for_function("() => window.scrollY > 0", timeout=2000)
                self.assertAlmostEqual(header.bounding_box()["y"], 0, delta=1)
                page.keyboard.press("Control+End")
                final = page.locator("#guide-content > :last-child")
                expect(final).to_be_in_viewport()
                self.assertAlmostEqual(header.bounding_box()["y"], 0, delta=1)
                toggle = page.get_by_role("button", name="Toggle light/dark theme")
                toggle.click()
                expect(toggle).to_be_in_viewport()

    def test_running_lab_guide_has_a_readable_scroll_area(self) -> None:
        registry.groups.assign("learner1", ["static-lab"])
        page = self.signed_in_as("learner1", "learner")
        page.locator("#start-screen-cards .start-screen-start").click()
        guide = page.locator("#lab-guide-content")
        expect(page.locator("#lab-guide-content")).to_contain_text("What you will learn")
        for width in (1280, 768, 600):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 720})
                page.wait_for_timeout(350)  # The sidebar has a 300ms width transition.
                expect(guide).to_be_visible()
                guide.scroll_into_view_if_needed()
                box = guide.bounding_box()
                self.assertGreater(box["height"], 120)
                before = guide.evaluate("e => e.scrollTop")
                page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                page.mouse.wheel(0, 400)
                page.wait_for_function(
                    "before => document.querySelector('#lab-guide-content').scrollTop > before",
                    arg=before,
                    timeout=2000,
                )
