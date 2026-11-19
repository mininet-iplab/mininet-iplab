"""Browser tests: Lab Guide heading dedup and Instructor panel diff-patch.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn server
on a real port, driven by headless Chromium over real HTTP.

The behavior under test:

* When a Lab Guide's authored Markdown begins with its own top-level heading
  (``# ...``), that heading is demoted before rendering, so the page never has
  two near-duplicate ``<h1>`` elements (the app chrome's own ``<h1>`` and the
  Guide's injected one). This holds both for the in-app Guide panel and for the
  standalone ``/guide`` viewer reached by "Open in New Tab", which promotes the
  same heading into its own ``<h1>`` and ``document.title``.
* The Instructor Sessions panel stops fully replacing its table's ``innerHTML``
  on every poll when nothing changed: the row's ``<tr>`` DOM node is preserved
  across a refresh, so a screen reader is not re-announced on every 10-second
  poll regardless of whether anything changed.
"""

import pytest

from mniplab.webserver.auth import create_session
from mniplab.tests.browser import LiveServerCase, choose_account_item

pytestmark = pytest.mark.browser_workspace


class ContentRenderingTests(LiveServerCase):
    """Drives guide rendering and the instructor panel refresh in a browser."""

    def test_guide_leading_h1_does_not_duplicate_page_h1(self):
        with self.signed_in_page() as page:
            # Render a guide whose Markdown begins with its own top-level
            # heading, through the real markdown pipeline, into the guide
            # content region.
            page.evaluate("""async () => {
                const { renderGuideMarkdown } = await import('/static/js/lab/guide.js');
                const html = renderGuideMarkdown('# Duplicate Title\\n\\nSome guide body.');
                document.getElementById('lab-guide-content').innerHTML = html;
            }""")

            # Within the app shell there is now exactly one <h1> — the chrome's
            # own — because the guide's leading heading was demoted to an <h2>.
            h1_count = page.evaluate(
                "document.querySelector('#app').querySelectorAll('h1').length"
            )
            self.assertEqual(
                h1_count, 1,
                "the guide's leading # heading must not create a second <h1>",
            )
            # The title text is preserved as a sub-heading, not dropped.
            guide_h2 = page.evaluate(
                "document.getElementById('lab-guide-content').querySelector('h2')?.textContent"
            )
            self.assertEqual(guide_h2, "Duplicate Title")

    def test_guide_heading_demotion_respects_markdown_syntax(self):
        """Blank lines don't hide a leading heading; indentation isn't one."""
        with self.signed_in_page() as page:
            rendered = page.evaluate("""async () => {
                const { renderGuideMarkdown } = await import('/static/js/lab/guide.js');
                return {
                    afterBlankLine: renderGuideMarkdown('\\n\\n# Title\\n\\nBody.'),
                    indented: renderGuideMarkdown('    # Not A Heading\\n'),
                };
            }""")

            self.assertNotIn("<h1", rendered["afterBlankLine"])
            self.assertIn("<h2", rendered["afterBlankLine"])
            # A four-space-indented # is Markdown's indented code block. Demoting
            # it would corrupt the rendered code, so the code block must come
            # back with its single # intact.
            self.assertIn("<code", rendered["indented"])
            self.assertNotIn("##", rendered["indented"])

    def test_standalone_guide_page_has_exactly_one_h1(self):
        """The ``/guide`` viewer promotes the title, so it must not render it twice."""
        session_id = create_session("content-render-guide", "instructor")
        context = self.browser.new_context()
        context.add_cookies([
            {"name": "session", "value": session_id, "url": self.base_url},
        ])
        try:
            page = context.new_page()
            # Serve a guide whose Markdown opens with its own top-level heading.
            # Routing the API rather than writing into examples/ keeps the test
            # off the filesystem while still driving the page's real pipeline.
            page.route(
                "**/api/load_guide*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/markdown; charset=utf-8",
                    body="# Duplicate Title\n\nSome guide body.\n",
                ),
            )
            page.goto(self.base_url + "/guide?example=static-lab")
            page.wait_for_selector("#guide-content h2")

            self.assertEqual(
                page.evaluate("document.querySelectorAll('h1').length"), 1,
                "the guide's leading # heading must not create a second <h1>",
            )
            # The one <h1> is the page chrome's, carrying the promoted title.
            self.assertEqual(
                page.inner_text("#guide-page-title"), "Duplicate Title",
            )
            # …and the guide body keeps the text as a sub-heading, not dropped.
            self.assertEqual(
                page.inner_text("#guide-content h2"), "Duplicate Title",
            )
        finally:
            context.close()

    def test_instructor_sessions_panel_preserves_row_node_on_unchanged_refresh(self):
        with self.signed_in_page() as page:
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            # The instructor sessions panel is polling (started on login); wait
            # for its first render to produce a row.
            page.wait_for_selector(
                "#instructor-connection-list .connection-session", timeout=10000
            )

            # Capture a reference to one row's <tr> DOM node.
            captured = page.evaluate(
                """() => {
                window.__capturedRow = document.querySelector(
                    '#instructor-connection-list .connection-session');
                return !!window.__capturedRow;
            }"""
            )
            self.assertTrue(captured, "expected a session row to capture")

            # Run the panel's refresh again — the exact function the 10-second
            # poll interval calls — with nothing changed.
            page.evaluate(
                """async () => {
                const { refreshInstructorConnections } =
                    await import('/static/js/lab/instructor-connections.js');
                await refreshInstructorConnections();
            }"""
            )

            # The row's <tr> is still the same node, not a freshly-created
            # replacement (which would re-announce the whole table).
            same_node = page.evaluate(
                """() => {
                const current = document.querySelector(
                    '#instructor-connection-list .connection-session');
                return window.__capturedRow === current;
            }"""
            )
            self.assertTrue(
                same_node,
                "an unchanged refresh replaced the session row's <tr> "
                "instead of patching it in place",
            )

    def test_instructor_sessions_panel_preserves_logout_button_on_unchanged_refresh(self):
        """The row's Sign-out control survives a poll too, not just its ``<tr>``.

        Rebuilding the button would swap a node inside the panel's aria-live
        region on every poll — re-announcing the cell and dropping focus if an
        instructor happened to be on it.
        """
        with self.signed_in_page() as page:
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            page.locator(
                "#instructor-connection-list .connection-session summary"
            ).first.click()
            page.wait_for_selector(
                "#instructor-connection-list .connection-signout",
                timeout=10000,
            )
            captured = page.evaluate(
                """() => {
                window.__capturedButton = document.querySelector(
                    '#instructor-connection-list .connection-signout');
                return !!window.__capturedButton;
            }"""
            )
            self.assertTrue(captured, "expected a Sign-out button to capture")

            page.evaluate(
                """async () => {
                const { refreshInstructorConnections } =
                    await import('/static/js/lab/instructor-connections.js');
                await refreshInstructorConnections();
            }"""
            )

            same_node = page.evaluate(
                """() => {
                const current = document.querySelector(
                    '#instructor-connection-list .connection-signout');
                return window.__capturedButton === current;
            }"""
            )
            self.assertTrue(
                same_node,
                "an unchanged refresh replaced the row's Sign-out button "
                "instead of reusing it in place",
            )
