"""Authoring canvas fitting and navigation against real Chromium."""

from typing import Literal

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase
from mniplab.tests.browser import Page, expect

pytestmark = pytest.mark.browser_authoring


class AuthoringCanvasViewBrowserTests(AuthoringPageCase):
    """Viewing a draft fits its topology without changing saved positions."""

    @staticmethod
    def _settle_view(page: Page) -> None:
        """Wait for rendering and any running canvas transition to finish."""
        page.evaluate("""async () => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }""")
        page.wait_for_function("() => !d3.active(document.querySelector('#draft-canvas'))")

    def test_phone_resize_fits_every_node_without_an_extra_fit_click(self) -> None:
        """A narrow canvas opens with the same fitted geometry as its Fit action."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 1800, 1600)])
            saved = self._saved_draft(page)["layout"]
            page.set_viewport_size({"width": 375, "height": 800})
            page.wait_for_function(
                "() => document.querySelector('#draft-canvas').clientWidth < 400"
            )
            # ResizeObserver updates the view on the following rendering frame.
            self._settle_view(page)
            canvas = page.locator("#draft-canvas").bounding_box()
            assert canvas is not None
            for name in ("h1", "h2"):
                box = self._node(page, name).bounding_box()
                assert box is not None
                self.assertGreaterEqual(box["x"], canvas["x"] - 1)
                self.assertGreaterEqual(box["y"], canvas["y"] - 1)
                self.assertLessEqual(box["x"] + box["width"], canvas["x"] + canvas["width"] + 1)
                self.assertLessEqual(box["y"] + box["height"], canvas["y"] + canvas["height"] + 1)
            initial = self._node(page, "h2").bounding_box()
            page.get_by_role("button", name="Fit", exact=True).click()
            self._settle_view(page)
            fitted = self._node(page, "h2").bounding_box()
            fitted_canvas = page.locator("#draft-canvas").bounding_box()
            assert initial is not None and fitted is not None and fitted_canvas is not None
            dimension: Literal["x", "y", "width", "height"]
            for dimension in ("x", "y"):
                self.assertAlmostEqual(initial[dimension] - canvas[dimension],
                                       fitted[dimension] - fitted_canvas[dimension], delta=1)
            for dimension in ("width", "height"):
                self.assertAlmostEqual(initial[dimension], fitted[dimension], delta=1)
            self.assertEqual(self._saved_draft(page)["layout"], saved)

    def test_pan_wheel_and_zoom_controls_preserve_saved_layout(self) -> None:
        """Canvas navigation moves the view while leaving Node positions unchanged."""
        with self.signed_in_page() as page:
            self._seed(page, [("h1", 100, 100), ("h2", 900, 700)])
            saved = self._saved_draft(page)
            canvas = page.locator("#draft-canvas").bounding_box()
            initial = self._node(page, "h1").bounding_box()
            assert canvas is not None and initial is not None
            x, y = canvas["x"] + 15, canvas["y"] + 15
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 60, y + 40, steps=5)
            page.mouse.up()
            panned = self._node(page, "h1").bounding_box()
            assert panned is not None
            self.assertAlmostEqual(panned["x"] - initial["x"], 60, delta=1)
            self.assertAlmostEqual(panned["y"] - initial["y"], 40, delta=1)
            page.mouse.wheel(0, -250)
            self._settle_view(page)
            wheeled = self._node(page, "h1").bounding_box()
            assert wheeled is not None
            self.assertGreater(wheeled["width"], panned["width"])
            page.get_by_role("button", name="Zoom out", exact=True).click()
            self._settle_view(page)
            zoomed = self._node(page, "h1").bounding_box()
            assert zoomed is not None
            self.assertLess(zoomed["width"], wheeled["width"])
            page.get_by_role("button", name="Fit", exact=True).click()
            self._settle_view(page)
            fitted = self._node(page, "h1").bounding_box()
            assert fitted is not None
            for dimension in ("x", "y", "width", "height"):
                self.assertAlmostEqual(initial[dimension], fitted[dimension], delta=1)
            expect(page.locator("#status-draft-state")).to_have_text("Saved")
            self.assertEqual(self._saved_draft(page), saved)

    def test_touch_drag_pans_the_canvas_without_moving_saved_nodes(self) -> None:
        """A real single-finger background drag pans just like the running Lab."""
        with self.signed_in_page() as page:
            touch = page.context.new_cdp_session(page)
            touch.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
            # D3 detects touch capability when the authoring page initializes.
            self._seed(page, [("h1", 100, 100), ("h2", 900, 700)])
            saved = self._saved_draft(page)
            canvas = page.locator("#draft-canvas").bounding_box()
            initial = self._node(page, "h1").bounding_box()
            assert canvas is not None and initial is not None
            x, y = canvas["x"] + 15, canvas["y"] + 15
            touch.send("Input.dispatchTouchEvent", {
                "type": "touchStart", "touchPoints": [{"id": 1, "x": x, "y": y}],
            })
            for step in range(1, 7):
                touch.send("Input.dispatchTouchEvent", {
                    "type": "touchMove", "touchPoints": [{"id": 1, "x": x + step * 10,
                                                          "y": y + step * 5}],
                })
            touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            self._settle_view(page)
            panned = self._node(page, "h1").bounding_box()
            assert panned is not None
            self.assertAlmostEqual(panned["x"] - initial["x"], 60, delta=1)
            self.assertAlmostEqual(panned["y"] - initial["y"], 30, delta=1)
            self.assertEqual(self._saved_draft(page), saved)
