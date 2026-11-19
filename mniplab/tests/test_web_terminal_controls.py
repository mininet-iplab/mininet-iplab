"""Static-asset checks for the Terminal pane's tab row and controls.

Browser coverage verifies behavior; these checks retain the tab-row markup,
accessible grouping, and font and Clear wiring.
"""

import unittest
from pathlib import Path

import pytest


@pytest.mark.unit
class TerminalControlsMarkupTests(unittest.TestCase):
    """The Terminal controls have accessible names and a named group."""

    STATIC = Path(__file__).resolve().parents[1] / "webserver" / "static"

    def _source(self, *parts: str) -> str:
        """Read one static asset.

        Args:
            *parts: Path components below the static directory.

        Returns:
            The file's text.
        """
        return self.STATIC.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_controls_follow_the_tab_list_in_the_tab_row(self) -> None:
        """One named group of controls, after the tabs and above the terminal."""
        html = self._source("index.html")
        self.assertNotIn('role="toolbar"', html[html.index('data-pane="terminal"'):html.index('class="terminal-content"')])
        tab_list = html.index('aria-label="Open terminals"')
        group = html.index('role="group" aria-label="Terminal controls"')
        content = html.index('class="terminal-content"')
        ids = [
            html.index(f'id="{control_id}"')
            for control_id in (
                "terminal-font-dec",
                "terminal-font-inc",
                "terminal-clear",
                "close-all-terminals",
            )
        ]
        self.assertLess(tab_list, group)
        self.assertEqual(ids, sorted(ids), "Close all comes last")
        self.assertLess(group, ids[0])
        self.assertLess(ids[-1], content)

    def test_no_toolbar_row_or_breadcrumb_remains(self) -> None:
        """The selected tab is the one place that names the terminal."""
        html = self._source("index.html")
        for gone in ("terminal-toolbar", "terminal-breadcrumb", "terminal-tab-bar-label"):
            self.assertNotIn(gone, html)


@pytest.mark.unit
class TerminalControlsWiringTests(unittest.TestCase):
    """The tab manager's ownership of the toolbar, pinned in the JS."""

    STATIC = Path(__file__).resolve().parents[1] / "webserver" / "static"

    def _source(self, *parts: str) -> str:
        """Read one static asset.

        Args:
            *parts: Path components below the static directory.

        Returns:
            The file's text.
        """
        return self.STATIC.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_tab_manager_wires_the_controls(self) -> None:
        """init() sets up the controls, so they are live on load."""
        js = self._source("js", "ui", "terminal-tabs.js")
        self.assertIn("setupToolbar", js)
        self.assertIn("this.setupToolbar()", js)

    def test_font_size_is_adjusted_and_clamped(self) -> None:
        """A- and A+ move the active terminal's font within a readable band."""
        js = self._source("js", "ui", "terminal-tabs.js")
        self.assertIn("adjustActiveFontSize", js)
        self.assertIn("fontSize", js)
        self.assertIn("10", js)
        self.assertIn("24", js)

    def test_clear_targets_the_active_terminal(self) -> None:
        """Clear terminal buffer empties the active terminal's buffer."""
        js = self._source("js", "ui", "terminal-tabs.js")
        self.assertIn("clearActiveTerminal", js)
        self.assertIn(".clear()", js)

    def test_the_tab_manager_keeps_no_breadcrumb(self) -> None:
        """Switching tabs no longer maintains a breadcrumb."""
        js = self._source("js", "ui", "terminal-tabs.js")
        self.assertNotIn("breadcrumb", js.lower())


@pytest.mark.unit
class TerminalControlsCssTests(unittest.TestCase):
    """The tab-row controls retain their styles."""

    STATIC = Path(__file__).resolve().parents[1] / "webserver" / "static"

    def _source(self, *parts: str) -> str:
        """Read one static asset.

        Args:
            *parts: Path components below the static directory.

        Returns:
            The file's text.
        """
        return self.STATIC.joinpath(*parts).read_text(encoding="utf-8")


    def test_the_tab_row_controls_are_styled_and_the_toolbar_is_gone(self) -> None:
        """The control group has its rules; the old toolbar's rules are gone."""
        css = self._source("css", "main.css")
        self.assertIn(".terminal-tab-actions", css)
        self.assertIn(".terminal-view-control", css)
        for gone in (".terminal-toolbar", ".terminal-breadcrumb"):
            self.assertNotIn(gone, css)
