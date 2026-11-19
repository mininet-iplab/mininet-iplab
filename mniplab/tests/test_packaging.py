"""Unit tests for the project's declared packaging metadata.

Two consecutive CI failures traced back to the same root cause: nothing in
this repo records what the project actually needs in order to run, and the
few places that do claim it disagree with each other or are simply wrong —
a build backend that names a module that does not exist, a Python floor
claimed three different ways, a `requirements.txt` hand-edited out of step
with `pyproject.toml`. These tests assert externally observable facts about
the declared environment — what a Contributor or a packaging tool would see
— rather than the internal shape of any one config file, and they run
offline in the unprivileged unit suite everyone already runs.

Whether the two lock files still match a fresh pip-compile is not checked
here: that needs PyPI and cost most of this file's runtime. Both images
install exactly those files and every test tier runs against them, so a
package the locks drop or pin too old fails there instead.
"""

import importlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"
DEV_LOCK_PATH = REPO_ROOT / "requirements-dev.lock"


def _load_pyproject() -> dict:
    """Parse pyproject.toml as data, the way a packaging tool would."""
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


class BuildBackendTests(unittest.TestCase):
    """The declared build backend must actually exist, not just look plausible."""

    def test_build_backend_module_is_importable(self):
        data = _load_pyproject()
        backend = data["build-system"]["build-backend"]
        module_name = backend.split(":", 1)[0]

        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            self.fail(
                f"declared build-backend module {module_name!r} is not "
                f"importable: {exc}"
            )


class PythonFloorTests(unittest.TestCase):
    """The Python floor must be claimed in one place, and agree with itself."""

    def test_requires_python_floor_matches_classifiers(self):
        data = _load_pyproject()
        project = data["project"]
        requires_python = project["requires-python"]

        # requires-python looks like ">=3.11" — pull the floor out of it.
        self.assertTrue(requires_python.startswith(">="), requires_python)
        floor = requires_python[len(">="):]
        floor_classifier = f"Programming Language :: Python :: {floor}"

        classifiers = [
            c for c in project["classifiers"]
            if c.startswith("Programming Language :: Python :: 3.")
        ]

        self.assertIn(
            floor_classifier, classifiers,
            f"requires-python floor {floor!r} has no matching classifier",
        )
        for classifier in classifiers:
            version = classifier.rsplit(" :: ", 1)[-1]
            self.assertGreaterEqual(
                tuple(int(p) for p in version.split(".")),
                tuple(int(p) for p in floor.split(".")),
                f"classifier {classifier!r} advertises a version below the "
                f"declared floor {floor!r}",
            )

    def test_mypy_does_not_duplicate_the_python_version(self):
        """mypy's own python_version setting is a second, driftable claim."""
        data = _load_pyproject()
        mypy_config = data.get("tool", {}).get("mypy", {})
        self.assertNotIn(
            "python_version", mypy_config,
            "tool.mypy.python_version duplicates requires-python and can drift",
        )


class GeneratedRequirementsTests(unittest.TestCase):
    """`requirements.txt` is generated output, not a second source of truth."""

    def test_mininet_is_absent_from_requirements(self):
        """Both Dockerfiles build Mininet from source; a pinned PyPI entry
        here would resolve to a stale release and downgrade that build."""
        text = REQUIREMENTS_PATH.read_text().lower()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertFalse(
                stripped.startswith("mininet"),
                f"requirements.txt pins mininet, which would downgrade the "
                f"source build: {line!r}",
            )


class GeneratedDevLockTests(unittest.TestCase):
    """The `dev` extra's lock is generated output too, with the same rule."""

    def test_dev_lock_extension_is_not_txt(self):
        """A `.lock` extension distinguishes this from the abandoned
        hand-written `requirements-dev.txt` stopgap it replaces."""
        self.assertEqual(DEV_LOCK_PATH.suffix, ".lock")
        self.assertFalse(
            (REPO_ROOT / "requirements-dev.txt").exists(),
            "the abandoned requirements-dev.txt stopgap should not exist",
        )


class WheelContentsTests(unittest.TestCase):
    """A built wheel carries every file the library loads at runtime.

    setuptools ships ``.py`` files and nothing else unless ``package-data``
    names the rest. A user who installs the wheel gets a Lab that fails at
    start with ``Template not found`` rather than a packaging error, so the
    wheel is built once per class and its file list is checked against the
    checkout. The build runs in a copy of the source so no ``build/`` or
    ``*.egg-info`` is left in the checkout.
    """

    wheel_names: set[str]

    @classmethod
    def setUpClass(cls) -> None:
        """Build the wheel offline from a copy of the source, once per class."""
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        root = Path(tmp.name)
        source = root / "source"
        source.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(REPO_ROOT / name, source / name)
        shutil.copytree(
            REPO_ROOT / "mniplab",
            source / "mniplab",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        outdir = root / "dist"
        built = subprocess.run(
            [
                sys.executable, "-m", "pip", "wheel", ".",
                "--no-deps", "--no-build-isolation", "-q", "-w", str(outdir),
            ],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
        )
        if built.returncode != 0:
            raise RuntimeError(f"pip wheel failed:\n{built.stderr}")
        wheels = list(outdir.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one wheel, found {wheels}")
        with zipfile.ZipFile(wheels[0]) as archive:
            cls.wheel_names = set(archive.namelist())

    def test_every_mako_template_under_the_package_is_in_the_wheel(self):
        """Every template the library renders must ship inside the wheel.

        Lists each ``*.mako`` under ``mniplab/`` in the checkout and fails
        naming any the built wheel lacks, which is the file a user would
        otherwise hit as ``Template not found`` when a Lab starts.
        """
        templates = {
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "mniplab").rglob("*.mako")
        }
        self.assertTrue(templates, "no .mako templates found under mniplab/")
        missing = sorted(templates - self.wheel_names)
        self.assertEqual(
            missing, [], "templates missing from the built wheel",
        )

    def test_every_static_file_the_webserver_serves_is_in_the_wheel(self):
        """Every file under the web UI's static directory must ship.

        The web UI serves the whole ``static/`` tree, so a file left out of
        ``package-data`` would be a 404 for a user who installed the wheel.
        """
        static = REPO_ROOT / "mniplab" / "webserver" / "static"
        served = {
            path.relative_to(REPO_ROOT).as_posix()
            for path in static.rglob("*")
            if path.is_file()
        }
        self.assertTrue(served, "no static files found under webserver/static")
        missing = sorted(served - self.wheel_names)
        self.assertEqual(
            missing, [], "static files missing from the built wheel",
        )
