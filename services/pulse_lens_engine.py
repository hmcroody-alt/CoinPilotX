"""PulseSoc Camera lens registry and lightweight AR-ready effect metadata."""

from __future__ import annotations

import json
from datetime import datetime


STARTER_LENSES = [
    {
        "key": "pulse_glow",
        "label": "PulseSoc Glow",
        "effect_type": "aura",
        "premium": False,
        "css_filter": "brightness(1.08) contrast(1.06) saturate(1.16)",
        "overlay": "radial-gradient(circle at 50% 38%, rgba(110,223,246,.18), transparent 34%)",
        "particles": "soft",
    },
    {
        "key": "cyber_visor",
        "label": "Cyber Visor",
        "effect_type": "face_overlay",
        "premium": False,
        "css_filter": "contrast(1.12) saturate(1.22) hue-rotate(12deg)",
        "overlay": "linear-gradient(90deg, transparent 30%, rgba(110,223,246,.28) 48%, transparent 66%)",
        "particles": "scanline",
    },
    {
        "key": "creator_crown",
        "label": "Creator Crown",
        "effect_type": "sticker",
        "premium": True,
        "css_filter": "brightness(1.08) contrast(1.04) saturate(1.1)",
        "overlay": "radial-gradient(circle at 50% 18%, rgba(255,209,102,.28), transparent 18%)",
        "particles": "gold",
    },
    {
        "key": "crypto_sparkle",
        "label": "Crypto Sparkle",
        "effect_type": "particles",
        "premium": False,
        "css_filter": "contrast(1.08) saturate(1.28)",
        "overlay": "radial-gradient(circle at 28% 25%, rgba(54,229,143,.22), transparent 14%), radial-gradient(circle at 72% 34%, rgba(110,223,246,.2), transparent 16%)",
        "particles": "sparkle",
    },
    {
        "key": "ai_aura",
        "label": "AI Aura",
        "effect_type": "segmentation_hook",
        "premium": True,
        "css_filter": "brightness(1.06) contrast(1.1) saturate(1.18)",
        "overlay": "conic-gradient(from 180deg at 50% 42%, rgba(110,223,246,.18), transparent, rgba(155,92,255,.18), transparent)",
        "particles": "aura",
    },
    {
        "key": "background_blur",
        "label": "Background Blur",
        "effect_type": "segmentation_hook",
        "premium": False,
        "css_filter": "brightness(1.05) contrast(1.04) saturate(1.04)",
        "overlay": "radial-gradient(circle at 50% 40%, transparent 25%, rgba(4,10,20,.18) 62%)",
        "particles": "none",
    },
    {
        "key": "neon_frame",
        "label": "Neon Frame",
        "effect_type": "frame",
        "premium": False,
        "css_filter": "contrast(1.1) saturate(1.2)",
        "overlay": "linear-gradient(135deg, rgba(54,229,143,.18), transparent 28%, transparent 70%, rgba(110,223,246,.18))",
        "particles": "edge",
    },
    {
        "key": "soft_studio_light",
        "label": "Soft Studio Light",
        "effect_type": "beauty_light",
        "premium": False,
        "css_filter": "brightness(1.12) contrast(.99) saturate(1.04)",
        "overlay": "radial-gradient(circle at 50% 20%, rgba(255,255,255,.2), transparent 42%)",
        "particles": "none",
    },
    {
        "key": "meme_face",
        "label": "Meme Face",
        "effect_type": "face_overlay",
        "premium": False,
        "css_filter": "contrast(1.14) saturate(1.3) brightness(1.04)",
        "overlay": "radial-gradient(circle at 50% 50%, transparent 34%, rgba(255,209,102,.12) 75%)",
        "particles": "pop",
    },
    {
        "key": "celebration_confetti",
        "label": "Celebration Confetti",
        "effect_type": "particles",
        "premium": False,
        "css_filter": "brightness(1.08) saturate(1.2)",
        "overlay": "radial-gradient(circle at 18% 18%, rgba(255,209,102,.24), transparent 11%), radial-gradient(circle at 82% 26%, rgba(255,107,122,.2), transparent 12%)",
        "particles": "confetti",
    },
]


BEAUTY_MODES = [
    {"key": "natural", "label": "Natural", "css_filter": "brightness(1.04) contrast(1.04) saturate(1.05)", "description": "Clean mirror preview with light tone balance."},
    {"key": "glow", "label": "Glow", "css_filter": "brightness(1.08) contrast(1.02) saturate(1.1)", "description": "Subtle skin-safe warmth and face light."},
    {"key": "smooth", "label": "Smooth", "css_filter": "brightness(1.07) contrast(.98) saturate(1.04)", "description": "Gentle smoothing without reshaping."},
    {"key": "bright", "label": "Bright", "css_filter": "brightness(1.16) contrast(1.02) saturate(1.05)", "description": "Low-light boost for dark rooms."},
    {"key": "cinematic", "label": "Cinematic", "css_filter": "brightness(1.03) contrast(1.14) saturate(1.12) sepia(.08)", "description": "Premium creator color balance."},
    {"key": "low_light", "label": "Low-light", "css_filter": "brightness(1.2) contrast(1.1) saturate(1.02)", "description": "Adaptive lift when lighting is weak."},
]


def lens_catalog(is_premium: bool = False) -> list[dict]:
    catalog = []
    for item in STARTER_LENSES:
        lens = dict(item)
        lens["locked"] = bool(lens.get("premium")) and not is_premium
        catalog.append(lens)
    return catalog


def beauty_catalog() -> list[dict]:
    return [dict(item) for item in BEAUTY_MODES]


def seed_lenses(cur) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    for lens in STARTER_LENSES:
        config = {
            "css_filter": lens.get("css_filter"),
            "overlay": lens.get("overlay"),
            "particles": lens.get("particles"),
            "premium": bool(lens.get("premium")),
            "tracking_hooks": ["face_landmarks", "hand_tracking", "segmentation"],
        }
        cur.execute(
            """
            INSERT OR IGNORE INTO pulse_camera_effects
            (effect_key, label, effect_type, config_json, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (
                lens["key"],
                lens["label"],
                lens.get("effect_type") or "overlay",
                json.dumps(config, separators=(",", ":")),
                now,
                now,
            ),
        )
