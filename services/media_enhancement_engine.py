"""Smart media validation and enhancement plan helpers."""

from __future__ import annotations


IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
FILTERS = [
    "Pulse Neon",
    "Founder Mode",
    "Market Vision",
    "Arena Energy",
    "Prestige",
    "Studio Pro",
    "Crypto Glow",
    "Soft Portrait",
    "Sharp HD",
    "Night Vision",
    "Scam Alert Red",
]


def validate_media(filename="", content_type="", size_bytes=0, max_image_mb=5, max_video_mb=25) -> dict:
    content_type = str(content_type or "").lower()
    size_bytes = int(size_bytes or 0)
    if content_type in IMAGE_TYPES and size_bytes <= max_image_mb * 1024 * 1024:
        return {"ok": True, "media_type": "image"}
    if content_type in VIDEO_TYPES and size_bytes <= max_video_mb * 1024 * 1024:
        return {"ok": True, "media_type": "video"}
    if content_type not in IMAGE_TYPES | VIDEO_TYPES:
        return {"ok": False, "message": "Unsupported media type."}
    return {"ok": False, "message": "Upload is too large."}


def enhancement_plan(media_type="image", filter_name="Studio Pro") -> dict:
    filter_name = filter_name if filter_name in FILTERS else "Studio Pro"
    steps = ["validate", "compress", "thumbnail", "medium", "orientation"]
    if media_type == "image":
        steps += ["smart_crop", "blurred_background_fill", "filter_preview"]
    if media_type == "video":
        steps += ["poster_frame", "duration_check", "muted_preview"]
    return {"filter": filter_name, "steps": steps, "preserve_original": True}
