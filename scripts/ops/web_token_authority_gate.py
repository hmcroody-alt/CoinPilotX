#!/usr/bin/env python3
"""Assert the website's two design-token files cannot contradict each other.

There are two of them, and there is a real reason for that:

  static/css/pulsesoc-tokens.css   the server-rendered surface -- the marketing
                                   page, the legal pages, and (through its
                                   legacy-alias section) the 151 routes that
                                   build HTML inline inside `bot.py` and cannot
                                   be restyled by editing a template.

  web/src/styles/tokens.css        the new web client, bundled by Vite into
                                   hashed assets.

Neither can absorb the other yet. The first exists to carry names that predate
the rebuild; the second exists to carry the names ported from the native theme.
What is not acceptable is for the two to be *unrelated*, and until this gate
they were: three names were declared in both files with different values, and
the legacy file held a second, entirely ungated copy of the native palette.

WHY A COLLISION IS THE QUIET KIND OF BUG
----------------------------------------
Nothing fails when two stylesheets declare `--z-modal` differently. The one
that loads last wins, per document. So the bug is not "the site is broken", it
is "a modal renders beneath a toast on the subset of pages that happen to link
both files, in whichever order the template happens to link them" -- which is
not reproducible from the stylesheet alone, only from the pair. The three found
here were `--radius-sm` (8 against 12), `--z-modal` (40 against 888) and
`--z-toast` (48 against 8888).

WHAT IS CHECKED
---------------
1. The legacy file's `--pulse-palette-*` primitives are the native palette.
   `scripts/ops/native_theme_parity_gate.py` already holds `web/src/styles/
   tokens.css` to `colors.ts`; this is the same claim for the other copy, which
   had no gate at all. Native is read directly, so the check is against the app
   rather than against the other CSS file -- a chain of web files agreeing with
   each other while all drifting from the app is exactly the failure the
   direction of this comparison rules out.

2. No custom property is declared in both files with a different value.

3. The legacy file's two layout measures are the web client's two layout
   measures. They read 680 and 1288 before Phase 1c traced `HomeScreen.tsx`;
   the app says 884 and 1480. A marketing page capped at 1288 while the product
   caps at 1480 is the website holding a second opinion about the product's
   shape, which is the thing this rebuild is not allowed to do.

RESOLVED VALUES, NOT DECLARATIONS
---------------------------------
The legacy file reaches its values through `var()` chains and
`calc(var(--pulse-base-unit) * N)`; the client file mostly writes literals.
Comparing declaration text would force one file to be written in the other's
shape. So both sides are resolved first and compared as numbers -- colours to
an RGBA tuple, lengths to px -- because `rgba(0, 150, 111, 0.10)` and
`rgba(0, 150, 111, 0.1)` are one colour, and a gate that calls them a
divergence is a gate somebody switches off.

Only the default scope is compared. `[data-theme="black"]`, `[data-hc="1"]` and
the `@media` overrides are conditional re-declarations, and the legacy file has
no themes at all -- so comparing them would report every theme the client
supports and the marketing surface does not as a divergence, which is a true
statement about the files and a useless one about the product.

Exit codes:
  0  verified -- the two token files agree, and the legacy palette is native's
  1  verified-broken -- a real divergence, named
  3  could-not-check -- the gate could not read one of the sides

Three, not two. Every way this script can break -- a renamed prefix, a
restructured block, a regex that stops matching -- makes it quieter rather than
louder, and an empty comparison passes. Exit 3 is the branch that turns "I
found no problems" back into "I found nothing".
"""

from __future__ import annotations

import pathlib
import re
import sys

EXIT_OK = 0
EXIT_DIVERGED = 1
EXIT_NO_DATA = 3

REPO = pathlib.Path(__file__).resolve().parents[2]
NATIVE_COLORS = REPO / "mobile-native" / "src" / "theme" / "colors.ts"
LEGACY_TOKENS = REPO / "static" / "css" / "pulsesoc-tokens.css"
CLIENT_TOKENS = REPO / "web" / "src" / "styles" / "tokens.css"

#: The default scope on each side. The client file opens with
#: `:root, [data-theme="dark"]` because dark is the only palette the binary can
#: currently produce; both selectors name the same resolved palette, so a block
#: carrying either (or both) is default scope.
DEFAULT_SELECTORS = frozenset({":root", '[data-theme="dark"]'})

#: native `colors.ts` key -> the legacy stylesheet's primitive name.
#:
#: Spelled out rather than derived by camelCase-to-kebab conversion. A derived
#: mapping silently drops any key whose spelling does not survive the rule, and
#: a dropped key is an unchecked colour that reports as a pass.
PALETTE = {
    "background": "--pulse-palette-background",
    "surface": "--pulse-palette-surface",
    "surfaceRaised": "--pulse-palette-surface-raised",
    "text": "--pulse-palette-text",
    "muted": "--pulse-palette-muted",
    "accent": "--pulse-palette-accent",
    "accentStrong": "--pulse-palette-accent-strong",
    "warning": "--pulse-palette-warning",
    "danger": "--pulse-palette-danger",
    "border": "--pulse-palette-border",
    "intelligence": "--pulse-palette-intelligence",
    "creator": "--pulse-palette-creator",
    "economy": "--pulse-palette-economy",
    "safety": "--pulse-palette-safety",
    "crypto": "--pulse-palette-crypto",
    "disabled": "--pulse-palette-disabled",
    "focus": "--pulse-palette-focus",
    "glass": "--pulse-palette-glass",
    "glassStrong": "--pulse-palette-glass-strong",
    "signalDim": "--pulse-palette-signal-dim",
    "signalSoft": "--pulse-palette-signal-soft",
    "dangerSoft": "--pulse-palette-danger-soft",
    "warningSoft": "--pulse-palette-warning-soft",
}

#: (legacy name, client name, what it means). The legacy surface names its
#: bounds `--measure-*`; the client names them after the parts of the shell they
#: bound. Different names, one number each.
MEASURES = [
    ("--measure-feed-max", "--feed-max", "the reading column ceiling"),
    ("--measure-container-max", "--shell-max", "the outer shell ceiling"),
]


class CouldNotCheck(Exception):
    """The gate could not read what it needs. Never the same as 'no problems'."""


# ---------------------------------------------------------------------------
# CSS side
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_AT_RULE_RE = re.compile(r"@(?:media|supports|container|layer)\b")
_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);")
_BLOCK_RE = re.compile(r"([^{}]*)\{([^{}]*)\}")


def strip_conditional_blocks(source: str, where: str) -> str:
    """Remove `@media`/`@supports` blocks, contents and all.

    Brace-matched rather than regexed, because these blocks nest: an at-rule
    contains whole rulesets, and `\\{[^}]*\\}` would stop at the first inner
    `}` and leave the tail of the at-rule looking like top-level CSS. That does
    not raise -- it silently promotes a conditional override into the default
    scope, which would make a `prefers-contrast` value look like a divergence.
    """
    out = []
    index = 0
    while True:
        found = _AT_RULE_RE.search(source, index)
        if not found:
            out.append(source[index:])
            return "".join(out)
        out.append(source[index : found.start()])
        open_brace = source.find("{", found.end())
        if open_brace == -1:
            raise CouldNotCheck(
                f"`{found.group(0)}` in {where} has no block -- the stylesheet "
                "does not parse, and a parser that gives up mid-file reports "
                "on whatever it managed to read"
            )
        depth = 0
        cursor = open_brace
        while cursor < len(source):
            if source[cursor] == "{":
                depth += 1
            elif source[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth != 0:
            raise CouldNotCheck(f"unbalanced braces after `{found.group(0)}` in {where}")
        if cursor + 1 <= index:
            # Unreachable while the `open_brace == -1` guard above stands, and
            # kept because mutation testing showed what happens when it does
            # not: `cursor` restarts from the same brace on every pass, `index`
            # never advances, and this loop spins forever on the same at-rule.
            # A gate that hangs is strictly worse than a gate that refuses --
            # CI reports a timeout with no diagnosis, and a timeout reads as
            # flake, so it gets retried rather than investigated. Termination
            # is therefore a property worth asserting rather than inferring.
            raise CouldNotCheck(
                f"parser made no progress at `{found.group(0)}` in {where}"
            )
        index = cursor + 1


def default_scope_declarations(source: str, where: str) -> dict:
    """Every custom property declared at the default scope, as written.

    Later declarations overwrite earlier ones, which is what the cascade does
    within a single file.
    """
    body = strip_conditional_blocks(_COMMENT_RE.sub("", source), where)
    found = {}
    for block in _BLOCK_RE.finditer(body):
        selector = " ".join(block.group(1).split())
        parts = [part.strip() for part in selector.split(",") if part.strip()]
        if not parts or not all(part in DEFAULT_SELECTORS for part in parts):
            continue
        for name, value in _DECL_RE.findall(block.group(2)):
            found[name] = " ".join(value.split())
    if not found:
        raise CouldNotCheck(
            f"no `:root` custom properties found in {where} -- a token file "
            "the gate reads as empty compares equal to anything"
        )
    return found


_VAR_RE = re.compile(r"^var\((--[A-Za-z0-9_-]+)\)$")
_CALC_RE = re.compile(r"^calc\(\s*var\((--[A-Za-z0-9_-]+)\)\s*\*\s*([0-9.]+)\s*\)$")
_PX_RE = re.compile(r"^(-?[0-9.]+)px$")


class Unresolved(Exception):
    """A declaration the resolver cannot reduce to a value."""


def resolve(name: str, table: dict, seen: frozenset = frozenset()) -> str:
    """One declaration reduced through `var()` and the one `calc()` shape used.

    `seen` makes a reference cycle raise rather than recurse: `--a: var(--b);
    --b: var(--a);` is a stylesheet that renders nothing, and a gate that hits
    the recursion limit there dies with a traceback instead of a verdict.
    """
    if name in seen:
        raise Unresolved(f"`{name}` is part of a var() cycle")
    if name not in table:
        raise Unresolved(f"`{name}` is not declared at the default scope")
    value = table[name]

    reference = _VAR_RE.match(value)
    if reference:
        return resolve(reference.group(1), table, seen | {name})

    scaled = _CALC_RE.match(value)
    if scaled:
        base = resolve(scaled.group(1), table, seen | {name})
        pixels = _PX_RE.match(base)
        if not pixels:
            raise Unresolved(f"`{name}` scales `{base}`, which is not a length")
        return f"{float(pixels.group(1)) * float(scaled.group(2)):g}px"

    if "var(" in value or "calc(" in value:
        raise Unresolved(f"`{name}` is `{value}`, which this resolver cannot reduce")
    return value


# ---------------------------------------------------------------------------
# Native side -- an independent reader, sharing no code with the CSS one
# ---------------------------------------------------------------------------


def native_palette(source: str) -> dict:
    """`colors.ts`'s object literal, as a key -> colour-string map."""
    block = re.search(r"export\s+const\s+colors\s*=\s*\{(.*?)\n\}", source, re.DOTALL)
    if not block:
        raise CouldNotCheck(
            f"no `export const colors = {{...}}` in {NATIVE_COLORS.name} -- the "
            "palette moved or was renamed, and the web copies are now checked "
            "against nothing"
        )
    found = dict(
        re.findall(r"([A-Za-z][A-Za-z0-9]*)\s*:\s*\"([^\"]+)\"", block.group(1))
    )
    if not found:
        raise CouldNotCheck(
            f"`colors` in {NATIVE_COLORS.name} parsed to no entries -- an empty "
            "expected side agrees with every possible actual side"
        )
    return found


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_RE = re.compile(r"^rgba?\(([^)]*)\)$")


def normalise(value: str):
    """A comparable form: colours as RGBA, lengths and bare numbers as floats.

    Anything else falls through to collapsed lowercase text, which is right for
    the values that are genuinely strings (font stacks, easing curves).
    """
    text = " ".join(str(value).split())

    hexed = _HEX_RE.match(text)
    if hexed:
        digits = hexed.group(1)
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits)
        channels = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
        if len(channels) == 3:
            channels.append(255)
        return ("colour", tuple(channels[:3]), round(channels[3] / 255, 4))

    rgb = _RGB_RE.match(text)
    if rgb:
        parts = [part.strip() for part in rgb.group(1).replace("/", ",").split(",")]
        parts = [part for part in parts if part]
        if len(parts) in (3, 4):
            try:
                channels = tuple(int(round(float(part))) for part in parts[:3])
                alpha = round(float(parts[3]), 4) if len(parts) == 4 else 1.0
                return ("colour", channels, alpha)
            except ValueError:
                pass

    pixels = _PX_RE.match(text)
    if pixels:
        return ("length", round(float(pixels.group(1)), 4))

    try:
        return ("number", round(float(text), 4))
    except ValueError:
        return ("text", text.lower())


def compare(native_source: str, legacy_source: str, client_source: str):
    """Every disagreement, plus how many values were actually compared."""
    problems = []
    checked = 0

    native = native_palette(native_source)
    legacy = default_scope_declarations(legacy_source, LEGACY_TOKENS.name)
    client = default_scope_declarations(client_source, CLIENT_TOKENS.name)

    # 1. The legacy stylesheet's primitives are the app's palette.
    for key, token in PALETTE.items():
        if key not in native:
            raise CouldNotCheck(
                f"`{key}` is gone from {NATIVE_COLORS.name} -- the stylesheet "
                f"still declares `{token}`, so the gate would be comparing a "
                "web colour against nothing"
            )
        try:
            declared = resolve(token, legacy)
        except Unresolved as exc:
            raise CouldNotCheck(
                f"{LEGACY_TOKENS.name}: {exc}. A primitive the gate cannot read "
                "is unchecked, not correct"
            ) from None
        checked += 1
        if normalise(declared) != normalise(native[key]):
            problems.append(
                f"{token}: native colors.{key} is {native[key]}, "
                f"{LEGACY_TOKENS.name} says {declared}"
            )

    # 2. Nothing is declared in both files with two different values.
    shared = sorted(set(legacy) & set(client))
    if not shared:
        raise CouldNotCheck(
            f"{LEGACY_TOKENS.name} and {CLIENT_TOKENS.name} share no custom "
            "property names at all -- either one of them was renamed wholesale "
            "or a parser stopped reading, and an empty overlap has no "
            "contradictions by definition"
        )
    for name in shared:
        try:
            left = resolve(name, legacy)
            right = resolve(name, client)
        except Unresolved as exc:
            raise CouldNotCheck(
                f"`{name}` is declared in both files but {exc} -- a name the "
                "gate cannot resolve on both sides is the one case where a "
                "collision would go unreported"
            ) from None
        checked += 1
        if normalise(left) != normalise(right):
            problems.append(
                f"{name} is declared twice with different values: "
                f"{LEGACY_TOKENS.name} says {left}, {CLIENT_TOKENS.name} says "
                f"{right} -- whichever stylesheet loads last wins, per page"
            )

    # 3. The two surfaces bound themselves at the same two widths.
    for legacy_name, client_name, meaning in MEASURES:
        try:
            left = resolve(legacy_name, legacy)
            right = resolve(client_name, client)
        except Unresolved as exc:
            raise CouldNotCheck(
                f"layout measure: {exc}. The marketing surface and the client "
                "are supposed to bound themselves at one width; the gate "
                "cannot confirm a width it cannot read"
            ) from None
        checked += 1
        if normalise(left) != normalise(right):
            problems.append(
                f"{meaning}: {LEGACY_TOKENS.name} says {legacy_name} is {left}, "
                f"{CLIENT_TOKENS.name} says {client_name} is {right}"
            )

    return problems, checked, len(shared)


def main() -> int:
    try:
        for path in (NATIVE_COLORS, LEGACY_TOKENS, CLIENT_TOKENS):
            if not path.is_file():
                raise CouldNotCheck(f"{path} does not exist")
        problems, checked, shared = compare(
            NATIVE_COLORS.read_text(encoding="utf-8"),
            LEGACY_TOKENS.read_text(encoding="utf-8"),
            CLIENT_TOKENS.read_text(encoding="utf-8"),
        )
    except CouldNotCheck as exc:
        print(f"web-token-authority: COULD NOT CHECK -- {exc}", file=sys.stderr)
        return EXIT_NO_DATA

    # Every palette entry, every shared name, and both measures. Anything less
    # means a loop above exited early and the gate is reporting on a comparison
    # it did not finish.
    expected = len(PALETTE) + shared + len(MEASURES)
    if checked < expected:
        print(
            f"web-token-authority: COULD NOT CHECK -- only {checked} of "
            f"{expected} values were compared",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    if problems:
        print("web-token-authority: DIVERGED", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_DIVERGED

    print(
        f"web-token-authority: verified {len(PALETTE)} palette values against "
        f"{NATIVE_COLORS.name}, {shared} shared token names across the two "
        f"stylesheets, and {len(MEASURES)} layout measures."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
