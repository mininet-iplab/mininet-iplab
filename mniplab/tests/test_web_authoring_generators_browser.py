"""Seeded Speaker Routes as an Instructor edits them in the browser."""

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase, audit
from mniplab.tests.browser import expect, screenshots

pytestmark = [pytest.mark.browser, pytest.mark.browser_authoring]


class SpeakerGeneratorBrowserTests(AuthoringPageCase):
    """Generators start per Neighbor, and crafted Routes stay behind More."""

    def test_new_speaker_has_editable_seeds_and_switches_to_global_routes(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 100, 200)])
            page.get_by_role("button", name="Add BGP Speaker", exact=True).click()
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="Speaker", exact=True).click()
            expect(
                page.get_by_role("radio", name="Per-Neighbor Routes", exact=True)
            ).to_be_checked()
            page.locator("#speaker-neighbors-rows summary").first.click()
            peer = page.get_by_label("Neighbor 1 Peer", exact=True)
            peer.fill("192.0.2.1")
            page.get_by_label("Neighbor 1 Local address", exact=True).fill("192.0.2.2")
            for field in ("Prefix seed", "AS-path seed"):
                self.assertTrue(
                    page.get_by_label(f"Neighbor 1 {field}", exact=True)
                    .input_value()
                    .isdigit()
                )
            page.get_by_label("Neighbor 1 Prefix seed", exact=True).fill("42")
            page.get_by_label("Neighbor 1 AS-path seed", exact=True).fill("88")
            page.get_by_label("Neighbor 1 Route count", exact=True).fill("3")
            page.get_by_label("Neighbor 1 Sub-prefixes per prefix", exact=True).fill(
                "2"
            )
            expect(
                page.get_by_role("button", name="Add Route to Neighbor 1", exact=True)
            ).to_be_hidden()
            page.get_by_role(
                "button", name="More settings for Neighbor 1", exact=True
            ).click()
            page.get_by_role(
                "button", name="Add Route to Neighbor 1", exact=True
            ).click()
            page.get_by_label("Neighbor 1 Route 1 Prefix", exact=True).fill(
                "203.0.113.0/24"
            )
            page.get_by_label("Neighbor 1 Route 1 Next hop", exact=True).fill(
                "192.0.2.2"
            )
            saved = self._saved_draft(page)
            speaker = next(
                node for node in saved["topology"]["nodes"] if node["type"] == "speaker"
            )
            self.assertEqual(
                speaker["neighbors"][0]["route_generator"],
                {"count": 3, "sub_prefixes": 2, "prefix_seed": 42, "as_path_seed": 88},
            )
            screenshots(page, "rework-02", "neighbor-generator", "#speaker-neighbors-0-count")
            page.get_by_label("Neighbor 1 Prefix seed", exact=True).focus()
            audit(
                self, page, "neighbor-generator", check_focus=True
            )
            screenshots(
                page, "rework-02", "neighbor-generator-seeds", "#speaker-neighbors-0-prefix_seed"
            )
            global_choice = page.get_by_role("radio", name="Global Routes", exact=True)
            global_choice.focus()
            page.keyboard.press("Space")
            expect(page.get_by_label("Global Route count", exact=True)).to_have_value(
                "3"
            )
            expect(page.get_by_label("Global Prefix seed", exact=True)).to_have_value(
                "42"
            )
            screenshots(page, "rework-02", "global-generator", "#speaker-generator-count")
            page.get_by_label("Global Prefix seed", exact=True).focus()
            audit(
                self, page, "global-generator", check_focus=True
            )
            saved = self._saved_draft(page)
            speaker = next(
                node for node in saved["topology"]["nodes"] if node["type"] == "speaker"
            )
            self.assertIn("route_generator", speaker)
            self.assertNotIn("route_generator", speaker["neighbors"][0])
            self.assertEqual(
                speaker["neighbors"][0]["routes"][0]["prefix"], "203.0.113.0/24"
            )
            page.reload()
            self._node(page, speaker["name"]).focus()
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="Speaker", exact=True).click()
            expect(global_choice).to_be_checked()
            expect(page.get_by_label("Global AS-path seed", exact=True)).to_have_value(
                "88"
            )

    def test_bad_generator_numbers_are_named_on_their_fields(self) -> None:
        with self.signed_in_page() as page:
            self._seed(page, [("r1", 100, 200)])
            page.get_by_role("button", name="Add BGP Speaker", exact=True).click()
            page.keyboard.press("Enter")
            page.get_by_role("tab", name="Speaker", exact=True).click()
            page.locator("#speaker-neighbors-rows summary").first.click()
            count = page.get_by_label("Neighbor 1 Route count", exact=True)
            count.fill("0")
            count.press("Tab")
            expect(count).to_have_attribute("aria-invalid", "true")
            count.fill("3")
            count.press("Tab")
            expect(count).not_to_have_attribute("aria-invalid", "true")
