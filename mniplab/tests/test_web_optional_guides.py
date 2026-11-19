"""Guide availability follows the Lab version and treats blank Guides as absent."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from mniplab.tests.fakes import FakeTerminalCloserMixin, LoginClientMixin
from mniplab.tests.test_web_multi_lab import MultiLabModeMixin
from mniplab.webserver.repositories import GuideRepository

pytestmark = pytest.mark.unit


class OptionalGuideMetadataTests(
    MultiLabModeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase
):
    """All Lab status surfaces share one explicit Guide availability value."""

    def test_status_and_dashboard_distinguish_optional_guides(self) -> None:
        self.open_session(role="instructor")
        shipped = GuideRepository.available

        def without_dc_guide(example, *args, **kwargs):
            # Every shipped Example has a Guide now; pretend one has none.
            return False if example == "bgp-dc-lab" else shipped(example, *args, **kwargs)

        for example, available in [("static-lab", True), ("bgp-dc-lab", False)]:
            with self.subTest(example=example), patch.object(
                GuideRepository, "available", side_effect=without_dc_guide
            ):
                key = self.start(example).json()["key"]
                own = self.client.get("/api/status").json()
                bound = self.client.get(f"/api/labs/{key}").json()
                listed = self.client.get("/api/labs").json()["labs"][0]
                dashboard = self.client.get("/api/admin/dashboard").json()
                for row in [own, bound, listed, dashboard["labs"][0]]:
                    self.assertIs(row["has_guide"], available)
                self.client.post(f"/api/labs/{key}/stop")


class OptionalGuideRepositoryTests(unittest.TestCase):
    """Availability handles shipped content and immutable authored versions."""

    def test_missing_empty_and_whitespace_only_guides_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            guides = Path(directory) / "guides"
            guides.mkdir()
            path = guides / "lesson.md"
            self.assertFalse(GuideRepository.available("lesson", directory))
            for content in ["", " \n\t", "# Lesson"]:
                path.write_text(content)
                self.assertEqual(
                    GuideRepository.available("lesson", directory),
                    bool(content.strip()),
                )

    def test_running_authored_guide_uses_the_pinned_builder_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            published = Path(directory) / "published"
            older = published / "old"
            newer = published / "new"
            older.mkdir(parents=True)
            newer.mkdir()
            (older / "guide.md").write_text("")
            (newer / "guide.md").write_text("# New Guide")
            with patch(
                "mniplab.webserver.repositories.authored_labs_root",
                return_value=directory,
            ):
                self.assertFalse(
                    GuideRepository.available(
                        "authored-lesson", builder_file=str(older / "lab.py")
                    )
                )
                self.assertTrue(
                    GuideRepository.available(
                        "authored-lesson", builder_file=str(newer / "lab.py")
                    )
                )

    def test_read_errors_still_allow_a_request_to_surface_the_failure(self) -> None:
        with patch.object(GuideRepository, "load", side_effect=PermissionError):
            self.assertTrue(GuideRepository.available("static-lab"))

    def test_preview_availability_uses_the_current_draft_guide(self) -> None:
        with patch("mniplab.webserver.repositories.AuthoringRepository") as repository:
            draft = {"guide": ""}
            repository.return_value.read.return_value = draft
            self.assertFalse(GuideRepository.available("preview-lesson"))
            draft["guide"] = "# Preview Guide"
            self.assertTrue(GuideRepository.available("preview-lesson"))
