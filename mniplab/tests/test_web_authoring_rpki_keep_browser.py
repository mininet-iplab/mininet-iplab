"""Keep reviews a Hosted CA's ROAs and ASPAs by keyboard, and a ticked Hosted CA lands in the draft.

Written after ``test_web_authoring_keep_browser.py``: the RPKI objects row is one
Tab stop, its Added and Removed lines are said in words, and the Keep ticks
the whole Hosted CA's objects together.
"""

import copy
from unittest.mock import patch

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase
from mniplab.tests.browser import expect

pytestmark = pytest.mark.browser_authoring

HOSTED_CA = {"asn": 65001, "prefixes": ["10.1.0.0/24"],
             "roas": ["10.1.0.0/24-24 => 65001"], "aspas": []}
RPKI_CA = {"hostname": None, "hosted_cas": [HOSTED_CA]}


@pytest.mark.browser
class RPKIObjectsKeepBrowserTests(AuthoringPageCase):
    """An RPKI CA's changed objects are one Keep row per node, with Added and Removed lines."""

    def test_removed_roa_and_added_aspa_are_ticked_by_keyboard_and_kept(self) -> None:
        """A ROA Krill no longer holds reads Removed, an ASPA it now holds reads Added, and Keep takes both."""
        listing = [{"asn": 65001, "handle": "as65001", "prefixes": ["10.1.0.0/24"],
                    "roas": [], "aspas": [{"customer": 65001, "providers": [65002]}]}]
        read = patch(
            "mniplab.labhandle.LocalLabHandle._op_list_rpki_cas",
            lambda _handle, _node: copy.deepcopy(listing),
        )
        read.start()
        self.addCleanup(read.stop)
        with self.signed_in_page() as page:
            self._seed(page, [("ca", 300, 200, "host", {"rpki_ca": RPKI_CA})], [])
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator("#status-preview-state")).to_have_text("running")
            page.get_by_role("button", name="Keep changes…", exact=True).click()
            dialog = page.get_by_role("dialog", name="Keep changes", exact=True)
            row = dialog.get_by_role("checkbox", name="Keep RPKI objects on ca", exact=True)
            expect(row).to_be_focused()
            expect(row).not_to_be_checked()
            expect(dialog).to_contain_text("ca · AS65001")
            expect(dialog.get_by_role("region", name="Changes on RPKI objects on ca")).to_be_hidden()

            row.focus()
            page.keyboard.press("Tab")
            expect(dialog.get_by_role("button", name="Keep selected", exact=True)).to_be_focused()
            page.keyboard.press("Shift+Tab")
            page.keyboard.press("ArrowRight")
            expect(dialog.get_by_role("button", name="Show changes on RPKI objects on ca")).to_be_focused()
            page.keyboard.press("Enter")
            changes = dialog.get_by_role("region", name="Changes on RPKI objects on ca")
            expect(changes).to_be_visible()
            table = changes.get_by_role("table")
            expect(table.get_by_role("columnheader")).to_have_text(["Field", "Before", "After"])
            expect(table.get_by_role("rowheader")).to_have_text(["ROA", "ASPA"])
            rows = table.get_by_role("row")
            expect(rows.filter(has_text="ROA").get_by_role("cell")).to_have_text(
                ["10.1.0.0/24-24 => 65001", "Removed"])
            expect(rows.filter(has_text="ASPA").get_by_role("cell")).to_have_text(
                ["none", "Added AS65001 => AS65002"])

            row.focus()
            page.keyboard.press("Space")
            expect(row).to_be_checked()
            page.keyboard.press("Tab")
            expect(dialog.get_by_role("button", name="Keep selected", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            expect(dialog).to_be_hidden()
            kept = self._saved_draft(page)
            hosted = next(node for node in kept["topology"]["nodes"]
                          if node["name"] == "ca")["rpki_ca"]["hosted_cas"][0]
            self.assertEqual(hosted["roas"], [])
            self.assertEqual(hosted["aspas"], ["AS65001 => AS65002"])
