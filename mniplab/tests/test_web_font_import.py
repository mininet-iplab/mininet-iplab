"""Static and browser-driven regression checks for the CSP-blocked Google
Fonts import removal.

Parses the shipped `main.css` and the webserver's CSP header directly (no
browser needed):
the page's own `style-src 'self' 'unsafe-inline'` / `font-src 'self'` policy
blocks the Google Fonts Inter import outright, so the `@import` and the
unreachable `'Inter'` stack entry are dead code. The fix removes them without
loosening the CSP.

The `LiveServerCase` class at the bottom (see `mniplab/tests/browser.py`)
closes the ticket's deferred follow-up: confirming a real page load produces
no CSP-violation console error.
"""

import re
from pathlib import Path


import pytest

from mniplab.tests.browser import LiveServerCase

STATIC_DIR = Path(__file__).resolve().parents[2] / "mniplab" / "webserver" / "static"
CSS_PATH = STATIC_DIR / "css" / "main.css"
WEBSERVER_INIT = Path(__file__).resolve().parents[2] / "mniplab" / "webserver" / "__init__.py"

FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


def _css_text() -> str:
    return CSS_PATH.read_text()


def test_stylesheet_does_not_reference_external_font_hosts():
    """main.css carries no @import or URL pointing at a font host the page's
    own CSP would block anyway — every such request was a guaranteed-failed
    network round-trip and a console error on every page load."""
    css = _css_text()
    for host in FONT_HOSTS:
        assert host not in css, f"main.css still references {host}"

    external_imports = re.findall(r"@import\s+url\(['\"]?https?://", css)
    assert not external_imports, f"main.css still has external @import(s): {external_imports}"


def test_font_main_stack_drops_unreachable_inter_entry():
    """--font-main no longer leads with 'Inter' — with the import gone, that
    entry could never resolve, so the declared stack must match what can
    actually load (system fonts)."""
    css = _css_text()
    match = re.search(r"--font-main:\s*([^;]+);", css)
    assert match, "could not find --font-main declaration in main.css"

    stack = match.group(1)
    assert "'Inter'" not in stack and '"Inter"' not in stack, (
        f"--font-main still lists the unreachable 'Inter' entry: {stack}"
    )


def _csp_header() -> str:
    """Extract the Content-Security-Policy header value from the middleware."""
    source = WEBSERVER_INIT.read_text()
    match = re.search(
        r'response\.headers\["Content-Security-Policy"\]\s*=\s*\(([^)]*)\)',
        source,
    )
    assert match, "could not find the CSP header assignment in webserver/__init__.py"
    return "".join(re.findall(r'"([^"]*)"', match.group(1)))


def test_csp_is_not_loosened_for_external_fonts():
    """The fix is dead-code removal, not a policy change: style-src and
    font-src must stay 'self'-only — no external font host, and no wildcard
    or scheme-wide loosening that would let one in through the side door."""
    csp = _csp_header()
    for host in FONT_HOSTS:
        assert host not in csp, f"CSP now allows {host}"

    for directive in ("style-src", "font-src"):
        match = re.search(directive + r"\s+([^;]*)", csp)
        assert match, f"CSP has no {directive} directive"
        sources = match.group(1)
        assert "'self'" in sources, f"{directive} no longer includes 'self': {sources}"
        assert " *" not in sources and "https:" not in sources and "http:" not in sources, (
            f"{directive} was loosened beyond 'self': {sources}"
        )


class FontImportBrowserTests(LiveServerCase):
    """Loads the real page in headless Chromium and checks for CSP errors."""

    pytestmark = [pytest.mark.browser_workspace]

    def test_root_page_load_has_no_csp_console_errors(self):
        """The dead `@import` used to fail on every load with a CSP-violation
        console error; a real page load must now come back clean."""
        console_errors = []
        page = self.browser.new_page()
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        try:
            page.goto(self.base_url + "/")
            page.wait_for_load_state("networkidle")
        finally:
            page.close()

        csp_errors = [text for text in console_errors if "Content Security Policy" in text]
        assert not csp_errors, f"page load produced CSP-violation console errors: {csp_errors}"

        font_errors = [
            text
            for text in console_errors
            if "fonts.googleapis.com" in text or "fonts.gstatic.com" in text
        ]
        assert not font_errors, f"page load produced font-request console errors: {font_errors}"
