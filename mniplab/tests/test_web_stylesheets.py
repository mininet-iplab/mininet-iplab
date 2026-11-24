"""Static checks across the Web UI's shipped stylesheets.

A page that loads two stylesheets defining keyframes of the same name gets
only the later one, so every rule using that name in the earlier stylesheet
silently animates with the wrong keyframes.
"""

import re
from collections import defaultdict
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parents[2] / "mniplab" / "webserver" / "static" / "css"

KEYFRAMES = re.compile(r"@keyframes\s+([\w-]+)")


def test_no_keyframes_name_is_defined_in_more_than_one_stylesheet() -> None:
    """Each keyframes name is defined in one stylesheet only."""
    defined_in = defaultdict(set)
    for sheet in CSS_DIR.rglob("*.css"):
        for name in KEYFRAMES.findall(sheet.read_text()):
            defined_in[name].add(sheet.relative_to(CSS_DIR).as_posix())

    shared = {name: sorted(sheets) for name, sheets in defined_in.items() if len(sheets) > 1}
    assert not shared, f"keyframes defined in more than one stylesheet: {shared}"
