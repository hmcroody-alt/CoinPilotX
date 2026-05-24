"""Deterministic external embed normalization for Pulse surfaces.

This service intentionally avoids live network scraping in hot feed paths. It
creates a stable, validated media-shaped object from external URLs so desktop,
mobile, websocket inserts, and cached feed payloads render the same structure.
OpenGraph fetching can be layered on top by workers without changing the
client contract.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from . import media_service


URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


PLATFORM_HINTS = {
    "youtube.com": ("video", "youtube"),
    "youtu.be": ("video", "youtube"),
    "tiktok.com": ("video", "tiktok"),
    "instagram.com": ("social", "instagram"),
    "x.com": ("social", "x"),
    "twitter.com": ("social", "x"),
    "facebook.com": ("social", "facebook"),
    "threads.net": ("social", "threads"),
    "soundcloud.com": ("audio", "soundcloud"),
    "spotify.com": ("audio", "spotify"),
}


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")


def extract_urls(text: str) -> list[str]:
    """Return unique HTTP(S) URLs in display order."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(").,!?;")
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


def source_platform(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower().removeprefix("www.")
    for domain, (_, platform) in PLATFORM_HINTS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return host.split(".")[0] if host else "external"


def embed_type(url: str) -> str:
    parsed = urlparse(url or "")
    path = (parsed.path or "").lower()
    host = (parsed.netloc or "").lower().removeprefix("www.")
    if path.endswith(IMAGE_EXTS):
        return "image"
    if path.endswith(VIDEO_EXTS):
        return "video"
    for domain, (kind, _) in PLATFORM_HINTS.items():
        if host == domain or host.endswith("." + domain):
            return kind
    return "link"


def canonical_embed(url: str, *, title: str = "", description: str = "", image_url: str = "") -> dict:
    """Return a stable media-like embed object for feed rendering."""
    clean_url = media_service.normalize_url(url)
    image = media_service.normalize_url(image_url)
    kind = embed_type(clean_url)
    platform = source_platform(clean_url)
    media_url = image if image else clean_url if kind in {"image", "video"} else ""
    resolved = media_service.resolve_media({"media_url": media_url, "media_type": kind if kind in {"image", "video"} else "image"}) if media_url else {}
    embed_id = hashlib.sha1(clean_url.encode("utf-8")).hexdigest()[:16] if clean_url else ""
    return {
        "id": f"embed-{embed_id}" if embed_id else "",
        "type": kind,
        "media_type": kind if kind in {"image", "video"} else "embed",
        "media_url": resolved.get("media_url") or media_url,
        "valid_url": resolved.get("valid_url") or media_url,
        "thumbnail_url": resolved.get("thumbnail_url") or image or media_service.FALLBACK_URL,
        "poster_url": resolved.get("poster_url") or resolved.get("thumbnail_url") or image or media_service.FALLBACK_URL,
        "fallback_url": media_service.FALLBACK_URL,
        "width": int(resolved.get("width") or 0),
        "height": int(resolved.get("height") or 0),
        "aspect_ratio": float(resolved.get("aspect_ratio") or 0),
        "mime_type": resolved.get("mime_type") or "",
        "embed_type": kind,
        "source_platform": platform,
        "source_url": clean_url,
        "title": title or clean_url,
        "description": description or "",
        "preload_priority": "lazy",
        "is_available": bool((resolved.get("is_available") if resolved else False) or media_url),
        "storage_provider": "external",
        "hydration_state": "ready" if media_url else "link_only",
    }


def embed_from_text(text: str) -> dict:
    urls = extract_urls(text or "")
    return canonical_embed(urls[0]) if urls else {}
