"""The web code and its tests speak of Panes and Slots, not of the retired word.

The retired word named the Terminal pane and the Packet Analysis pane together
as one area. They are now two Panes. This reads the web
frontend and the tests that drive it and fails on the retired word, so a
stray identifier, comment, accessible name or test name is caught. ``docker``
is a different word and is not matched.
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
# Built from pieces so that this file does not contain the word it bans.
RETIRED = re.compile("do" + "ck(?!er)", re.IGNORECASE)
SCANNED_SUFFIXES = {".py", ".js", ".html", ".css"}


def test_the_retired_word_is_nowhere_in_the_web_code_or_its_tests() -> None:
    roots = [PACKAGE / "webserver" / "static", *PACKAGE.joinpath("tests").glob("*.py")]
    hits = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in files:
            if path.suffix not in SCANNED_SUFFIXES or "lib" in path.relative_to(PACKAGE).parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if RETIRED.search(line):
                    hits.append(f"{path.relative_to(PACKAGE)}:{number}: {line.strip()}")

    assert hits == []
