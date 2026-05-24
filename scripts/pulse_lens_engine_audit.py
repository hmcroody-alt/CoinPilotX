#!/usr/bin/env python3
"""Audit Pulse Lens Engine registry, seeded DB effects, and AR-ready hooks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import pulse_lens_engine  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    lenses = pulse_lens_engine.lens_catalog(is_premium=True)
    keys = {lens["key"] for lens in lenses}
    for key in [
        "pulse_glow",
        "cyber_visor",
        "creator_crown",
        "crypto_sparkle",
        "ai_aura",
        "background_blur",
        "neon_frame",
        "soft_studio_light",
        "meme_face",
        "celebration_confetti",
    ]:
        expect(key in keys, f"starter lens exists: {key}")
    expect(len(pulse_lens_engine.beauty_catalog()) >= 6, "beauty modes include natural/glow/smooth/bright/cinematic/low-light")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT effect_key, config_json FROM pulse_camera_effects WHERE active=1")
    rows = cur.fetchall()
    conn.close()
    seeded = {row[0]: json.loads(row[1] or "{}") for row in rows}
    expect(keys.issubset(set(seeded.keys())), "starter lenses are seeded in pulse_camera_effects")
    sample = seeded.get("pulse_glow") or {}
    for hook in ["face_landmarks", "hand_tracking", "segmentation"]:
        expect(hook in sample.get("tracking_hooks", []), f"AR tracking hook exists: {hook}")
    print("pulse lens engine audit ok")


if __name__ == "__main__":
    main()
