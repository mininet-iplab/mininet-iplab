"""Static-asset tests for the Lab tab strip in Multi-Lab Mode.

The browser tests in ``test_web_multi_lab_browser.py`` exercise the strip's
behaviour; this class pins its wiring in the static assets, where a Python
test is the only witness: the strip sits between the header and the main
content as a tablist, the class-wide listing moved out of the sidebar to
become the Overview tab's panel, the strip module owns arrow-key navigation
and selection state, and auth starts and stops it with the role.
"""

import unittest
from pathlib import Path

import pytest


@pytest.mark.unit
class LabTabstripMarkupTests(unittest.TestCase):
    """The Lab tab strip, pinned in the static assets.

    A strip that renders but is not a tablist, a listing that is still
    trapped in the sidebar, or a role switch that forgets to stop the strip
    would each pass the browser tests only by accident; the markup is what
    the strip's behaviour hangs on.
    """

    STATIC = Path(__file__).resolve().parents[1] / "webserver" / "static"

    def _source(self, *parts: str) -> str:
        """Read one static asset.

        Args:
            *parts: Path components below the static directory.

        Returns:
            The file's text.
        """
        return self.STATIC.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_tabstrip_is_a_tablist_between_the_header_and_main(self) -> None:
        """It is a tablist that sits above the main content, inside the app."""
        html = self._source("index.html")
        self.assertIn('id="lab-tabstrip"', html)
        self.assertIn('role="tablist"', html)
        header = html.index("</header>")
        strip = html.index('id="lab-tabstrip"')
        main = html.index('<main id="main"')
        self.assertLess(header, strip)
        self.assertLess(strip, main)

    def test_the_class_wide_listing_left_the_sidebar(self) -> None:
        """The Overview panel still exists, but no longer inside the aside."""
        html = self._source("index.html")
        self.assertIn('id="instructor-dashboard"', html)
        self.assertLess(
            html.index('id="instructor-dashboard"'),
            html.index('<aside class="sidebar"'),
        )

    def test_the_tabstrip_module_owns_keyboard_and_selection(self) -> None:
        """Arrow keys move between tabs; selection is ARIA state."""
        strip = self._source("js", "ui", "tabstrip.js")
        self.assertIn("ArrowLeft", strip)
        self.assertIn("ArrowRight", strip)
        self.assertIn("aria-selected", strip)

    def test_auth_starts_and_stops_the_tabstrip(self) -> None:
        """The role switch starts it in Multi-Lab Mode and stops it otherwise."""
        auth = self._source("js", "lab", "auth.js")
        self.assertIn("startLabTabstrip", auth)
        self.assertIn("stopLabTabstrip", auth)

    def test_the_tabstrip_is_styled(self) -> None:
        """The strip and its tabs have their own rules."""
        css = self._source("css", "main.css")
        self.assertIn(".lab-tabstrip", css)
        self.assertIn(".lab-tab", css)
