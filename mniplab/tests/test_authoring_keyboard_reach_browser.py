"""Keyboard authoring helpers recognize painted SVG controls without an area."""

import contextlib
from typing import Iterator

import pytest

from mniplab.tests.authoring_editors import activate, reach
from mniplab.tests.browser import LiveServerCase, Page, expect

pytestmark = pytest.mark.browser_authoring


class AuthoringKeyboardReachBrowserTests(LiveServerCase):
    """Real Chromium distinguishes painted Links from hidden keyboard controls."""

    @contextlib.contextmanager
    def _page(self, control: str) -> Iterator[Page]:
        assert self.browser is not None
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content(
                '<button id="before">Before</button>'
                f"{control}"
                '<button id="after">After</button>'
                '<output id="activated">Waiting</output>'
                '<script>document.querySelector("#target").addEventListener('
                '"keydown", event => { if (event.key === "Enter") '
                'document.querySelector("#activated").textContent = "Activated"; '
                "});</script>"
            )
            page.locator("#before").focus()
            yield page
        finally:
            context.close()

    def _assert_line_activates(self, coordinates: str) -> None:
        with self._page(
            '<svg width="320" height="320">'
            f'<line id="target" {coordinates} role="button" tabindex="0" '
            'aria-label="Edit Link" stroke="black" stroke-width="4" />'
            "</svg>"
        ) as page:
            target = page.locator("#target")

            reach(page, target)

            expect(target).to_be_focused()
            expect(page.locator("#activated")).to_have_text("Waiting")
            page.keyboard.press("Tab")
            expect(page.locator("#after")).to_be_focused()

            activate(page, target)

            expect(target).to_be_focused()
            expect(page.locator("#activated")).to_have_text("Activated")

    def _assert_rejected(self, control: str) -> None:
        with self._page(control) as page:
            with self.assertRaises(AssertionError):
                activate(page, page.locator("#target"))
            expect(page.locator("#before")).to_be_focused()
            expect(page.locator("#activated")).to_have_text("Waiting")

    def test_painted_horizontal_link_is_reached_and_activated(self) -> None:
        """A horizontal Link has a visible stroke despite its zero height."""
        self._assert_line_activates('x1="20" y1="40" x2="300" y2="40"')

    def test_painted_vertical_link_is_reached_and_activated(self) -> None:
        """A vertical Link has a visible stroke despite its zero width."""
        self._assert_line_activates('x1="40" y1="20" x2="40" y2="300"')

    def test_a_link_in_a_hidden_ancestor_is_rejected(self) -> None:
        """A stroke inside a display:none ancestor must never be accepted."""
        self._assert_rejected(
            '<div style="display:none"><svg width="320" height="100">'
            '<line id="target" x1="20" y1="40" x2="300" y2="40" '
            'role="button" tabindex="0" stroke="black" stroke-width="4" />'
            "</svg></div>"
        )

    def test_a_link_with_hidden_visibility_is_rejected(self) -> None:
        """A hidden stroke must never count as a painted Link."""
        self._assert_rejected(
            '<svg width="320" height="100">'
            '<line id="target" x1="20" y1="40" x2="300" y2="40" '
            'role="button" tabindex="0" stroke="black" stroke-width="4" '
            'style="visibility:hidden" /></svg>'
        )

    def test_a_link_without_a_stroke_is_rejected(self) -> None:
        """Focusable geometry without paint does not make a visible control."""
        self._assert_rejected(
            '<svg width="320" height="100">'
            '<line id="target" x1="20" y1="40" x2="300" y2="40" '
            'role="button" tabindex="0" stroke="none" stroke-width="4" />'
            "</svg>"
        )

    def test_a_link_with_zero_stroke_width_is_rejected(self) -> None:
        """A stroke color with no painted width remains invisible."""
        self._assert_rejected(
            '<svg width="320" height="100">'
            '<line id="target" x1="20" y1="40" x2="300" y2="40" '
            'role="button" tabindex="0" stroke="black" stroke-width="0" />'
            "</svg>"
        )

    def test_an_ordinary_hidden_button_is_rejected(self) -> None:
        """Supporting SVG strokes must preserve normal control visibility checks."""
        self._assert_rejected('<button id="target" hidden>Hidden control</button>')
