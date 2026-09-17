#!/usr/bin/env python3
"""Sample a screenshot's actual pixels against the blue-graphite tokens.

Visual verification by eye cannot tell #363D46 from #2C333B on a phone-sized
image, and the whole question this mission turns on is whether a surface is
graphite or near-black. So the check is numeric: read the rendered pixels, and
report the nearest token plus the distance to it.

Usage:
  blue_graphite_sample.py shot.png                 # grid overview
  blue_graphite_sample.py shot.png x,y [x,y ...]   # named probes
"""
from __future__ import annotations

import sys

from PIL import Image

TOKENS = {
    "cardCore": "#363D46",
    "cardEdge": "#243D5D",
    "cardDeep": "#263854",
    "navCore": "#303843",
    "navEdge": "#243B5A",
    "navDeep": "#1F314B",
    "spaceTop": "#02050A",
    "spaceMid": "#040A14",
    "spaceBot": "#06101C",
    "legacyDock": "#070E20",
}


def rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def nearest(px: tuple[int, int, int]) -> tuple[str, float]:
    best, bd = "", 1e9
    for name, h in TOKENS.items():
        t = rgb(h)
        d = sum((a - b) ** 2 for a, b in zip(px, t)) ** 0.5
        if d < bd:
            best, bd = name, d
    return best, bd


def show(im: Image.Image, x: int, y: int, label: str = "") -> None:
    px = im.convert("RGB").getpixel((x, y))
    name, d = nearest(px)
    hexv = "#%02X%02X%02X" % px
    # Blueness is the property the brief actually asked for -- "deep navy
    # entering subtly at edges" -- and it is invisible in a hex string.
    blueness = px[2] - px[0]
    print(
        f"  {label or f'{x},{y}':<14} {hexv}  rgb{px}  B-R={blueness:+4d}  "
        f"~{name} (d={d:.0f})"
    )


def main() -> int:
    im = Image.open(sys.argv[1])
    w, h = im.size
    print(f"{sys.argv[1]}  {w}x{h}")
    if len(sys.argv) > 2:
        for arg in sys.argv[2:]:
            xs, ys = arg.split(",")[:2]
            show(im, int(xs), int(ys))
        return 0
    for fy in (0.06, 0.18, 0.26, 0.34, 0.5, 0.66, 0.8, 0.9, 0.96):
        for fx in (0.08, 0.3, 0.5, 0.7, 0.92):
            show(im, int(w * fx), int(h * fy), f"{fx:.2f},{fy:.2f}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
