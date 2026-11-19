"""Markup contracts for the combined Overview and its connection details."""

from pathlib import Path
import unittest


class OverviewMarkupTests(unittest.TestCase):
    """The class summary replaces the old split Group/connection subtabs."""

    STATIC = Path(__file__).resolve().parents[1] / "webserver" / "static"

    def _source(self, *parts: str) -> str:
        return self.STATIC.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_overview_is_one_labeled_class_surface(self) -> None:
        html = self._source("index.html")
        self.assertIn('id="instructor-dashboard" role="tabpanel"', html)
        self.assertIn('aria-labelledby="lab-tab-overview"', html)
        self.assertNotIn('id="overview-subtabs"', html)

    def test_groups_are_visible_before_details(self) -> None:
        html = self._source("index.html")
        self.assertLess(
            html.index('id="overview-group-list"'),
            html.index('id="overview-group-details"'),
        )
        tag = html.split('id="overview-group-list"', 1)[1].split(">", 1)[0]
        self.assertNotIn("hidden", tag)

    def test_group_details_have_an_accessible_heading(self) -> None:
        html = self._source("index.html")
        self.assertIn('aria-labelledby="overview-group-heading"', html)
        self.assertIn('id="overview-group-heading"', html)
        self.assertIn('id="overview-details-close"', html)

    def test_details_start_closed(self) -> None:
        html = self._source("index.html")
        tag = html.split('id="overview-group-details"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", tag)

    def test_group_actions_live_inside_selected_details(self) -> None:
        html = self._source("index.html")
        self.assertLess(
            html.index('id="overview-group-details"'),
            html.index('id="instructor-dashboard-list"'),
        )

    def test_connections_have_group_and_lab_scoped_containers(self) -> None:
        html = self._source("index.html")
        self.assertIn('id="overview-connection-details"', html)
        self.assertIn('id="instructor-connection-list"', html)
        self.assertNotIn('id="overview-sessions-list"', html)
        self.assertNotIn('id="overview-terminals-list"', html)

    def test_group_cards_keep_activity_and_lab_actions(self) -> None:
        source = self._source("js", "lab", "instructor-dashboard.js")
        for text in (
            "group-card",
            "activity-dot",
            "group-card-summary",
            'data-action="start"',
            'data-action="stop"',
        ):
            self.assertIn(text, source)

    def test_bounded_tabs_keep_keyboard_selection(self) -> None:
        source = self._source("js", "ui", "tabstrip.js")
        for text in ("ArrowLeft", "ArrowRight", "aria-selected", "filterGroupOptions"):
            self.assertIn(text, source)

    def test_overview_owns_one_connection_refresh(self) -> None:
        source = self._source("js", "lab", "instructor-dashboard.js")
        self.assertIn("await refreshInstructorConnections()", source)
        self.assertNotIn("startOverviewSubtabs", source)
        self.assertNotIn("refreshInstructorTerminals", source)

    def test_classroom_styles_are_loaded(self) -> None:
        self.assertIn("/static/css/classroom.css", self._source("index.html"))
        css = self._source("css", "classroom.css")
        for text in (
            ".group-summary",
            ".connection-session",
            ".group-navigation-controls",
        ):
            self.assertIn(text, css)
