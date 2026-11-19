"""Browser tests for a signed-in page whose Session the server no longer holds.

The Session store lives in memory, so a server restart forgets every
Session while each browser still carries its cookie. Every request such a
page makes is refused, and no retry brings the Session back: the page must
return to the login page at once instead of polling into a stream of 401s.
Clearing ``active_sessions`` under a live page stands in for the restart.
"""

import pytest

from mniplab.tests.browser import LiveServerCase
from mniplab.webserver.auth import active_sessions

pytestmark = pytest.mark.browser_workspace

LOGIN_PAGE = "#login-page:not(.hidden)"


class SessionLostTests(LiveServerCase):
    """A page whose Session is gone lands on login and stops asking."""

    def test_a_refused_request_returns_the_page_to_login(self) -> None:
        """A 401 from any poll ends the page rather than retrying."""
        with self.signed_in_page() as page:
            active_sessions.clear()
            refusals: list[str] = []
            page.on(
                "response",
                lambda response: (
                    refusals.append(response.url) if response.status == 401 else None
                ),
            )

            page.evaluate("""async () => {
                const { updateStatus } = await import('/static/js/lab/lifecycle.js');
                await updateStatus();
            }""")

            page.wait_for_selector(LOGIN_PAGE)
            refused_on_landing = len(refusals)
            page.wait_for_timeout(3000)
            self.assertEqual(len(refusals), refused_on_landing, refusals)

    def test_a_refused_labs_feed_returns_the_page_to_login(self) -> None:
        """The Labs feed refused for its Session does not reconnect again."""
        with self.signed_in_page() as page:
            active_sessions.clear()

            # Drop the feed as a server restart would; its first reconnect
            # meets a server that no longer knows the Session.
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.labsWs.close();
            }""")

            page.wait_for_selector(LOGIN_PAGE)
            self.assertFalse(page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                return appState.isAuthenticated;
            }"""))

    def test_a_wrong_password_stays_on_the_login_form(self) -> None:
        """Login's own 401 is a wrong password, not a lost Session."""
        assert self.browser is not None
        page = self.browser.new_page()
        try:
            page.goto(self.base_url + "/")
            page.wait_for_selector(LOGIN_PAGE)
            reloads: list[str] = []
            page.on("load", lambda _page: reloads.append("load"))

            status = page.evaluate("""async () => {
                const { login } = await import('/static/js/services/api.js');
                return (await login(btoa('nobody:wrong-password1'))).status;
            }""")

            page.wait_for_timeout(1000)
            self.assertEqual(status, 401)
            self.assertEqual(reloads, [])
        finally:
            page.close()

    def test_guide_and_layout_preserve_optional_keys(self) -> None:
        """Keyed and unkeyed requests keep their URLs and Guide cache policy."""
        with self.signed_in_page() as page:
            page.route("**/api/load_guide?*", lambda route: route.fulfill(json={}))
            page.route("**/api/load_layout?*", lambda route: route.fulfill(json={}))
            requests = page.evaluate("""async () => {
                const api = await import('/static/js/services/api.js');
                const originalFetch = window.fetch;
                const requests = [];
                window.fetch = (url, options) => {
                    requests.push({url, cache: options?.cache ?? null});
                    return originalFetch(url, options);
                };
                try {
                    await api.loadGuide('preview one', 'key/a&b');
                    await api.loadLayout('preview one', 'key/a&b');
                    await api.loadGuide('static-lab');
                    await api.loadLayout('static-lab');
                } finally {
                    window.fetch = originalFetch;
                }
                return requests.filter(request => /load_guide|load_layout/.test(request.url));
            }""")
            self.assertEqual(
                requests,
                [
                    {
                        "url": "/api/load_guide?example=preview%20one&key=key%2Fa%26b",
                        "cache": "no-store",
                    },
                    {
                        "url": "/api/load_layout?example=preview%20one&key=key%2Fa%26b",
                        "cache": None,
                    },
                    {"url": "/api/load_guide?example=static-lab", "cache": "no-store"},
                    {"url": "/api/load_layout?example=static-lab", "cache": None},
                ],
            )

    def test_keyed_guide_and_layout_refusals_return_to_login(self) -> None:
        """Both conflict-resolved helpers handle lost Sessions."""
        for helper in ("loadGuide", "loadLayout"):
            with self.subTest(helper=helper), self.signed_in_page() as page:
                active_sessions.clear()
                page.evaluate(
                    """async helper => {
                    const api = await import('/static/js/services/api.js');
                    await api[helper]('preview-test', 'preview-key');
                }""",
                    helper,
                )
                page.wait_for_selector(LOGIN_PAGE)

    def test_simultaneous_refusals_reload_once_and_keep_the_lab_url(self) -> None:
        """Concurrent refusals cause one reload, retaining the Lab URL."""
        with self.signed_in_page() as page:
            page.evaluate("() => history.replaceState(null, '', '/labs/session-test')")
            reloads: list[str] = []
            page.on("load", lambda _page: reloads.append("load"))
            active_sessions.clear()
            # The refusals intentionally reload the page; do not await a
            # promise in the execution context that navigation will destroy.
            page.evaluate("""() => {
                void import('/static/js/services/api.js').then(({ getStatus, getMe }) => {
                    void Promise.all([getStatus(), getMe(), getStatus()]);
                });
            }""")
            page.wait_for_selector(LOGIN_PAGE)
            page.wait_for_timeout(6000)
            self.assertEqual(reloads, ["load"])
            self.assertEqual(page.url, self.base_url + "/labs/session-test")
