"""Public Session identity is recognizable without changing authentication."""

import unittest

from mniplab.tests.fakes import (
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    LoginClientMixin,
)
from mniplab.webserver import auth


class SessionIdentityTests(
    LoginClientMixin,
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    unittest.TestCase,
):
    """The caller can rename its own connection, retaining its public code."""

    def test_same_account_sessions_receive_distinct_public_codes(self) -> None:
        tokens = [auth.create_session("learner1", "learner") for _ in range(40)]
        rows = auth.list_sessions()
        codes = [row["connection_code"] for row in rows]
        self.assertEqual(len(set(codes)), 40)
        for token, row in zip(tokens, rows):
            self.assertTrue(row["nickname"])
            self.assertNotIn(row["connection_code"], token)
            self.assertLessEqual(len(row["connection_code"]), 8)

    def test_rename_survives_refresh_without_affecting_another_session(self) -> None:
        first = auth.create_session("learner1", "learner")
        other = auth.create_session("learner1", "learner")
        self.client.cookies.set("session", first)
        before = self.client.get("/api/me").json()
        response = self.client.patch("/api/me", json={"nickname": "  Sari  "})
        self.assertEqual(response.status_code, 200)
        refreshed = self.client.get("/api/me").json()
        self.assertEqual(refreshed["nickname"], "Sari")
        self.assertEqual(refreshed["connection_code"], before["connection_code"])
        self.assertNotEqual(auth.active_sessions[other]["nickname"], "Sari")
        self.assertEqual(self.client.cookies.get("session"), first)
        self.assertNotIn("session_id", refreshed)

    def test_rename_rejects_invalid_values_and_other_session_handles(self) -> None:
        token = auth.create_session("learner1", "learner")
        self.client.cookies.set("session", token)
        for body in (
            {"nickname": " "},
            {"nickname": "x" * 41},
            {"nickname": "two\nnames"},
            {"nickname": "name\u202e"},
            {"nickname": 123},
            {"nickname": "Sari", "session_id": "another"},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.patch("/api/me", json=body).status_code, 422
                )

    def test_nickname_punctuation_is_kept_as_data(self) -> None:
        token = auth.create_session("learner1", "learner")
        self.client.cookies.set("session", token)
        name = "Sari <host> & 'router'"
        response = self.client.patch("/api/me", json={"nickname": name})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/me").json()["nickname"], name)

    def test_rename_requires_a_live_authenticated_session(self) -> None:
        self.assertEqual(
            self.client.patch("/api/me", json={"nickname": "Sari"}).status_code, 401
        )
