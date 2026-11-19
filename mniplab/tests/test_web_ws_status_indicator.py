"""Browser test: the visible WebSocket connection-status indicator.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn server
on a real port, driven by headless Chromium over real HTTP.

The behavior under test
is that a small, visible indicator near the header's status block reflects the
topology WebSocket's state through at least: connected, disconnected/reconnecting,
and fell-back-to-polling (after the existing max reconnect-attempt count is
exhausted). Its container is an ``aria-live="polite"`` region so a state change
is announced without moving focus.

One test drives a real connect → forced-disconnect → reconnect cycle against a
live server. A second exhausts the reconnect budget for real: it runs against
its own server, shuts that server down so the endpoint genuinely stops
answering, and lets all five reconnect attempts fail in turn before asserting
the fallback state. Only the retry *pacing* is tightened by the test — the
budget itself is the shipped one.
"""

import pytest

from mniplab.webserver.auth import create_session
from mniplab.tests.browser import LiveServer, LiveServerCase

pytestmark = pytest.mark.browser_workspace


class WebSocketStatusIndicatorTests(LiveServerCase):
    """Drives the topology WebSocket through its states and reads the indicator."""

    def _indicator_text(self, page):
        return page.inner_text("#ws-status")

    def _expose_app_state(self, page):
        """Hand the test a synchronous handle to `appState` for assertions."""
        page.evaluate("""async () => {
            window.__appState = (await import('/static/js/state/app-state.js')).appState;
        }""")

    def test_indicator_tracks_connect_and_reconnect_cycle(self):
        with self.signed_in_page(wait_for_topology=False) as page:
            self._expose_app_state(page)

            # The indicator's container is an aria-live region.
            self.assertEqual(
                page.get_attribute("#ws-status", "aria-live"), "polite",
                "the connection-status indicator must be an aria-live region",
            )

            # 1. Connected: the WS opens on login (even with no lab running).
            page.wait_for_function("""() => {
                const appState = window.__appState;
                return appState.labsWs && appState.labsWs.readyState === 1;
            }""", timeout=10000)
            self.assertIn("Connected", self._indicator_text(page))

            # 2. Disconnect → Reconnecting: close the live socket; the close
            #    handler schedules a reconnect and the indicator reflects it.
            page.evaluate("window.__appState.labsWs.close()")
            page.wait_for_function("""() => {
                const appState = window.__appState;
                return appState.wsReconnectAttempts >= 1;
            }""", timeout=10000)
            self.assertIn("Reconnecting", self._indicator_text(page))

            # 3. Reconnect: the server is still up, so the retry succeeds.
            page.wait_for_function("""() => {
                const appState = window.__appState;
                return appState.labsWs && appState.labsWs.readyState === 1;
            }""", timeout=15000)
            self.assertIn("Connected", self._indicator_text(page))

    def test_indicator_falls_back_to_polling_when_reconnects_are_exhausted(self):
        """Kill the endpoint and let the shipped retry budget genuinely run out."""
        server = LiveServer()
        server.start()
        session_id = create_session("ws-status-polling", "instructor")
        context = self.browser.new_context()
        context.add_cookies([
            {"name": "session", "value": session_id, "url": server.base_url},
        ])
        try:
            page = context.new_page()
            page.goto(server.base_url + "/")
            page.wait_for_selector("#app:not(.hidden)")
            self._expose_app_state(page)

            page.wait_for_function("""() => {
                const appState = window.__appState;
                return appState.labsWs && appState.labsWs.readyState === 1;
            }""", timeout=10000)
            self.assertIn("Connected", self._indicator_text(page))

            # Record every label the indicator shows from here on, so the
            # intermediate states can be asserted without racing the retries.
            page.evaluate("""() => {
                const label = document.querySelector('#ws-status .ws-status-text');
                window.__wsStates = [label.textContent];
                new MutationObserver(() => {
                    const text = label.textContent;
                    if (window.__wsStates[window.__wsStates.length - 1] !== text) {
                        window.__wsStates.push(text);
                    }
                }).observe(label, { childList: true, characterData: true, subtree: true });
            }""")

            # Tighten the retry *pacing* only. The five attempts below are the
            # shipped budget, genuinely spent — they just don't take the 31s the
            # production backoff would need.
            page.evaluate("window.__appState.wsReconnectDelay = 25")

            # Take the endpoint away: every reconnect from here on fails for
            # real, rather than the test writing the attempt counter itself.
            server.stop()

            # The budget is spent one genuinely-failed attempt at a time…
            page.wait_for_function("""() => {
                const appState = window.__appState;
                return appState.wsReconnectAttempts >= appState.wsMaxReconnectAttempts;
            }""", timeout=20000)
            # …and one more failed close with it spent tips into polling.
            page.wait_for_function("""() => {
                const label = document.querySelector('#ws-status .ws-status-text');
                return !!label && label.textContent.includes('Polling');
            }""", timeout=20000)
            self.assertIn("Polling", self._indicator_text(page))

            states = page.evaluate("() => window.__wsStates")
            self.assertTrue(
                any("Reconnecting" in state for state in states),
                f"the indicator never reported Reconnecting on the way to the "
                f"fallback (saw {states})",
            )
            self.assertIn("Polling", states[-1])
            self.assertEqual(
                page.get_attribute("#ws-status", "aria-live"), "polite",
                "the fallback state must land in the aria-live region too",
            )
        finally:
            context.close()
            server.stop()
