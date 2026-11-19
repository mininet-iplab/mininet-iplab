"""Browser test: a failed Link disconnect/reconnect lands in the Link Config
modal's error region, not the persistent header status readout.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn server
on a real port, driven by headless Chromium over real HTTP.

The behavior under test
is that when a Link disconnect or reconnect fails, the error is reported through
the Link Config modal's own modal-scoped error region — the same region the
modal's other errors and the action's happy path already use — instead of
overwriting the persistent header status readout (Lab / Nodes / Active
Terminals) with unrelated text, which is what the old code did.

The failure is forced without a running Lab: with no Lab, the server answers
the link action 400 with a ``detail`` of "No lab running", which is exactly the
shape of a failed action the UI must route to the modal.
"""

import pytest

from mniplab.tests.browser import LiveServerCase

pytestmark = pytest.mark.browser_workspace


class LinkActionErrorRoutingTests(LiveServerCase):
    """Drives a failing Link action and checks where its error lands."""

    def _run_failing_link_action(self, page):
        """Point the console at two nodes and fire a disconnect with no Lab
        running, so the server rejects it and the UI must route the error."""
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            document.getElementById('link-node1').value = 'r1';
            document.getElementById('link-node2').value = 'r2';
            await appState.networkConsole.handleLinkStatus('disconnect');
        }""")
        # Wait for the Link Config modal to open with the error region shown.
        page.wait_for_selector("#link-modal-error.show", timeout=5000)

    def test_failed_link_action_error_lands_in_modal_not_header(self):
        with self.signed_in_page() as page:
            # Capture the header status readout before the action so we can
            # prove it is untouched by the failure.
            header_before = page.inner_text("#status")
            self.assertIn("Active Terminals", header_before)

            self._run_failing_link_action(page)

            # The error is in the Link Config modal's own error region, and the
            # modal is open (its overlay display is flex, not none).
            error_text = page.inner_text("#link-modal-error")
            self.assertIn("No lab running", error_text)
            self.assertEqual(page.evaluate("getComputedStyle(document.getElementById('link-modal')).display"), "flex")

            # The persistent header status readout is unchanged — the error did
            # not overwrite Lab / Nodes / Active Terminals.
            header_after = page.inner_text("#status")
            self.assertEqual(header_after, header_before)
            self.assertNotIn("Error:", header_after)
