"""Static markup regression checks for a batch of accessibility micro-fixes.

Parses the shipped `index.html`/`main.css` directly (no browser needed) for
placeholder contrast, redundant `role="img"` on decorative SVGs already
`aria-hidden`, and five buttons whose accessible name must contain their
visible label.
"""

import re
from pathlib import Path

from mniplab.tests.test_web_accessibility_contrast import VAR_RE, CssTheme

STATIC_DIR = Path(__file__).resolve().parents[2] / "mniplab" / "webserver" / "static"
HTML_PATH = STATIC_DIR / "index.html"
CSS_PATH = STATIC_DIR / "css" / "main.css"

# Each of these buttons' accessible name must start with or contain the
# button's own rendered visible text (a voice-control command matching what's
# on screen must activate the control it names).
BUTTON_IDS = [
    "stop-lab-btn",
    "lab-guide-open-btn",
    "auto-arrange-nodes",
    "save-layout-btn",
    "delete-group-btn",
]


def _html_text() -> str:
    return HTML_PATH.read_text()


def _css_text() -> str:
    return CSS_PATH.read_text()


def _extract_button(html: str, button_id: str) -> tuple[str, str]:
    """Return `(aria_label, visible_text)` for `<button id="button_id">...</button>`."""
    match = re.search(
        r'<button id="' + re.escape(button_id) + r'"[^>]*>(.*?)</button>',
        html,
        re.S,
    )
    assert match, f"could not find <button id={button_id!r}> in index.html"

    opening_tag_end = html.index(">", match.start())
    opening_tag = html[match.start() : opening_tag_end + 1]
    aria_label_match = re.search(r'aria-label="([^"]*)"', opening_tag)
    assert aria_label_match, f"<button id={button_id!r}> has no aria-label"

    visible_text = re.sub(r"<[^>]+>", " ", match.group(1))
    visible_text = " ".join(visible_text.split())
    return aria_label_match.group(1), visible_text


def test_five_buttons_accessible_name_contains_visible_label():
    """Each of the five previously-mismatched buttons' aria-label starts with
    its own visible text, so a voice-control command matching the label
    activates the right control."""
    for button_id in BUTTON_IDS:
        aria_label, visible_text = _extract_button(_html_text(), button_id)
        assert visible_text, f"<button id={button_id!r}> has no visible text"
        assert visible_text in aria_label, (
            f"<button id={button_id!r}>'s aria-label {aria_label!r} "
            f"does not contain its visible text {visible_text!r}"
        )


def test_decorative_svgs_do_not_carry_redundant_role_img():
    """No aria-hidden SVG also carries role="img", which exists specifically
    to require an accessible name that a hidden element can't meaningfully
    have."""
    html = _html_text()
    svg_tags = re.findall(r"<svg\b[^>]*>", html)
    assert svg_tags, "expected at least one <svg> element in index.html"

    hidden_with_role_img = [
        tag for tag in svg_tags if 'aria-hidden="true"' in tag and 'role="img"' in tag
    ]
    assert not hidden_with_role_img, (
        "aria-hidden SVGs must not also carry role=\"img\" "
        f"(role=\"img\" requires an accessible name): {hidden_with_role_img}"
    )


def test_placeholder_color_resolves_to_muted_text_token():
    """Every ::placeholder rule (a universal one, not per-field) sets its
    color from --text-muted, the already-verified muted-text token."""
    css = _css_text()
    theme = CssTheme(css)

    placeholder_blocks = re.findall(r"([^{}\n]*::placeholder[^{}]*)\{([^}]*)\}", css)
    assert placeholder_blocks, "expected at least one ::placeholder rule in main.css"

    # The fix must be the general pattern (every field, via one universal
    # rule), not a one-off carried over from the sidebar's search field —
    # that field-specific selector was the *only* correct one before this
    # ticket, and the ticket asks for it to stop being the exception.
    selectors = [selector.strip() for selector, _ in placeholder_blocks]
    assert "::placeholder" in selectors, (
        "expected a universal '::placeholder' rule covering every form field "
        f"(login, Network Tools modal, Link Config modal), found only: {selectors}"
    )

    for selector, body in placeholder_blocks:
        color_match = re.search(r"color:\s*([^;]+);", body)
        assert color_match, f"{selector.strip()!r} sets no placeholder color"

        raw = color_match.group(1).strip()
        var_match = VAR_RE.match(raw)
        assert var_match, (
            f"{selector.strip()!r}'s placeholder color {raw!r} should reference "
            "a custom property, not a literal color"
        )
        assert var_match.group(1) == "text-muted", (
            f"{selector.strip()!r} resolves to --{var_match.group(1)}, "
            "not the muted-text token --text-muted"
        )

    for theme_name in ("light", "dark"):
        assert theme.resolve("text-muted", theme_name)  # sanity: token exists


def test_authoring_page_skip_link_landmarks_and_no_info_disclosures() -> None:
    """Lab Authoring skips to a labelled canvas region, labels its palette and sidebar,
    and has no ⓘ disclosure buttons left."""
    html = (STATIC_DIR / "authoring.html").read_text()
    skip = re.search(r'<a class="skip-link" href="#([\w-]+)">([^<]+)</a>', html)
    assert skip and skip.group(2) == "Skip to canvas"
    region = re.search(r'<section id="' + skip.group(1) + r'"[^>]*aria-labelledby="([\w-]+)"', html)
    assert region, "the skip link target is not a labelled region"
    assert re.search(r'id="' + region.group(1) + r'"[^>]*>Topology canvas<', html)
    assert re.search(r'role="toolbar" aria-label="Add a Node"', html)
    assert re.search(r'<aside id="authoring-sidebar"[^>]*aria-labelledby="sidebar-heading"', html)
    assert "authoring-info" not in html
