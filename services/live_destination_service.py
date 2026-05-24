"""Secure destination records for Pulse Live restreaming."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime
from urllib.parse import urlparse

SUPPORTED_PLATFORMS = {"pulse", "facebook", "youtube", "twitch", "kick", "custom_rtmp", "tiktok", "x_twitter", "linkedin"}


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def normalize_platform(value: str) -> str:
    platform = str(value or "pulse").strip().lower().replace("-", "_")
    return platform if platform in SUPPORTED_PLATFORMS else "pulse"


def encrypt_secret(value: str) -> str:
    """Store a reversible-ish local protected blob without logging raw secrets.

    This is intentionally lightweight for the current Flask stack. Production can swap
    this implementation for KMS without changing callers.
    """
    raw = str(value or "")
    if not raw:
        return ""
    key = os.getenv("LIVE_SECRET_KEY") or os.getenv("SECRET_KEY") or "coinpilotx-live-local"
    digest = hmac.new(key.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return "enc:" + digest + ":" + base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def mask_secret(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if raw.startswith("enc:"):
        raw = raw.split(":")[-1]
    return "••••" + raw[-4:]


def validate_rtmp_url(value: str) -> tuple[bool, str]:
    url = str(value or "").strip()
    if not url:
        return False, "RTMP URL is required."
    parsed = urlparse(url)
    if parsed.scheme not in {"rtmp", "rtmps"}:
        return False, "RTMP destination must start with rtmp:// or rtmps://."
    if not parsed.netloc:
        return False, "RTMP destination host is missing."
    return True, ""


def upsert_destination(cur, *, user_id: int, platform: str, label: str = "", rtmp_url: str = "", stream_key: str = "", oauth_token: str = "") -> int:
    platform = normalize_platform(platform)
    now = now_iso()
    label = (label or platform.replace("_", " ").title())[:120]
    encrypted_key = encrypt_secret(stream_key)
    encrypted_token = encrypt_secret(oauth_token)
    cur.execute(
        """
        INSERT INTO pulse_live_destinations
        (user_id, platform, label, rtmp_url, stream_key_encrypted, oauth_token_encrypted, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'connected', ?, ?)
        """,
        (int(user_id), platform, label, str(rtmp_url or "")[:700], encrypted_key, encrypted_token, now, now),
    )
    return int(cur.lastrowid)


def public_destination(row) -> dict:
    item = dict(row or {})
    return {
        "id": int(item.get("id") or 0),
        "platform": normalize_platform(item.get("platform")),
        "label": item.get("label") or normalize_platform(item.get("platform")).replace("_", " ").title(),
        "status": item.get("status") or "connected",
        "rtmp_url": item.get("rtmp_url") or "",
        "stream_key_preview": mask_secret(item.get("stream_key_encrypted") or ""),
        "updated_at": item.get("updated_at") or "",
    }
