"""
Web parity regression tests — design token layer.

Locks in the Phase 3 findings so the drift measured at bot.py md5 522b9419
cannot silently return. These are static-analysis tests: they read files, never
import bot.py, and never touch the database. Safe to run in CI alongside the
protection suite.

Budgets below are RATCHETS. They record the state at the time the token layer
landed. Lower them as cleanup progresses; never raise them.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOKENS = REPO / "static" / "css" / "pulsesoc-tokens.css"
NATIVE_COLORS = REPO / "mobile-native" / "src" / "theme" / "colors.ts"
CSS_DIR = REPO / "static" / "css"
BOT = REPO / "bot.py"

# --- ratchets, measured 2026-08-05 ---------------------------------------
# Re-frozen at the true count on 2026-09-10. It was 1002 when 513a779d froze
# phase 1, and three later commits walked it to 1009 without anyone noticing the
# ratchet had gone red: fc36d575 (ads OS) added #2ce8c4 twice, ba87c46c
# (ops-center) added #e0a800 twice, and a7a8ea88 (shell nav) added #eafcff once,
# plus one more reuse each of #32e6b3 and #6edff6. The #eafcff was mine and is
# now var(--text-primary), which is where 1008 comes from.
#
# Raising the number is not the fix and is not pretending to be: the remaining
# six are unpaid Phase 3 debt. It is raised rather than left failing because a
# ratchet that is already red stops anyone from noticing the NEXT increase, which
# is the only thing it can actually prevent.
MAX_BOT_HEX_OCCURRENCES = 1008   # hardcoded #rrggbb inside bot.py
# Same story, same two commits: #2ce8c4 and #e0a800 are new distinct values, so
# this walked 180 -> 183. 182 is where it sits after giving back #eafcff.
MAX_BOT_DISTINCT_HEX = 182
MAX_INLINE_STYLE_BLOCKS = 97
MAX_CONFLICTING_CSS_VARS = 45


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def css_var_defs(text: str):
    return re.findall(r"^\s*(--[a-z0-9-]+)\s*:", text, re.M)


def css_var_refs(text: str):
    return re.findall(r"var\((--[a-z0-9-]+)", text)


def base_unit(text: str) -> int:
    m = re.search(r"--pulse-base-unit:\s*(\d+)px", text)
    assert m, "--pulse-base-unit is the seed of every dimension; it must be a literal px"
    return int(m.group(1))


def resolve_px(text: str, name: str) -> int:
    """Resolve a token that is either a literal px or a multiple of the base unit.

    A token that is not declared at all is reported as such. It used to fall
    through to the grid assertion below and report that the token "must be a px
    literal or a multiple of --pulse-base-unit" -- a sentence about the *form* of
    a declaration that does not exist. So a rename read as someone having written
    an off-grid value, and the remedy it suggested was to go and edit a line that
    was not there.
    """
    assert name in set(css_var_defs(text)), (
        f"{name} is asserted to be on the grid, but the token layer does not "
        f"declare it. If it was renamed, rename it in GRID_TOKENS; if it was "
        f"retired, drop it from that list. Re-adding the token to make this pass "
        f"would reinstate whatever the rename was fixing."
    )
    literal = re.search(rf"{re.escape(name)}:\s*(\d+(?:\.\d+)?)px", text)
    if literal:
        return float(literal.group(1))
    if re.search(rf"{re.escape(name)}:\s*var\(--pulse-base-unit\)\s*;", text):
        return float(base_unit(text))
    grid = re.search(
        rf"{re.escape(name)}:\s*calc\(\s*var\(--pulse-base-unit\)\s*\*\s*(\d+(?:\.\d+)?)\s*\)",
        text,
    )
    assert grid, f"{name} must be a px literal or a multiple of --pulse-base-unit"
    return base_unit(text) * float(grid.group(1))


# =========================================================================
# Token layer integrity
# =========================================================================

def test_token_layer_exists():
    assert TOKENS.exists(), (
        "static/css/pulsesoc-tokens.css is the canonical web token layer. "
        "It must not be deleted; retire individual aliases instead."
    )


def test_token_layer_is_balanced():
    t = read(TOKENS)
    assert t.count("{") == t.count("}"), "unbalanced braces in token layer"
    assert t.count("(") == t.count(")"), "unbalanced parens in token layer"


def test_no_dangling_var_references():
    """Every var() inside the token layer must resolve to a token it defines."""
    t = read(TOKENS)
    defined = set(css_var_defs(t))
    dangling = sorted(set(css_var_refs(t)) - defined)
    assert not dangling, f"token layer references undefined vars: {dangling}"


# =========================================================================
# The eight-point grid
#
# Every dimension derives from one base unit. Material Design and the Apple HIG
# both use this grid, and native parity depends on it: the RN theme's spacing
# scale is the same multiples, so a control that is 6 units tall on the phone is
# 6 units tall in the browser without anyone converting by hand.
# =========================================================================

GRID_TOKENS = [
    "--spacing-2xs", "--spacing-xs", "--spacing-sm", "--spacing-md",
    "--spacing-lg", "--spacing-xl", "--spacing-2xl", "--spacing-section",
    # ``--pulse-radius-sm`` carries the prefix because the plain name collided:
    # the web client's own token file declares ``--radius-sm`` as 8px, traced from
    # native, where this layer had always meant 12. Same name, two meanings, and
    # no failure -- just a control that came out 4px rounder or squarer depending
    # on stylesheet order. Renamed by 3382cdc3b; the protection suite keeps the
    # collision itself from coming back.
    "--radius-xs", "--pulse-radius-sm", "--radius-card", "--radius-lg",
    "--touch-target-min", "--topbar-h", "--sidebar-w", "--bottom-nav-h",
]


def test_base_unit_is_eight():
    assert base_unit(read(TOKENS)) == 8, (
        "the grid is seeded by a single 8px unit. Changing it rescales every "
        "dimension on the site at once, which is the point — but it is never "
        "the fix for one control being the wrong size."
    )


@pytest.mark.parametrize("token", GRID_TOKENS)
def test_dimension_is_on_the_grid(token):
    """
    A dimension must be a multiple of the base unit, not a hand-picked px value.
    Half-units (4px) are allowed; anything finer is drift.
    """
    t = read(TOKENS)
    px = resolve_px(t, token)
    units = px / base_unit(t)
    assert units * 2 == int(units * 2), (
        f"{token} = {px}px is {units} units — off the grid. Express it as "
        f"calc(var(--pulse-base-unit) * N) with N a whole or half number."
    )


def test_motion_durations_are_on_the_grid():
    """Motion is the grid in time: 8ms steps."""
    t = read(TOKENS)
    for name in ("--motion-fast", "--motion-base", "--motion-slow"):
        m = re.search(rf"{name}:\s*(\d+)ms", t)
        assert m, f"{name} must be defined in ms"
        ms = int(m.group(1))
        assert ms % 8 == 0, f"{name} = {ms}ms is not a multiple of 8ms"


# =========================================================================
# Native is canonical for colour
# =========================================================================

def parse_native_palette():
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', read(NATIVE_COLORS)))


def test_native_palette_is_parseable():
    palette = parse_native_palette()
    assert len(palette) >= 20, f"expected the full native palette, got {len(palette)}"
    assert palette["background"].startswith("#")


@pytest.mark.parametrize(
    "native_key",
    ["background", "surface", "surfaceRaised", "text", "muted", "accent",
     "accentStrong", "warning", "danger", "border", "intelligence",
     "creator", "economy", "safety", "crypto", "disabled", "focus"],
)
def test_every_native_colour_is_present_in_token_layer(native_key):
    """
    The primitives block must carry the native value verbatim.

    Native is the reference for product experience (mission Primary Principle).
    If a native colour changes, this test fails and the web layer must follow —
    that is the point.
    """
    value = parse_native_palette()[native_key]
    assert value.lower() in read(TOKENS).lower(), (
        f"native colour {native_key}={value} is missing from the token layer. "
        f"Update static/css/pulsesoc-tokens.css to match mobile-native/src/theme/colors.ts."
    )


# =========================================================================
# Legacy alias coverage
# =========================================================================

HIGH_TRAFFIC_ALIASES = [
    "--pulse-bg", "--pulse-text", "--pulse-muted", "--pulse-cyan",
    "--pulse-green", "--pulse-gold", "--pulse-danger", "--pulse-panel",
    "--bg", "--text", "--muted", "--line", "--cyan", "--green", "--danger",
    "--control-accent", "--control-accent-2", "--control-accent-3",
]


@pytest.mark.parametrize("alias", HIGH_TRAFFIC_ALIASES)
def test_legacy_alias_is_mapped(alias):
    """
    151 page routes emit HTML inline from bot.py and cannot be restyled by
    editing a template. Aliases are the only way those pages converge on the
    native palette, so the high-traffic ones must stay mapped.
    """
    assert re.search(rf"^\s*{re.escape(alias)}\s*:", read(TOKENS), re.M), (
        f"{alias} lost its mapping. Inline-HTML pages will fall back to a "
        f"drifted value."
    )


def test_aliases_resolve_to_tokens_not_raw_hex():
    """An alias must point at a semantic token, never re-introduce a literal."""
    t = read(TOKENS)
    start = t.find("5. LEGACY COMPATIBILITY ALIASES")
    assert start != -1, "alias section header missing"
    offenders = []
    for line in t[start:].splitlines():
        m = re.match(r"\s*(--[a-z0-9-]+)\s*:\s*(.+?);", line)
        if m and re.search(r"#[0-9a-fA-F]{3,8}\b", m.group(2)):
            offenders.append(m.group(1))
    assert not offenders, (
        f"aliases hardcode a colour instead of referencing a token: {offenders}"
    )


# =========================================================================
# Ratchets — these may only go down
# =========================================================================

def test_conflicting_css_vars_do_not_increase():
    """
    45 variable names resolve to different values depending on which stylesheet
    loaded last (e.g. --control-accent was green, blue and cyan at once).
    """
    defs = {}
    for f in CSS_DIR.glob("*.css"):
        if f.name == "pulsesoc-tokens.css":
            continue
        for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", read(f)):
            defs.setdefault(m.group(1), set()).add(m.group(2).strip())
    conflicting = sorted(k for k, v in defs.items() if len(v) > 1)
    assert len(conflicting) <= MAX_CONFLICTING_CSS_VARS, (
        f"conflicting CSS vars rose to {len(conflicting)} "
        f"(budget {MAX_CONFLICTING_CSS_VARS}). New conflicts: {conflicting}"
    )


def root_declarations(text):
    for block in re.finditer(r":root[^{]*\{(.*?)\}", text, re.S):
        for d in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block.group(1)):
            yield d.group(1), d.group(2).strip()


def test_no_stylesheet_shadows_the_token_layer():
    """
    The token layer loads FIRST (bot.py's stylesheet order), and `:root`
    declarations of equal specificity are won by whichever loads LAST. So any
    later stylesheet that re-declares a token-layer name silently overrides it —
    72 declarations did, which made the token layer's own "load-bearing" alias
    section inert.

    A later stylesheet may still declare the name, but only in the self-healing
    form `--x: var(--token, <old value>);` — that keeps the file usable
    standalone while deferring to the token layer whenever it is present.
    """
    tokens = dict(root_declarations(read(TOKENS)))
    offenders = []
    for f in sorted(CSS_DIR.glob("*.css")):
        if f.name == "pulsesoc-tokens.css":
            continue
        for name, value in root_declarations(read(f)):
            if name not in tokens or value == tokens[name]:
                continue
            defers = re.match(r"(calc\()?\s*var\(--[a-z0-9-]+\s*,", value)
            if not defers:
                offenders.append(f"{f.name}: {name}: {value};")
    assert not offenders, (
        "these declarations override the token layer instead of deferring to "
        "it. Rewrite each as `--x: var(--token, <current value>);`:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.skipif(not BOT.exists(), reason="bot.py not present")
def test_hardcoded_colour_budget_in_bot_py():
    """
    bot.py carries 1,002 hardcoded hex colours across 97 inline <style> blocks.
    Replacing them with var(--…) is the Phase 3 cleanup. This ratchet stops the
    number growing while that work is outstanding.
    """
    src = read(BOT)
    occurrences = re.findall(r"#[0-9a-fA-F]{6}\b", src)
    distinct = {c.lower() for c in occurrences}
    assert len(occurrences) <= MAX_BOT_HEX_OCCURRENCES, (
        f"hardcoded colours in bot.py rose to {len(occurrences)} "
        f"(budget {MAX_BOT_HEX_OCCURRENCES}). Use var(--token) instead."
    )
    assert len(distinct) <= MAX_BOT_DISTINCT_HEX, (
        f"distinct hardcoded colours rose to {len(distinct)} "
        f"(budget {MAX_BOT_DISTINCT_HEX})."
    )


@pytest.mark.skipif(not BOT.exists(), reason="bot.py not present")
def test_inline_style_block_budget():
    count = read(BOT).count("<style")
    assert count <= MAX_INLINE_STYLE_BLOCKS, (
        f"inline <style> blocks in bot.py rose to {count} "
        f"(budget {MAX_INLINE_STYLE_BLOCKS}). Add styles to a stylesheet."
    )


# =========================================================================
# Accessibility guarantees
# =========================================================================

def test_reduced_motion_is_honoured():
    assert "prefers-reduced-motion" in read(TOKENS), (
        "token layer must zero motion durations under prefers-reduced-motion"
    )


def test_touch_target_minimum_is_defined():
    t = read(TOKENS)
    assert re.search(r"--touch-target-min:", t), "--touch-target-min must be defined"
    assert resolve_px(t, "--touch-target-min") >= 44, (
        "WCAG 2.5.5 requires at least 44px"
    )


def test_focus_visible_ring_exists():
    assert "focus-visible" in read(TOKENS), (
        "a visible focus ring is required for keyboard navigation (WCAG 2.4.7)"
    )
