"""Unit tests for the web server's Settings: defaults, floors, renamed env names."""

import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Tuple
from unittest.mock import patch

from pydantic import ValidationError

from mniplab.constants import ENV_FILE_VAR
from mniplab.webserver.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

# The Terminal settings renamed away from the PTY vocabulary: the field, its
# current env var, the superseded spellings still read via AliasChoices, and a
# valid value for the new and the old name.  The values differ per field so
# that each one stays inside its own validator's range.
RENAMED_TERMINAL_SETTINGS = (
    (
        "terminal_per_session_cap",
        "MNIPLAB_TERMINAL_PER_SESSION_CAP",
        ("MNIPLAB_PTY_PER_SESSION_CAP", "MNIPLAB_PTY_PER_CLIENT_CAP"),
        "7",
        "9",
    ),
    (
        "terminal_global_cap",
        "MNIPLAB_TERMINAL_GLOBAL_CAP",
        ("MNIPLAB_PTY_GLOBAL_CAP",),
        "42",
        "77",
    ),
    (
        "terminal_idle_timeout",
        "MNIPLAB_TERMINAL_IDLE_TIMEOUT",
        ("MNIPLAB_PTY_IDLE_TIMEOUT",),
        "90",
        "120",
    ),
    (
        "terminal_ping_interval",
        "MNIPLAB_TERMINAL_PING_INTERVAL",
        ("MNIPLAB_PTY_PING_INTERVAL",),
        "20",
        "25",
    ),
)


class SettingsTests(unittest.TestCase):
    """Test web server configuration settings."""

    def test_session_idle_timeout_defaults_to_two_and_a_half_hours(self) -> None:
        """A Session should be allowed to go quiet for 9000 seconds.

        The default matches ``terminal_idle_timeout``'s unit so the two limits can be
        compared without converting, and is long enough to span a lab period.
        """
        self.assertEqual(Settings().session_idle_timeout, 9000)

    def test_a_dotenv_in_the_working_directory_does_not_reach_the_suite(self) -> None:
        """A developer's ``.env`` must not decide what the defaults are.

        ``Settings`` reads its env file at class definition, so a per-test
        patch is too late; the suite's isolation is the repo-root
        ``conftest.py`` pointing ``MNIPLAB_ENV_FILE`` at ``os.devnull``
        before any package import.  A scenario file written into the
        working directory is what a checkout with a Multi-Lab ``.env`` looks
        like, and it must leave the defaults untouched in the full run.
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(
                "MNIPLAB_LAB_MODE=multi\nMNIPLAB_PORT=9999\n", encoding="utf-8"
            )
            origin = os.getcwd()
            os.chdir(tmp)
            try:
                settings = Settings()
            finally:
                os.chdir(origin)

        self.assertEqual(settings.lab_mode, "single")
        self.assertEqual(settings.port, 8050)

    def test_env_file_variable_names_the_file_settings_read(self) -> None:
        """MNIPLAB_ENV_FILE points Settings at a file that is not ./.env.

        Read once, at class definition, so the variable is set for a fresh
        interpreter rather than patched here: the shape an operator uses to
        run two web servers from one checkout, each with its own file.
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text("MNIPLAB_PORT=9999\n", encoding="utf-8")
            named = Path(tmp) / "second.env"
            named.write_text("MNIPLAB_PORT=8051\n", encoding="utf-8")
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("MNIPLAB_")
            }
            env["PYTHONPATH"] = str(REPO_ROOT)
            env[ENV_FILE_VAR] = str(named)

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from mniplab.webserver.config import Settings; print(Settings().port)",
                ],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )

        self.assertEqual(result.stdout.strip(), "8051")

    def test_lab_mode_is_single_unless_set_and_accepts_multi(self) -> None:
        """An existing deployment comes up in Single-Lab Mode; ``multi`` is
        the one other value, and anything else is refused at startup."""
        self.assertEqual(Settings().lab_mode, "single")
        self.assertEqual(Settings(lab_mode="multi").lab_mode, "multi")
        with self.assertRaises(ValueError):
            Settings(lab_mode="several")  # type: ignore[arg-type]

    def test_lab_cap_is_four_unless_set_and_never_zero(self) -> None:
        """Multi-Lab Mode bounds alive Labs at four by default; a cap that
        admits no Lab at all is refused at startup."""
        self.assertEqual(Settings().lab_cap, 4)
        self.assertEqual(Settings(lab_cap=2).lab_cap, 2)
        with self.assertRaises(ValueError):
            Settings(lab_cap=0)

    def test_session_per_user_cap_is_off_by_default(self) -> None:
        """The Session Cap must not be guessed at.

        A runtime cannot tell a group account from a personal one, so any
        non-zero default locks out every class sharing one credential.
        Off is the decision, not a placeholder.
        """
        self.assertEqual(Settings().session_per_user_cap, 0)

    def test_each_limit_rejects_values_below_its_floor(self) -> None:
        """A zero idle timeout would expire every Session on sight, a zero
        global cap would refuse every Terminal, and a negative Session Cap
        means nothing — while zero, the cap's off switch, must validate."""
        self.assertEqual(Settings(session_per_user_cap=0).session_per_user_cap, 0)
        below_floor = (
            ("session_idle_timeout", (0, -1)),
            ("session_per_user_cap", (-1, -5)),
            ("terminal_global_cap", (0,)),
        )
        for field, invalid_values in below_floor:
            for invalid in invalid_values:
                with self.subTest(**{field: invalid}):
                    with self.assertRaises(ValidationError):
                        Settings(**{field: invalid})  # type: ignore[arg-type]

    @contextmanager
    def _isolated_env(self, **env: str):
        """Construct Settings against a hermetic env for the renamed names.

        Ignores this machine's real ``.env`` — a dev container may still carry
        ``MNIPLAB_PTY_*`` entries from before the rename, which would otherwise
        make these tests depend on local state.

        Args:
            **env: Env vars to set for the duration of the block.
        """
        with patch("mniplab.webserver.config.dotenv_values", return_value={}):
            with patch.dict(os.environ, {}, clear=False):
                for current, superseded in Settings.renamed_env_names().items():
                    os.environ.pop(current, None)
                    for old_name in superseded:
                        os.environ.pop(old_name, None)
                os.environ.update(env)
                yield

    def test_the_defaults_survive_the_terminal_renames(self) -> None:
        """The renames change the names, never the values.

        The global cap is a backstop chosen to clear a realistic classroom
        peak, not measured.
        """
        with self._isolated_env():
            settings = Settings()
        self.assertEqual(settings.host, '0.0.0.0')
        self.assertEqual(settings.port, 8050)
        self.assertEqual(settings.terminal_per_session_cap, 5)
        self.assertEqual(settings.terminal_global_cap, 100)
        self.assertEqual(settings.terminal_idle_timeout, 300)
        self.assertEqual(settings.terminal_ping_interval, 15)

    def test_the_renamed_names_are_the_ones_this_suite_expects(self) -> None:
        """The derived table must match the spellings declared above.

        ``Settings.renamed_env_names()`` reads the fields' own ``AliasChoices``
        instead of a table kept beside them, which removes the drift but also
        means the production code can no longer disagree with itself.  This is
        what still notices a dropped alias: RENAMED_TERMINAL_SETTINGS is
        hand-written, so it fails when an old spelling stops being read.
        """
        self.assertEqual(
            {
                current: superseded
                for _field, current, superseded, _new, _old in RENAMED_TERMINAL_SETTINGS
            },
            Settings.renamed_env_names(),
        )

    def test_every_old_env_name_still_supplies_its_value_and_warns(self) -> None:
        """Each MNIPLAB_PTY_* name must keep working for one release, and
        tell the operator on it to move rather than leave them guessing.

        Without the alias, extra="ignore" would discard the unrecognised name
        in silence — the same failure that let MNIPLAB_SESSION_SECRET sit dead
        in `.env` for three months after it was removed from the code.
        """
        for field, current, superseded, _new, old_value in RENAMED_TERMINAL_SETTINGS:
            for old_name in superseded:
                with self.subTest(env=old_name):
                    with self._isolated_env(**{old_name: old_value}):
                        with self.assertWarns(DeprecationWarning) as caught:
                            settings = Settings()
                    self.assertEqual(getattr(settings, field), int(old_value))
                    message = str(caught.warning)
                    self.assertIn(old_name, message)
                    self.assertIn(current, message)

    def test_every_new_env_name_wins_when_an_old_one_is_also_set(self) -> None:
        """A transition period where both names are set must not be ambiguous."""
        for field, current, superseded, new_value, old_value in RENAMED_TERMINAL_SETTINGS:
            for old_name in superseded:
                with self.subTest(env=old_name):
                    with self._isolated_env(**{current: new_value, old_name: old_value}):
                        settings = Settings()
                    self.assertEqual(getattr(settings, field), int(new_value))

    def test_the_per_session_cap_still_accepts_both_historical_spellings(self) -> None:
        """This field has been renamed twice, so it carries two dead names.

        `MNIPLAB_PTY_PER_CLIENT_CAP` became `MNIPLAB_PTY_PER_SESSION_CAP`, and
        both are now superseded.  The older of the two is the one
        at risk of being dropped by a second rename, so the middle spelling
        must still win over it.
        """
        with self._isolated_env(
            MNIPLAB_PTY_PER_SESSION_CAP="7", MNIPLAB_PTY_PER_CLIENT_CAP="9"
        ):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                self.assertEqual(Settings().terminal_per_session_cap, 7)

    def test_only_an_old_name_on_its_own_is_deprecated(self) -> None:
        """An operator who has moved, or never set a name, must not be warned;
        nor one who set both, since the new name wins."""
        current_names = {
            current: new_value
            for _field, current, _superseded, new_value, _old in RENAMED_TERMINAL_SETTINGS
        }
        quiet_envs: Tuple[Tuple[str, Dict[str, str]], ...] = (
            ("new names alone", current_names),
            ("both names", {"MNIPLAB_TERMINAL_PER_SESSION_CAP": "7",
                            "MNIPLAB_PTY_PER_CLIENT_CAP": "9"}),
            ("neither name", {}),
        )
        for label, env in quiet_envs:
            with self.subTest(env=label):
                with self._isolated_env(**env):
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        Settings()
                self.assertEqual(caught, [])

    def test_the_user_registry_parses_users_or_falls_back_to_the_single_account(self):
        """MNIPLAB_USERS entries become accounts; without them the single
        MNIPLAB_USERNAME/PASSWORD pair is the one Instructor."""
        with self._isolated_env():
            parsed = Settings(users="ann:pw1:instructor,bob:pw2:learner").get_user_registry()
            single = Settings(users="", username="solo", password="lab12345").get_user_registry()

        self.assertEqual(parsed, {
            "ann": {"password": "pw1", "role": "instructor"},
            "bob": {"password": "pw2", "role": "learner"},
        })
        self.assertEqual(single, {"solo": {"password": "lab12345", "role": "instructor"}})

    def test_an_unset_password_is_generated_afresh_and_passes_the_strength_check(self):
        """Without MNIPLAB_PASSWORD every start gets its own random password,
        strong enough for the validator a person's password meets."""
        with self._isolated_env():
            os.environ.pop("MNIPLAB_PASSWORD", None)
            first, second = Settings(), Settings()

        self.assertTrue(first.password_was_generated)
        self.assertNotEqual(first.password, second.password)
        self.assertNotIn(first.password, ("changeme", "changeme123"))
        self.assertGreaterEqual(len(first.password), 8)
        self.assertTrue(any(c.isalpha() for c in first.password))
        self.assertTrue(any(c.isdigit() for c in first.password))

    def test_a_set_password_is_used_as_given(self):
        """MNIPLAB_PASSWORD from the environment is the password, not a generated one."""
        with self._isolated_env(MNIPLAB_PASSWORD="lab12345"):
            chosen = Settings()

        self.assertFalse(chosen.password_was_generated)
        self.assertEqual(chosen.password, "lab12345")

    def test_production_refuses_an_old_default_password_unless_allowed(self):
        """A set changeme123 is refused in production, accepted with
        MNIPLAB_ALLOW_DEFAULT_PASSWORD; the generated default never trips it."""
        origins = '["http://lab.example.com"]'
        with self._isolated_env(MNIPLAB_ENVIRONMENT="production",
                                MNIPLAB_CORS_ORIGINS=origins):
            os.environ.pop("MNIPLAB_PASSWORD", None)
            Settings()
            with self.assertRaises(ValidationError):
                Settings(password="changeme123")
            Settings(password="changeme123", allow_default_password=True)

    def test_production_refuses_the_cors_wildcard(self):
        """Production needs explicit origins, the default wildcard included;
        development keeps the wildcard."""
        with self._isolated_env(MNIPLAB_ENVIRONMENT="production"):
            os.environ.pop("MNIPLAB_CORS_ORIGINS", None)
            with self.assertRaises(ValidationError):
                Settings()
            Settings(cors_origins=["http://lab.example.com"])
        with self._isolated_env(MNIPLAB_ENVIRONMENT="development"):
            self.assertEqual(Settings().cors_origins, ["*"])
