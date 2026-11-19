"""Server settings: the Instructor changes a Limit while the class runs.

Seam 1 of the feature: the Instructor-only settings routes, driven over the
test client in both Lab modes.  What is checked is what an Instructor or
Learner would observe — the Limits the dialog reads back, a refusal and
the field it names, a Terminal refused at a lowered cap, an idle Session
gone after the timeout is shortened — and, for the Settings Store, what a
restarted server would read.
"""

import asyncio
import json
import os
import stat
import tempfile
import unittest
import warnings
from typing import Any, Dict
from unittest.mock import PropertyMock, patch

import pytest

from mniplab.constants import ENV_FILE_VAR
from mniplab.labhandle import LocalLabHandle
from mniplab.persistent_config import ConfigCopies
from mniplab.runner import load_lab_environment
from mniplab.tests.browser import free_port
from mniplab.tests.fakes import FAKE_LAB_KEY
from mniplab.tests.fakes import FakeLab as ScriptLab
from mniplab.tests.fakes import (
    FakeTerminalCloserMixin,
    FrozenSessionClockMixin,
    LoginClientMixin,
)
from mniplab.tests.test_web_multi_lab import MultiLabModeMixin
from mniplab.tests.test_web_terminal_budget import FakeLab, FakeSpawner
from mniplab.webserver import auth, registry, start_server, stop_server, websocket
from mniplab.webserver.auth import create_session
from mniplab.webserver.config import Settings, settings
from mniplab.webserver.groups import Groups, catalog_ids
from mniplab.webserver.private_file import write_private_json
from mniplab.webserver.services import TerminalService
from mniplab.webserver.logins import LoginStore, generate_login_password
from mniplab.webserver.server_settings import LIMITS, SettingsStore

#: The six Limits, as the dialog shows them in Multi-Lab Mode.
ALL_LIMITS = [
    "terminal_per_session_cap",
    "terminal_global_cap",
    "terminal_idle_timeout",
    "session_idle_timeout",
    "session_per_user_cap",
    "lab_cap",
]


class ServerFilesMixin:
    """Give one test a directory for the server's files, and undo what it changes.

    The Assignments file is put there, so a Settings Store opened beside it
    is too.  The live settings' Limits and the registry's store are put
    back afterwards, since a save changes the one process-wide settings
    object.

    Attributes:
        storage: The directory holding the server's files.
        store_path: Where the Settings Store is written.
    """

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)  # type: ignore[attr-defined]
        self.storage = tmp.name
        self.store_path = os.path.join(self.storage, "settings.json")
        for limit in LIMITS:
            self.addCleanup(  # type: ignore[attr-defined]
                setattr, settings, limit.name, getattr(settings, limit.name)
            )
        previous_groups = registry.groups
        registry.groups = Groups(os.path.join(self.storage, "assignments.json"))
        self.addCleanup(setattr, registry, "groups", previous_groups)  # type: ignore[attr-defined]
        previous_store = registry.settings_store
        self.addCleanup(setattr, registry, "settings_store", previous_store)  # type: ignore[attr-defined]
        registry.settings_store = None
        self.addCleanup(settings.use_login_store, settings.login_store)  # type: ignore[attr-defined]
        settings.use_login_store(None)


class ServeMixin(ServerFilesMixin):
    """Run one test as a server started with ``serve``, with its routes' helpers."""

    def setUp(self) -> None:
        super().setUp()
        self.serve()

    def serve(self) -> None:
        """Open the Settings Store as ``serve`` does at start, over the same files."""
        registry.settings_store = SettingsStore(self.store_path, settings)
        registry.settings_store.load()
        settings.use_login_store(LoginStore(self.logins_path))

    @property
    def logins_path(self) -> str:
        """Where the logins added on the page are written."""
        return os.path.join(self.storage, "accounts.json")

    def limits(self) -> Dict[str, Dict[str, Any]]:
        """The Limits the dialog reads, by name."""
        response = self.client.get("/api/admin/server-settings")  # type: ignore[attr-defined]
        assert response.status_code == 200, response.text
        return {limit["name"]: limit for limit in response.json()["limits"]}

    def save(self, **limits: Any) -> Any:
        """Save some Limits as the dialog does, and return the answer."""
        return self.client.put(  # type: ignore[attr-defined]
            "/api/admin/server-settings", json={"limits": limits}
        )


@pytest.mark.unit
class ReadLimitsTests(ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Instructor reads the Limits; a Learner reaches no settings route."""

    def test_the_instructor_reads_five_limits_in_single_lab_mode(self) -> None:
        self.open_session(role="instructor")

        limits = self.limits()

        self.assertEqual(list(limits), ALL_LIMITS[:5])
        self.assertEqual(limits["terminal_idle_timeout"]["value"], settings.terminal_idle_timeout)

    def test_a_learner_is_refused_every_settings_route(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.client.get("/api/admin/server-settings").status_code, 403)
        self.assertEqual(self.save(terminal_idle_timeout=600).status_code, 403)
        self.assertEqual(settings.terminal_idle_timeout, 300)

    def test_the_instructor_reads_six_limits_in_multi_lab_mode(self) -> None:
        self.open_session(role="instructor")

        with patch.object(settings, "lab_mode", "multi"):
            limits = self.limits()

        self.assertEqual(list(limits), ALL_LIMITS)
        self.assertEqual(limits["lab_cap"]["value"], settings.lab_cap)


@pytest.mark.unit
class SaveLimitsTests(ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """A save applies at once, is kept in the Settings Store, and survives a restart."""

    def stored(self) -> Dict[str, Any]:
        """What the Settings Store file holds."""
        with open(self.store_path, encoding="utf-8") as f:
            return json.load(f)

    def test_a_save_applies_at_once_and_stores_only_what_changed(self) -> None:
        self.open_session(role="instructor")

        saved = self.save(terminal_idle_timeout=600, terminal_per_session_cap=5)

        self.assertEqual(saved.status_code, 200, saved.text)
        values = {limit["name"]: limit["value"] for limit in saved.json()["limits"]}
        self.assertEqual(values["terminal_idle_timeout"], 600)
        self.assertEqual(settings.terminal_idle_timeout, 600)
        self.assertEqual(self.limits()["terminal_idle_timeout"]["value"], 600)
        # The cap was saved at the value it already had: nothing to override.
        self.assertEqual(self.stored(), {"terminal_idle_timeout": 600})
        self.assertEqual(stat.S_IMODE(os.stat(self.store_path).st_mode), 0o600)

    def test_each_limit_a_save_changes_is_logged_with_who_and_old_and_new(self) -> None:
        self.open_session(role="instructor")

        with self.assertLogs("mniplab.webserver.server_settings", level="INFO") as logged:
            self.save(terminal_idle_timeout=600, session_per_user_cap=2, terminal_global_cap=100)

        self.assertEqual(len(logged.records), 2, logged.output)
        self.assertIn("instructor1", logged.output[0])
        self.assertIn("terminal_idle_timeout", logged.output[0])
        self.assertIn("from 300 to 600", logged.output[0])
        self.assertIn("from 0 to 2", logged.output[1])

    def test_lab_cap_is_saved_in_multi_lab_mode_and_refused_in_single(self) -> None:
        self.open_session(role="instructor")

        refused = self.save(lab_cap=2)
        with patch.object(settings, "lab_mode", "multi"):
            saved = self.save(lab_cap=2)

        self.assertEqual((refused.status_code, refused.json()["field"]), (422, "lab_cap"))
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(settings.lab_cap, 2)
        self.assertEqual(registry.lab_cap, 1, "Single-Lab Mode is a cap of one whatever is saved")


@pytest.mark.unit
class MultiLabModeTests(ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Saving and refusing work the same in Multi-Lab Mode, Labs alive included."""

    def setUp(self) -> None:
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        super().setUp()

    def test_a_learner_is_refused_every_settings_route(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.client.get("/api/admin/server-settings").status_code, 403)
        self.assertEqual(self.save(lab_cap=2).status_code, 403)
        self.assertEqual(settings.lab_cap, 4)

    def test_a_save_applies_at_once_and_survives_a_restart(self) -> None:
        self.open_session(role="instructor")

        saved = self.save(lab_cap=2, session_idle_timeout=3600)
        restarted = Settings(lab_mode="multi")
        SettingsStore(self.store_path, restarted).load()

        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual((settings.lab_cap, registry.lab_cap), (2, 2))
        self.assertEqual((restarted.lab_cap, restarted.session_idle_timeout), (2, 3600))

    def test_a_refused_save_changes_nothing(self) -> None:
        self.open_session(role="instructor")

        refused = self.save(lab_cap=2, terminal_per_session_cap=11)

        self.assertEqual((refused.status_code, refused.json()["field"]), (422, "terminal_per_session_cap"))
        self.assertEqual(settings.lab_cap, 4)
        self.assertFalse(os.path.exists(self.store_path))


@pytest.mark.unit
class WhereItRunsTests(ServerFilesMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Only a server started with ``serve`` reads the store and offers Server settings."""

    def setUp(self) -> None:
        super().setUp()
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump({"terminal_idle_timeout": 600}, f)

    def offers_server_settings(self) -> bool:
        """Whether the page would show the Instructor the toolbar item."""
        self.open_session(role="instructor")
        return self.client.get("/api/me").json()["server_settings"]

    def test_serve_reads_the_store_and_offers_server_settings(self) -> None:
        start_server(None, host="127.0.0.1", port=free_port(), block=False)
        self.addCleanup(stop_server)

        self.assertEqual(settings.terminal_idle_timeout, 600)
        self.assertTrue(self.offers_server_settings())
        self.assertEqual(self.client.get("/api/admin/server-settings").status_code, 200)

    def test_a_lab_script_ignores_a_leftover_store_and_offers_nothing(self) -> None:
        start_server(ScriptLab(), host="127.0.0.1", port=free_port(), block=False)
        self.addCleanup(stop_server)
        self.addCleanup(lambda: asyncio.run(registry.stop_lab(FAKE_LAB_KEY)))

        self.assertEqual(settings.terminal_idle_timeout, 300)
        self.assertFalse(self.offers_server_settings())
        self.assertEqual(self.client.get("/api/admin/server-settings").status_code, 404)
        self.assertEqual(self.save_status(), 404)
        with open(self.store_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"terminal_idle_timeout": 600})

    def test_serve_accepts_page_added_logins_and_banners_only_their_count(self) -> None:
        write_private_json(
            os.path.join(self.storage, "accounts.json"),
            {"version": 1, "logins": {"table-3": "crane-ruby-4827", "table-4": "dawn-mesa-2345"}},
        )
        banner: list = []
        with patch("mniplab.webserver.info", side_effect=banner.append), \
                patch.object(settings, "users", "boss:pw12345a:instructor"):
            start_server(None, host="127.0.0.1", port=free_port(), block=False)
            self.addCleanup(stop_server)
            accepted = settings.get_user_registry()

        self.assertEqual(accepted["table-3"]["password"], "crane-ruby-4827")
        printed = "".join(banner)
        self.assertIn("boss / pw12345a / instructor", printed)
        self.assertIn("2 logins added in Server settings", printed)
        self.assertNotIn("crane-ruby-4827", printed)
        self.assertNotIn("table-3", printed)

    def test_a_lab_script_ignores_a_leftover_login_file(self) -> None:
        write_private_json(
            os.path.join(self.storage, "accounts.json"),
            {"version": 1, "logins": {"table-3": "crane-ruby-4827"}},
        )
        start_server(ScriptLab(), host="127.0.0.1", port=free_port(), block=False)
        self.addCleanup(stop_server)
        self.addCleanup(lambda: asyncio.run(registry.stop_lab(FAKE_LAB_KEY)))

        self.assertNotIn("table-3", settings.get_user_registry())
        self.open_session(role="instructor")
        self.assertEqual(self.client.get("/api/admin/logins").status_code, 404)
        self.assertEqual(
            self.client.post("/api/admin/logins", json={"name": "table-4"}).status_code, 404
        )

    def save_status(self) -> int:
        """The status of a save attempted as the signed-in account."""
        return self.client.put(
            "/api/admin/server-settings", json={"limits": {"terminal_idle_timeout": 900}}
        ).status_code

    def test_serve_mentions_a_generated_password_only_when_a_login_uses_it(self) -> None:
        # With MNIPLAB_USERS set, the shared login is not accepted, so a
        # password generated for it is no one's and the hint would mislead.
        for users, shown in (("", True), ("boss:pw12345a:instructor", False)):
            with self.subTest(users=users):
                banner: list = []
                with patch("mniplab.webserver.info", side_effect=banner.append), \
                        patch.object(settings, "users", users), \
                        patch.object(Settings, "password_was_generated",
                                     new_callable=PropertyMock, return_value=True):
                    start_server(None, host="127.0.0.1", port=free_port(), block=False)
                    stop_server()
                self.assertEqual("password generated for this run" in "".join(banner), shown)


@pytest.mark.unit
class RestartTests(ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """A restarted server reads the store again: environment > store > .env > default."""

    def restart(self, **sources: Any) -> Settings:
        """The settings a server restarted over the same files comes up with.

        Args:
            sources: Passed to ``Settings``, e.g. ``_env_file``.
        """
        restarted = Settings(**sources)
        SettingsStore(self.store_path, restarted).load()
        return restarted

    def test_a_restart_picks_the_stored_values_up_again(self) -> None:
        self.open_session(role="instructor")
        self.save(terminal_idle_timeout=600, session_per_user_cap=3)

        restarted = self.restart()

        self.assertEqual(restarted.terminal_idle_timeout, 600)
        self.assertEqual(restarted.session_per_user_cap, 3)
        self.assertEqual(restarted.session_idle_timeout, 9000, "a Limit never saved keeps its default")

    def test_the_store_wins_over_env_file_and_the_env_file_stays_the_fallback(self) -> None:
        env_file = os.path.join(self.storage, ".env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("MNIPLAB_TERMINAL_IDLE_TIMEOUT=400\nMNIPLAB_SESSION_IDLE_TIMEOUT=4000\n")
        self.open_session(role="instructor")
        self.save(terminal_idle_timeout=600)

        restarted = self.restart(_env_file=env_file)

        self.assertEqual(restarted.terminal_idle_timeout, 600)
        self.assertEqual(restarted.session_idle_timeout, 4000)

    def test_the_servers_environment_wins_over_the_store(self) -> None:
        self.open_session(role="instructor")
        self.save(terminal_idle_timeout=600, terminal_global_cap=50, session_idle_timeout=7200)

        pins = {"MNIPLAB_TERMINAL_IDLE_TIMEOUT": "900", "MNIPLAB_PTY_GLOBAL_CAP": "70"}
        with patch.dict(os.environ, pins), warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            restarted = self.restart()

        self.assertEqual(restarted.terminal_idle_timeout, 900)
        self.assertEqual(restarted.terminal_global_cap, 70, "an older spelling pins it too")
        self.assertEqual(restarted.session_idle_timeout, 7200)


@pytest.mark.unit
class TerminalCapTests(
    ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.IsolatedAsyncioTestCase
):
    """A changed Terminals-per-Session cap meets the next Terminal; open ones run on.

    Terminals are opened through the spawner seam, as the Terminal budget
    tests do; the cap is changed through the settings route.
    """

    async def asyncSetUp(self) -> None:
        self.spawner = FakeSpawner()
        self.addCleanup(self.spawner.cleanup)
        self.service = TerminalService(spawner=self.spawner)
        self.lab = LocalLabHandle(FakeLab())
        grace = patch("mniplab.webserver.services.REAP_GRACE_PERIOD", 0)
        grace.start()
        self.addCleanup(grace.stop)
        self.open_session(role="instructor")

    async def open_terminal(self) -> Any:
        """Open one Terminal for the Learner's Session."""
        return await self.service.spawn_terminal(self.lab, "learner-token", "h1", "learner1")

    async def test_raising_the_cap_lets_a_session_at_the_old_cap_open_another(self) -> None:
        for _ in range(5):
            await self.open_terminal()
        with self.assertRaises(ValueError):
            await self.open_terminal()

        self.assertEqual(self.save(terminal_per_session_cap=6).status_code, 200)
        await self.open_terminal()

        self.assertEqual(self.service.terminal_count("learner-token"), 6)
        await self.service.close_session_terminals("learner-token")

    async def test_lowering_the_cap_refuses_the_next_and_leaves_open_ones_running(self) -> None:
        opened = [await self.open_terminal() for _ in range(3)]

        self.assertEqual(self.save(terminal_per_session_cap=2).status_code, 200)
        with self.assertRaises(ValueError) as refused:
            await self.open_terminal()

        self.assertIn("Terminal cap exceeded: 3/2", str(refused.exception))
        self.assertEqual(set(self.service.terminals), {t.id for t in opened})
        for child in self.spawner.children:
            self.assertIsNone(child.poll(), "an open Terminal's shell was ended")
        await self.service.close_session_terminals("learner-token")


@pytest.mark.unit
class PrivateFileTests(unittest.TestCase):
    """The server's own files are replaced whole and readable by their owner only."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(self.dir, "settings.json")

    def test_the_file_is_written_at_mode_0600(self) -> None:
        write_private_json(self.path, {"lab_cap": 3})

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"lab_cap": 3})

    def test_a_failed_write_leaves_the_old_file_whole_and_nothing_beside_it(self) -> None:
        write_private_json(self.path, {"lab_cap": 3})

        with self.assertRaises(TypeError):
            write_private_json(self.path, {"lab_cap": object()})

        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"lab_cap": 3})
        self.assertEqual(os.listdir(self.dir), ["settings.json"])


@pytest.mark.unit
class TerminalReaderPoolTests(unittest.TestCase):
    """The Terminal output pool fits the largest server-wide cap a save allows."""

    def test_the_pool_is_sized_from_a_fixed_ceiling_not_the_cap_at_start(self) -> None:
        largest_cap = next(l.maximum for l in LIMITS if l.name == "terminal_global_cap")
        with patch.object(websocket, "_terminal_reader_executor", None), \
                patch.object(settings, "terminal_global_cap", 1):
            pool = websocket.get_terminal_reader_executor()
            self.addCleanup(pool.shutdown, wait=False)

            # Every Terminal holds a reader thread for its whole life.
            self.assertGreaterEqual(pool._max_workers, largest_cap)


@pytest.mark.unit
class SessionTimeoutTests(
    ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, FrozenSessionClockMixin, unittest.TestCase
):
    """A shortened idle Session timeout reaches Sessions already open."""

    def test_lowering_the_idle_session_timeout_ends_an_already_idle_session(self) -> None:
        idle = create_session("learner2", "learner")
        self.open_session(role="instructor")
        self.clock.advance(600)

        self.assertEqual(self.save(session_idle_timeout=300).status_code, 200)
        listed = self.client.get("/api/admin/sessions")

        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(idle, auth.active_sessions, "the idle Session outlived the shorter timeout")
        self.assertEqual(self.terminals.closed, [idle])


@pytest.mark.unit
class RefusedSaveTests(ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """A refused save names its field and changes nothing at all."""

    def assert_refused(self, response: Any, field: str) -> str:
        """Assert a save was refused over ``field``, with nothing changed.

        Returns:
            The refusal's message.
        """
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["field"], field)
        self.assertFalse(os.path.exists(self.store_path), "a refused save wrote the store")
        self.assertEqual(settings.terminal_idle_timeout, 300)
        self.assertEqual(settings.terminal_per_session_cap, 5)
        return response.json()["detail"]

    def test_a_value_outside_its_bounds_is_refused(self) -> None:
        self.open_session(role="instructor")

        for name, value in (
            ("terminal_idle_timeout", 59),
            ("session_idle_timeout", 30),
            ("terminal_per_session_cap", 0),
            ("terminal_global_cap", 1001),
            ("session_per_user_cap", -1),
        ):
            with self.subTest(name=name):
                message = self.assert_refused(self.save(**{name: value}), name)
                self.assertIn("between", message)

    def test_a_value_that_is_not_a_whole_number_is_refused(self) -> None:
        self.open_session(role="instructor")

        for value in ("600", 600.5, True):
            with self.subTest(value=value):
                self.assert_refused(self.save(terminal_idle_timeout=value), "terminal_idle_timeout")

    def test_a_value_the_cross_field_validators_reject_is_refused(self) -> None:
        """Eleven Terminals a Session would outrun the default reconnect budget of 30."""
        self.open_session(role="instructor")

        self.assert_refused(self.save(terminal_per_session_cap=11), "terminal_per_session_cap")

    def test_a_store_that_cannot_be_written_leaves_the_live_settings_alone(self) -> None:
        store = registry.settings_store
        assert store is not None

        with patch("mniplab.webserver.server_settings.write_private_json",
                   side_effect=OSError("disk full")), self.assertRaises(OSError):
            store.save({"terminal_idle_timeout": 600}, "instructor1")

        self.assertEqual(settings.terminal_idle_timeout, 300)

    def test_one_refused_field_refuses_the_whole_save(self) -> None:
        self.open_session(role="instructor")

        self.assert_refused(
            self.save(terminal_idle_timeout=600, terminal_per_session_cap=11),
            "terminal_per_session_cap",
        )

    def test_the_idle_terminal_timeout_below_what_pings_need_says_so_in_the_dialogs_words(self) -> None:
        self.open_session(role="instructor")

        with patch.object(settings, "terminal_ping_interval", 100):
            message = self.assert_refused(self.save(terminal_idle_timeout=100), "terminal_idle_timeout")

        self.assertEqual(
            message,
            "Idle Terminal timeout must be at least 101 seconds, so Terminals stay connected.",
        )

    def test_a_cap_too_high_for_the_reconnect_budget_names_only_what_the_dialog_shows(self) -> None:
        self.open_session(role="instructor")

        message = self.assert_refused(self.save(terminal_per_session_cap=11), "terminal_per_session_cap")

        self.assertEqual(
            message,
            "Terminals per Session must be at most 10, so reloading the page "
            "does not lock a Learner out.",
        )
        self.assertNotIn("ws_", message)


@pytest.mark.unit
class ClearLimitTests(ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """Clearing a Limit returns it to the value the server was started with."""

    def read_store(self) -> Dict[str, Any]:
        with open(self.store_path, encoding="utf-8") as f:
            return json.load(f)

    def test_the_read_route_reports_value_started_with_bounds_and_pinned(self) -> None:
        self.open_session(role="instructor")
        self.save(terminal_idle_timeout=600)

        limit = self.limits()["terminal_idle_timeout"]

        self.assertEqual(
            limit,
            {"name": "terminal_idle_timeout", "value": 600, "started_with": 300,
             "min": 60, "max": 86400, "pinned": False},
        )

    def test_clearing_a_field_removes_it_from_the_store_and_restores_the_started_value(self) -> None:
        self.open_session(role="instructor")
        self.save(terminal_idle_timeout=600, session_per_user_cap=3)

        response = self.save(terminal_idle_timeout=None)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(settings.terminal_idle_timeout, 300)
        self.assertEqual(self.read_store(), {"session_per_user_cap": 3})
        self.assertEqual(self.limits()["terminal_idle_timeout"]["value"], 300)

    def test_a_cleared_limit_reads_as_the_env_file_value_after_a_restart(self) -> None:
        env_file = os.path.join(self.storage, ".env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("MNIPLAB_TERMINAL_IDLE_TIMEOUT=400\n")
        # pydantic-settings accepts _env_file at runtime without declaring it, so mypy flags it here.
        registry.settings_store = SettingsStore(self.store_path, Settings(_env_file=env_file))  # type: ignore[call-arg]
        live = registry.settings_store.live
        self.open_session(role="instructor")
        registry.settings_store.save({"terminal_idle_timeout": 600}, "i")

        registry.settings_store.save({"terminal_idle_timeout": None}, "i")

        self.assertEqual(live.terminal_idle_timeout, 400)
        restarted = Settings(_env_file=env_file)  # type: ignore[call-arg]
        SettingsStore(self.store_path, restarted).load()
        self.assertEqual(restarted.terminal_idle_timeout, 400)

    def test_clearing_is_checked_with_the_other_values_of_the_same_save(self) -> None:
        self.open_session(role="instructor")
        self.save(terminal_idle_timeout=600)

        with patch.object(settings, "terminal_ping_interval", 400):
            response = self.save(terminal_idle_timeout=None)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.read_store(), {"terminal_idle_timeout": 600})

    def test_deleting_the_store_returns_every_limit_and_touches_nothing_else(self) -> None:
        self.open_session(role="instructor")
        self.save(terminal_idle_timeout=600, terminal_per_session_cap=7)
        os.remove(self.store_path)
        beside = os.path.join(self.storage, "assignments.json")
        with open(beside, "w", encoding="utf-8") as f:
            f.write("{}")

        restarted = Settings()
        SettingsStore(self.store_path, restarted).load()

        self.assertEqual(restarted.terminal_idle_timeout, 300)
        self.assertEqual(restarted.terminal_per_session_cap, 5)
        self.assertTrue(os.path.exists(beside))


@pytest.mark.unit
class PinnedLimitTests(ServeMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """A Limit exported where the server was started is shown fixed and cannot be saved."""

    def test_the_read_route_marks_a_limit_the_environment_sets_as_pinned(self) -> None:
        self.open_session(role="instructor")

        with patch.dict(os.environ, {"MNIPLAB_PTY_GLOBAL_CAP": "70"}):
            limits = self.limits()

        self.assertTrue(limits["terminal_global_cap"]["pinned"], "an older spelling pins it too")
        self.assertFalse(limits["terminal_idle_timeout"]["pinned"])

    def test_a_save_that_changes_a_pinned_limit_is_refused_and_changes_nothing(self) -> None:
        self.open_session(role="instructor")

        with patch.dict(os.environ, {"MNIPLAB_TERMINAL_IDLE_TIMEOUT": "300"}):
            changing = self.save(terminal_idle_timeout=600, session_per_user_cap=3)
            clearing = self.save(terminal_idle_timeout=None)

        for response in (changing, clearing):
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["field"], "terminal_idle_timeout")
            self.assertIn("set where the server was started", response.json()["detail"])
        self.assertEqual(settings.terminal_idle_timeout, 300)
        self.assertEqual(settings.session_per_user_cap, 0)
        self.assertFalse(os.path.exists(self.store_path))

    def test_a_limit_set_in_the_env_file_under_serve_is_not_pinned(self) -> None:
        env_file = os.path.join(self.storage, ".env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("MNIPLAB_TERMINAL_IDLE_TIMEOUT=420\n")
        with patch.dict(os.environ, {ENV_FILE_VAR: env_file}):
            os.environ.pop("MNIPLAB_TERMINAL_IDLE_TIMEOUT", None)
            # What `python -m mniplab serve` runs before the server starts.
            load_lab_environment()
            registry.settings_store = SettingsStore(self.store_path, Settings(_env_file=env_file))  # type: ignore[call-arg]
            registry.settings_store.load()
            self.open_session(role="instructor")

            pinned = self.limits()["terminal_idle_timeout"]["pinned"]
            response = self.save(terminal_idle_timeout=600)
            restarted = Settings(_env_file=env_file)  # type: ignore[call-arg]
            SettingsStore(self.store_path, restarted).load()

        self.assertFalse(pinned, ".env is below the Settings Store, not a shell export")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(restarted.terminal_idle_timeout, 600)


class LoginRoutesMixin(ServeMixin):
    """The login routes as the dialog calls them."""

    def setUp(self) -> None:
        super().setUp()
        # The Session store is process-wide: a Session another test left for
        # the same login would be ended, and counted, by a reset or remove.
        auth.active_sessions.clear()
        self.addCleanup(auth.active_sessions.clear)  # type: ignore[attr-defined]

    def logins(self) -> Dict[str, Dict[str, Any]]:
        """Every login the dialog lists, by name."""
        response = self.client.get("/api/admin/logins")  # type: ignore[attr-defined]
        assert response.status_code == 200, response.text
        return {login["name"]: login for login in response.json()["logins"]}

    def add(self, name: str) -> Any:
        """Add a login as the dialog does, and return the answer."""
        return self.client.post("/api/admin/logins", json={"name": name})  # type: ignore[attr-defined]

    def show(self, name: str) -> Any:
        """Ask for a login's password as the dialog does."""
        return self.client.get(f"/api/admin/logins/{name}/password")  # type: ignore[attr-defined]

    def reset(self, name: str) -> Any:
        """Reset a login's password as the dialog does, and return the answer."""
        return self.client.post(f"/api/admin/logins/{name}/reset")  # type: ignore[attr-defined]

    def remove(self, name: str) -> Any:
        """Remove a login as the dialog does, and return the answer."""
        return self.client.delete(f"/api/admin/logins/{name}")  # type: ignore[attr-defined]

    def signs_in(self, name: str, password: str) -> bool:
        """Whether a fresh browser signs in with this login and password."""
        from fastapi.testclient import TestClient

        auth.login_limiter.reset(name)
        self.addCleanup(auth.login_limiter.reset, name)  # type: ignore[attr-defined]
        browser = TestClient(self.client.app)  # type: ignore[attr-defined]
        return browser.post("/api/login", auth=(name, password)).status_code == 200


@pytest.mark.unit
class AddLoginTests(LoginRoutesMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Instructor adds a Learner login and reads its password (Single-Lab Mode)."""

    def test_the_list_marks_env_accounts_and_carries_no_passwords(self) -> None:
        self.open_session(role="instructor")
        self.add("table-3")

        response = self.client.get("/api/admin/logins")

        self.assertEqual(
            response.json()["logins"],
            [
                {"name": "learner1", "role": "learner", "added": False},
                {"name": "learner2", "role": "learner", "added": False},
                {"name": "instructor1", "role": "instructor", "added": False},
                {"name": "table-3", "role": "learner", "added": True},
            ],
        )
        self.assertNotIn("password", response.text)
        self.assertNotIn("lab12345", response.text)

    def test_an_added_login_gets_a_generated_password_and_signs_in_with_it(self) -> None:
        self.open_session(role="instructor")

        added = self.add("table-3")

        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual(added.json()["name"], "table-3")
        password = added.json()["password"]
        self.assertRegex(password, f"^{PASSWORD_SHAPE}$")
        self.assertTrue(self.signs_in("table-3", password))
        self.assertFalse(self.signs_in("table-3", "wrong-password-2345"))
        self.assertEqual(self.logins()["table-3"]["role"], "learner")

    def test_an_added_login_is_kept_atomically_at_mode_0600(self) -> None:
        self.open_session(role="instructor")

        password = self.add("table-3").json()["password"]

        self.assertEqual(stat.S_IMODE(os.stat(self.logins_path).st_mode), 0o600)
        self.assertEqual(os.listdir(self.storage), ["accounts.json"], "no temporary file left")
        self.assertEqual(LoginStore(self.logins_path).passwords(), {"table-3": password})

    def test_an_added_login_survives_a_restart(self) -> None:
        self.open_session(role="instructor")
        password = self.add("table-3").json()["password"]

        self.serve()

        self.assertEqual(self.show("table-3").json()["password"], password)
        self.assertTrue(self.signs_in("table-3", password))

    def test_a_name_outside_the_rule_is_refused(self) -> None:
        self.open_session(role="instructor")

        for name in ("a", "Table3", "3table", "-table", "table_3", "table 3", "t" * 33, "tâble"):
            with self.subTest(name=name):
                refused = self.add(name)
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertIn("lowercase letters, digits and hyphens", refused.json()["detail"])
        self.assertEqual(self.add("ab").status_code, 200)
        self.assertEqual(self.add("t" * 32).status_code, 200)
        self.assertEqual(self.add("table-3x9").status_code, 200)

    def test_a_name_already_a_login_is_refused(self) -> None:
        self.open_session(role="instructor")
        self.add("table-3")

        for name in ("table-3", "learner1", "instructor1"):
            with self.subTest(name=name):
                refused = self.add(name)
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertIn("already a login", refused.json()["detail"])
        self.assertEqual(len(LoginStore(self.logins_path).passwords()), 1)

    def test_show_reads_back_a_page_added_password_and_not_an_env_one(self) -> None:
        self.open_session(role="instructor")
        password = self.add("table-3").json()["password"]

        self.assertEqual(self.show("table-3").json(), {"name": "table-3", "password": password})
        self.assertEqual(self.show("learner1").status_code, 404)
        self.assertEqual(self.show("nobody").status_code, 404)

    def test_add_and_show_each_log_one_line_without_the_password(self) -> None:
        self.open_session(role="instructor")

        with self.assertLogs("mniplab.webserver.logins", level="INFO") as logged:
            password = self.add("table-3").json()["password"]
            self.show("table-3")

        self.assertEqual(len(logged.output), 2, logged.output)
        for line in logged.output:
            self.assertIn("instructor1", line)
            self.assertIn("table-3", line)
            self.assertNotIn(password, line)

    def test_a_learner_is_refused_every_login_route(self) -> None:
        self.open_session(role="instructor")
        self.add("table-3")
        self.open_session(role="learner")

        self.assertEqual(self.client.get("/api/admin/logins").status_code, 403)
        self.assertEqual(self.add("table-4").status_code, 403)
        self.assertEqual(self.show("table-3").status_code, 403)
        self.assertNotIn("table-4", LoginStore(self.logins_path).passwords())


@pytest.mark.unit
class AddGroupTests(LoginRoutesMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """In Multi-Lab Mode an added login is a Group like any other."""

    def setUp(self) -> None:
        mode = patch.object(settings, "lab_mode", "multi")
        mode.start()
        self.addCleanup(mode.stop)
        super().setUp()

    def test_an_added_login_is_a_group_on_the_overview_and_takes_an_assignment(self) -> None:
        self.open_session(role="instructor")
        password = self.add("table-3").json()["password"]
        example = catalog_ids()[0]

        assigned = self.client.put(
            "/api/admin/assignments/table-3", json={"example_ids": [example]}
        )
        groups = {g["name"]: g for g in self.client.get("/api/admin/dashboard").json()["groups"]}

        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(groups["table-3"]["role"], "learner")
        self.assertEqual(groups["table-3"]["assignment"], [example])
        self.assertTrue(self.signs_in("table-3", password))

    def test_a_group_added_beside_the_single_env_login_has_only_its_assignment(self) -> None:
        """With only MNIPLAB_PASSWORD set, the one .env login holds the whole
        catalog; a Group added on the page holds what it is assigned."""
        self.open_session(role="instructor")
        self.add("table-3")
        with patch.object(settings, "users", ""):
            self.assertEqual(registry.groups.assignment("table-3"), [])
            self.assertEqual(registry.groups.assignment(settings.username), catalog_ids())

    def test_a_learner_is_refused_every_login_route(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.client.get("/api/admin/logins").status_code, 403)
        self.assertEqual(self.add("table-4").status_code, 403)

    def test_name_rules_and_collisions_hold_in_multi_lab_mode(self) -> None:
        self.open_session(role="instructor")

        self.assertEqual(self.add("Table").status_code, 422)
        self.assertEqual(self.add("learner2").status_code, 409)
        self.assertEqual(self.add("table-3").status_code, 200)


@pytest.mark.unit
class ResetLoginTests(LoginRoutesMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Instructor resets a page-added login's password (Single-Lab Mode)."""

    def test_reset_gives_a_new_password_and_the_old_one_stops_working(self) -> None:
        self.open_session(role="instructor")
        old = self.add("table-3").json()["password"]

        reset = self.reset("table-3")

        self.assertEqual(reset.status_code, 200, reset.text)
        new = reset.json()["password"]
        self.assertEqual(reset.json()["name"], "table-3")
        self.assertRegex(new, f"^{PASSWORD_SHAPE}$")
        self.assertNotEqual(new, old)
        self.assertFalse(self.signs_in("table-3", old))
        self.assertTrue(self.signs_in("table-3", new))
        self.assertEqual(self.show("table-3").json()["password"], new)
        self.assertEqual(LoginStore(self.logins_path).passwords(), {"table-3": new})
        self.assertEqual(stat.S_IMODE(os.stat(self.logins_path).st_mode), 0o600)

    def test_reset_ends_every_session_of_the_login_and_only_that_logins(self) -> None:
        instructor = self.open_session(role="instructor")
        self.add("table-3")
        first = create_session("table-3", "learner")
        second = create_session("table-3", "learner")
        other = create_session("learner1", "learner")

        self.reset("table-3")

        self.assertNotIn(first, auth.active_sessions)
        self.assertNotIn(second, auth.active_sessions)
        self.assertIn(other, auth.active_sessions)
        self.assertIn(instructor, auth.active_sessions)
        self.assertCountEqual(self.terminals.closed, [first, second])

    def test_reset_logs_one_line_without_either_password(self) -> None:
        self.open_session(role="instructor")
        old = self.add("table-3").json()["password"]

        with self.assertLogs("mniplab.webserver.logins", level="INFO") as logged:
            new = self.reset("table-3").json()["password"]

        self.assertEqual(len(logged.output), 1, logged.output)
        self.assertIn("instructor1", logged.output[0])
        self.assertIn("table-3", logged.output[0])
        self.assertNotIn(old, logged.output[0])
        self.assertNotIn(new, logged.output[0])

    def test_reset_is_not_accepted_for_an_env_account_or_a_stranger(self) -> None:
        self.open_session(role="instructor")
        victim = create_session("learner1", "learner")

        for name in ("learner1", "instructor1", "nobody"):
            with self.subTest(name=name):
                self.assertEqual(self.reset(name).status_code, 404)
        self.assertIn(victim, auth.active_sessions)
        self.assertFalse(os.path.exists(self.logins_path))
        self.assertTrue(self.signs_in("learner1", "lab12345"))

    def test_a_login_file_that_cannot_be_written_leaves_the_password_and_sessions_alone(self) -> None:
        self.open_session(role="instructor")
        old = self.add("table-3").json()["password"]
        held = create_session("table-3", "learner")

        with patch("mniplab.webserver.logins.write_private_json",
                   side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.reset("table-3")

        self.assertEqual(LoginStore(self.logins_path).passwords(), {"table-3": old})
        self.assertTrue(self.signs_in("table-3", old))
        self.assertIn(held, auth.active_sessions)

    def test_a_learner_is_refused_reset(self) -> None:
        self.open_session(role="instructor")
        old = self.add("table-3").json()["password"]
        self.open_session(role="learner")

        self.assertEqual(self.reset("table-3").status_code, 403)
        self.assertEqual(LoginStore(self.logins_path).passwords(), {"table-3": old})


@pytest.mark.unit
class RemoveLoginTests(LoginRoutesMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """The Instructor removes a page-added login (Single-Lab Mode)."""

    def test_remove_deletes_the_login_so_it_no_longer_signs_in_or_lists(self) -> None:
        self.open_session(role="instructor")
        password = self.add("table-3").json()["password"]

        removed = self.remove("table-3")

        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json(), {"name": "table-3", "removed": True})
        self.assertFalse(self.signs_in("table-3", password))
        self.assertNotIn("table-3", self.logins())
        self.assertEqual(self.show("table-3").status_code, 404)
        self.assertEqual(LoginStore(self.logins_path).passwords(), {})
        self.serve()
        self.assertNotIn("table-3", self.logins())

    def test_remove_ends_every_session_of_the_login_and_only_that_logins(self) -> None:
        instructor = self.open_session(role="instructor")
        self.add("table-3")
        first = create_session("table-3", "learner")
        second = create_session("table-3", "learner")
        other = create_session("learner1", "learner")

        self.remove("table-3")

        self.assertNotIn(first, auth.active_sessions)
        self.assertNotIn(second, auth.active_sessions)
        self.assertIn(other, auth.active_sessions)
        self.assertIn(instructor, auth.active_sessions)
        self.assertCountEqual(self.terminals.closed, [first, second])

    def test_a_removed_name_can_be_added_again_with_a_new_password(self) -> None:
        self.open_session(role="instructor")
        old = self.add("table-3").json()["password"]
        self.remove("table-3")

        again = self.add("table-3")

        self.assertEqual(again.status_code, 200, again.text)
        self.assertNotEqual(again.json()["password"], old)

    def test_remove_logs_one_line_without_the_password(self) -> None:
        self.open_session(role="instructor")
        password = self.add("table-3").json()["password"]

        with self.assertLogs("mniplab.webserver.logins", level="INFO") as logged:
            self.remove("table-3")

        self.assertEqual(len(logged.output), 1, logged.output)
        self.assertIn("instructor1", logged.output[0])
        self.assertIn("table-3", logged.output[0])
        self.assertNotIn(password, logged.output[0])

    def test_remove_is_not_accepted_for_an_env_account_or_a_stranger(self) -> None:
        self.open_session(role="instructor")
        victim = create_session("learner1", "learner")

        for name in ("learner1", "instructor1", "nobody"):
            with self.subTest(name=name):
                self.assertEqual(self.remove(name).status_code, 404)
        self.assertIn(victim, auth.active_sessions)
        self.assertTrue(self.signs_in("learner1", "lab12345"))
        self.assertIn("learner1", self.logins())

    def test_a_login_file_that_cannot_be_written_leaves_the_login_and_sessions_alone(self) -> None:
        self.open_session(role="instructor")
        password = self.add("table-3").json()["password"]
        held = create_session("table-3", "learner")

        with patch("mniplab.webserver.logins.write_private_json",
                   side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.remove("table-3")

        self.assertTrue(self.signs_in("table-3", password))
        self.assertIn(held, auth.active_sessions)

    def test_a_learner_is_refused_remove(self) -> None:
        self.open_session(role="instructor")
        self.add("table-3")
        self.open_session(role="learner")

        self.assertEqual(self.remove("table-3").status_code, 403)
        self.assertIn("table-3", LoginStore(self.logins_path).passwords())


class GroupWithALabMixin(MultiLabModeMixin, LoginRoutesMixin):
    """A Group added on the page that is assigned a Lab Example, has a Config Copy and runs a Lab.

    Attributes:
        password: The Group's password as added.
        group_session: A live Session of the Group.
        copy: The Group's Config Copy of the Lab Example it runs.
    """

    GROUP = "table-3"
    EXAMPLE = "ospf-lab"

    def setUp(self) -> None:
        super().setUp()
        previous = registry.config_copies
        registry.config_copies = ConfigCopies(os.path.join(self.storage, "group-config"))
        self.addCleanup(setattr, registry, "config_copies", previous)  # type: ignore[attr-defined]
        self.open_session(role="instructor")  # type: ignore[attr-defined]
        self.password = self.add(self.GROUP).json()["password"]
        self.client.put(  # type: ignore[attr-defined]
            f"/api/admin/assignments/{self.GROUP}", json={"example_ids": [self.EXAMPLE]}
        )
        self.group_session = self.sign_in(self.GROUP, self.password)  # type: ignore[attr-defined]
        self.key = self.start(self.EXAMPLE).json()["key"]
        self.copy = registry.config_copies.copy_dir(self.GROUP, self.EXAMPLE)
        os.makedirs(self.copy)
        with open(os.path.join(self.copy, "work.txt"), "w", encoding="utf-8") as f:
            f.write("the Group's work")
        self.open_session(role="instructor")  # type: ignore[attr-defined]

    def lab_alive(self) -> bool:
        """Whether the Group's Lab is still running."""
        return registry.lab_service.get(self.key) is not None


@pytest.mark.unit
class ResetGroupTests(GroupWithALabMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """In Multi-Lab Mode a reset costs a Group its Sessions and nothing else."""

    def test_reset_leaves_the_groups_lab_assignment_and_config_copy_alone(self) -> None:
        reset = self.reset(self.GROUP)

        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertNotIn(self.group_session, auth.active_sessions)
        self.assertTrue(self.lab_alive())
        self.assertFalse(self.launcher.children[0].stopped)
        self.assertEqual(registry.groups.assignment(self.GROUP), [self.EXAMPLE])
        self.assertTrue(registry.config_copies.exists(self.GROUP, self.EXAMPLE))
        self.assertTrue(os.path.exists(os.path.join(self.copy, "work.txt")))

    def test_the_group_signs_back_in_with_the_new_password_to_its_running_lab(self) -> None:
        new = self.reset(self.GROUP).json()["password"]

        self.assertFalse(self.signs_in(self.GROUP, self.password))
        self.sign_in(self.GROUP, new)
        status = self.client.get("/api/status").json()
        self.assertEqual((status["running"], status["key"]), (True, self.key))

    def test_a_learner_is_refused_reset(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.reset(self.GROUP).status_code, 403)
        self.assertTrue(self.lab_alive())


@pytest.mark.unit
class RemoveGroupTests(GroupWithALabMixin, LoginClientMixin, FakeTerminalCloserMixin, unittest.TestCase):
    """In Multi-Lab Mode removing a Group stops its Lab and drops its Assignment, keeping its work."""

    def test_remove_stops_the_groups_lab_and_ends_its_sessions(self) -> None:
        removed = self.remove(self.GROUP)

        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(self.lab_alive())
        self.assertTrue(self.launcher.children[0].stopped)
        self.assertNotIn(self.group_session, auth.active_sessions)
        groups = {g["name"] for g in self.client.get("/api/admin/dashboard").json()["groups"]}
        self.assertNotIn(self.GROUP, groups)

    def test_remove_leaves_another_groups_lab_running(self) -> None:
        self.client.put("/api/admin/assignments/learner2", json={"example_ids": [self.EXAMPLE]})
        self.sign_in("learner2", "lab54321")
        other = self.start(self.EXAMPLE).json()["key"]
        self.open_session(role="instructor")

        self.remove(self.GROUP)

        self.assertFalse(self.launcher.children[1].stopped)
        self.assertEqual(
            [lab["key"] for lab in self.client.get("/api/labs").json()["labs"]], [other]
        )

    def test_remove_drops_the_assignment_and_keeps_the_config_copy_for_a_re_added_name(self) -> None:
        self.remove(self.GROUP)

        self.assertNotIn(self.GROUP, registry.groups.assignments())
        self.assertTrue(registry.config_copies.exists(self.GROUP, self.EXAMPLE))
        self.assertTrue(os.path.exists(os.path.join(self.copy, "work.txt")))

        password = self.add(self.GROUP).json()["password"]

        self.assertEqual(registry.groups.assignment(self.GROUP), [], "the old Assignment came back")
        self.assertTrue(registry.config_copies.exists(self.GROUP, self.EXAMPLE))
        self.assertTrue(self.signs_in(self.GROUP, password))
        with open(os.path.join(self.storage, "assignments.json"), encoding="utf-8") as f:
            self.assertNotIn(self.GROUP, json.load(f)["assignments"])

    def test_a_group_with_no_lab_and_no_assignment_is_removed_all_the_same(self) -> None:
        self.add("table-4")

        self.assertEqual(self.remove("table-4").status_code, 200)
        self.assertNotIn("table-4", self.logins())

    def test_remove_is_not_accepted_for_an_env_group(self) -> None:
        self.assertEqual(self.remove("learner1").status_code, 404)
        self.assertTrue(self.lab_alive())

    def test_a_learner_is_refused_remove(self) -> None:
        self.open_session(role="learner")

        self.assertEqual(self.remove(self.GROUP).status_code, 403)
        self.assertTrue(self.lab_alive())


#: The shape of a generated password: two words and four digits, with no
#: letter or digit that reads like another (no i, l, o, 0 or 1).
PASSWORD_SHAPE = r"[a-hjkmnp-z]+-[a-hjkmnp-z]+-[2-9]{4}"


@pytest.mark.unit
class GeneratedPasswordTests(unittest.TestCase):
    """A page-added login's password is readable aloud and hard to guess."""

    def test_a_password_is_two_words_and_four_digits_without_look_alikes(self) -> None:
        passwords = {generate_login_password() for _ in range(200)}

        for password in passwords:
            self.assertRegex(password, f"^{PASSWORD_SHAPE}$")
        self.assertGreater(len(passwords), 190, "drawn at random, so almost never repeated")

    def test_it_is_drawn_with_cryptographic_randomness(self) -> None:
        with patch("mniplab.webserver.logins.secrets.choice", side_effect=lambda seq: seq[0]):
            self.assertEqual(generate_login_password(), generate_login_password())


@pytest.mark.unit
class LoginRegistryTests(unittest.TestCase):
    """The login registry every consumer reads merges page-added logins under serve."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "accounts.json")
        write_private_json(
            self.path, {"version": 1, "logins": {"table-3": "crane-ruby-4827", "ann": "x-y-2345"}}
        )

    def test_page_added_logins_join_the_env_accounts_as_learners(self) -> None:
        live = Settings(users="ann:pw1:instructor,bob:pw2:learner")
        live.use_login_store(LoginStore(self.path))

        logins = live.get_user_registry()

        self.assertEqual(list(logins), ["ann", "bob", "table-3"])
        self.assertEqual(logins["table-3"]["password"], "crane-ruby-4827")
        self.assertEqual(logins["table-3"]["role"], "learner")
        self.assertTrue(logins["table-3"]["added"])
        self.assertNotIn("added", logins["bob"])

    def test_a_hand_edited_collision_resolves_to_the_env_account(self) -> None:
        live = Settings(users="ann:pw1:instructor")
        live.use_login_store(LoginStore(self.path))

        self.assertEqual(live.get_user_registry()["ann"], {"password": "pw1", "role": "instructor"})

    def test_page_added_logins_join_the_single_env_login_too(self) -> None:
        live = Settings(users="", username="solo", password="lab12345")
        live.use_login_store(LoginStore(self.path))

        self.assertEqual(list(live.get_user_registry()), ["solo", "ann", "table-3"])

    def test_without_a_login_store_the_file_is_ignored(self) -> None:
        """A Lab script's server opens no login store, so a leftover file adds nobody."""
        self.assertEqual(list(Settings(users="ann:pw1:instructor").get_user_registry()), ["ann"])


if __name__ == "__main__":
    unittest.main()
