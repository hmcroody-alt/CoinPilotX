"""Which foregrounds did lifting the surfaces break?

Lifting a surface can only *lower* contrast for a light foreground. So the set
worth knowing is not "what fails" — plenty failed before and is out of scope —
but "what passed on the near-blacks and fails on graphite". That set is the
damage this migration does, and it is the only set it owes a fix for.

Scope is the four values that moved: `surface`, `surfaceRaised`, and the two
glass tokens that are those same two colours at an alpha. Everything drawn on
`background` is untouched and is not considered.

Candidate foregrounds are the hex literals in `theme/`, because the accent
tables there (`progressTheme.violet`, `premiumTheme.gold`, …) are the colours
that get spent as label text on panels.

Two filters keep the output readable, and both can hide a real defect, so they
are stated rather than buried:

  Comments are stripped. Without this the file documenting a colour move
  reports the colour it moved away from — `colors.ts` says "Was `#ff5f7e`" and
  scores a regression for a value it no longer holds.

  A file is only considered if it references one of the moved tokens. The
  `*Light.ts` families paint on their own white surfaces and their greens and
  ambers never touch graphite; including them buries the handful of real hits
  under thirty that cannot happen. The cost is that a screen importing a colour
  from a light theme and drawing it on a dark panel is invisible here — that
  crossing is a screen-level question, and Stage 3 is where it gets asked.
"""
from __future__ import annotations

import re
from pathlib import Path

THEME = Path(__file__).resolve().parents[2] / "mobile-native" / "src" / "theme"

OLD = {"surface": "#0b141c", "surfaceRaised": "#111f2a"}
NEW = {"surface": "#303843", "surfaceRaised": "#363D46"}

HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
BINDS_MOVED_TOKEN = re.compile(r"colors\.(surface|surfaceRaised|glass|glassStrong)\b")

# Crossings that were read and cleared, each with the reason it is not damage.
# A crossing not listed here is a new one and fails the run. Clearing an entry
# means checking the claim again, not deleting the line.
CLEARED = {
    "#0f8f6c": "presenceTheme.tealDeep — gradient tail and pressed fill, never text.",
    "#1f8f6a": "moneyTheme.greenMuted — no consumer, and moneyTheme panels are its own bg.card.",
    "#9da3a3": "logiNexus businessLive.textMuted — the black/white/green locked business "
               "palette, drawn on its own #050A08 base. Its four colours are pinned at "
               "matched luminance by that lock; lifting one would break the lock's invariant.",
    "#b4801f": "premiumTheme.planGradient / progressTheme.badgeGradient tails. The body-copy "
               "token that shared this hex was lifted to #D99B25; these are fills and stay.",
    "#ff7a6b": "moneyTheme.tone.error — MoneyChip renders it on moneyTheme's own bg.card "
               "(#101C28), not on the moved surfaces. REVISIT when Stage 3 migrates the "
               "money near-blacks to graphite: at that point this becomes a real crossing.",
}


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    total = 0.0
    for weight, index in ((0.2126, 0), (0.7152, 2), (0.0722, 4)):
        raw = int(value[index : index + 2], 16) / 255
        total += weight * (raw / 12.92 if raw <= 0.03928 else ((raw + 0.055) / 1.055) ** 2.4)
    return total


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def main() -> int:
    found: dict[str, set[str]] = {}
    for path in sorted(THEME.rglob("*.ts")) + sorted(THEME.rglob("*.tsx")):
        if "__tests__" in path.parts:
            continue
        source = COMMENT.sub("", path.read_text())
        if not BINDS_MOVED_TOKEN.search(source):
            continue
        for match in HEX.findall(source):
            found.setdefault(match.lower(), set()).add(path.name)

    new_crossings, cleared_seen = [], set()
    for color, files in sorted(found.items()):
        for level in ("surface", "surfaceRaised"):
            if not contrast(color, OLD[level]) >= 4.5 > contrast(color, NEW[level]):
                continue
            row = (color, level, contrast(color, OLD[level]), contrast(color, NEW[level]), sorted(files))
            (cleared_seen.add(color) if color in CLEARED else new_crossings.append(row))

    stale = sorted(set(CLEARED) - cleared_seen)
    if stale:
        print(f"{len(stale)} cleared entries no longer cross — delete them:\n")
        for color in stale:
            print(f"  {color}  {CLEARED[color]}")
        return 1

    if new_crossings:
        print(f"{len(new_crossings)} foreground/surface pairs newly cross 4.5:1 pass -> fail:\n")
        for color, level, before, after, files in new_crossings:
            print(f"  {color}  on {level:<13} {before:.2f} -> {after:.2f}   {', '.join(files)}")
        print("\nRead each one, then either lift it or add it to CLEARED with the reason.")
        return 1

    print(f"no new crossings; {len(cleared_seen)} cleared colours still accounted for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
