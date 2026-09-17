#!/usr/bin/env python3
"""Inventory every near-black colour literal so Stage 0 can classify them.

This exists because the obvious command -- grep for "#000" -- answers the wrong
question twice over. It misses the forms the codebase actually uses most
(`rgba(3, 9, 18, 0.96)`, `#02050b`), and it floods the result with text and
icon colours, which are the one category that must stay black. So the script
parses each literal into RGB, keeps only what is genuinely near-black by
luminance, and records the CSS/RN property the literal was assigned to -- which
is what makes the difference between a surface and a glyph mechanically
visible.

Output is TSV on stdout: path, line, property, literal, r, g, b, alpha, luma.
Classification is a human judgement and is NOT attempted here; the property
column is the evidence for it, not a substitute.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# Perceptual-ish luminance is deliberate rather than WCAG relative luminance.
# The question here is "would a person call this black?", not "what does it
# contrast against". sRGB linearisation would push every dark value toward 0 and
# flatten the very distinctions being triaged (#02050b vs #363D46).
LUMA_CEILING = 0.16

HEX = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")
# The property a literal is assigned to, reading leftward from the literal.
# The trailing quote class is the whole reason this works on React Native: a
# StyleSheet writes `backgroundColor: "#02050b"`, and a pattern that stops at
# the colon reports no property for every RN surface in the repo -- which is
# most of them.
#
# Any identifier is accepted, not a whitelist of colour-ish names. A whitelist
# looked tighter and was worse: the semantic role is the classification
# evidence, and the roles this migration cares about most are called `surface`,
# `surfaceRaised`, `glass` and `scrim` -- none of which contain "color".
PROP = re.compile(r"([A-Za-z_$][\w$-]*|--[A-Za-z0-9-]+)\s*:\s*[\"'`]?\s*$")
# A gradient ramp is a surface even though no property precedes the stop:
# `colors={["#02050b", "#040A14"]}`. Recognising the array is what keeps the
# app's largest painted areas from being filed as "unknown".
RAMP = re.compile(r"(colors|colours)\s*[:=]\s*[\[{]")
# The nearest enclosing declaration, used when the literal is not adjacent to
# its property. Both CSS shapes that matter put values in between:
# `box-shadow: 0 40px 120px rgba(0,0,0,.6)` and
# `--bg: var(--surface-primary, #050b14)`. Taking the *last* match before the
# literal picks the innermost declaration, so a one-line RN style object with
# several keys still attributes each literal to its own key.
DECL = re.compile(r"(--[A-Za-z][\w-]*|[A-Za-z_$][\w$-]*)\s*:")

SKIP_DIRS = {
    "node_modules", ".git", "Pods", "build", "dist", "__pycache__",
    "DerivedData", ".expo", "coverage", "ios/build",
}
EXTS = {".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".scss", ".swift", ".m", ".mm"}


def expand_hex(raw: str) -> tuple[int, int, int, float] | None:
    h = raw.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        h += "ff"
    if len(h) != 8:
        return None
    try:
        r, g, b, a = (int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
    except ValueError:
        return None
    return r, g, b, a / 255


def luma(r: int, g: int, b: int) -> float:
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def file_state(lines: list[str], css_like: bool) -> list[tuple[bytearray, str]]:
    """Per line: which columns are inside a comment, and the declaration in effect.

    Both facts need the whole file, not one line. A CSS declaration routinely
    spans lines -- `background:\\n  linear-gradient(150deg,\\n    #070b14,\\n
    #0b1424);` -- and only the first stop sits on the same line as the property,
    so a line-local parser reports every later stop as role-unknown. That was
    345 of 547 unclassified rows, and they are gradient stops on page and panel
    backgrounds: the audit's largest surfaces landing in its vaguest bucket.

    Comments need the same treatment for the opposite reason. This repo explains
    its colour decisions in prose, so `blueGraphite.ts` and `profileGraphite.ts`
    quote near-blacks in doc comments. Those are not painted anything, and
    counting them inflates the migration's size with text that has no pixels.
    """
    state: list[tuple[bytearray, str]] = []
    in_block = in_html = in_str = False
    quote = ""
    decl = ""
    ident = ""
    for line in lines:
        mask = bytearray(len(line))
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if in_block or in_html:
                close, width = ("*/", 2) if in_block else ("-->", 3)
                mask[i] = 1
                if line.startswith(close, i):
                    for k in range(i, min(i + width, n)):
                        mask[k] = 1
                    i += width
                    in_block = in_html = False
                    continue
                i += 1
                continue
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    in_str = False
                i += 1
                continue
            if line.startswith("/*", i):
                in_block = True
                continue
            if line.startswith("<!--", i):
                in_html = True
                continue
            # `//` is a comment everywhere except CSS, and except when it is the
            # scheme separator in a bare `url(http://...)`, which CSS does allow
            # unquoted.
            if not css_like and line.startswith("//", i) and (i == 0 or line[i - 1] != ":"):
                for k in range(i, n):
                    mask[k] = 1
                break
            if ch in "\"'`":
                in_str, quote = True, ch
                i += 1
                continue
            if ch.isalnum() or ch in "_$-":
                ident += ch
            else:
                if ch == ":" and ident:
                    decl = ident
                elif ch in ";{}":
                    decl = ""
                ident = ""
            i += 1
        ident = ""
        state.append((mask, decl))
    return state


def property_before(line: str, start: int, carried: str = "") -> str:
    head = line[:start]
    m = PROP.search(head)
    if m:
        return m.group(1)
    if RAMP.search(head):
        return "gradientStop"
    decls = DECL.findall(head)
    if decls:
        return decls[-1]
    if carried:
        return carried
    # Inside an unclosed bracket with no property in front of it: a bare ramp
    # declared as a module constant, `const SPACE = ["#02050A", "#06101C"]`.
    # These are some of the largest painted areas in the app and reporting them
    # as role-unknown would have left the audit's biggest gap exactly where its
    # biggest surfaces are.
    if head.count("[") > head.count("]"):
        return "arrayStop"
    return "-"


def scan_line(line: str):
    for m in HEX.finditer(line):
        parsed = expand_hex(m.group(0))
        if parsed:
            r, g, b, a = parsed
            yield m.start(), m.group(0), r, g, b, a
    for m in RGBA.finditer(line):
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) else 1.0
        yield m.start(), m.group(0), r, g, b, a


def walk(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1] in EXTS:
                yield os.path.join(dirpath, name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--ceiling", type=float, default=LUMA_CEILING)
    args = ap.parse_args()

    print("path\tline\tproperty\tliteral\tr\tg\tb\talpha\tluma")
    for root in args.roots:
        for path in sorted(walk(root)):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            css_like = os.path.splitext(path)[1] in {".css", ".scss", ".html"}
            state = file_state(lines, css_like)
            for n, line in enumerate(lines, 1):
                mask, _ = state[n - 1]
                # The declaration in effect *entering* this line. Taking the
                # previous line's carry rather than this one's is deliberate:
                # this line's value is already post-`;`, so a literal on the
                # last line of a declaration would be attributed to nothing.
                carried = state[n - 2][1] if n >= 2 else ""
                for start, literal, r, g, b, a in scan_line(line):
                    lum = luma(r, g, b)
                    if lum > args.ceiling:
                        continue
                    rel = os.path.relpath(path)
                    if start < len(mask) and mask[start]:
                        prop = "comment"
                    else:
                        prop = property_before(line, start, carried)
                    print(f"{rel}\t{n}\t{prop}\t{literal}\t{r}\t{g}\t{b}\t{a:.3f}\t{lum:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
