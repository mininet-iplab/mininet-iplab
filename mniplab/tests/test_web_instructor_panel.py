"""Unit tests for the Instructor's view of Sessions and Terminals.

Covers the /api/admin routes over HTTP with both roles, and the static
assets that wire the two sidebar panels to them.
"""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mniplab.tests.fakes import (
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    LoginClientMixin,
)
from mniplab.webserver import auth
from mniplab.webserver.auth import create_session, touch_session
from mniplab.webserver.session import registry


class SignedInSessionsMixin(
    LoginClientMixin, FakeTerminalCloserMixin, FrozenSessionClockMixin
):
    """Shared setup for the Instructor's view of who is signed in.

    Both sides of ``require_role("instructor")`` are exercised over HTTP,
    signing in through the real login endpoint so the Session under test is
    the one a browser would hold.
    """


class AdminTerminalRoutesTests(SignedInSessionsMixin, unittest.TestCase):
    """/api/admin/terminals is the Terminal list, under the glossary's noun.

    It used to live at /api/admin/sessions while the Session list sat under
    /api/admin/users — both nouns wrong, and swapped.  These tests pin the
    resource each path now names, since the two payloads are similar enough
    that a consumer reading the wrong one would not obviously fail.
    """

    def test_the_list_is_keyed_on_terminals(self) -> None:
        """The array's key must say Terminal, not reuse the Session word."""
        self.open_session(role="instructor")

        response = self.client.get("/api/admin/terminals")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"terminals", "total"})
        self.assertEqual(body["terminals"], [])

    def test_closing_a_terminal_names_a_terminal_id(self) -> None:
        """The request and the reply both have to say which noun they carry.

        Force-logout's own model has a ``session_id``, and while this one did
        too, the same field name meant a Terminal on one route and a Session on
        the other.
        """
        self.open_session(role="instructor")

        with patch.object(
            registry, "close_terminal", new=AsyncMock()
        ) as close_terminal:
            response = self.client.post(
                "/api/admin/terminals/close", json={"terminal_id": "t1"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"closed": True, "terminal_id": "t1"})
        close_terminal.assert_awaited_once_with("t1")

    def test_the_old_session_id_field_is_rejected(self) -> None:
        """A caller still sending the old field name must be told, not ignored."""
        self.open_session(role="instructor")

        response = self.client.post(
            "/api/admin/terminals/close", json={"session_id": "t1"}
        )

        self.assertEqual(response.status_code, 422)

    def test_the_terminal_list_no_longer_answers_under_the_session_noun(self) -> None:
        """The old shape is gone outright — no shim, no redirect, no tombstone."""
        self.open_session(role="instructor")

        self.assertEqual(
            self.client.post(
                "/api/admin/sessions/close", json={"session_id": "t1"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/admin/users").status_code, 404)
        self.assertEqual(
            self.client.post(
                "/api/admin/users/logout", json={"session_id": "s1"}
            ).status_code,
            404,
        )


class InstructorPanelMarkupTests(unittest.TestCase):
    """The two Instructor panels, pinned in the static assets.

    Nothing in the Python suite reaches the browser, so this class holds the
    claims a browser would otherwise be the only witness to: each panel reads
    the key its own route returns (a panel left on the old key renders
    "nobody is signed in" against a full list and says nothing about why),
    each is headed for what it lists, and the Sessions one stays hidden until
    the page knows the viewer is an Instructor.
    """

    STATIC = Path(__file__).resolve().parents[1] / "webserver" / "static"

    def _source(self, *parts: str) -> str:
        """Read one static asset.

        Args:
            *parts: Path components below the static directory.

        Returns:
            The file's text.
        """
        return self.STATIC.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_terminal_view_is_wired_to_the_terminal_route(self) -> None:
        """It fetches /api/admin/terminals and reads the terminals key."""
        self.assertIn("apiFetch('/api/admin/terminals')", self._source("js", "services", "api.js"))

        panel = self._source("js", "lab", "instructor-connections.js")
        self.assertIn("getTerminals()", panel)
        self.assertIn(".terminals", panel)

    def test_the_session_panel_is_wired_to_the_session_route(self) -> None:
        """It fetches /api/admin/sessions and reads the sessions key."""
        api = self._source("js", "services", "api.js")
        self.assertIn("apiFetch('/api/admin/sessions')", api)
        self.assertIn("/api/admin/sessions/logout", api)

        panel = self._source("js", "lab", "instructor-connections.js")
        self.assertIn("getSessions()", panel)
        self.assertIn(".sessions", panel)

    def test_the_sidebar_carries_a_sessions_section(self) -> None:
        """The new block exists and is named for what it lists."""
        markup = self._source("index.html")

        self.assertIn('id="instructor-connection-list"', markup)
        self.assertIn("Sessions &amp; Terminals", markup)

    def test_a_learner_is_shown_nothing_new(self) -> None:
        """The section ships hidden, and only an Instructor's page reveals it."""
        markup = self._source("index.html")
        block = markup.split('id="workspace-monitor"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", block)

        source = self._source("js", "lab", "instructor-connections.js")
        self.assertIn("currentUserRole !== 'instructor'", source)

    def test_the_two_panels_are_told_apart_by_their_headings(self) -> None:
        """One panel names Terminals, the other Sessions.

        Both were headed with the word "Session" while one of them listed
        Terminals, which is the collision this pair of assertions exists to
        keep closed.
        """
        markup = self._source("index.html")

        self.assertIn('id="workspace-monitor"', markup)
        self.assertIn('id="instructor-connection-list"', markup)
        self.assertIn("Sessions &amp; Terminals", markup)
        self.assertNotIn("<h3>Active Sessions</h3>", markup)


class SignedInSessionsListTests(SignedInSessionsMixin, unittest.TestCase):
    """GET /api/admin/sessions tells an Instructor who is signed in right now.

    The Terminal list moved to /api/admin/terminals so that this noun could
    mean the glossary's Session: an Instructor asking "why is this Learner
    locked out?" needs Sessions, not the shells they happen to hold.
    """

    def test_the_list_describes_every_live_session(self) -> None:
        """Each row carries username, role, created, last seen, and Terminals."""
        self.open_session(role="instructor")
        learner = create_session("learner1", "learner")
        self.terminals.terminals[learner] = 2
        self.clock.advance(30)
        touch_session(learner)

        response = self.client.get("/api/admin/sessions")

        self.assertEqual(response.status_code, 200)
        rows = {row["username"]: row for row in response.json()["sessions"]}
        self.assertEqual(
            rows["learner1"],
            {
                "session_id": learner,
                "username": "learner1",
                "role": "learner",
                "created_at": 1000.0,
                "last_seen": 1030.0,
                "last_input_at": None,
                "terminals": 2,
                **auth.session_identity(learner),
            },
        )

    def test_expired_sessions_are_swept_before_the_list_is_built(self) -> None:
        """A ghost tab must not be shown as somebody who is still signed in.

        The Instructor reads this list to decide whether to intervene, so a
        Session already past the idle timeout has to be gone from it — and its
        Terminals released — rather than merely aged.  The Instructor's own
        Session is refreshed by the request itself, which is why the two can be
        aged apart without a second clock.
        """
        stale = create_session("learner1", "learner")
        self.terminals.terminals[stale] = 1
        self.clock.advance(auth.settings.session_idle_timeout - 1)
        self.open_session(role="instructor")
        self.clock.advance(2)

        response = self.client.get("/api/admin/sessions")

        self.assertEqual(response.status_code, 200)
        signed_in = [row["username"] for row in response.json()["sessions"]]
        self.assertEqual(signed_in, ["instructor1"])
        self.assertEqual(self.terminals.closed, [stale])

    def test_the_list_carries_no_password_material(self) -> None:
        """Account credentials must never travel to a browser.

        Session records hold no password today, so the guard is against the
        list learning to publish whatever a record grows later: the fields are
        named one by one in `list_sessions`, never copied wholesale.
        """
        self.open_session(role="instructor")
        create_session("learner1", "learner")

        response = self.client.get("/api/admin/sessions")

        body = response.text
        for _username, password, _role in self.ACCOUNTS:
            self.assertNotIn(password, body)


class SignedInSessionsForceLogoutTests(SignedInSessionsMixin, unittest.TestCase):
    """POST /api/admin/sessions/logout is the Instructor's escape hatch.

    Eviction at the Session Cap is automatic and sometimes not enough: a
    Learner is stuck mid-class and the Instructor has to end that Session by
    hand.  Destroying it closes its Terminals, so the Learner's allowance is
    freed with it.
    """

    def test_force_logout_ends_the_session_and_closes_its_terminals(self) -> None:
        """The named Session must stop authenticating and give up its shells."""
        self.open_session(role="instructor")
        learner = create_session("learner1", "learner")
        self.terminals.terminals[learner] = 2

        response = self.client.post(
            "/api/admin/sessions/logout", json={"session_id": learner}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["logged_out"])
        self.assertEqual(self.terminals.closed, [learner])
        self.assertFalse(self.authenticates(learner))

    def test_force_logout_of_an_id_no_session_was_issued_under_is_a_no_op(self) -> None:
        """A stale row, a restarted server, or a Session the sweep already
        took between the refresh and the click: none may answer with an error."""
        self.open_session(role="instructor")

        response = self.client.post(
            "/api/admin/sessions/logout", json={"session_id": "never-issued"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["logged_out"])
        self.assertEqual(self.terminals.closed, [])


class InstructorRoutesRefuseLearnersTests(SignedInSessionsMixin, unittest.TestCase):
    """Every /api/admin route is an Instructor's power alone.

    Who holds which shell, who is signed in, and the ending of either are
    none of a Learner's business, and a 403 must land before the route does
    any work.
    """

    def test_a_learner_is_refused_by_every_instructor_route(self) -> None:
        """Each route answers 403 and leaves the named Terminal or Session alone."""
        victim = create_session("instructor1", "instructor")
        self.terminals.terminals[victim] = 1
        self.open_session(role="learner")
        requests = (
            ("GET", "/api/admin/terminals", None),
            ("POST", "/api/admin/terminals/close", {"terminal_id": "t1"}),
            ("GET", "/api/admin/sessions", None),
            ("POST", "/api/admin/sessions/logout", {"session_id": victim}),
        )

        with patch.object(registry, "close_terminal", new=AsyncMock()) as close_terminal:
            for method, path, body in requests:
                with self.subTest(route=f"{method} {path}"):
                    response = self.client.request(method, path, json=body)
                    self.assertEqual(response.status_code, 403)

        close_terminal.assert_not_awaited()
        self.assertEqual(self.terminals.closed, [])
        self.assertTrue(self.authenticates(victim))
