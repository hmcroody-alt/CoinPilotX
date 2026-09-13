#!/usr/bin/env python3
"""Assert the web design tokens still match the native theme they were ported from.

WHY THIS EXISTS
---------------
`web/src/styles/tokens.css` is a hand-written translation of
`mobile-native/src/theme/colors.ts` and the palettes in
`mobile-native/src/theme/ThemeContext.tsx`. Two copies of one table is a defect
waiting for a date: someone adjusts a surface colour in the app, ships it, and
the website keeps rendering last quarter's palette. Nothing breaks. No test
fails. The site just stops being the same product as the app, one hex value at
a time, and the first report comes from a member who says it "looks a bit off"
months later.

The web cannot simply import the native module -- `mobile-native/` is a React
Native workspace with its own toolchain, and coupling the web build to it would
make a web deploy depend on the app's dependency tree. So the copy is real and
unavoidable; what is avoidable is the copy being *unchecked*.

WHAT IT COMPARES
----------------
Resolved palettes, not declarations. Native builds `BLACK` as
`{ ...legacyColors, ...overrides }`; the CSS gets the same effect from `:root`
plus a short `[data-theme="black"]` block. Those are different spellings of one
result, and comparing spellings would force the CSS to be written in whatever
shape the TS happens to use today. So both sides are resolved to a full
key -> colour map first, and the maps are compared.

Colours are compared as numbers, not as text. `rgba(0, 150, 111, 0.10)` and
`rgba(0, 150, 111, 0.1)` are the same colour, and a gate that reports them as a
divergence is a gate that gets switched off within a month. `#FFF`, `#ffffff`
and `rgb(255,255,255)` all reduce to the same tuple.

NOT TAUTOLOGICAL
----------------
The two sides are read by two independent parsers -- a TypeScript object-literal
reader and a CSS custom-property reader -- that share no extraction code. This
matters more than it looks: the failure mode of a naive parity checker is that
one parser is used to produce both sides, so a parser that stops seeing a
palette drops it from both and the comparison still agrees. That is the same
shape as the tautological-record bug already found in the web build freshness
gate's tests. Here, a parser that stops seeing something yields an empty side,
and an empty side is reported as could-not-check rather than as a pass.

EXIT CODES
----------
  0  verified      -- every ported value matches native
  1  verified-broken -- a real divergence, named
  3  could-not-check -- a file is missing, or a parser extracted nothing

Three, not two. A checker that cannot check must not report success: every way
this script can break -- a renamed file, a restructured literal, a regex that
stops matching -- makes it quieter, not louder. Exit 3 is the branch that turns
"I found no problems" back into "I found nothing", which are not the same claim.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NATIVE_COLORS = REPO / "mobile-native" / "src" / "theme" / "colors.ts"
NATIVE_CONTEXT = REPO / "mobile-native" / "src" / "theme" / "ThemeContext.tsx"
WEB_TOKENS = REPO / "web" / "src" / "styles" / "tokens.css"
WEB_THEMES = REPO / "web" / "src" / "theme" / "themes.ts"

EXIT_OK = 0
EXIT_DIVERGED = 1
EXIT_NO_DATA = 3


# ---------------------------------------------------------------------------
# The one hand-written thing in this file
# ---------------------------------------------------------------------------

# Native palette key -> CSS custom property. This mapping is the only place the
# translation is asserted, which is why it is a literal table and not derived by
# camelCase-to-kebab conversion: a derivation would silently accept a key that
# native renamed, by generating a new property name nobody declared and then
# reporting it as missing from the CSS -- correct, but with a confusing message.
# Spelled out, an unmapped key is caught by the completeness check below and
# reported as exactly what it is.
PALETTE_TO_CSS = {
    "background": "--pulse-bg",
    "surface": "--pulse-surface",
    "surfaceRaised": "--pulse-surface-raised",
    "text": "--pulse-text",
    "muted": "--pulse-muted",
    "accent": "--pulse-accent",
    "accentStrong": "--pulse-accent-strong",
    "warning": "--pulse-warning",
    "danger": "--pulse-danger",
    "border": "--pulse-border",
    "intelligence": "--pulse-intelligence",
    "creator": "--pulse-creator",
    "economy": "--pulse-economy",
    "safety": "--pulse-safety",
    "crypto": "--pulse-crypto",
    "disabled": "--pulse-disabled",
    "focus": "--pulse-focus",
    "glass": "--pulse-glass",
    "glassStrong": "--pulse-glass-strong",
    "signalDim": "--pulse-signal-dim",
    "signalSoft": "--pulse-signal-soft",
    "dangerSoft": "--pulse-danger-soft",
    "warningSoft": "--pulse-warning-soft",
}

# Native `metrics` value -> CSS custom property, for the values ported verbatim
# rather than onto the eight-point grid.
METRICS_TO_CSS = {
    "rowMinHeight": "--native-row-min-height",
    "rowMinHeightCompact": "--native-row-min-height-compact",
    "rowPaddingVertical": "--native-row-pad-y",
    "rowPaddingVerticalCompact": "--native-row-pad-y-compact",
    "rowPaddingHorizontal": "--native-row-pad-x",
    "sectionGap": "--native-section-gap",
    "sectionGapCompact": "--native-section-gap-compact",
    "radius": "--native-radius",
}

# native palette name -> the `data-theme` value that must resolve to it.
THEME_SELECTORS = {
    "DARK": "dark",
    "BLACK": "black",
    "LIGHT_FUTURISTIC": "light_futuristic",
    "WHITE": "white",
}

# native partial-overlay name -> the composed attribute selector pair.
CONTRAST_SELECTORS = {
    "HIGH_CONTRAST_DARK": ("dark", "black"),
    "HIGH_CONTRAST_LIGHT": ("light_futuristic", "white"),
}


class CouldNotCheck(Exception):
    """A parser extracted nothing. Never reported as a pass."""


# ---------------------------------------------------------------------------
# Colour normalisation
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3,8})$")
_FUNC_RE = re.compile(r"^rgba?\(([^)]*)\)$")


def as_rgba(value: str) -> tuple[float, float, float, float]:
    """Reduce any CSS colour spelling to one comparable tuple.

    Raises ValueError on anything unrecognised rather than returning a sentinel.
    A sentinel would make two unparseable values compare equal to each other,
    which is the failure mode where the gate passes precisely because it stopped
    understanding both sides.
    """
    raw = value.strip().lower()

    hex_match = _HEX_RE.match(raw)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        if len(digits) not in (6, 8):
            raise ValueError(f"malformed hex colour: {value!r}")
        r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        a = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (float(r), float(g), float(b), round(a, 4))

    func_match = _FUNC_RE.match(raw)
    if func_match:
        parts = [p.strip() for p in func_match.group(1).replace("/", ",").split(",")]
        parts = [p for p in parts if p]
        if len(parts) not in (3, 4):
            raise ValueError(f"malformed rgb()/rgba(): {value!r}")
        try:
            nums = [float(p.rstrip("%")) for p in parts]
        except ValueError as exc:
            raise ValueError(f"non-numeric channel in {value!r}") from exc
        alpha = round(nums[3], 4) if len(nums) == 4 else 1.0
        return (nums[0], nums[1], nums[2], alpha)

    raise ValueError(f"unrecognised colour: {value!r}")


# ---------------------------------------------------------------------------
# Side A: the native TypeScript
# ---------------------------------------------------------------------------

_ENTRY_RE = re.compile(r'(?:"([A-Za-z0-9_]+)"|([A-Za-z0-9_]+))\s*:\s*"([^"]*)"')
_SPREAD_RE = re.compile(r"\.\.\.\s*([A-Za-z0-9_]+)")


def _object_body(source: str, declaration: str) -> str:
    """The text between the braces of `<declaration> = { ... }`.

    Brace-counted rather than regex-matched to the first `}`. The palettes are
    flat today, but a nested object added later would make a lazy regex silently
    truncate the literal -- dropping keys, which reads downstream as "the CSS is
    missing values it never had".
    """
    start = source.find(declaration)
    if start < 0:
        raise CouldNotCheck(f"could not find `{declaration}` in the native source")
    open_at = source.find("{", start)
    if open_at < 0:
        raise CouldNotCheck(f"`{declaration}` is not followed by an object literal")

    depth = 0
    for index in range(open_at, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_at + 1 : index]
    raise CouldNotCheck(f"unbalanced braces after `{declaration}`")


def parse_ts_object(source: str, declaration: str) -> tuple[dict[str, str], list[str]]:
    """String-valued entries of a TS object literal, plus the names it spreads."""
    body = _object_body(source, declaration)
    entries = {
        (quoted or bare): val for quoted, bare, val in _ENTRY_RE.findall(body)
    }
    return entries, _SPREAD_RE.findall(body)


def native_palettes() -> dict[str, dict[str, str]]:
    """Every native palette, resolved through its spreads.

    Resolution order matters and follows the language: a spread is applied
    first, then the literal keys after it override. `WHITE` spreads
    `LIGHT_FUTURISTIC`, which is itself a full literal, so the recursion is one
    level deep in practice and bounded by the declaration table below.
    """
    if not NATIVE_COLORS.is_file():
        raise CouldNotCheck(f"missing {NATIVE_COLORS.relative_to(REPO)}")
    if not NATIVE_CONTEXT.is_file():
        raise CouldNotCheck(f"missing {NATIVE_CONTEXT.relative_to(REPO)}")

    colors_src = NATIVE_COLORS.read_text(encoding="utf-8")
    context_src = NATIVE_CONTEXT.read_text(encoding="utf-8")

    base, _ = parse_ts_object(colors_src, "export const colors")
    if not base:
        raise CouldNotCheck("parsed zero entries from the native `colors` object")

    resolved: dict[str, dict[str, str]] = {"legacyColors": base}

    # Declaration text -> the name the rest of this module uses. Ordered so a
    # palette's spread source is always resolved before it is needed.
    declarations = [
        ("const DARK: Palette", "DARK"),
        ("const BLACK: Palette", "BLACK"),
        ("const LIGHT_FUTURISTIC: Palette", "LIGHT_FUTURISTIC"),
        ("const WHITE: Palette", "WHITE"),
        ("const HIGH_CONTRAST_DARK: Partial<Palette>", "HIGH_CONTRAST_DARK"),
        ("const HIGH_CONTRAST_LIGHT: Partial<Palette>", "HIGH_CONTRAST_LIGHT"),
    ]

    for declaration, name in declarations:
        entries, spreads = parse_ts_object(context_src, declaration)
        merged: dict[str, str] = {}
        for spread in spreads:
            source_palette = resolved.get(spread)
            if source_palette is None:
                raise CouldNotCheck(
                    f"`{name}` spreads `{spread}`, which is not a palette this "
                    f"gate knows how to resolve"
                )
            merged.update(source_palette)
        merged.update(entries)
        if not merged:
            raise CouldNotCheck(f"parsed zero entries for native `{name}`")
        resolved[name] = merged

    return resolved


def read_active_theme(source: str) -> str:
    """The appearance `buildTheme` pins, extracted from TS source text.

    Split from `native_active_theme` so it can be tested against source a test
    supplies. That is not tidiness: a test that only checks the real file
    asserts `== "dark"`, and `return "dark"` satisfies it exactly. The property
    "this value is read rather than assumed" is unobservable while the file
    under test is the only input, and it is the entire point of the function.
    """
    found = re.search(
        r"const\s+activeTheme\s*:\s*ThemeMode\s*=\s*\"([a-z_]+)\"", source
    )
    if not found:
        raise CouldNotCheck(
            "could not find `const activeTheme: ThemeMode = \"...\"` in "
            "ThemeContext.tsx -- if the pin was removed, this gate's premise "
            "changed and the web pin needs a decision, not a silent pass"
        )
    return found.group(1)


def native_active_theme() -> str:
    return read_active_theme(NATIVE_CONTEXT.read_text(encoding="utf-8"))


def native_metrics() -> dict[str, int]:
    """The `metrics` values the web carries verbatim.

    Native writes them as ternaries on `compact`, e.g.
    `rowMinHeight: Math.round((compact ? 46 : 56) * ...)`. Both arms are
    extracted: the compact arm is a real value the web must also carry, and
    reading only the default arm would leave half the parity unchecked.
    """
    source = NATIVE_CONTEXT.read_text(encoding="utf-8")
    # `"metrics: {"` and not `"metrics:"`. The loose form matched the `Theme`
    # type's `metrics: Metrics;` field declaration ~80 lines earlier, and
    # `_object_body` then walked forward to the next unrelated `{` -- the body of
    # `resolveScheme` -- and returned it. The zero-values guard below caught it,
    # which is the only reason this is a comment rather than a silent hole.
    body = _object_body(source, "metrics: {")

    values: dict[str, int] = {}
    ternary = re.compile(
        r"([A-Za-z][A-Za-z0-9]*)\s*:[^,}]*?compact\s*\?\s*(\d+)\s*:\s*(\d+)"
    )
    for name, compact_arm, default_arm in ternary.findall(body):
        values[name] = int(default_arm)
        values[f"{name}Compact"] = int(compact_arm)

    plain = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*:\s*(\d+)\s*,?\s*$", re.MULTILINE)
    for name, number in plain.findall(body):
        values.setdefault(name, int(number))

    if not values:
        raise CouldNotCheck("parsed zero values from native `metrics`")
    return values


# ---------------------------------------------------------------------------
# Side B: the web CSS
# ---------------------------------------------------------------------------

_DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


# Properties an at-rule is permitted to redeclare. Everything here is a value
# native *derives* from a condition rather than stores in a palette, so it is
# outside colour parity by construction:
#   - glass/glass-strong collapse to surface/background under reduced
#     transparency, which native computes in `buildTheme` rather than storing.
#   - the durations zero out under reduced motion, which native does in
#     `theme.duration()`.
# Any other property appearing inside an at-rule means this gate's resolution
# model no longer describes the stylesheet, and it reports could-not-check
# rather than comparing a value some media query overrides.
AT_RULE_ALLOWED = frozenset(
    {
        "--pulse-glass",
        "--pulse-glass-strong",
        "--dur-instant",
        "--dur-quick",
        "--dur-standard",
        "--dur-reveal",
        "--dur-entrance",
        "--dur-stagger",
    }
)


def _split_at_rules(css: str) -> tuple[str, str]:
    """Separate top-level at-rule blocks from ordinary rules.

    An earlier version did not do this, and the consequence was subtle enough to
    be worth recording: the block regex cannot match a body containing braces, so
    for `@media (...) { :root { ... } }` it skipped the `@media` line and matched
    the INNER `:root` block on its own. The media query's contents were therefore
    hoisted and applied unconditionally -- and because that block sits last in
    the file, it overrode every palette above it. The gate did not mis-report;
    it compared a stylesheet that does not exist.
    """
    ordinary: list[str] = []
    at_rules: list[str] = []

    index = 0
    while True:
        at = css.find("@", index)
        if at < 0:
            ordinary.append(css[index:])
            return "".join(ordinary), "".join(at_rules)

        open_at = css.find("{", at)
        if open_at < 0:
            ordinary.append(css[index:])
            return "".join(ordinary), "".join(at_rules)

        depth = 0
        end = len(css)
        for position in range(open_at, len(css)):
            if css[position] == "{":
                depth += 1
            elif css[position] == "}":
                depth -= 1
                if depth == 0:
                    end = position + 1
                    break

        ordinary.append(css[index:at])
        at_rules.append(css[open_at:end])
        index = end


def parse_css_blocks(css: str) -> list[tuple[list[str], dict[str, str]]]:
    """Every `selectors { --prop: value; }` block, in source order.

    At-rule bodies are excluded -- see `_split_at_rules`. Order is preserved
    because CSS resolution depends on it, and a set would quietly discard the
    later of two blocks declaring the same property.
    """
    stripped = _strip_comments(css)
    ordinary, at_rules = _split_at_rules(stripped)

    offenders = sorted(
        {
            prop
            for prop, _ in _DECL_RE.findall(at_rules)
            if prop not in AT_RULE_ALLOWED
        }
    )
    if offenders:
        raise CouldNotCheck(
            "these properties are declared inside an at-rule, which this gate "
            "does not model: " + ", ".join(offenders) + ". Either move them out "
            "or extend AT_RULE_ALLOWED with a reason."
        )

    blocks: list[tuple[list[str], dict[str, str]]] = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", ordinary):
        selector_text, body = match.group(1), match.group(2)
        selectors = [s.strip() for s in selector_text.split(",") if s.strip()]
        declarations = {
            prop: val.strip() for prop, val in _DECL_RE.findall(body)
        }
        if selectors and declarations:
            blocks.append((selectors, declarations))

    return blocks


def _selector_matches(selector: str, theme: str, high_contrast: bool) -> bool:
    """Whether a selector applies to `<html data-theme=theme [data-hc=1]>`.

    Only the shapes this stylesheet actually uses are understood, and anything
    else returns False. That is the conservative direction: an unrecognised
    selector contributes nothing, so a value it was supposed to supply shows up
    as missing. The opposite default would let a typo'd selector satisfy the
    gate by being treated as universal.
    """
    normalized = selector.strip()
    if normalized in (":root", "html", "*"):
        return True

    parts = re.findall(r"\[([a-z-]+)=\"([^\"]+)\"\]", normalized)
    if not parts:
        return False
    # Reject anything with content outside the attribute selectors, e.g. a
    # descendant or class qualifier, which this resolver does not model.
    if re.sub(r"\[[a-z-]+=\"[^\"]+\"\]", "", normalized).strip():
        return False

    for attribute, value in parts:
        if attribute == "data-theme":
            if value != theme:
                return False
        elif attribute == "data-hc":
            if not (high_contrast and value == "1"):
                return False
        elif attribute == "data-reduce-transparency":
            # Not part of palette parity: native derives it, and `tokens.css`
            # expresses it as a self-reference rather than as colour literals.
            return False
        else:
            return False
    return True


def resolve_css_palette(
    blocks: list[tuple[list[str], dict[str, str]]], theme: str, high_contrast: bool
) -> dict[str, str]:
    """The custom properties in effect for a given theme + contrast state.

    Later blocks win, which models source order. It does not model specificity,
    and that is a real limitation rather than an oversight: `tokens.css` is
    ordered so the two agree (the high-contrast blocks come last), and the
    alternative -- a specificity engine -- would be a CSS implementation this
    gate has no business containing. The ordering requirement is asserted by
    `test_high_contrast_blocks_come_last` rather than assumed.
    """
    resolved: dict[str, str] = {}
    for selectors, declarations in blocks:
        if any(_selector_matches(s, theme, high_contrast) for s in selectors):
            resolved.update(declarations)
    return _resolve_vars(resolved)


_VAR_RE = re.compile(r"^var\(\s*(--[a-z0-9-]+)\s*\)$")


def _resolve_vars(properties: dict[str, str], limit: int = 8) -> dict[str, str]:
    """Follow `var(--x)` references to the literal they stand for.

    Needed because `tokens.css` expresses the reduced-transparency collapse as
    `--pulse-glass: var(--pulse-surface)` rather than by repeating a colour.
    That is the better stylesheet -- a literal copy stops tracking the day a
    surface value changes -- so the gate resolves the reference instead of
    demanding the worse spelling.

    Chains are followed, with a bound. An unresolvable or cyclic reference is
    left exactly as written so it surfaces downstream as `unrecognised colour`
    with the real text in the message, rather than being silently dropped.
    """
    output = dict(properties)
    for _ in range(limit):
        changed = False
        for name, value in list(output.items()):
            match = _VAR_RE.match(value.strip())
            if not match:
                continue
            target = output.get(match.group(1))
            if target is None or target.strip() == value.strip():
                continue
            output[name] = target
            changed = True
        if not changed:
            break
    return output


def web_active_theme() -> str:
    if not WEB_THEMES.is_file():
        raise CouldNotCheck(f"missing {WEB_THEMES.relative_to(REPO)}")
    source = WEB_THEMES.read_text(encoding="utf-8")
    found = re.search(r"ACTIVE_THEME\s*:\s*ThemeMode\s*=\s*\"([a-z_]+)\"", source)
    if not found:
        raise CouldNotCheck("could not find `ACTIVE_THEME` in web/src/theme/themes.ts")
    return found.group(1)


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def compare(
    natives: dict[str, dict[str, str]],
    blocks: list[tuple[list[str], dict[str, str]]],
    metrics: dict[str, int],
    native_pin: str,
    web_pin: str,
) -> list[str]:
    """Every divergence, named. Empty means verified."""
    problems: list[str] = []

    def check_palette(native_name: str, theme: str, high_contrast: bool) -> None:
        native_palette = natives[native_name]
        css = resolve_css_palette(blocks, theme, high_contrast)
        label = f'{theme}{" + high-contrast" if high_contrast else ""}'

        for key, native_value in sorted(native_palette.items()):
            prop = PALETTE_TO_CSS.get(key)
            if prop is None:
                problems.append(
                    f"{label}: native palette key `{key}` has no CSS property "
                    f"mapped for it -- add it to PALETTE_TO_CSS and to tokens.css"
                )
                continue
            web_value = css.get(prop)
            if web_value is None:
                problems.append(f"{label}: `{prop}` is not declared for this theme")
                continue
            try:
                if as_rgba(native_value) != as_rgba(web_value):
                    problems.append(
                        f"{label}: `{prop}` is {web_value} but native "
                        f"`{key}` is {native_value}"
                    )
            except ValueError as exc:
                problems.append(f"{label}: `{prop}`: {exc}")

    for native_name, theme in THEME_SELECTORS.items():
        check_palette(native_name, theme, high_contrast=False)

    # High contrast is a PARTIAL overlay: only the keys it names change, and
    # every other key keeps the base theme's value. Both halves are checked --
    # that the overrides landed, and that nothing else moved. The second half is
    # the one worth having: an overlay accidentally written as a full palette
    # would apply dark surfaces under the light themes, and checking only the
    # named keys would call that correct.
    for native_name, themes in CONTRAST_SELECTORS.items():
        overlay = natives[native_name]
        for theme in themes:
            base_name = next(n for n, t in THEME_SELECTORS.items() if t == theme)
            expected = dict(natives[base_name])
            expected.update(overlay)
            natives[f"__{native_name}_{theme}"] = expected
            check_palette(f"__{native_name}_{theme}", theme, high_contrast=True)

    # Metrics carried verbatim.
    root = resolve_css_palette(blocks, "dark", high_contrast=False)
    for key, prop in METRICS_TO_CSS.items():
        native_value = metrics.get(key)
        if native_value is None:
            problems.append(f"native `metrics.{key}` was not found in ThemeContext")
            continue
        web_value = root.get(prop)
        if web_value is None:
            problems.append(f"`{prop}` is not declared in tokens.css")
            continue
        if web_value.strip() != f"{native_value}px":
            problems.append(
                f"`{prop}` is {web_value} but native `metrics.{key}` is "
                f"{native_value}"
            )

    if native_pin != web_pin:
        problems.append(
            f"the web pins ACTIVE_THEME={web_pin!r} but native `buildTheme` pins "
            f"activeTheme={native_pin!r}. If native has activated theme "
            f"selection, the web pin is now a divergence and needs lifting "
            f"deliberately -- see web/src/theme/themes.ts"
        )

    return problems


def main(argv: list[str]) -> int:
    del argv

    try:
        natives = native_palettes()
        metrics = native_metrics()
        native_pin = native_active_theme()
        web_pin = web_active_theme()

        if not WEB_TOKENS.is_file():
            raise CouldNotCheck(f"missing {WEB_TOKENS.relative_to(REPO)}")
        blocks = parse_css_blocks(WEB_TOKENS.read_text(encoding="utf-8"))
        if not blocks:
            raise CouldNotCheck("parsed zero custom-property blocks from tokens.css")
    except CouldNotCheck as exc:
        print(f"native-theme-parity: {exc}", file=sys.stderr)
        print(
            "native-theme-parity: could not check. This is not a pass.",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    problems = compare(natives, blocks, metrics, native_pin, web_pin)

    if problems:
        print("native-theme-parity: the web tokens have drifted from native.")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nNative is the source of truth. Update web/src/styles/tokens.css to "
            "match, not the other way round."
        )
        return EXIT_DIVERGED

    themes = len(THEME_SELECTORS) + sum(len(v) for v in CONTRAST_SELECTORS.values())
    print(
        f"native-theme-parity: verified {themes} resolved palettes "
        f"({len(PALETTE_TO_CSS)} colours each), {len(METRICS_TO_CSS)} metrics, "
        f"and the {native_pin!r} appearance pin."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
