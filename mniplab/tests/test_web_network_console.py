"""Static markup checks for the Network Console's test-output panels.

Parses the shipped ``index.html`` directly (no browser needed):
every Network Console test-output panel (ping, traceroute, iperf3,
routing table) must carry ``aria-live="polite"`` so a screen reader is told
when a result or error lands without the user re-navigating to the pane.
"""

import re
from pathlib import Path

HTML_PATH = (
    Path(__file__).resolve().parents[2]
    / "mniplab"
    / "webserver"
    / "static"
    / "index.html"
)

# The five Network Console test-output panels, by element id.
OUTPUT_PANEL_IDS = [
    "ping-output",
    "traceroute-output",
    "iperf3-output",
    "route-output",
]


def _html_text() -> str:
    return HTML_PATH.read_text()


def _extract_element(html: str, element_id: str) -> str:
    """Return the opening tag of an element with the given id."""
    match = re.search(r'<[a-zA-Z][^>]*\bid="' + re.escape(element_id) + r'"[^>]*>', html)
    assert match, f"could not find an element with id={element_id!r} in index.html"
    return match.group(0)


def test_each_console_output_panel_is_an_aria_live_region():
    """Every Network Console test-output panel is an aria-live region, so a
    screen reader announces a result or error when it lands."""
    html = _html_text()
    for element_id in OUTPUT_PANEL_IDS:
        tag = _extract_element(html, element_id)
        assert 'aria-live="polite"' in tag, (
            f"the {element_id!r} output panel must carry "
            f'aria-live="polite" so a screen reader announces results; '
            f"found: {tag}"
        )
