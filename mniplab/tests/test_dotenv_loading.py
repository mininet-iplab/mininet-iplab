"""Unit tests for reading lab settings out of ``.env``.

``.env.example`` tells every user the same thing -- copy it to ``.env``, edit
the values, restart the lab -- but that instruction was false for every
setting read through a bare ``os.getenv`` call rather than through the web
server's pydantic ``Settings`` model. ``MNIPLAB_PRIVATE_NAMESPACES=0`` in
``.env`` did nothing at all: the Private Namespace escape hatch, the one an
instructor is told to reach for when a Lab breaks in front of a class, answered only to
a literal shell export, which is documented nowhere.

The load deliberately happens at the lab entry points and *not* at package
import. An import-time ``load_dotenv()`` mutates ``os.environ`` as a side
effect of the working directory, which feeds a developer's local ``.env``
into the unit suite: measured on this branch, it silently flipped thirteen
tests in ``test_netns_guard.py`` and ``test_net_link.py`` onto the wrong
side of the hatch, each failing as a missing assertion rather than as a
configuration error. ``test_importing_the_package_does_not_read_dotenv``
below is what keeps that from coming back.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mniplab.constants import ENV_FILE_VAR
from mniplab.namespace import PRIVATE_NAMESPACES_ENV_VAR, has_private_namespaces
from mniplab.runner import load_lab_environment

REPO_ROOT = Path(__file__).resolve().parents[2]

PROBE_VAR = "MNIPLAB_DOTENV_PROBE"


class _DotenvDir:
    """A temporary working directory holding one ``.env``.

    Enters the directory for the duration of the block, so the relative
    ``.env`` path that :func:`load_lab_environment` uses resolves to the
    file written here rather than to the repository's own. The suite's
    isolation -- the repo-root ``conftest.py`` pointing ``MNIPLAB_ENV_FILE``
    at ``os.devnull`` -- is lifted for the block, since the working-directory
    default is the very thing these tests exercise.
    """

    def __init__(self, contents: str) -> None:
        self._contents = contents
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._origin: str | None = None
        self._env_file: str | None = None

    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        (Path(self._tmp.name) / ".env").write_text(self._contents, encoding="utf-8")
        self._origin = os.getcwd()
        os.chdir(self._tmp.name)
        self._env_file = os.environ.pop(ENV_FILE_VAR, None)
        return self._tmp.name

    def __exit__(self, *exc_info: object) -> None:
        if self._env_file is not None:
            os.environ[ENV_FILE_VAR] = self._env_file
        if self._origin is not None:
            os.chdir(self._origin)
        if self._tmp is not None:
            self._tmp.cleanup()


class LoadLabEnvironmentTests(unittest.TestCase):
    """``load_lab_environment`` puts ``.env`` values into the environment."""

    def test_value_set_only_in_dotenv_becomes_readable(self):
        """A name present only in .env resolves through os.getenv."""
        with _DotenvDir(f"{PROBE_VAR}=from_dotenv\n"):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(PROBE_VAR, None)

                load_lab_environment()

                self.assertEqual(os.getenv(PROBE_VAR), "from_dotenv")

    def test_shell_export_wins_over_the_same_name_in_dotenv(self):
        """An already-set variable is not overwritten by .env.

        This is what keeps ``MNIPLAB_PRIVATE_NAMESPACES=0 python3
        examples/static-lab.py`` working as the escape hatch's in-the-moment
        form: the file supplies defaults, the shell overrides them.
        """
        with _DotenvDir(f"{PROBE_VAR}=from_dotenv\n"):
            with patch.dict(os.environ, {PROBE_VAR: "from_shell"}):

                load_lab_environment()

                self.assertEqual(os.getenv(PROBE_VAR), "from_shell")

    def test_env_file_variable_names_the_file_to_read(self):
        """MNIPLAB_ENV_FILE points the load at a file that is not ./.env.

        The one knob that lets an operator run two web servers from one
        checkout, each with its own file -- and that the test suite turns
        towards ``os.devnull``.
        """
        with _DotenvDir(f"{PROBE_VAR}=from_cwd_dotenv\n") as tmp:
            other = Path(tmp) / "other.env"
            other.write_text(f"{PROBE_VAR}=from_named_file\n", encoding="utf-8")
            with patch.dict(os.environ, {ENV_FILE_VAR: str(other)}):
                os.environ.pop(PROBE_VAR, None)

                load_lab_environment()

                self.assertEqual(os.getenv(PROBE_VAR), "from_named_file")


class EscapeHatchReadsDotenvTests(unittest.TestCase):
    """The Private Namespace escape hatch answers to ``.env``, as documented."""

    def test_hatch_off_in_dotenv_disables_a_capable_node(self):
        """A capable Node answers False with the hatch set to 0 in .env.

        The behaviour ``.env.example`` has always described and which, until
        ``.env`` was read at all, only a shell export could produce.
        """
        from mniplab.host import mnIPLabHost

        with _DotenvDir(f"{PRIVATE_NAMESPACES_ENV_VAR}=0\n"):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(PRIVATE_NAMESPACES_ENV_VAR, None)

                load_lab_environment()

                self.assertFalse(has_private_namespaces(mnIPLabHost))


class ImportIsFreeOfEnvironmentSideEffectsTests(unittest.TestCase):
    """Importing the package must not read ``.env``.

    Regression guard for the failure this design exists to avoid: a
    ``load_dotenv()`` at package import made every test inherit whatever the
    developer happened to have in ``.env``, flipping hatch-dependent
    assertions silently rather than failing loudly.
    """

    def test_importing_the_package_does_not_read_dotenv(self):
        """`import mniplab` leaves a .env-only name unset."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(
                f"{PROBE_VAR}=from_dotenv\n", encoding="utf-8"
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(REPO_ROOT)
            env.pop(PROBE_VAR, None)
            # The suite's own isolation must not be what keeps this green.
            env.pop(ENV_FILE_VAR, None)

            result = subprocess.run(
                ["python3", "-c", f"import mniplab, os; print(os.getenv('{PROBE_VAR}'))"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )

            self.assertEqual(result.stdout.strip(), "None")
