"""RPKI CA and Validator in the Node dialog: Hosted CAs are labelled rows, and the refresh is a field.

Written against the Services tab of a Node's dialog, following the row editor
tests: every control is reached and set from the keyboard, a refusal is named
on the row or field that holds it, and the saved definition matches what was
typed.
"""

import re

import pytest

from mniplab.tests.authoring_editors import AuthoringPageCase
from mniplab.tests.browser import Page, expect

pytestmark = [pytest.mark.browser, pytest.mark.browser_authoring]

HOSTED_CA = {"asn": 65001, "prefixes": ["10.1.0.0/24"], "roas": [], "aspas": []}
RPKI_CA = {"hostname": None, "hosted_cas": [HOSTED_CA]}


class RpkiServiceRowsTests(AuthoringPageCase):
    """The RPKI CA's Hosted CAs are rows, and the Validator's refresh is a field."""

    def test_an_rpki_ca_and_a_hosted_ca_are_authored_by_keyboard_only(self) -> None:
        """Tick the RPKI CA, add a Hosted CA and Tab through its fields: the saved values are as typed."""
        with self.signed_in_page() as page:
            self._seed(page, [("ca", 300, 300)])
            self.open_services(page, "ca")
            page.get_by_role("checkbox", name="RPKI CA", exact=True).focus()
            page.keyboard.press("Space")
            expect(page.get_by_label("Hostname", exact=True)).to_be_visible()
            page.get_by_role("button", name="Add Hosted CA", exact=True).focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Hosted CA 1 ASN", exact=True)).to_be_focused()
            page.keyboard.type("65002")
            page.keyboard.press("Tab")
            page.keyboard.type("10.2.0.0/24")
            page.keyboard.press("Tab")
            page.keyboard.type("10.2.0.0/24-24 => 65002")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page, "ca")["rpki_ca"], {
                "hostname": None,
                "hosted_cas": [{"asn": 65002, "prefixes": ["10.2.0.0/24"],
                                "roas": ["10.2.0.0/24-24 => 65002"], "aspas": []}]})

    def test_collapsed_hosted_cas_summarise_and_no_json_remains(self) -> None:
        """Each Hosted CA is a closed row reading its ASN, prefixes and starting objects."""
        with self.signed_in_page() as page:
            self._seed(page, [("ca", 300, 300, "host", {"rpki_ca": RPKI_CA})])
            self.open_services(page, "ca")
            first = page.locator("#rpki-ca-hosted-cas-rows summary").first
            expect(first).to_contain_text("AS65001 · 1 prefix · 0 ROAs · 0 ASPAs")
            expect(page.get_by_label("Hosted CA 1 Prefixes", exact=True)).to_be_hidden()
            first.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Hosted CA 1 Prefixes", exact=True)).to_have_value("10.1.0.0/24")
            self.assertEqual(page.locator("#host-dialog textarea#rpki-ca-hosted-cas").count(), 0)

    def test_a_starting_roa_outside_the_resources_is_named_on_its_row(self) -> None:
        """The ROA line that the Hosted CA cannot sign for is named on that row, and saving waits for the fix."""
        with self.signed_in_page() as page:
            self._seed(page, [("ca", 300, 300, "host", {"rpki_ca": RPKI_CA})])
            self.open_services(page, "ca")
            page.locator("#rpki-ca-hosted-cas-rows summary").first.click()
            roas = page.get_by_label("Hosted CA 1 Starting ROAs", exact=True)
            roas.fill("10.9.0.0/24-24 => 65001")
            page.keyboard.press("Tab")
            message = "Line 1: Starting ROA 10.9.0.0/24 is outside this Hosted CA's prefixes."
            expect(roas).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#rpki-ca-hosted-cas-0-roas-error")).to_have_text(message)
            expect(roas).to_have_accessible_description(message)
            expect(page.locator("#status-draft-state")).to_contain_text("Can't save yet")
            roas.fill("10.1.0.0/24-24 => 65001")
            page.keyboard.press("Tab")
            expect(roas).not_to_have_attribute("aria-invalid", "true")

    def test_a_validator_refresh_is_saved_beside_an_rpki_ca(self) -> None:
        """Tick the Validator, keep its default refresh or change it, and the saved refresh is the value typed."""
        with self.signed_in_page() as page:
            self._seed(page, [("ca", 300, 300, "host", {"rpki_ca": RPKI_CA}), ("rv", 500, 300)])
            self.open_services(page, "rv")
            page.get_by_role("checkbox", name="Validator", exact=True).focus()
            page.keyboard.press("Space")
            refresh = page.get_by_label("Refresh (seconds)", exact=True)
            expect(refresh).to_have_value("10")
            refresh.fill("20")
            page.keyboard.press("Escape")
            self.assertEqual(self.saved_node(page, "rv")["validator"], {"refresh": 20})

    def test_a_validator_with_no_rpki_ca_in_the_lab_is_refused_on_its_checkbox(self) -> None:
        """Without an RPKI CA anywhere in the Lab, the Validator's refusal is put on the Validator's checkbox."""
        phrase = "a Validator needs an RPKI CA in the Lab"
        with self.signed_in_page() as page:
            self._seed(page, [("rv", 300, 300)])
            self.open_services(page, "rv")
            page.get_by_role("checkbox", name="Validator", exact=True).focus()
            page.keyboard.press("Space")
            expect(page.locator("#status-draft-state")).to_contain_text("Can't save yet")
            self._follow_to_field(page, phrase)
            validator = page.get_by_role("checkbox", name="Validator", exact=True)
            expect(validator).to_be_focused()
            expect(validator).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#validator-enabled-error")).to_contain_text(phrase)

    @staticmethod
    def _follow_to_field(page: Page, phrase: str) -> None:
        """Close the dialog, follow the draft's fix link, and take the error summary's link to the control."""
        page.keyboard.press("Escape")
        fix = page.locator("#status-draft-fix")
        expect(fix).to_contain_text(phrase)
        fix.focus()
        page.keyboard.press("Enter")
        page.locator("#node-dialog-error-summary").get_by_role(
            "link", name=re.compile(re.escape(phrase))).click()
