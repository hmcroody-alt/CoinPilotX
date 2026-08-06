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
MAX_BOT_HEX_OCCURRENCES = 1002   # hardcoded #rrggbb inside bot.py
MAX_BOT_DISTINCT_HEX = 180
MAX_INLINE_STYLE_BLOCKS = 97
MAX_CONFLICTING_CSS_VARS = 45


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def css_var_defs(text: str):
    return re.findall(r"^\s*(--[a-z0-9-]+)\s*:", text, re.M)


def css_var_refs(text: str):
    return re.findall(r"var\((--[a-z0-9-]+)", text)


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
    m = re.search(r"--touch-target-min:\s*(\d+)px", t)
    assert m, "--touch-target-min must be defined"
    assert int(m.group(1)) >= 44, "WCAG 2.5.5 requires at least 44px"


def test_focus_visible_ring_exists():
    assert "focus-visible" in read(TOKENS), (
        "a visible focus ring is required for keyboard navigation (WCAG 2.4.7)"
    )
