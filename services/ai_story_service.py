"""PulseSoc AI Story generation facade.

This module creates deterministic story drafts locally and exposes a clean
provider seam for image/video generation backends when production keys exist.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime


STYLE_PRESETS = {
    "cinematic": {
        "background": "radial-gradient(circle at 28% 18%, rgba(110,223,246,.46), transparent 24%), linear-gradient(145deg,#061426,#02050b)",
        "filter": "contrast(1.1) saturate(1.12)",
    },
    "cyberpunk": {
        "background": "radial-gradient(circle at 20% 12%, rgba(155,92,255,.55), transparent 24%), radial-gradient(circle at 76% 30%, rgba(54,229,143,.34), transparent 20%), linear-gradient(150deg,#090318,#03121f)",
        "filter": "contrast(1.18) saturate(1.24)",
    },
    "warm": {
        "background": "radial-gradient(circle at 30% 18%, rgba(255,209,102,.45), transparent 24%), linear-gradient(145deg,#211206,#071321)",
        "filter": "sepia(.13) saturate(1.08)",
    },
}


def provider_status() -> dict:
    provider = os.getenv("AI_STORY_PROVIDER", "local_draft").strip().lower() or "local_draft"
    return {
        "ok": True,
        "provider": provider,
        "image_generation_configured": bool(os.getenv("AI_STORY_IMAGE_ENDPOINT") or os.getenv("OPENAI_API_KEY")),
        "video_generation_configured": bool(os.getenv("AI_STORY_VIDEO_ENDPOINT")),
        "fallback": "local cinematic draft renderer",
    }


def _style_for_prompt(prompt: str, requested_style: str = "") -> str:
    text = f"{requested_style} {prompt}".lower()
    if "cyber" in text or "neon" in text:
        return "cyberpunk"
    if "gold" in text or "sunset" in text or "warm" in text:
        return "warm"
    return requested_style if requested_style in STYLE_PRESETS else "cinematic"


def generate_story(prompt: str, style: str = "", duration_seconds: int = 12) -> dict:
    clean_prompt = (prompt or "Create a PulseSoc AI story").strip()[:500]
    selected_style = _style_for_prompt(clean_prompt, style)
    digest = hashlib.sha256(f"{clean_prompt}:{selected_style}".encode("utf-8")).hexdigest()[:16]
    preset = STYLE_PRESETS[selected_style]
    caption = clean_prompt if len(clean_prompt) <= 120 else clean_prompt[:117].rstrip() + "..."
    tags = ["AI Story", selected_style.title(), "PulseSoc"]
    if "crypto" in clean_prompt.lower():
        tags.append("Crypto")
    if "haiti" in clean_prompt.lower():
        tags.append("Haiti")
    return {
        "ok": True,
        "story_id": f"ai-story-{digest}",
        "status_type": "ai",
        "prompt": clean_prompt,
        "caption": caption,
        "style": selected_style,
        "duration_seconds": max(3, min(int(duration_seconds or 12), 60)),
        "visual": {
            "kind": "css_motion_background",
            "background": preset["background"],
            "filter": preset["filter"],
            "motion": "slow_parallax_particles",
            "aspect_ratio": 9 / 16,
        },
        "music_suggestion": "pulse-original-rise",
        "tags": tags,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "provider": provider_status(),
    }


def caption_suggestions(prompt: str) -> list[str]:
    base = (prompt or "PulseSoc moment").strip()[:80]
    return [
        base,
        f"{base} · built with PulseSoc AI",
        f"Signal, story, and momentum: {base}",
    ]
