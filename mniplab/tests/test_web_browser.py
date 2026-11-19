"""Browser-driven test seam: drives the real Web UI in a real browser.

Every other test module in this suite exercises the FastAPI layer in-process,
with fakes standing in for the browser (`fakes.py`'s `FakeWebSocket`,
Starlette's `TestClient`). None of that reaches the frontend itself — focus
behavior, computed styles, and DOM state after a keyboard action only exist
once a real browser renders the real page against a real socket. This module
is the reference pattern later `requires_browser` tests follow: a `uvicorn`
server bound to a real port, driven over real HTTP by headless Chromium via
Playwright.
"""

import pytest

from mniplab.tests.browser import LiveServerCase

pytestmark = pytest.mark.browser_workspace


class BrowserSmokeTests(LiveServerCase):
    """Loads the root page in headless Chromium and checks its title."""

    def test_root_page_loads_with_expected_title(self):
        page = self.browser.new_page()
        try:
            page.goto(self.base_url + "/")
            self.assertEqual(page.title(), "Mininet-IPLab")
        finally:
            page.close()
