"""WCAG 2.2 AA contrast regression checks for the Web UI's design tokens.

Computes contrast ratios directly from `main.css`'s custom property values
(and the `.link` resting-state stroke rule) using the standard WCAG
relative-luminance formula, with no browser involved. This is the regression
guard for the contrast tokens: a future token change that regresses any of
these ratios should fail here.
"""

import re
from pathlib import Path

CSS_PATH = (
    Path(__file__).resolve().parents[2]
    / "mniplab"
    / "webserver"
    / "static"
    / "css"
    / "main.css"
)

HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
VAR_RE = re.compile(r"^var\(--([\w-]+)\)$")


def _extract_block(css: str, selector_pattern: str) -> dict[str, str]:
    """Return the `--name: value` custom properties declared in one CSS block."""
    match = re.search(selector_pattern + r"\s*\{([^}]*)\}", css, re.S)
    assert match, f"could not find block matching {selector_pattern!r} in main.css"
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1)))


class CssTheme:
    """Resolves a custom property to its literal hex value for one theme."""

    def __init__(self, css: str) -> None:
        self._root = _extract_block(css, r":root")
        self._dark = _extract_block(css, r'\[data-theme="dark"\]')

    def resolve(self, token: str, theme: str) -> str:
        """Return the literal hex value of `--token` for `theme` ('light'/'dark').

        Follows `var(--other-token)` indirection and the light-theme fallback a
        dark-theme token that isn't overridden inherits, matching the CSS
        cascade.
        """
        raw = self._dark.get(token) if theme == "dark" else None
        if raw is None:
            raw = self._root[token]
        raw = raw.strip()

        var_match = VAR_RE.match(raw)
        if var_match:
            return self.resolve(var_match.group(1), theme)

        hex_match = HEX_RE.match(raw)
        assert hex_match, f"unsupported token value for --{token} ({theme}): {raw!r}"
        return raw


def _srgb_to_linear(channel: float) -> float:
    c = channel / 255
    if c <= 0.03928:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb(hex_color)
    return (
        0.2126 * _srgb_to_linear(r)
        + 0.7152 * _srgb_to_linear(g)
        + 0.0722 * _srgb_to_linear(b)
    )


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def blend_over(fg_hex: str, bg_hex: str, alpha: float) -> str:
    """Alpha-composite `fg_hex` at `alpha` opacity over the opaque `bg_hex`."""
    fr, fg, fb = _hex_to_rgb(fg_hex)
    br, bgc, bb = _hex_to_rgb(bg_hex)
    out = (
        round(br + alpha * (fr - br)),
        round(bgc + alpha * (fg - bgc)),
        round(bb + alpha * (fb - bb)),
    )
    return "#%02x%02x%02x" % out


def _css_text() -> str:
    return CSS_PATH.read_text()


def _theme() -> CssTheme:
    return CssTheme(_css_text())


def _link_resting_opacity() -> float:
    css = _css_text()
    match = re.search(r"\n\.link\s*\{([^}]*)\}", css, re.S)
    assert match, "could not find the .link rule in main.css"
    opacity_match = re.search(r"stroke-opacity:\s*([\d.]+)", match.group(1))
    assert opacity_match, ".link rule has no stroke-opacity"
    return float(opacity_match.group(1))


def test_dark_theme_primary_button_background_meets_text_contrast():
    """Dark-theme primary buttons' white text reaches 4.5:1 against the fill."""
    theme = _theme()
    bg = theme.resolve("primary-button-bg", "dark")
    assert contrast_ratio("#ffffff", bg) >= 4.5


def test_dark_theme_danger_button_background_meets_text_contrast():
    """Dark-theme danger buttons' white text reaches 4.5:1 against the fill."""
    theme = _theme()
    bg = theme.resolve("danger-button-bg", "dark")
    assert contrast_ratio("#ffffff", bg) >= 4.5


def test_light_theme_button_backgrounds_still_meet_text_contrast():
    """Light theme's button fills, unaffected by the dark-only fix, still pass."""
    theme = _theme()
    assert contrast_ratio("#ffffff", theme.resolve("primary-button-bg", "light")) >= 4.5
    assert contrast_ratio("#ffffff", theme.resolve("danger-button-bg", "light")) >= 4.5


def test_dark_theme_primary_and_error_colors_still_pass_as_text():
    """--primary-color/--error-color are also used as *text* elsewhere (badges,
    links, headings) against dark surfaces, unaffected by the new
    button-only tokens above — this must keep passing."""
    theme = _theme()
    primary = theme.resolve("primary-color", "dark")
    error = theme.resolve("error-color", "dark")
    surface = theme.resolve("surface-color", "dark")
    bg = theme.resolve("bg-color", "dark")

    assert contrast_ratio(primary, surface) >= 4.5
    assert contrast_ratio(primary, bg) >= 4.5
    assert contrast_ratio(error, surface) >= 4.5
    assert contrast_ratio(error, bg) >= 4.5


def test_form_field_border_meets_non_text_contrast_at_rest():
    """Form-field borders reach 3:1 against their fill and their surround."""
    theme = _theme()
    for theme_name in ("light", "dark"):
        border = theme.resolve("input-border-color", theme_name)
        bg = theme.resolve("bg-color", theme_name)
        surface = theme.resolve("surface-color", theme_name)
        assert contrast_ratio(border, bg) >= 3.0
        assert contrast_ratio(border, surface) >= 3.0


def test_topology_link_resting_state_meets_non_text_contrast():
    """An unselected, unhovered Link's rendered stroke reaches 3:1 at rest."""
    theme = _theme()
    opacity = _link_resting_opacity()
    for theme_name in ("light", "dark"):
        muted = theme.resolve("text-muted", theme_name)
        canvas_bg = theme.resolve("bg-color", theme_name)
        rendered = blend_over(muted, canvas_bg, opacity)
        assert contrast_ratio(rendered, canvas_bg) >= 3.0


def test_badge_and_role_badge_literal_colors_meet_text_contrast():
    """Literal (non-token) badge/role-badge colors documented in main.css's
    comments — recomputed here so the comments can't silently drift from
    the actual rendered contrast again."""
    pairs = [
        ("#1e3a5f", "#eff6ff"),  # protocol badge, light
        ("#93c5fd", "#1e1e1e"),  # protocol badge, dark
        ("#374151", "#f3f4f6"),  # node-count badge, light
        ("#9ca3af", "#1e1e1e"),  # node-count badge, dark
        ("#92400e", "#ffffff"),  # role badge, instructor, light (outlined, on surface)
        ("#1e40af", "#ffffff"),  # role badge, learner, light (outlined, on surface)
        ("#fcd34d", "#1e1e1e"),  # role badge, instructor, dark (outlined, on surface)
        ("#93c5fd", "#1e1e1e"),  # role badge, learner, dark (outlined, on surface)
        ("#374151", "#ffffff"),  # instructor terminal table, light
        ("#d1d5db", "#1e1e1e"),  # instructor terminal table, dark
        ("#1e3a5f", "#eff6ff"),  # service badge, light
        ("#93c5fd", "#1e1e1e"),  # service badge, dark
        ("#7c2d12", "#fff7ed"),  # relay badge, light
        ("#fdba74", "#1e1e1e"),  # relay badge, dark
        ("#134e4a", "#f0fdfa"),  # resolver service badge, light
        ("#5eead4", "#1e1e1e"),  # resolver service badge, dark
        ("#14532d", "#dcfce7"),  # resolver cache HIT badge, light
        ("#86efac", "#052e16"),  # resolver cache HIT badge, dark
        ("#713f12", "#fef9c3"),  # resolver cache negative badge, light
        ("#fde047", "#422006"),  # resolver cache negative badge, dark
    ]
    for fg, bg in pairs:
        assert contrast_ratio(fg, bg) >= 4.5, (fg, bg)


def _check_topology_badge_theme_contrast(css: str, prefix: str, badge: str) -> None:
    """Assert text and stroke contrast for one kind of Topology badge."""
    text_pattern = rf"{prefix}\.{badge}-badge-text\s*\{{[^}}]*fill:\s*(#[0-9a-fA-F]{{6}})"
    bg_pattern = rf"{prefix}\.{badge}-badge-bg\s*\{{[^}}]*fill:\s*(#[0-9a-fA-F]{{6}})"
    stroke_pattern = rf"{prefix}\.{badge}-badge-bg\s*\{{[^}}]*stroke:\s*(#[0-9a-fA-F]{{6}})"

    text_match = re.search(text_pattern, css)
    assert text_match, f"could not find fill in {prefix}.{badge}-badge-text"
    bg_match = re.search(bg_pattern, css)
    assert bg_match, f"could not find fill in {prefix}.{badge}-badge-bg"
    stroke_match = re.search(stroke_pattern, css)
    assert stroke_match, f"could not find stroke in {prefix}.{badge}-badge-bg"

    text_color = text_match.group(1).lower()
    bg_color = bg_match.group(1).lower()
    stroke_color = stroke_match.group(1).lower()

    assert contrast_ratio(text_color, bg_color) >= 4.5
    assert contrast_ratio(stroke_color, bg_color) >= 3.0


def test_topology_badge_rules_in_main_css_meet_contrast() -> None:
    """Topology badge CSS rules meet WCAG 2.2 AA text and non-text contrast."""
    css = _css_text()
    for badge in ("service", "relay"):
        _check_topology_badge_theme_contrast(css, r"(?:^|\n)", badge)
        _check_topology_badge_theme_contrast(css, r'\[data-theme="dark"\]\s+', badge)


def test_topology_vlan_badge_text_rule() -> None:
    """Topology VLAN badge text maintains white fill and no stroke in CSS."""
    css = _css_text()
    match = re.search(r"\.vlan-badge text,\s*\.vlan-badge-text\s*\{([^}]*)\}", css)
    assert match, "could not find .vlan-badge text rule in main.css"
    block = match.group(1)
    assert "fill: #ffffff !important" in block
    assert "stroke: none !important" in block


def test_nat_color_meets_non_text_contrast_on_canvas_node_and_palette() -> None:
    """Internet Gateway icons reach 3:1 against the canvas, the Node fill and the palette."""
    theme = _theme()
    for theme_name in ("light", "dark"):
        nat = theme.resolve("nat-color", theme_name)
        for background in ("bg-color", "node-bg", "surface-color"):
            ratio = contrast_ratio(nat, theme.resolve(background, theme_name))
            assert ratio >= 3.0, (theme_name, background, round(ratio, 2))


def test_hover_grey_is_visibly_distinct_from_the_surface() -> None:
    """The hover grey stands out from the surface: darker in Light, lighter in Dark.

    In Light it is also darker than the page background, so a hovered control
    sitting on the page gets darker, never lighter.
    """
    theme = _theme()
    for theme_name in ("light", "dark"):
        hover = theme.resolve("surface-hover", theme_name)
        surface = theme.resolve("surface-color", theme_name)
        ratio = contrast_ratio(hover, surface)
        assert ratio >= 1.15, (theme_name, round(ratio, 2))
    light_hover = theme.resolve("surface-hover", "light")
    assert relative_luminance(light_hover) < relative_luminance(theme.resolve("surface-color", "light"))
    assert relative_luminance(light_hover) < relative_luminance(theme.resolve("bg-color", "light"))
    assert relative_luminance(theme.resolve("surface-hover", "dark")) > relative_luminance(
        theme.resolve("surface-color", "dark")
    )


def test_text_on_the_hover_grey_stays_readable() -> None:
    """Muted and full text on the hover grey reach 4.5:1 in both themes."""
    theme = _theme()
    for theme_name in ("light", "dark"):
        hover = theme.resolve("surface-hover", theme_name)
        for text in ("text-color", "text-muted"):
            ratio = contrast_ratio(theme.resolve(text, theme_name), hover)
            assert ratio >= 4.5, (theme_name, text, round(ratio, 2))


def test_filled_button_hover_is_a_visibly_darker_step_with_readable_text() -> None:
    """Primary and danger button hovers darken their fill by 1.15:1 and keep white text at 4.5:1."""
    theme = _theme()
    for theme_name in ("light", "dark"):
        for kind in ("primary", "danger"):
            rest = theme.resolve(f"{kind}-button-bg", theme_name)
            hover = theme.resolve(f"{kind}-button-hover", theme_name)
            assert relative_luminance(hover) < relative_luminance(rest), (theme_name, kind)
            step = contrast_ratio(hover, rest)
            assert step >= 1.15, (theme_name, kind, round(step, 2))
            text = contrast_ratio("#ffffff", hover)
            assert text >= 4.5, (theme_name, kind, round(text, 2))


def test_danger_button_hover_uses_the_theme_colour() -> None:
    """The danger button's hover takes its fill from the theme, not a literal colour."""
    css = _css_text()
    match = re.search(r"\n\.btn-danger:hover:not\(:disabled\)\s*\{([^}]*)\}", css)
    assert match, "could not find the .btn-danger hover rule in main.css"
    background = re.search(r"background:\s*([^;]+);", match.group(1))
    assert background, ".btn-danger hover rule sets no background"
    assert background.group(1).strip() == "var(--danger-button-hover)"


def test_selected_tab_text_is_readable_where_tabs_sit() -> None:
    """A selected tab's primary text reaches 4.5:1 wherever the tab sits.

    Selected tabs take no fill, so their text sits on the dialog or panel
    surface, or on the page background behind the Terminal tabs; a fill such
    as the hover grey would drop it under 4.5:1 in Dark.
    """
    css = _css_text()
    match = re.search(r"\n:is\(\.tools-tab[^{)]*\)\[aria-selected=\"true\"\][^{]*\{([^}]*)\}", css)
    assert match, "could not find the shared selected-tab rule in main.css"
    assert re.search(r"(?<![-\w])color:\s*var\(--primary-color\);", match.group(1))
    assert re.search(r"background:\s*transparent;", match.group(1))
    theme = _theme()
    for theme_name in ("light", "dark"):
        primary = theme.resolve("primary-color", theme_name)
        for background in ("surface-color", "bg-color"):
            ratio = contrast_ratio(primary, theme.resolve(background, theme_name))
            assert ratio >= 4.5, (theme_name, background, round(ratio, 2))
