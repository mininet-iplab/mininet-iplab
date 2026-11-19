"""Unit tests for the Session Cap: the most Sessions one account may hold.

The cap is enforced at /api/login and off unless an Instructor sets it;
every test here declares the number it is exercising through
``SessionCapMixin.cap``.
"""

import unittest
from unittest.mock import AsyncMock, patch

from mniplab.tests.fakes import (
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    LoginClientMixin,
    SessionCapMixin,
)
from mniplab.webserver import auth
from mniplab.webserver.auth import create_session, verify_session, touch_session


class SessionCapTests(
    LoginClientMixin,
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    SessionCapMixin,
    unittest.TestCase,
):
    """POST /api/login enforces the Session Cap for the account signing in.

    The cap is off unless an Instructor sets it, so every test here declares
    the number it is exercising.  No default is safe: a runtime cannot tell a
    shared class credential from a personal one.
    """

    def _hold(self, count: int, username: str = "learner1") -> list[str]:
        """Seed Sessions for one account, each seen one second after the last.

        Seeded rather than signed in: the login rate limiter allows five
        attempts a minute per username, which several of these tests would
        otherwise exhaust before reaching the behaviour under test.

        Args:
            count: How many Sessions the account should already hold.
            username: The owning account.

        Returns:
            The Session tokens, least-recently-seen first.
        """
        tokens = []
        for _ in range(count):
            tokens.append(create_session(username, "learner"))
            self.clock.advance(1)
        return tokens

    def test_at_the_cap_the_least_recently_seen_session_is_evicted(self) -> None:
        """The browser least likely to have a Learner in front of it goes first.

        Its next request lands back on the login screen, and its Terminals
        do not outlive it.
        """
        self.cap(2)
        oldest, newer = self._hold(2)

        self.sign_in()

        self.assertFalse(self.authenticates(oldest))
        self.assertTrue(self.authenticates(newer))
        self.assertEqual(self.terminals.closed, [oldest])

    def test_the_last_session_under_the_cap_costs_nothing(self) -> None:
        """The count compared is what the account already holds, not what it will.

        An account one Session below the cap is entitled to that Session
        outright.  Comparing ``held + 1`` against the cap instead would evict
        here, one login early, and a class of two on a cap of two would spend
        the whole lesson signing each other out.
        """
        self.cap(2)
        (existing,) = self._hold(1)

        issued = self.sign_in()

        self.assertTrue(self.authenticates(existing))
        self.assertTrue(self.authenticates(issued))
        self.assertEqual(self.terminals.closed, [])

    def test_an_account_below_the_cap_signs_in_untouched(self) -> None:
        """Room to spare must cost nobody their Session."""
        self.cap(4)
        held = self._hold(2)

        self.sign_in()

        self.assertTrue(all(self.authenticates(token) for token in held))
        self.assertEqual(self.terminals.closed, [])

    def test_a_cap_of_one_evicts_the_only_session_the_account_holds(self) -> None:
        """The tightest cap an Instructor can set must still admit the Learner."""
        self.cap(1)
        (only_one,) = self._hold(1)

        issued = self.sign_in()

        self.assertFalse(self.authenticates(only_one))
        self.assertTrue(self.authenticates(issued))

    def test_a_cap_of_zero_disables_the_cap_however_many_sessions_are_held(self) -> None:
        """Zero is the off switch, not a cap of nought that refuses everyone.

        It is also the default, pinned in :class:`SettingsTests`; shipping
        the cap on would lock out every class sharing one credential.
        """
        self.cap(0)
        held = self._hold(6)

        issued = self.sign_in()

        self.assertTrue(self.authenticates(issued))
        self.assertTrue(all(self.authenticates(token) for token in held))
        self.assertEqual(self.terminals.closed, [])

    def test_a_session_holding_a_terminal_is_never_the_cap_victim(self) -> None:
        """'Genuinely active' means holding an open Terminal, not merely being live.

        The oldest, least-recently-seen Session would ordinarily go first —
        but not while a Terminal is open in it.  Evicting it anyway would end
        a Learner's live shell to make room for a browser that only just
        signed in.
        """
        self.cap(2)
        active, idle = self._hold(2)
        self.terminals.terminals[active] = 1

        self.sign_in()

        self.assertTrue(self.authenticates(active))
        self.assertFalse(self.authenticates(idle))
        self.assertEqual(self.terminals.closed, [idle])

    def test_a_login_at_the_cap_with_every_session_active_is_refused_with_403(self) -> None:
        """When every held Session has a Learner in it, there is nothing to evict.

        The Learner is
        told to sign out on another device, not silently kicked out of one
        that is in active use.
        """
        self.cap(2)
        held = self._hold(2)
        for token in held:
            self.terminals.terminals[token] = 1

        response = self.log_in()

        self.assertEqual(response.status_code, 403)
        detail = response.json()["detail"]
        self.assertIn("too many devices", detail)
        self.assertTrue(all(self.authenticates(token) for token in held))
        self.assertEqual(self.terminals.closed, [])

    def test_retrying_a_login_the_cap_refuses_never_turns_into_429(self) -> None:
        """A Learner who retries at the cap keeps hearing the real reason.

        The password was right each time, so no attempt counts against the
        login limit; otherwise the sixth retry would say "too many login
        attempts", a problem the Learner does not have.
        """
        self.cap(2)
        held = self._hold(2)
        for token in held:
            self.terminals.terminals[token] = 1

        codes = [self.log_in().status_code for _ in range(auth.settings.login_rate_limit + 2)]

        self.assertEqual(set(codes), {403})

    def test_a_refused_login_leaves_the_callers_own_session_standing(self) -> None:
        """A 403 must not also sign the browser out of the Session it already had.

        The cap arithmetic excludes the caller's own outgoing Session so a
        reload never spends its own slot, but the Session itself must survive
        until the login is known to succeed — otherwise a refused Learner is
        refused *and* signed out.
        """
        self.cap(2)
        mine, other_a, other_b = self._hold(3)
        for token in (other_a, other_b):
            self.terminals.terminals[token] = 1

        response = self.log_in(cookie=mine)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.authenticates(mine))
        self.assertTrue(self.authenticates(other_a))
        self.assertTrue(self.authenticates(other_b))

    def test_a_ghost_session_does_not_spend_the_accounts_allowance(self) -> None:
        """The idle sweep runs before the cap, so expired tabs cost nothing.

        Were the cap evaluated first, the ghost would still count towards the
        account's total and a second live browser would be evicted to make
        room for it.  ``newest`` surviving is what tells the two orders apart.
        """
        self.cap(2)
        (ghost,) = self._hold(1)
        self.clock.advance(auth.settings.session_idle_timeout + 1)
        older, newest = self._hold(2)

        self.sign_in()

        self.assertTrue(self.authenticates(newest))
        self.assertFalse(self.authenticates(ghost))
        self.assertFalse(self.authenticates(older))
        self.assertEqual(self.terminals.closed, [ghost, older])

    def test_signing_in_again_from_one_browser_costs_no_other_browser(self) -> None:
        """The caller's own outgoing Session is retired before the cap is read.

        A reload replaces the Session this browser holds, which frees the slot
        it occupied.  Reading the cap first would count that Session, and
        evict a second browser to make room for a Session that was about to be
        replaced anyway.
        """
        self.cap(2)
        (other_browser,) = self._hold(1)
        mine = self.open_session()

        self.sign_in(cookie=mine)

        self.assertTrue(self.authenticates(other_browser))
        self.assertFalse(self.authenticates(mine))
        self.assertEqual(self.terminals.closed, [mine])

    def test_the_cap_counts_one_account_and_ignores_the_rest(self) -> None:
        """One account at its cap must not cost another account anything."""
        self.cap(1)
        theirs = self._hold(3, username="learner2")

        self.sign_in()

        self.assertTrue(all(self.authenticates(token) for token in theirs))
        self.assertEqual(self.terminals.closed, [])

    def test_another_accounts_sessions_are_never_the_victim(self) -> None:
        """Eviction at the cap takes from the account signing in, and only it."""
        self.cap(1)
        theirs = self._hold(2, username="learner2")
        (mine,) = self._hold(1)

        self.sign_in()

        self.assertEqual(self.terminals.closed, [mine])
        self.assertTrue(all(self.authenticates(token) for token in theirs))

    def test_a_login_the_cap_cannot_make_room_for_is_refused_with_403(self) -> None:
        """403, not 429: too many devices is not too many attempts.

        Distinct from
        ``test_a_login_at_the_cap_with_every_session_active_is_refused_with_403``:
        there, nothing is idle and there is no candidate to begin with.  Here
        a candidate exists but the destroy fails — the record vanished
        between the scan and the eviction — which is the other way this
        helper can come back empty-handed.
        """
        self.cap(2)
        held = self._hold(2)
        frozen_store = patch.object(auth, "destroy_session", AsyncMock(return_value=False))
        frozen_store.start()
        self.addCleanup(frozen_store.stop)

        response = self.log_in()

        self.assertEqual(response.status_code, 403)
        detail = response.json()["detail"]
        self.assertIn("too many devices", detail)
        self.assertIn("idle Session", detail)
        self.assertTrue(all(self.authenticates(token) for token in held))


class SessionCapEvictionOrderTests(
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    SessionCapMixin,
    unittest.IsolatedAsyncioTestCase,
):
    """make_room_for_session picks its victim by last-seen, and by nothing else.

    Ordering is tested against the helper rather than over HTTP because
    last-seen order and creation order only diverge once a Session has been
    refreshed, which a login cannot arrange for a browser it does not own.
    Every other outcome of the helper is proven through the login route in
    :class:`SessionCapTests`.
    """

    async def test_the_victim_is_the_least_recently_seen_not_the_first_created(self) -> None:
        """A Learner still working in their oldest browser must keep it.

        The Session opened first is the one most likely to be the Learner's
        main window; what makes a Session evictable is nobody having used it,
        which is ``last_seen`` and never ``created_at``.
        """
        self.cap(3)
        first, second, third = (create_session("learner1", "learner") for _ in range(3))
        self.clock.advance(60)
        touch_session(first)

        self.assertTrue(await auth.make_room_for_session("learner1"))

        self.assertEqual(self.terminals.closed, [second])
        self.assertEqual(await verify_session(first), "learner1")
        self.assertEqual(await verify_session(third), "learner1")

    async def test_sessions_seen_at_the_same_moment_go_in_creation_order(self) -> None:
        """A tie must resolve the same way every time, not by dict luck."""
        self.cap(2)
        first = create_session("learner1", "learner")
        second = create_session("learner1", "learner")

        self.assertTrue(await auth.make_room_for_session("learner1"))

        self.assertEqual(self.terminals.closed, [first])
        self.assertEqual(await verify_session(second), "learner1")
