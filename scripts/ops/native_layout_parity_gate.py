#!/usr/bin/env python3
"""Assert the web shell's geometry still equals the native app's wide canvas.

`web/src/styles/tokens.css` carries five numbers copied out of
`mobile-native/src/screens/HomeScreen.tsx`: the shell's max width and padding,
the gap between columns, and the two rail widths. They decide how wide the feed
is on every desktop viewport, and they diverge in the quietest way available --
someone widens a rail in the app to fit a longer label, the website keeps the
old width, and the two products are subtly different shapes forever. Nothing
fails, because nothing on either side knows the other number exists.

A sixth value, `--feed-max`, is not copied but *derived*, and the derivation is
the thing most likely to rot:

    feed = shell-max - 2*shell-pad - rail-command - rail-side - 2*rail-gap

Bump a rail without re-deriving it and the recorded feed width becomes a claim
about a layout that no longer exists. So the gate recomputes it rather than
comparing it to anything.

Why this side is parsed and not the other way around
----------------------------------------------------
Native is the source of truth, so native's numbers are read out of the screen's
`StyleSheet.create` block and the CSS is checked against them. That direction
matters: a gate that read the CSS and asserted the app agreed would be a gate
that lets the website define the product.

The usual hazard applies and is the reason for the shape of this file. Every
way the reader can fail -- a renamed style key, a reformatted StyleSheet, a
regex that stops matching -- produces *no* values to compare rather than wrong
ones, and a comparison over no values passes. So each value must be found on
both sides or the run is could-not-check, and a run that verified fewer than
every expected key is could-not-check too.

Exit codes:
  0  verified -- the web shell matches the native canvas
  1  verified-broken -- a real divergence, named
  3  could-not-check -- the gate could not read one of the sides
"""

from __future__ import annotations

import pathlib
import re
import sys

EXIT_OK = 0
EXIT_DIVERGED = 1
EXIT_NO_DATA = 3

REPO = pathlib.Path(__file__).resolve().parents[2]
NATIVE_HOME = REPO / "mobile-native" / "src" / "screens" / "HomeScreen.tsx"
WEB_TOKENS = REPO / "web" / "src" / "styles" / "tokens.css"
WEB_LAYOUT = REPO / "web" / "src" / "styles" / "layout.css"

#: (css custom property, native style key, native property).
#:
#: Each row is one number that exists twice. The native side is addressed by
#: style key rather than by line, so moving a block in the screen file does not
#: break the gate -- only renaming or deleting it does, and both of those are
#: exactly the events that should stop the build.
COPIED = [
    ("--shell-max", "content", "maxWidth"),
    ("--shell-pad", "content", "padding"),
    ("--rail-gap", "homeCanvasWide", "gap"),
    ("--rail-command", "commandRail", "width"),
    ("--rail-side", "sideRail", "width"),
]

#: The width at which native switches to its multi-column canvas, and the width
#: the web shell brings the command rail in at. Web honours native's number
#: rather than picking its own; this is what says so.
WIDE_CANVAS_RULE = re.compile(r"const\s+wideCanvas\s*=\s*width\s*>=\s*(\d+)\s*;")


class CouldNotCheck(Exception):
    """The gate could not read what it needs. Never the same as 'no problems'."""


def native_style_value(source: str, key: str, prop: str) -> int:
    """One numeric property of one `StyleSheet.create` entry.

    Scoped to the named block rather than searched for globally: `width: 226`
    appears in a screen file with hundreds of style objects, and a global search
    would happily return some other component's width and call it agreement.
    """
    block = re.search(
        r"^  " + re.escape(key) + r":\s*\{(.*?)^  \}",
        source,
        re.DOTALL | re.MULTILINE,
    )
    if not block:
        raise CouldNotCheck(
            f"no `{key}` style block in {NATIVE_HOME.name} -- it was renamed or "
            "removed, and the web shell is now copying a layout that is gone"
        )

    found = re.search(r"\b" + re.escape(prop) + r":\s*(-?\d+(?:\.\d+)?)\b", block.group(1))
    if not found:
        raise CouldNotCheck(
            f"`{key}` has no numeric `{prop}` in {NATIVE_HOME.name} -- if it "
            "became a computed value this gate cannot read it, and silently "
            "skipping it would be the failure it exists to prevent"
        )
    return _as_number(found.group(1))


def css_custom_property(source: str, name: str) -> int:
    """One `--token: <number>px;` declaration, as a number.

    Only the first declaration is read. A second one later in the cascade would
    win at runtime while this gate went on checking the first, so finding two is
    could-not-check rather than a guess about which the browser prefers.
    """
    found = re.findall(
        r"^\s*" + re.escape(name) + r":\s*(-?\d+(?:\.\d+)?)px\s*;",
        source,
        re.MULTILINE,
    )
    if not found:
        raise CouldNotCheck(
            f"no `{name}: <number>px;` declaration in {WEB_TOKENS.name} -- a "
            "token that is absent, or written as a calc/var the gate cannot "
            "evaluate, is unchecked rather than correct"
        )
    if len(found) > 1:
        raise CouldNotCheck(
            f"`{name}` is declared {len(found)} times in {WEB_TOKENS.name} -- "
            "the last one wins at runtime, so the gate cannot say which value "
            "the browser actually uses"
        )
    return _as_number(found[0])


def _as_number(text: str):
    """Ints stay ints so messages read `226` rather than `226.0`."""
    value = float(text)
    return int(value) if value.is_integer() else value


def media_query_widths(source: str) -> set:
    """Every `min-width` the stylesheet changes the grid at."""
    return {_as_number(m) for m in re.findall(r"min-width:\s*(\d+)px", source)}


def derive_feed_max(values: dict) -> int:
    """The feed width the shell arrives at once it has capped."""
    return (
        values["--shell-max"]
        - 2 * values["--shell-pad"]
        - values["--rail-command"]
        - values["--rail-side"]
        - 2 * values["--rail-gap"]
    )


def compare(native_source: str, css_source: str, layout_source: str):
    """Every disagreement, plus how many values were actually checked."""
    problems = []
    checked = 0
    values = {}

    for token, key, prop in COPIED:
        native = native_style_value(native_source, key, prop)
        web = css_custom_property(css_source, token)
        values[token] = web
        checked += 1
        if native != web:
            problems.append(
                f"{token}: native {key}.{prop} is {native}, CSS says {web}"
            )

    # Derived, not copied -- so it is recomputed from the CSS's own numbers
    # rather than compared against native. If the rails and the shell agree with
    # native and the arithmetic holds, the feed width is right by construction.
    declared = css_custom_property(css_source, "--feed-max")
    expected = derive_feed_max(values)
    checked += 1
    if declared != expected:
        problems.append(
            f"--feed-max: declared {declared}, but the shell leaves {expected} "
            f"({values['--shell-max']} - 2x{values['--shell-pad']} - "
            f"{values['--rail-command']} - {values['--rail-side']} - "
            f"2x{values['--rail-gap']})"
        )

    # There is deliberately no separate "do the three columns fit inside the
    # shell" check here. It reads like a second, independent guarantee and is
    # not one: substitute the derivation above and the condition reduces to
    # `-2*pad - cmd - gap > 0`, which no non-negative geometry satisfies. It
    # could only ever fire alongside the derivation failure that caused it. A
    # check that can never be the only thing to go red is not protection, it is
    # a line that makes the count look better.

    native_wide = WIDE_CANVAS_RULE.search(native_source)
    if not native_wide:
        raise CouldNotCheck(
            f"no `const wideCanvas = width >= N;` in {NATIVE_HOME.name} -- the "
            "web shell's first breakpoint is native's number, and the gate "
            "cannot confirm it against a rule it can no longer find"
        )
    threshold = _as_number(native_wide.group(1))

    widths = media_query_widths(layout_source)
    if not widths:
        raise CouldNotCheck(
            f"no `min-width` media queries in {WEB_LAYOUT.name} -- a stylesheet "
            "with no breakpoints is not a responsive grid, and comparing an "
            "empty set to anything would pass"
        )
    checked += 1
    if threshold not in widths:
        problems.append(
            f"native goes wide at {threshold}px but the shell has no "
            f"`min-width: {threshold}px` breakpoint (it has {sorted(widths)})"
        )

    # The shell caps and the side rail arrives at the same width on purpose:
    # that width is where all three columns first fit at their real sizes.
    checked += 1
    if values["--shell-max"] not in widths:
        problems.append(
            f"the shell caps at {values['--shell-max']}px but nothing happens "
            f"there -- the side rail is expected at that width, not before "
            f"(breakpoints are {sorted(widths)})"
        )

    return problems, checked


def main() -> int:
    try:
        for path in (NATIVE_HOME, WEB_TOKENS, WEB_LAYOUT):
            if not path.exists():
                raise CouldNotCheck(f"{path} does not exist")
        problems, checked = compare(
            NATIVE_HOME.read_text(encoding="utf-8"),
            WEB_TOKENS.read_text(encoding="utf-8"),
            WEB_LAYOUT.read_text(encoding="utf-8"),
        )
    except CouldNotCheck as exc:
        print(f"native-layout-parity: COULD NOT CHECK -- {exc}", file=sys.stderr)
        return EXIT_NO_DATA

    # Every row of COPIED, plus the derivation and the two breakpoint checks.
    # Anything less means the loop above exited early and the gate is reporting
    # on a comparison it did not finish.
    expected_checks = len(COPIED) + 3
    if checked < expected_checks:
        print(
            f"native-layout-parity: COULD NOT CHECK -- only {checked} of "
            f"{expected_checks} values were compared",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    if problems:
        print("native-layout-parity: DIVERGED", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_DIVERGED

    print(
        f"native-layout-parity: verified {checked} geometry values against "
        f"{NATIVE_HOME.name}'s wide canvas."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
