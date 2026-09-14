#!/usr/bin/env python3
"""Compose the gold PulseSoc iOS app icon from the Gold/White logo pack.

The pack art is a full lockup: the neon "P" mark over a brushed-gold burst,
with a "PulseSoc" wordmark and a "LIFE IN SYNC" tagline beneath it. The tagline
is illegible below about 120px and the wordmark is barely better, so the phone
icon keeps the gold field and the mark and drops both lines of type.

That cannot be done with a crop alone. In the source the mark's centre sits at
y=518 while the clean gold under it runs out at y=786 -- 18px of clearance --
so a square crop that both centres the mark and excludes the wordmark would
have to be smaller than the mark itself. Roughly 130 rows of gold have to be
synthesised below the mark no matter how the crop is framed.

Two approaches were tried and rejected. Pasting clean gold from below the
tagline leaves a visible shelf: the per-column brightness can be matched across
the join but the brushed rays do not line up, so the texture breaks. Mirroring
the left half over the right fails outright -- the wordmark is centred, not
right-aligned, so the mirror reproduces it backwards.

What works is continuing the last clean row downward under the vignette
measured from a column strip that is gold at every row. That is continuous at
the join by construction, so there is no texture break to hide, and grain
re-injected from the gold below the tagline keeps the band from reading flat.

Writes an RGB master with no alpha channel: iOS rejects an app icon that has
one, and the asset catalog carries a single 1024 entry, so Xcode derives every
home screen, Spotlight, Settings and notification rendition from this file.

    python3 scripts/build_gold_app_icon.py --from-pack ~/Desktop/PulseSoc_Gold_White_Logo_Pack
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(ROOT, "assets", "brand", "pulsesoc-appicon-gold-1024.png")
RN_ICON = os.path.join(ROOT, "mobile-native", "assets", "icon.png")
XC_ICON = os.path.join(
    ROOT, "mobile-native", "ios", "PulseSoc", "Images.xcassets",
    "AppIcon.appiconset", "App-Icon-1024x1024@1x.png",
)

PACK_ART = os.path.join(
    "PulseSoc_Gold_White_Logo_Pack", "PulseSoc_Gold_Life_In_Sync",
    "PulseSoc_Gold_Life_In_Sync_Master_1254.png",
)

# Measured on the 1254px master by isolating saturated non-gold hues.
MARK_BOX = (343, 268, 915, 768)   # the neon "P", glow excluded
WORDMARK_TOP = 790                # first row of "PulseSoc"
SEAM = 786                        # last row of clean gold above it
GOLD_STRIP = (40, 200)            # columns that are gold at every row
CLEAN_GOLD_TOP = 1000             # first row below the tagline
FILL = 0.72                       # mark width as a fraction of the icon


def compose(pack_dir):
    path = os.path.join(pack_dir, PACK_ART)
    if not os.path.exists(path):
        sys.exit(f"missing pack file: {path}")
    m = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)

    mx0, my0, mx1, my1 = MARK_BOX
    side = int(round((mx1 - mx0) / FILL))
    cx, cy = (mx0 + mx1) // 2, (my0 + my1) // 2
    x0, y0 = cx - side // 2, cy - side // 2
    if x0 < 0 or y0 < 0 or x0 + side > m.shape[1]:
        sys.exit(f"crop {side}px at ({x0},{y0}) falls outside the {m.shape[1]}px master")
    crop = m[y0:y0 + side, x0:x0 + side].copy()

    start = SEAM - y0
    n = side - start
    if n <= 0:
        sys.exit("crop ends above the wordmark; no fill needed -- check MARK_BOX")

    # Vertical falloff, sampled where the art is gold on every row.
    profile = m[:, GOLD_STRIP[0]:GOLD_STRIP[1], :].mean(axis=1)
    ratio = profile[SEAM:SEAM + n] / np.maximum(profile[SEAM], 1.0)
    base = crop[start - 1][None, :, :] * ratio[:, None, :]

    # High-pass of real gold, faded in so the seam row stays exact.
    texture = m[CLEAN_GOLD_TOP:CLEAN_GOLD_TOP + n, x0:x0 + side]
    blurred = np.asarray(
        Image.fromarray(texture.astype(np.uint8)).filter(ImageFilter.GaussianBlur(9))
    ).astype(np.float32)
    grain = (texture - blurred) * np.linspace(0, 1, n)[:, None, None]

    crop[start:] = base + grain
    return Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8), "RGB").resize(
        (1024, 1024), Image.LANCZOS
    )


def save(img, path):
    if img.mode != "RGB":
        sys.exit(f"refusing to write {path}: mode is {img.mode}, iOS app icons must not carry alpha")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, optimize=True)
    print(f"  {os.path.relpath(path, ROOT):<72} {img.size[0]}x{img.size[1]} {os.path.getsize(path)/1024:.1f}KB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-pack", metavar="DIR", help="recompose the master from the gold logo pack")
    args = ap.parse_args()

    if args.from_pack:
        print("master (composed from pack)")
        save(compose(os.path.expanduser(args.from_pack)), MASTER)
    elif not os.path.exists(MASTER):
        sys.exit(f"no master at {MASTER}; pass --from-pack to build one")

    icon = Image.open(MASTER).convert("RGB")
    print("ios app icon")
    save(icon, RN_ICON)   # app.json's expo icon, the source prebuild regenerates from
    save(icon, XC_ICON)   # the checked-in catalog entry Xcode builds against


if __name__ == "__main__":
    main()
