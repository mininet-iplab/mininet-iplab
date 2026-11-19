"""Unit tests for the Session layer: issue, refresh, expire, and destroy.

A Session is one person's authenticated connection to the web UI.  These
tests drive ``mniplab.webserver.auth`` directly and through /api/login and
/api/logout, on a frozen clock, with a fake Terminal registry standing in as
the closer.  The Session Cap has its own module, test_web_session_cap.
"""

import ast
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from mniplab.tests.fakes import (
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    LoginClientMixin,
)
from mniplab.webserver import auth
from mniplab.webserver.auth import (
    RateLimiter,
    create_session,
    verify_session,
    destroy_session,
    get_session_data,
    touch_session,
    expire_idle_sessions,
)


class RateLimiterTests(unittest.TestCase):
    """Test rate limiting functionality."""

    def test_check_within_limit(self):
        """Rate limiter should allow requests within limit."""
        limiter = RateLimiter(max_attempts=5, window=60)
        for _ in range(5):
            self.assertTrue(limiter.check('test_key'))

    def test_check_exceeds_limit(self):
        """Rate limiter should block requests exceeding limit."""
        limiter = RateLimiter(max_attempts=3, window=60)
        limiter.check('test_key')
        limiter.check('test_key')
        limiter.check('test_key')
        self.assertFalse(limiter.check('test_key'))

    def test_reset_clears_attempts(self):
        """Rate limiter reset should clear attempts."""
        limiter = RateLimiter(max_attempts=2, window=60)
        limiter.check('test_key')
        limiter.check('test_key')
        self.assertFalse(limiter.check('test_key'))
        
        limiter.reset('test_key')
        self.assertTrue(limiter.check('test_key'))


class SessionManagementTests(unittest.IsolatedAsyncioTestCase):
    """Test session creation and management."""

    def setUp(self):
        """Clear active sessions before each test."""
        from mniplab.webserver import auth
        auth.active_sessions.clear()

    async def test_verify_session_returns_none_for_invalid(self) -> None:
        """verify_session should return None for invalid session."""
        username = await verify_session('invalid_session_id')
        self.assertIsNone(username)

    async def test_get_session_data_returns_dict(self) -> None:
        """get_session_data should return session data dict."""
        session_id = create_session('testuser', 'instructor')
        data = await get_session_data(session_id)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data['username'], 'testuser')
        self.assertEqual(data['role'], 'instructor')


class SessionRefreshTests(FrozenSessionClockMixin, unittest.TestCase):
    """A Session's last_seen can be refreshed without any HTTP traffic."""

    def test_touch_session_advances_last_seen(self) -> None:
        """touch_session should move last_seen to the current time."""
        session_id = create_session("learner1", "learner")
        self.assertEqual(self.last_seen(session_id), 1000.0)

        self.clock.advance(120.0)
        self.assertTrue(touch_session(session_id))

        self.assertEqual(self.last_seen(session_id), 1120.0)

    def test_touch_session_ignores_an_unknown_session(self) -> None:
        """touch_session should be a no-op for an id that was never issued."""
        self.assertFalse(touch_session("never-issued"))
        self.assertEqual(auth.active_sessions, {})

    def test_touch_session_ignores_a_missing_session_id(self) -> None:
        """touch_session should tolerate the absent-cookie case."""
        self.assertFalse(touch_session(None))


class SessionIdleTimeoutTests(
    FakeTerminalCloserMixin, FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase
):
    """A Session unseen for longer than the idle timeout is dead on next read.

    The timeout read here is the configured one rather than the literal 9000;
    what the default should be is pinned separately, in :class:`SettingsTests`.
    """

    @property
    def timeout(self) -> int:
        """Return the configured Session idle timeout, in seconds."""
        return auth.settings.session_idle_timeout

    async def test_expiring_a_session_closes_its_terminals(self) -> None:
        """A Terminal must not outlive the Session that opened it.

        Otherwise an abandoned tab is the one way to keep a root shell running
        inside the Lab with nobody signed in to own it.
        """
        session_id = create_session("learner1", "learner")
        self.terminals.terminals[session_id] = 2
        self.clock.advance(self.timeout + 1)

        self.assertIsNone(await verify_session(session_id))

        self.assertEqual(self.terminals.closed, [session_id])

    async def test_get_session_data_rejects_a_session_idle_past_the_timeout(self) -> None:
        """The HTTP path must judge idleness the same way the WebSocket path does."""
        session_id = create_session("learner1", "learner")

        self.clock.advance(self.timeout + 1)

        self.assertIsNone(await get_session_data(session_id))

    async def test_an_expired_session_is_removed_and_not_merely_rejected(self) -> None:
        """Rejecting without removing would leave the record to be swept forever."""
        session_id = create_session("learner1", "learner")
        self.clock.advance(self.timeout + 1)

        self.assertIsNone(await verify_session(session_id))

        # destroy_session reports False for a Session that is already gone.
        self.assertFalse(await destroy_session(session_id))

    async def test_a_session_dies_the_moment_it_reaches_the_timeout(self) -> None:
        """The boundary is inclusive, matching the Terminal idle timeout.

        `idle_timeout_check` in the WebSocket handler closes a Terminal at
        `>= terminal_idle_timeout`.  Two idle timeouts in one package that disagreed
        at the boundary would be a trap for whoever read one and assumed the
        other.
        """
        session_id = create_session("learner1", "learner")
        self.terminals.terminals[session_id] = 2

        self.clock.advance(self.timeout - 1)
        self.assertEqual(await verify_session(session_id), "learner1")
        self.assertEqual(self.terminals.closed, [])

        # The read above reset the countdown, so this reaches the boundary.
        self.clock.advance(self.timeout)
        self.assertIsNone(await verify_session(session_id))

    async def test_the_sweep_destroys_every_idle_session_at_once(self) -> None:
        """The sweep must reach Sessions no read will ever touch again.

        A browser that is simply gone stops asking about its own Session, so
        expiry-on-read alone would leave its record and its Terminals forever.
        """
        idle_one = create_session("learner1", "learner")
        idle_two = create_session("learner2", "learner")
        self.clock.advance(self.timeout + 1)
        warm = create_session("learner3", "learner")

        self.assertEqual(await expire_idle_sessions(), 2)

        self.assertEqual(sorted(self.terminals.closed), sorted([idle_one, idle_two]))
        self.assertEqual(await verify_session(warm), "learner3")

    async def test_the_sweep_leaves_a_store_of_warm_sessions_alone(self) -> None:
        """A sweep that finds nothing idle must destroy nothing."""
        session_id = create_session("learner1", "learner")

        self.assertEqual(await expire_idle_sessions(), 0)

        self.assertEqual(self.terminals.closed, [])
        self.assertEqual(await verify_session(session_id), "learner1")

    async def test_a_session_kept_warm_by_terminal_traffic_outlives_the_timeout(self) -> None:
        """A Learner working in a Terminal for hours is never signed out.

        Terminal output refreshes the owning Session through
        touch_session, and that alone must be enough: the Learner makes no HTTP
        request while they work.
        """
        session_id = create_session("learner1", "learner")
        self.terminals.terminals[session_id] = 1

        # Four whole timeouts of Terminal traffic, each refresh landing just
        # inside the previous one.  Without the refresh the second pass alone
        # would already put the Session past the timeout.
        for _ in range(4):
            self.clock.advance(self.timeout - 1)
            self.assertTrue(touch_session(session_id))

        self.assertEqual(await expire_idle_sessions(), 0)
        self.assertEqual(await verify_session(session_id), "learner1")
        self.assertEqual(self.terminals.closed, [])

    async def test_reading_a_live_session_restarts_its_idle_countdown(self) -> None:
        """Two reads a timeout apart must both succeed, or use is not a sign of life."""
        session_id = create_session("learner1", "learner")

        self.clock.advance(self.timeout - 1)
        self.assertEqual(await verify_session(session_id), "learner1")
        self.clock.advance(self.timeout - 1)

        self.assertEqual(await verify_session(session_id), "learner1")


class LoginSweepTests(
    LoginClientMixin, FakeTerminalCloserMixin, FrozenSessionClockMixin, unittest.TestCase
):
    """POST /api/login sweeps every idle Session before issuing a new one.

    Sweeping lazily, at login, keeps the machinery to a dict scan on an event
    a classroom triggers a few dozen times a day, with no background task to
    start, stop, or leak.
    """

    def test_login_sweeps_an_idle_session_belonging_to_another_account(self) -> None:
        """The sweep is a full scan, not a check of the account signing in."""
        stale = create_session("learner2", "learner")
        self.terminals.terminals[stale] = 1
        self.clock.advance(auth.settings.session_idle_timeout + 1)

        self.sign_in()

        self.assertEqual(self.terminals.closed, [stale])

    def test_login_leaves_a_warm_session_alone(self) -> None:
        """Someone else signing in must not end a Learner's live Session."""
        create_session("learner2", "learner")
        self.clock.advance(auth.settings.session_idle_timeout - 1)

        self.sign_in()

        self.assertEqual(self.terminals.closed, [])

    def test_login_still_issues_a_working_session(self) -> None:
        """Sweeping must not disturb the Session the login itself creates."""
        session_id = self.sign_in()

        self.client.cookies.set("session", session_id)
        response = self.client.get("/api/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "learner1")


class LoginIdempotencePerBrowserTests(
    LoginClientMixin, FakeTerminalCloserMixin, FrozenSessionClockMixin, unittest.TestCase
):
    """POST /api/login replaces the Session the same browser already holds.

    A reload or a re-login used to mint a second Session and orphan the first
    on the spot, because nothing could ever present the old cookie again.  That
    is the fastest way for a Learner to spend their own allowance without
    opening a second browser.  A new token is still issued every time, which is
    the defence against session fixation; only the old record is retired.
    """

    def test_signing_in_again_replaces_the_session_the_browser_held(self) -> None:
        """The presented token stops working, its Terminals close, and a fresh
        token is issued — create-fresh is deliberate, a login never reuses
        the old token."""
        first = self.sign_in()
        self.terminals.terminals[first] = 2

        second = self.sign_in(cookie=first)

        self.assertFalse(self.authenticates(first))
        self.assertEqual(self.terminals.closed, [first])
        self.assertNotEqual(second, first)
        self.assertTrue(self.authenticates(second))

    def test_a_cookie_for_another_account_leaves_that_session_alone(self) -> None:
        """One Learner signing in must not end, or even refresh, another
        Learner's Session — but this browser still gets a Session of its own."""
        theirs = self.sign_in("learner2", "lab54321")
        self.terminals.terminals[theirs] = 1
        seen_at = self.last_seen(theirs)
        self.clock.advance(60)

        mine = self.sign_in(cookie=theirs)

        # last_seen first: authenticates() is itself a read that refreshes it.
        self.assertEqual(self.last_seen(theirs), seen_at)
        self.assertTrue(self.authenticates(theirs))
        self.assertEqual(self.terminals.closed, [])
        self.assertNotEqual(mine, theirs)
        self.assertTrue(self.authenticates(mine))

    def test_an_unknown_cookie_signs_in_as_before(self) -> None:
        """A cookie naming no Session — a restarted server — destroys nothing."""
        issued = self.sign_in(cookie="a-token-no-session-was-ever-issued-under")

        self.assertTrue(self.authenticates(issued))
        self.assertEqual(self.terminals.closed, [])

    def test_an_expired_cookie_signs_in_as_before(self) -> None:
        """A Learner returning after the idle timeout signs in cleanly.

        Their prior Session is gone by the time the replacement step looks,
        because the sweep ahead of it has already retired it as expired.
        """
        stale = create_session("learner1", "learner")
        self.terminals.terminals[stale] = 1
        self.clock.advance(auth.settings.session_idle_timeout + 1)

        issued = self.sign_in(cookie=stale)

        self.assertTrue(self.authenticates(issued))
        self.assertFalse(self.authenticates(stale))
        self.assertEqual(self.terminals.closed, [stale])

    def test_a_failed_login_leaves_the_browsers_session_standing(self) -> None:
        """A wrong password must not retire the Session the browser already holds.

        `verify_credentials` raises before the replacement runs, so mistyping a
        password cannot cost a Learner the Session they are working in.  That
        ordering is easy to invert by accident, so it is pinned here.
        """
        mine = self.sign_in()

        self.client.cookies.clear()
        self.client.cookies.set("session", mine)
        response = self.client.post(
            "/api/login", auth=("learner1", "not-the-password")
        )

        self.assertEqual(response.status_code, 401)
        self.assertTrue(self.authenticates(mine))
        self.assertEqual(self.terminals.closed, [])


class LoginLimitTests(LoginClientMixin, FakeTerminalCloserMixin, FrozenSessionClockMixin, unittest.TestCase):
    """The login limit counts wrong passwords per login, nothing else.

    A Group shares one Learner login, so a table signing in on several screens
    at the start of a class must never meet the limit.  The count is never
    kept per address: behind a reverse proxy or a classroom NAT every browser
    has the same one.
    """

    def wrong_password(self, username: str = "learner1") -> int:
        """Try one wrong password and return the response's status code."""
        self.client.cookies.clear()
        return self.client.post("/api/login", auth=(username, "not-the-password")).status_code

    def test_correct_logins_never_meet_the_limit(self) -> None:
        """A whole table signs in to its Group's login inside one minute."""
        codes = [self.log_in().status_code for _ in range(auth.settings.login_rate_limit * 3)]

        self.assertEqual(set(codes), {200})

    def test_wrong_passwords_up_to_the_limit_are_each_refused_with_401(self) -> None:
        """Every wrong password inside the limit gets the plain answer."""
        codes = [self.wrong_password() for _ in range(auth.settings.login_rate_limit)]

        self.assertEqual(set(codes), {401})

    def test_past_the_limit_even_the_right_password_is_refused_with_429(self) -> None:
        """Otherwise a 200 among the 429s would still tell a guesser the password."""
        for _ in range(auth.settings.login_rate_limit):
            self.wrong_password()

        self.assertEqual(self.wrong_password(), 429)
        self.assertEqual(self.log_in().status_code, 429)

    def test_the_limit_ends_a_minute_after_the_wrong_passwords(self) -> None:
        """A login locked by a guesser opens again on its own."""
        for _ in range(auth.settings.login_rate_limit):
            self.wrong_password()

        later = time.time() + auth.login_limiter.window
        with patch.object(auth.time, "time", return_value=later):
            code = self.log_in().status_code

        self.assertEqual(code, 200)

    def test_wrong_passwords_on_one_login_leave_another_login_open(self) -> None:
        """Guessing at one Group's login does not lock the class out."""
        for _ in range(auth.settings.login_rate_limit + 1):
            self.wrong_password("learner2")

        self.assertEqual(self.log_in().status_code, 200)


class SessionTeardownTests(
    FakeTerminalCloserMixin, FrozenSessionClockMixin, unittest.IsolatedAsyncioTestCase
):
    """Destroying a Session releases every Terminal that Session owns."""

    async def test_destroying_a_session_closes_its_terminals(self) -> None:
        """destroy_session should hand the Session's token to the closer."""
        session_id = create_session("learner1", "learner")
        self.terminals.terminals[session_id] = 2

        self.assertTrue(await destroy_session(session_id))

        self.assertEqual(self.terminals.closed, [session_id])

    async def test_a_terminal_that_refuses_to_close_still_destroys_the_session(self) -> None:
        """A failing closer should be logged, not raised at the caller.

        Raising would log a Learner out server-side while their browser kept a
        stale cookie and saw a 500, and would let one stuck Terminal fail an
        Instructor's force-logout.
        """
        async def refuse(session_token: str) -> int:
            raise OSError("terminal is wedged")

        auth.set_terminal_closer(refuse)
        session_id = create_session("learner1", "learner")

        with self.assertLogs("mniplab.webserver.auth", level="ERROR"):
            self.assertTrue(await destroy_session(session_id))

        self.assertIsNone(await verify_session(session_id))

    async def test_destroying_a_session_holding_no_terminals_still_destroys_it(self) -> None:
        """A Session with no Terminals should be destroyed just the same."""
        session_id = create_session("learner1", "learner")

        self.assertTrue(await destroy_session(session_id))

        self.assertEqual(self.terminals.closed, [session_id])
        self.assertIsNone(await verify_session(session_id))

    async def test_destroying_an_unknown_session_closes_nothing(self) -> None:
        """An id that was never issued should be a no-op, not an error."""
        self.assertFalse(await destroy_session("never-issued"))

        self.assertEqual(self.terminals.closed, [])

    async def test_a_session_is_destroyed_without_a_registered_closer(self) -> None:
        """Session bookkeeping should not depend on a Terminal registry existing."""
        auth.set_terminal_closer(None)
        session_id = create_session("learner1", "learner")

        self.assertTrue(await destroy_session(session_id))

        self.assertIsNone(await verify_session(session_id))


class AuthDependencyDirectionTests(unittest.TestCase):
    """The Session layer never reaches out to the Terminal registry itself.

    The Terminal closer is registered by the web layer, so this dependency runs
    inward only.  If auth ever imports the registry, Session bookkeeping stops
    being testable without a Lab and the two modules become mutually recursive.
    """

    def test_auth_does_not_import_the_terminal_registry(self) -> None:
        """auth must import no module that owns Terminals."""
        tree = ast.parse(Path(auth.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        registry_modules = {
            "session",
            "services",
            "mniplab.webserver.session",
            "mniplab.webserver.services",
        }
        self.assertEqual(imported & registry_modules, set())


class LogoutEndpointTests(
    LoginClientMixin, FakeTerminalCloserMixin, FrozenSessionClockMixin, unittest.TestCase
):
    """POST /api/logout ends the caller's Session and the Terminals it owns."""

    def test_logout_closes_the_callers_terminals(self) -> None:
        """Signing out must not leave the Learner's Terminals running."""
        session_id = create_session("learner1", "learner")
        self.terminals.terminals[session_id] = 2
        self.client.cookies.set("session", session_id)

        response = self.client.post("/api/logout")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["logged_out"])
        self.assertEqual(self.terminals.closed, [session_id])

        # The cookie is put back by hand: the browser dropping it is not proof
        # the Session is gone server-side, and that is what is under test.
        self.client.cookies.set("session", session_id)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_logout_without_a_session_cookie_closes_nothing(self) -> None:
        """A browser holding no Session has no Terminals to release."""
        response = self.client.post("/api/logout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.terminals.closed, [])
