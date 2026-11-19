"""Native authoring dialogs stay compact in tall and short browser windows."""

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, draft_menu
from mniplab.tests.browser import Locator, Page, expect

pytestmark = pytest.mark.browser_authoring


class AuthoringDialogLayoutBrowserTests(AuthoringPageCase):
    """Content determines dialog height while long editors remain scrollable."""

    def _assert_compact(
        self, page: Page, dialog: Locator, maximum_height: int
    ) -> float:
        """Check visible controls and centered content rather than CSS declarations."""
        expect(dialog).to_be_visible()
        box = dialog.bounding_box()
        viewport = page.viewport_size
        assert box is not None and viewport is not None
        self.assertLess(box["height"], maximum_height)
        self.assertAlmostEqual(
            box["x"] + box["width"] / 2, viewport["width"] / 2, delta=2
        )
        self.assertAlmostEqual(
            box["y"] + box["height"] / 2, viewport["height"] / 2, delta=2
        )
        for control in dialog.locator(
            "button, input:not([type='checkbox']):not([type='radio']), select"
        ).all():
            if control.is_visible():
                control_box = control.bounding_box()
                assert control_box is not None
                self.assertLessEqual(control_box["height"], 60)
        return box["height"]

    def test_node_general_sizes_to_its_content_in_a_tall_window(self) -> None:
        """A simple Host editor does not stretch its form and Close action."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 400, 100)])
            heights = []
            for height in (720, 1200):
                page.set_viewport_size({"width": 1280, "height": height})
                self._node(page, "h1").focus()
                page.keyboard.press("Enter")
                dialog = page.locator("#host-dialog")
                heights.append(self._assert_compact(page, dialog, 650))
                dialog.locator("#host-close").click()
            self.assertAlmostEqual(heights[0], heights[1], delta=2)

    def test_publish_sizes_to_its_content_in_a_tall_window(self) -> None:
        """The Publish summary keeps normal-sized actions on a tall screen."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 400, 100)])
            page.locator("#catalog-name").fill("Dialog layout lab")
            heights = []
            for height in (720, 1200):
                page.set_viewport_size({"width": 1280, "height": height})
                page.locator("#publish-draft").click()
                dialog = page.locator("#publish-dialog")
                expect(dialog.locator("#publish-new")).to_be_focused()
                heights.append(self._assert_compact(page, dialog, 450))
                dialog.locator("#publish-close").click()
            self.assertAlmostEqual(heights[0], heights[1], delta=2)

    def test_delete_confirmation_sizes_to_its_content_in_a_tall_window(self) -> None:
        """Delete and Cancel remain compact while the confirmation stays centered."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 400, 100)])
            heights = []
            for height in (720, 1200):
                page.set_viewport_size({"width": 1280, "height": height})
                draft_menu(page, "Untitled lab").get_by_role(
                    "button", name="Delete Untitled lab", exact=True
                ).click()
                dialog = page.get_by_role(
                    "alertdialog", name="Delete draft", exact=True
                )
                heights.append(self._assert_compact(page, dialog, 400))
                dialog.get_by_role("button", name="Cancel", exact=True).click()
            self.assertAlmostEqual(heights[0], heights[1], delta=2)

    def test_long_services_editor_stays_inside_a_short_phone_viewport(self) -> None:
        """A long editor scrolls to its final Close action without stretching controls."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 400, 100)])
            for width in (320, 390):
                page.set_viewport_size({"width": width, "height": 480})
                self._node(page, "h1").focus()
                page.keyboard.press("Enter")
                dialog = page.locator("#host-dialog")
                dialog.get_by_role("tab", name="Services", exact=True).click()
                dialog.locator("#dns-enabled").check()
                dialog.locator("#resolver-enabled").check()
                box = dialog.bounding_box()
                assert box is not None
                self.assertGreaterEqual(box["x"], 0)
                self.assertGreaterEqual(box["y"], 0)
                self.assertLessEqual(box["x"] + box["width"], width + 1)
                self.assertLessEqual(box["y"] + box["height"], 481)
                self.assertTrue(
                    dialog.evaluate("el => el.scrollHeight > el.clientHeight")
                )
                close = dialog.locator("#host-close")
                close.focus()
                expect(close).to_be_focused()
                expect(close).to_be_in_viewport(ratio=1)
                close_box = close.bounding_box()
                assert close_box is not None
                self.assertLessEqual(close_box["height"], 60)
                page.keyboard.press("Enter")
                expect(dialog).to_be_hidden()
