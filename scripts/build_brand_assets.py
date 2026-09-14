#!/usr/bin/env python3
"""Derive every runtime PulseSoc logo from the four masters in assets/brand/.

The masters are RGBA. The pack art is emissive neon rendered over near-black,
which is mathematically premultiplied colour, so `--from-pack` recovers straight
alpha with alpha=max(r,g,b) under a soft knee that rejects the background
vignette. Without that step the art carries a baked-in dark rectangle and seams
against every surface it sits on.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTERS = os.path.join(ROOT, "assets", "brand")
RN = os.path.join(ROOT, "mobile-native", "src", "assets", "brand")
XCASSETS = os.path.join(ROOT, "mobile-native", "ios", "PulseSoc", "Images.xcassets")
ANDROID_RES = os.path.join(ROOT, "mobile-native", "android", "app", "src", "main", "res")
WEB = os.path.join(ROOT, "static", "brand")
STAMP = "20260913"

# Matches SplashScreenBackground.colorset and the icon tiles already shipped.
NAVY = (0, 7, 48)

# Source pack -> master, with the crop that isolates each lockup. The mark box
# comes from the horizontal file: its column profile separates mark from
# wordmark cleanly, and the leaf tapers inside the frame there.
PACK = {
    "pulsesoc-primary.png": ("PulseSoc_Neon_Health_Logo.png", (229, 241, 1005, 993)),
    "pulsesoc-horizontal.png": ("PulseSoc_Neon_Life_In_Sync_Horizontal.png", (219, 129, 1861, 629)),
    "pulsesoc-monochrome.png": ("PulseSoc_Monochrome_Dark_Life_In_Sync.png", (306, 137, 1136, 918)),
    "pulsesoc-mark.png": ("PulseSoc_Neon_Life_In_Sync_Horizontal.png", (219, 129, 892, 630)),
}


def alpha_key(path, lo=38.0, hi=88.0):
    rgb = np.array(Image.open(path).convert("RGB")).astype(np.float32)
    lum = rgb.max(axis=2)
    t = np.clip((lum - lo) / (hi - lo), 0, 1)
    alpha = lum * (t * t * (3 - 2 * t))
    straight = np.clip(rgb * (255.0 / np.maximum(lum, 1.0))[:, :, None], 0, 255)
    return Image.fromarray(np.dstack([straight, np.clip(alpha, 0, 255)]).astype(np.uint8), "RGBA")


def save(img, path, quantize=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if quantize:
        img = img.quantize(colors=255, method=Image.FASTOCTREE)
    img.save(path, optimize=True)
    print(f"  {os.path.relpath(path, ROOT):<78} {img.size[0]}x{img.size[1]} {os.path.getsize(path)/1024:.1f}KB")


def place(src, size, fill, bg=None):
    """Scale src so its longest side covers `fill` of the canvas, then centre it."""
    cw, ch = size
    sw, sh = src.size
    k = min(cw * fill / sw, ch * fill / sh)
    art = src.resize((max(1, round(sw * k)), max(1, round(sh * k))), Image.LANCZOS)
    canvas = Image.new("RGBA", size, (*bg, 255) if bg else (0, 0, 0, 0))
    canvas.alpha_composite(art, ((cw - art.size[0]) // 2, (ch - art.size[1]) // 2))
    return canvas.convert("RGB") if bg else canvas


def wide(src, width):
    return src.resize((width, max(1, round(width * src.size[1] / src.size[0]))), Image.LANCZOS)


def build_masters(pack_dir):
    print("masters (alpha-keyed from pack)")
    for name, (src, box) in PACK.items():
        path = os.path.join(pack_dir, src)
        if not os.path.exists(path):
            sys.exit(f"missing pack file: {path}")
        save(alpha_key(path).crop(box), os.path.join(MASTERS, name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-pack", metavar="DIR", help="re-key the masters from the raw logo pack")
    args = ap.parse_args()
    if args.from_pack:
        build_masters(os.path.expanduser(args.from_pack))

    m = {n: Image.open(os.path.join(MASTERS, n)).convert("RGBA") for n in PACK}
    mark, primary, horiz, mono = m["pulsesoc-mark.png"], m["pulsesoc-primary.png"], m["pulsesoc-horizontal.png"], m["pulsesoc-monochrome.png"]

    print("react native (transparent, composites on any dark surface)")
    save(wide(mark, 768), os.path.join(RN, "pulsesoc-mark.png"))
    save(wide(primary, 768), os.path.join(RN, "pulsesoc-primary.png"))
    save(wide(horiz, 1024), os.path.join(RN, "pulsesoc-horizontal.png"))
    save(wide(mono, 768), os.path.join(RN, "pulsesoc-monochrome.png"))

    print("ios native")
    # The iOS home-screen icon is gold, not navy, and is composed from a
    # different pack -- see scripts/build_gold_app_icon.py for why it cannot be
    # a crop. It is copied here rather than derived so that a run of this script
    # cannot quietly revert the shipped icon; the gold master is the source of
    # truth for both files. Everything else on this page stays navy.
    gold_icon = os.path.join(MASTERS, "pulsesoc-appicon-gold-1024.png")
    if not os.path.exists(gold_icon):
        sys.exit(f"missing gold app icon master: {gold_icon} (run scripts/build_gold_app_icon.py)")
    icon = Image.open(gold_icon).convert("RGB")
    save(icon, os.path.join(ROOT, "mobile-native", "assets", "icon.png"))
    save(icon, os.path.join(XCASSETS, "AppIcon.appiconset", "App-Icon-1024x1024@1x.png"))
    save(place(mark, (1024, 1024), 0.58), os.path.join(ROOT, "mobile-native", "assets", "adaptive-icon.png"))
    for px, suffix in ((200, ""), (400, "@2x"), (600, "@3x")):
        save(place(primary, (px, px), 0.94), os.path.join(XCASSETS, "SplashScreenLogo.imageset", f"image{suffix}.png"))

    print("android")
    for bucket, launcher, fg, splash in (
        ("mdpi", 48, 108, 288), ("hdpi", 72, 162, 432), ("xhdpi", 96, 216, 576),
        ("xxhdpi", 144, 324, 864), ("xxxhdpi", 192, 432, 1152),
    ):
        save(place(mark, (launcher, launcher), 0.80, NAVY), os.path.join(ANDROID_RES, f"mipmap-{bucket}", "ic_launcher.webp"))
        # Adaptive foregrounds are cropped to the inner 66/108 of the canvas.
        save(place(mark, (fg, fg), 0.58), os.path.join(ANDROID_RES, f"mipmap-{bucket}", "ic_launcher_foreground.webp"))
        save(place(primary, (splash, splash), 0.86), os.path.join(ANDROID_RES, f"drawable-{bucket}", "splashscreen_logo.png"), quantize=True)

    print("web")
    save(place(primary, (1024, 1024), 0.86, NAVY), os.path.join(WEB, f"pulsesoc-logo-{STAMP}.png"))
    save(place(mark, (512, 512), 0.86, NAVY), os.path.join(WEB, f"pulsesoc-mark-{STAMP}.png"))
    save(wide(horiz, 1024), os.path.join(WEB, f"pulsesoc-horizontal-{STAMP}.png"))
    save(place(mark, (180, 180), 0.84, NAVY), os.path.join(WEB, f"pulsesoc-apple-touch-icon-{STAMP}.png"))
    save(place(mark, (192, 192), 0.84, NAVY), os.path.join(WEB, f"pulsesoc-icon-192-{STAMP}.png"))
    save(place(mark, (512, 512), 0.84, NAVY), os.path.join(WEB, f"pulsesoc-icon-512-{STAMP}.png"))
    # Maskable: Android crops to an inner circle, so content must fit the safe 80%.
    save(place(mark, (512, 512), 0.60, NAVY), os.path.join(WEB, f"pulsesoc-icon-maskable-512-{STAMP}.png"))
    for px in (16, 32):
        save(place(mark, (px, px), 0.92, NAVY), os.path.join(WEB, f"pulsesoc-favicon-{px}-{STAMP}.png"))
    # Social cards crop to 1.91:1; the horizontal lockup is the only variant that
    # keeps the wordmark legible at that shape.
    save(place(horiz, (1200, 630), 0.70, NAVY), os.path.join(WEB, f"pulsesoc-og-{STAMP}.png"))
    save(place(mono, (360, 260), 0.92), os.path.join(WEB, f"pulsesoc-gateway-mark-{STAMP}.png"))
    for px in (16, 32):
        save(place(mark, (px, px), 0.92, NAVY), os.path.join(ROOT, "static", "img", f"favicon-{px}.png"))
    # Legacy unstamped PWA icons behind the public /icons/<file> route. Installed
    # home-screen apps still hold these exact URLs, so they are rewritten in place
    # rather than renamed -- a stamped filename would orphan them on the old art.
    ICONS = os.path.join(ROOT, "static", "icons")
    save(place(mark, (180, 180), 0.84, NAVY), os.path.join(ICONS, "apple-touch-icon.png"))
    save(place(mark, (192, 192), 0.84, NAVY), os.path.join(ICONS, "icon-192.png"))
    save(place(mark, (512, 512), 0.84, NAVY), os.path.join(ICONS, "icon-512.png"))


if __name__ == "__main__":
    main()
