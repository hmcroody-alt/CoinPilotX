#!/usr/bin/env python3
"""Classify the near-black inventory into the Stage 0 buckets.

Reads the TSV from `blue_graphite_color_inventory.py` and assigns each literal
one bucket. The rules are ordered and the first match wins, so the exclusions
that must never be overridden are stated first: a literal inside a protected
real-time-audio path is reported as such before anything gets a chance to call
it a migratable surface.

Two sources of truth are read from the repo rather than restated here, because
a copy of either would drift the moment someone edits the original:

  * the protected-audio path list, from
    config/realtime-audio-protected-paths.json
  * the immersive / fixed-palette surfaces that must keep their own opaque
    fill, from the OPAQUE map in
    mobile-native/src/navigation/__tests__/backgroundSurfaces.test.ts

Anything no rule recognises is reported as `visual_review`, never dropped. The
count of that bucket is the honest measure of how much of this audit is still
a human judgement.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The brief's ten buckets, plus two the brief does not name but the repo
# demands. `protected_audio` is a hard exclusion that outranks every other
# classification; `test_pin` is a test asserting on a literal, which is not a
# surface but must be edited in the same commit as the surface it pins.
BUCKETS = [
    "structural_surface",
    "text_or_icon",
    "shadow",
    "dimming_scrim",
    "media_canvas",
    "user_content",
    "black_theme_requirement",
    "system_controlled",
    "dead_code",
    "visual_review",
    "protected_audio",
    "test_pin",
    "documentation",
]

TEXT_PROPS = {
    "color", "colour", "tintColor", "textColor", "placeholderTextColor",
    "iconColor", "fill", "stroke", "text", "-webkit-text-fill-color",
    "caret-color", "accent-color", "textDecorationColor",
}
SHADOW_PROPS = {
    "shadowColor", "textShadowColor", "box-shadow", "text-shadow", "shadow",
    "-webkit-box-shadow", "filter", "drop-shadow",
}
SCRIM_WORDS = ("scrim", "backdrop", "dim", "overlay", "veil", "shade", "curtain")
SURFACE_PROPS = {
    "backgroundColor", "background", "background-color", "background-image",
    "gradientStop", "gradient", "surface", "surfaceRaised", "surfaceRecessed",
    "glass", "glassStrong", "panel", "panelRaised", "panelStrong", "card",
    "page", "canvas", "base", "well", "sunken", "track", "strip", "skeleton",
}


def read_protected() -> set[str]:
    with open(os.path.join(REPO, "config", "realtime-audio-protected-paths.json")) as fh:
        manifest = json.load(fh)
    paths = {p for c in manifest.get("categories", []) for p in c.get("paths", [])}
    paths |= set(manifest.get("dependency_watch", {}).get("files", []))
    return paths


def read_opaque_exceptions() -> set[str]:
    """The OPAQUE map keys from backgroundSurfaces.test.ts, as src-relative paths.

    Parsed rather than copied. If someone adds a screen to that map, this audit
    starts honouring it on the next run with no edit here; if someone removes
    one, the literal reappears as a migration candidate, which is the correct
    signal and not a silent pass.
    """
    path = os.path.join(
        REPO, "mobile-native", "src", "navigation", "__tests__", "backgroundSurfaces.test.ts"
    )
    src = open(path, encoding="utf-8").read()
    block = re.search(r"const OPAQUE: Record<string, string> = \{(.*?)\n  \};", src, re.S)
    if not block:
        raise SystemExit(
            "could not find the OPAQUE map in backgroundSurfaces.test.ts -- it was "
            "renamed or reshaped, and this audit must not silently fall back to an "
            "empty exception list"
        )
    return {
        f"mobile-native/src/{m}"
        for m in re.findall(r'"([^"]+\.tsx?)"\s*:', block.group(1))
    }


def classify(row: dict, protected: set[str], opaque: set[str]) -> tuple[str, str]:
    """Return (bucket, reason). First matching rule wins; order is the policy."""
    path, prop, literal = row["path"], row["property"], row["literal"]
    alpha = float(row["alpha"])
    name = os.path.basename(path)

    if path in protected:
        return "protected_audio", (
            "protected real-time-audio path; the manifest names 'General UI' as a "
            "mission that must not edit these"
        )

    if "__tests__" in path or name.endswith((".test.ts", ".test.tsx")):
        return "test_pin", "a test pinning a literal, not a painted surface"

    # Prose, not pixels. This repo argues its colour decisions in doc comments,
    # so the files that reason hardest about near-black quote the most of it.
    # Counting those as surfaces would have inflated the migration with text.
    if prop == "comment":
        return "documentation", "quoted inside a comment; nothing paints it"

    if prop in SHADOW_PROPS:
        return "shadow", f"`{prop}` is a shadow; black is correct and stays"

    if prop in TEXT_PROPS:
        return "text_or_icon", f"`{prop}` paints a glyph, which needs the contrast"

    low = (prop + " " + literal).lower()
    if any(w in low for w in SCRIM_WORDS):
        return "dimming_scrim", "a dimming scrim over content; functional, stays black"
    # A translucent black fill with no other role is a scrim in effect even when
    # nothing in the name says so. The threshold is deliberately strict: alpha
    # at or above 0.8 is being used as a surface, not as a dimmer.
    if alpha < 0.8 and (row["r"], row["g"], row["b"]) == ("0", "0", "0"):
        return "dimming_scrim", f"pure black at alpha {alpha:.2f} acts as a dimmer"

    if path in opaque:
        return "media_canvas", (
            "immersive or fixed-palette surface already pinned by "
            "backgroundSurfaces.test.ts"
        )

    if name == "ThemeContext.tsx":
        return "black_theme_requirement", (
            "a theme palette definition; the user-selectable Black theme must keep "
            "its near-blacks"
        )

    if prop in SURFACE_PROPS:
        return "structural_surface", f"`{prop}` paints structural chrome"

    return "visual_review", f"property `{prop}` does not determine the role"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inventory")
    ap.add_argument("--out-csv")
    ap.add_argument("--bucket", help="print only rows in this bucket")
    args = ap.parse_args()

    protected = read_protected()
    opaque = read_opaque_exceptions()

    with open(args.inventory, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        rows = [dict(zip(header, line.rstrip("\n").split("\t"))) for line in fh if line.strip()]

    counts: Counter[str] = Counter()
    by_file: dict[str, Counter[str]] = defaultdict(Counter)
    out = []
    for row in rows:
        bucket, reason = classify(row, protected, opaque)
        counts[bucket] += 1
        by_file[row["path"]][bucket] += 1
        out.append({**row, "bucket": bucket, "reason": reason})

    if args.bucket:
        for r in out:
            if r["bucket"] == args.bucket:
                print(f"{r['path']}:{r['line']}\t{r['property']}\t{r['literal']}")
        return 0

    if args.out_csv:
        import csv
        with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        print(f"wrote {args.out_csv} ({len(out)} rows)")

    total = sum(counts.values())
    print(f"\n{total} near-black literals\n")
    for b in BUCKETS:
        if counts[b]:
            print(f"  {counts[b]:>5}  {b}")

    print("\nstructural_surface by file:")
    ranked = sorted(
        ((f, c["structural_surface"]) for f, c in by_file.items() if c["structural_surface"]),
        key=lambda kv: -kv[1],
    )
    for f, n in ranked[:45]:
        print(f"  {n:>4}  {f}")
    print(f"\n  ({len(ranked)} files carry at least one structural near-black)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
