"""Camera creator studio filter catalog and lightweight media validation."""

from __future__ import annotations

import os


FREE_FILTERS = {
    "clean_hd": {"label": "Clean HD", "css": "brightness(1.06) contrast(1.12) saturate(1.06)", "premium": False},
    "pulse_neon": {"label": "PulseSoc Neon", "css": "contrast(1.08) saturate(1.22) brightness(1.04)", "premium": False},
    "sharp_hd": {"label": "Sharp HD", "css": "contrast(1.12) saturate(1.04)", "premium": False},
    "warm_cinema": {"label": "Warm Cinema", "css": "sepia(.14) saturate(1.12) contrast(1.05)", "premium": False},
    "warm_studio": {"label": "Warm Studio", "css": "sepia(.14) saturate(1.14) brightness(1.06) contrast(1.04)", "premium": False},
    "beauty_natural": {"label": "Beauty Natural", "css": "brightness(1.08) contrast(1.01) saturate(1.04)", "premium": False},
    "soft_light": {"label": "Soft Light", "css": "brightness(1.12) contrast(.99) saturate(1.04)", "premium": False},
    "teacher_clean": {"label": "Teacher Clean", "css": "brightness(1.06) contrast(1.04) saturate(.98)", "premium": False},
    "marketplace_bright": {"label": "Marketplace Bright", "css": "brightness(1.1) contrast(1.06) saturate(1.08)", "premium": False},
}

PREMIUM_FILTERS = {
    "cinema_glow": {"label": "Cinema Glow", "css": "brightness(1.05) contrast(1.12) saturate(1.12) sepia(.08)", "premium": True},
    "founder_gold": {"label": "Founder Gold", "css": "sepia(.22) saturate(1.24) contrast(1.08)", "premium": True},
    "night_vision_boost": {"label": "Night Vision Boost", "css": "brightness(1.16) contrast(1.18) saturate(1.08) hue-rotate(8deg)", "premium": True},
    "smooth_portrait": {"label": "Smooth Portrait", "css": "brightness(1.08) contrast(1.02) saturate(1.06)", "premium": True},
    "luxury_skin": {"label": "Luxury Skin", "css": "brightness(1.08) contrast(1.02) saturate(1.08)", "premium": True},
    "glass_premium": {"label": "Glass Premium", "css": "contrast(1.05) saturate(1.18) hue-rotate(8deg)", "premium": True},
    "ai_studio": {"label": "AI Studio", "css": "brightness(1.04) contrast(1.14) saturate(1.18)", "premium": True},
    "creator_glow": {"label": "Creator Glow", "css": "brightness(1.08) saturate(1.26)", "premium": True},
    "midnight_elite": {"label": "Midnight Elite", "css": "brightness(.92) contrast(1.24) saturate(1.08)", "premium": True},
    "dark_luxury": {"label": "Dark Luxury", "css": "brightness(.94) contrast(1.24) saturate(1.1)", "premium": True},
    "arena_fire": {"label": "Arena Fire", "css": "sepia(.18) hue-rotate(-12deg) saturate(1.32) contrast(1.12)", "premium": True},
}

EXTRA_FILTERS = {
    "cyber_glow": {"label": "Cyber Glow", "css": "contrast(1.1) saturate(1.35) hue-rotate(14deg)", "premium": True},
    "crypto_blue": {"label": "Crypto Blue", "css": "contrast(1.12) saturate(1.22) hue-rotate(18deg)", "premium": False},
    "crypto_vision": {"label": "Crypto Vision", "css": "contrast(1.16) saturate(1.2) hue-rotate(4deg)", "premium": False},
    "beauty_soft": {"label": "Beauty Soft", "css": "brightness(1.08) contrast(.98) saturate(1.04)", "premium": False},
    "market_heat": {"label": "Market Heat", "css": "sepia(.18) saturate(1.28) contrast(1.08)", "premium": False},
    "scam_alert_red": {"label": "Scam Alert Red", "css": "contrast(1.18) saturate(1.28) hue-rotate(-18deg)", "premium": False},
    "blue_future": {"label": "Blue Future", "css": "contrast(1.08) saturate(1.18) hue-rotate(20deg)", "premium": False},
    "portrait_pro": {"label": "Portrait Pro", "css": "brightness(1.07) contrast(1.03) saturate(1.07)", "premium": True},
    "sharp_creator": {"label": "Sharp Creator", "css": "contrast(1.18) saturate(1.1) brightness(1.03)", "premium": False},
    "live_pro": {"label": "Live Pro", "css": "contrast(1.1) saturate(1.08) brightness(1.08)", "premium": False},
    "viral_pop": {"label": "Viral Pop", "css": "contrast(1.14) saturate(1.35) brightness(1.06)", "premium": False},
    "music_video": {"label": "Music Video", "css": "contrast(1.16) saturate(1.24) hue-rotate(-6deg)", "premium": True},
    "reels_viral": {"label": "Reels Viral", "css": "contrast(1.15) saturate(1.3) brightness(1.04)", "premium": False},
}


FILTERS = {**FREE_FILTERS, **PREMIUM_FILTERS, **EXTRA_FILTERS}


def filter_catalog(is_premium: bool = False) -> list[dict]:
    catalog = []
    for key, item in FILTERS.items():
        catalog.append({
            "key": key,
            "label": item["label"],
            "css": item["css"],
            "premium": bool(item.get("premium")),
            "locked": bool(item.get("premium")) and not is_premium,
        })
    return catalog


def validate_media(filename: str, mime_type: str = "", size_bytes: int = 0) -> dict:
    ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
    allowed_images = {"jpg", "jpeg", "png", "webp", "gif"}
    allowed_videos = {"mp4", "webm", "mov"}
    allowed_audio = {"mp3", "m4a", "wav", "ogg"}
    allowed = allowed_images | allowed_videos | allowed_audio
    if ext not in allowed:
        return {"ok": False, "message": "Upload an image, short video, or audio file."}
    media_type = "video" if ext in allowed_videos else "audio" if ext in allowed_audio else "image"
    max_mb = {
        "video": os.getenv("PULSE_CAMERA_MAX_VIDEO_MB", os.getenv("MEDIA_UPLOAD_MAX_VIDEO_MB", "150")),
        "audio": os.getenv("PULSE_CAMERA_MAX_AUDIO_MB", os.getenv("MEDIA_UPLOAD_MAX_AUDIO_MB", "15")),
        "image": os.getenv("PULSE_CAMERA_MAX_IMAGE_MB", os.getenv("MEDIA_UPLOAD_MAX_IMAGE_MB", "12")),
    }.get(media_type, "12")
    max_bytes = int(float(max_mb) * 1024 * 1024)
    if size_bytes and size_bytes > max_bytes:
        return {"ok": False, "message": f"{media_type.title()} is too large. Please choose a file under {int(float(max_mb))} MB."}
    if mime_type and not (mime_type.startswith("image/") or mime_type.startswith("video/") or mime_type.startswith("audio/") or mime_type in {"application/ogg", "application/octet-stream"}):
        return {"ok": False, "message": "Only image, video, and audio media can be used here."}
    return {"ok": True, "media_type": media_type, "max_bytes": max_bytes}
