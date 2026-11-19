"""Tests that the web UI package speaks the project's own vocabulary.

`CONTEXT.md` is the domain glossary, and each entry carries an `_Avoid_` line
naming the words that must not be used for that concept.  Nothing enforced
those lines, so the code drifted: for four months `PTYSession`,
`registry.get_sessions()` and `/api/admin/sessions` all meant **Terminal**
while `active_sessions` meant **Session**, and two Instructor panels shipped
side by side, both headed with the word "Session" and listing different
things.

This test examines the repository rather than the product.  It reads the
glossary at run time so the rules live in one place: adding a word to an
`_Avoid_` line is all it takes to ban it.

Two deliberate narrowings, both of which a widening ticket may lift:

* Only the **Terminal** and **Session** entries are enforced, and only inside
  `mniplab/webserver/`.  Those two collided; the glossary's other 36 terms have
  not been audited, and enforcing them all at once would land as a rename of
  the whole codebase rather than as a check.
* Four banned words are exempted wholesale, each with its reason below.  A
  plain search finds them constantly in uses that have nothing to do with the
  concept being protected — `console.error` is not a Terminal — and an
  exemption that says so is more honest than a regex pretending to tell them
  apart.

The glossary is gitignored and never reaches the remote, so this test skips
itself in CI and has teeth only on a Contributor's machine who keeps one.  That
is by design, not an oversight: do not delete it as a test nothing runs.
"""

import re
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_PATH = REPO_ROOT / "CONTEXT.md"
WEBSERVER_PATH = REPO_ROOT / "mniplab" / "webserver"

# The glossary entries whose _Avoid_ lines are enforced.  Terminal and Session
# are the pair that collided; widening this tuple is a separate ticket.
ENFORCED_TERMS = ("Terminal", "Session")

# Text files worth reading.  Compiled bytecode and images are not.
SCANNED_SUFFIXES = frozenset({".py", ".js", ".html", ".css"})

# Third-party code, which does not answer to this project's glossary.
VENDORED_PARTS = frozenset({"lib", "__pycache__"})

# Banned words this package may use freely, and why.  Each names a real thing
# that is not the concept the glossary is protecting.
WORD_EXEMPTIONS: Dict[str, str] = {
    "console": (
        "the browser's console.* logging API and the network-tools output "
        "panel, neither of which is a Terminal"
    ),
    "shell": (
        "a Terminal delivers a shell process that the registry really does "
        "spawn and reap; the ban is on calling the Terminal itself a shell"
    ),
    "login": (
        "signing in is an act, not the Session it produces — the login route, "
        "form and rate limiter are all named for the act"
    ),
    "connection": (
        "WebSocket and browser connections are real transport-level things; "
        "the ban is on calling a Session a connection"
    ),
}

# Lines allowed to carry an otherwise-banned word, by word.
LINE_EXEMPTIONS: Dict[str, Tuple[re.Pattern, ...]] = {
    "pty": (
        # MNIPLAB_PTY_* survives as an AliasChoices entry so an existing .env
        # keeps working for one release, and the comments and warnings that
        # explain the deprecation have to spell the old name out.
        re.compile(r"PTY_[A-Z_]+"),
        # pty(7) is the OS mechanism a Terminal is built on.  os.openpty and
        # pty.fork are its function names and cannot be renamed.
        re.compile(r"\bimport pty\b|\bpty\.(fork|openpty)\b|\bos\.openpty\b|pty\(7\)"),
    ),
}


def _avoid_words() -> Dict[str, List[str]]:
    """Read the enforced terms' `_Avoid_` lines out of the glossary.

    Returns:
        Each enforced term mapped to the words its entry bans.
    """
    term_pattern = re.compile(r"^\*\*(.+?)\*\*:\s*$")
    avoid_pattern = re.compile(r"^_Avoid_:\s*(.+)$")

    found: Dict[str, List[str]] = {}
    current: str = ""
    for line in CONTEXT_PATH.read_text(encoding="utf-8").splitlines():
        term_match = term_pattern.match(line)
        if term_match:
            current = term_match.group(1)
            continue
        avoid_match = avoid_pattern.match(line)
        if avoid_match and current in ENFORCED_TERMS:
            found[current] = _split_avoid_line(avoid_match.group(1))
            current = ""
    return found


def _split_avoid_line(text: str) -> List[str]:
    """Split one `_Avoid_` line into the words it bans.

    Entries are comma-separated and may carry emphasis or a parenthetical
    qualifier — `**link state** — that phrase means...` — neither of which is
    part of the word itself.

    Args:
        text: Everything after the `_Avoid_:` marker.

    Returns:
        The banned words, lowercased.
    """
    words = []
    for entry in text.split(","):
        entry = entry.split("(")[0].split("—")[0]
        entry = entry.replace("**", "").strip().lower()
        if entry:
            words.append(entry)
    return words


def _banned_words() -> List[str]:
    """Return every enforced banned word, minus the wholesale exemptions."""
    words = {word for words in _avoid_words().values() for word in words}
    return sorted(words - set(WORD_EXEMPTIONS))


def _word_pattern(word: str) -> re.Pattern:
    """Compile the search used for one banned word.

    Anchored on the left only.  A trailing anchor would miss `pty_fd` and
    `active_ptys`, which are exactly the drift being hunted; a leading one
    keeps `empty` and `openpty` out of the results, since a banned word buried
    inside a longer word is not a use of it.

    Args:
        word: The banned word, which may be a phrase.

    Returns:
        A case-insensitive pattern matching that word.
    """
    escaped = re.escape(word).replace("\\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z]){escaped}", re.IGNORECASE)


def _scanned_files() -> List[Path]:
    """Return the web UI package's own source files, vendored code excluded."""
    return sorted(
        path
        for path in WEBSERVER_PATH.rglob("*")
        if path.is_file()
        and path.suffix in SCANNED_SUFFIXES
        and not VENDORED_PARTS.intersection(path.parts)
    )


@unittest.skipUnless(
    CONTEXT_PATH.exists(),
    "CONTEXT.md is gitignored and never reaches the remote, so this check is "
    "local-only by design",
)
class GlossaryVocabularyTests(unittest.TestCase):
    """The webserver package must not use the glossary's banned words."""

    def test_the_glossary_still_bans_the_words_this_test_exists_for(self):
        """A reformatted glossary must not silently empty the ban list.

        Everything below passes trivially if the parser finds nothing, which
        would turn the whole file into a test that cannot fail.
        """
        found = _avoid_words()
        self.assertEqual(
            sorted(ENFORCED_TERMS), sorted(found),
            f"CONTEXT.md no longer has an _Avoid_ line for every enforced "
            f"term; found {sorted(found)}",
        )
        self.assertIn("pty", found["Terminal"])
        self.assertIn("login", found["Session"])

    def test_the_terminal_ban_is_actually_enforced(self):
        """`pty` is the word this test was written to keep out.

        Adding it to WORD_EXEMPTIONS would leave the file passing while
        enforcing nothing that matters, so that shortcut is closed here.
        """
        self.assertNotIn("pty", WORD_EXEMPTIONS)
        self.assertIn("pty", _banned_words())

    def test_the_webserver_package_uses_the_glossarys_vocabulary(self):
        """No banned word may appear outside its documented exemption."""
        patterns = [(word, _word_pattern(word)) for word in _banned_words()]

        offences = []
        for path in _scanned_files():
            relative = path.relative_to(REPO_ROOT)
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                for word, pattern in patterns:
                    if not pattern.search(line):
                        continue
                    allowed = LINE_EXEMPTIONS.get(word, ())
                    if any(exemption.search(line) for exemption in allowed):
                        continue
                    offences.append(f"{relative}:{number}: {word!r} in {line.strip()!r}")

        self.assertEqual(
            [], offences,
            "the web UI package uses words CONTEXT.md bans for Terminal or "
            "Session. Rename them, or — if the use is genuinely something "
            "else — add an exemption with its reason in "
            "mniplab/tests/test_vocabulary.py:\n" + "\n".join(offences),
        )


if __name__ == "__main__":
    unittest.main()
