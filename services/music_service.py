"""License-safe PulseSoc music discovery and attachment helpers.

PulseSoc never scrapes or downloads random music. This service only exposes
tracks that are original, internally approved, or imported as licensed metadata
and approved by an admin.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from typing import Iterable

from services import user_context


SAFE_MUSIC_PROVIDERS = {
    "soundstripe",
    "epidemic_sound_partner",
    "feed_fm",
    "sounds_hq",
    "jamendo_licensing",
    "custom_licensed_catalog",
    "original_pulse_sound",
}

LICENSE_REQUIRED_FIELDS = [
    "title",
    "artist",
    "source",
    "license_type",
    "commercial_use_allowed",
    "remix_edit_allowed",
    "attribution_required",
    "proof_url",
    "approved_by_admin",
    "active",
]

BLOCKED_LICENSE_TYPES = {"noncommercial", "cc-by-nc", "no-derivatives", "cc-by-nd"}

DEFAULT_TRACKS = [
    {
        "id": "pulse-original-rise",
        "title": "PulseSoc Rise",
        "artist": "CoinPilotXAI Originals",
        "duration_seconds": 28,
        "license": "original_pulse_sound",
        "license_type": "PulseSoc original work",
        "source": "original_pulse_sound",
        "commercial_use_allowed": True,
        "remix_edit_allowed": True,
        "attribution_required": False,
        "proof_url": "internal:pulse-original-rise",
        "approved_by_admin": True,
        "active": True,
        "mood": "cinematic",
        "genre": "ambient",
        "bpm": 92,
        "preview_url": "",
        "waveform": [0.12, 0.22, 0.35, 0.42, 0.38, 0.58, 0.66, 0.48, 0.72, 0.64, 0.4, 0.3],
        "tags": ["story", "cinematic", "creator", "video"],
    },
    {
        "id": "pulse-neon-motion",
        "title": "Neon Motion",
        "artist": "PulseSoc Studio",
        "duration_seconds": 31,
        "license": "original_pulse_sound",
        "license_type": "PulseSoc original work",
        "source": "original_pulse_sound",
        "commercial_use_allowed": True,
        "remix_edit_allowed": True,
        "attribution_required": False,
        "proof_url": "internal:pulse-neon-motion",
        "approved_by_admin": True,
        "active": True,
        "mood": "energetic",
        "genre": "electronic",
        "bpm": 118,
        "preview_url": "",
        "waveform": [0.18, 0.3, 0.46, 0.7, 0.54, 0.78, 0.82, 0.62, 0.9, 0.72, 0.52, 0.34],
        "tags": ["reels", "status", "neon", "motion"],
    },
    {
        "id": "pulse-soft-signal",
        "title": "Soft Signal",
        "artist": "PulseSoc Studio",
        "duration_seconds": 24,
        "license": "original_pulse_sound",
        "license_type": "PulseSoc original work",
        "source": "original_pulse_sound",
        "commercial_use_allowed": True,
        "remix_edit_allowed": True,
        "attribution_required": False,
        "proof_url": "internal:pulse-soft-signal",
        "approved_by_admin": True,
        "active": True,
        "mood": "warm",
        "genre": "lofi",
        "bpm": 76,
        "preview_url": "",
        "waveform": [0.1, 0.15, 0.24, 0.32, 0.3, 0.36, 0.42, 0.4, 0.35, 0.28, 0.2, 0.12],
        "tags": ["photo", "family", "warm", "post"],
    },
]


def configured_providers() -> list[str]:
    raw = os.getenv("PULSE_MUSIC_PROVIDERS", "original_pulse_sound")
    providers = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return [p for p in providers if p in SAFE_MUSIC_PROVIDERS] or ["original_pulse_sound"]


def provider_status() -> dict:
    providers = configured_providers()
    return {
        "ok": True,
        "providers": providers,
        "licensed_external_enabled": any(p != "original_pulse_sound" for p in providers),
        "policy": "Only original, admin-approved, or explicitly licensed commercial-use music is exposed.",
        "required_env": {
            "PULSE_MUSIC_PROVIDERS": "comma-separated provider keys",
            "PULSE_MUSIC_LICENSE_KEY": "required by external licensed catalog providers",
        },
        "supported_surfaces": ["reels", "videos", "statuses", "posts"],
    }


def _connection():
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    return conn


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_track(track: dict) -> bool:
    return not public_visibility_reasons(track)


def _playable_track(track: dict) -> bool:
    return bool(str(track.get("audio_url") or track.get("preview_url") or "").strip())


def public_visibility_reasons(track: dict) -> list[str]:
    """Return public catalog blockers for a music track without exposing secrets."""
    source = str(track.get("source") or track.get("source_provider") or track.get("source_type") or "").strip().lower()
    license_type = str(track.get("license_type") or track.get("license") or "").strip().lower()
    safety_status = str(track.get("safety_status") or track.get("moderation_status") or "approved").strip().lower()
    reasons: list[str] = []
    if safety_status != "approved":
        reasons.append("safety_status must be approved")
    if not _bool(track.get("active", True)):
        reasons.append("active must be true")
    if not _bool(track.get("approved_by_admin", True)):
        reasons.append("approved_by_admin must be true")
    if not _bool(track.get("commercial_use_allowed", True)):
        reasons.append("commercial use must be allowed")
    if not _bool(track.get("remix_edit_allowed", True)):
        reasons.append("edit/remix use must be allowed")
    if not (track.get("proof_url") or track.get("proof_file") or source.startswith("original_pulse_sound")):
        reasons.append("license proof or PulseSoc original source is required")
    if license_type in BLOCKED_LICENSE_TYPES:
        reasons.append("license type is not allowed for creator reuse")
    return reasons


def _db_track(row) -> dict:
    item = dict(row)
    try:
        tags = json.loads(item.get("tags_json") or "[]")
    except Exception:
        tags = []
    try:
        waveform = json.loads(item.get("waveform_json") or "[]")
    except Exception:
        waveform = []
    return {
        "id": str(item.get("id") or ""),
        "title": item.get("title") or "PulseSoc sound",
        "artist": item.get("artist") or "PulseSoc Studio",
        "artist_user_id": int(item.get("uploader_user_id") or 0),
        "duration_seconds": float(item.get("duration_seconds") or 0),
        "license": item.get("license_type") or "unknown",
        "license_type": item.get("license_type") or "unknown",
        "source": item.get("source_provider") or item.get("source_type") or "custom_licensed_catalog",
        "source_provider": item.get("source_provider") or item.get("source_type") or "custom_licensed_catalog",
        "commercial_use_allowed": _bool(item.get("commercial_use_allowed")),
        "remix_edit_allowed": _bool(item.get("remix_edit_allowed")),
        "attribution_required": _bool(item.get("attribution_required")),
        "proof_url": item.get("proof_url") or item.get("proof_file") or "",
        "proof_file": item.get("proof_file") or "",
        "approved_by_admin": _bool(item.get("approved_by_admin")),
        "active": _bool(item.get("active")) and str(item.get("safety_status") or "approved") == "approved",
        "mood": item.get("mood") or "",
        "genre": item.get("genre") or "",
        "bpm": int(item.get("bpm") or 0),
        "preview_url": item.get("audio_url") or "",
        "audio_url": item.get("audio_url") or "",
        "cover_art_url": item.get("cover_art_url") or "",
        "waveform": waveform,
        "tags": tags,
        "language": item.get("language") or "",
        "description": item.get("description") or "",
        "rights_confirmed": _bool(item.get("rights_confirmed")),
        "moderation_status": item.get("safety_status") or "approved",
        "usage_count": int(item.get("usage_count") or 0),
        "trend_score": int(item.get("trend_score") or 0),
        "play_count": int(item.get("play_count") or 0),
        "reel_use_count": int(item.get("reel_use_count") or 0),
        "video_use_count": int(item.get("video_use_count") or 0),
        "save_count": int(item.get("save_count") or 0),
        "share_count": int(item.get("share_count") or 0),
    }


def _load_db_tracks(query: str = "", limit: int = 300) -> list[dict]:
    try:
        conn = _connection()
        cur = conn.cursor()
        limit = max(50, min(int(limit or 300), 1000))
        query = str(query or "").strip().lower()[:120]
        search_clause = ""
        params: list[object] = []
        if query:
            like = f"%{query}%"
            search_clause = """
              AND (
                LOWER(COALESCE(title,'')) LIKE ?
                OR LOWER(COALESCE(artist,'')) LIKE ?
                OR LOWER(COALESCE(genre,'')) LIKE ?
                OR LOWER(COALESCE(mood,'')) LIKE ?
                OR LOWER(COALESCE(language,'')) LIKE ?
                OR LOWER(COALESCE(tags_json,'')) LIKE ?
              )
            """
            params.extend([like, like, like, like, like, like])
        cur.execute(
            f"""
            SELECT * FROM pulse_audio_tracks
            WHERE COALESCE(safety_status,'approved')='approved'
              AND COALESCE(active,1)=1
              AND COALESCE(audio_url,'')!=''
              AND COALESCE(approved_by_admin,0)=1
              AND COALESCE(commercial_use_allowed,0)=1
              AND COALESCE(remix_edit_allowed,0)=1
              AND COALESCE(license_type,'') NOT IN ('noncommercial','cc-by-nc','no-derivatives','cc-by-nd')
              {search_clause}
            ORDER BY trend_score DESC, usage_count DESC, created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = [_db_track(row) for row in cur.fetchall()]
        conn.close()
        return [row for row in rows if _safe_track(row)]
    except Exception:
        return []


def _load_db_track_by_id(track_id: str) -> dict:
    try:
        conn = _connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM pulse_audio_tracks
            WHERE id=?
              AND COALESCE(safety_status,'approved')='approved'
              AND COALESCE(active,1)=1
              AND COALESCE(audio_url,'')!=''
              AND COALESCE(approved_by_admin,0)=1
              AND COALESCE(commercial_use_allowed,0)=1
              AND COALESCE(remix_edit_allowed,0)=1
              AND COALESCE(license_type,'') NOT IN ('noncommercial','cc-by-nc','no-derivatives','cc-by-nd')
            LIMIT 1
            """,
            (track_id,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return {}
        track = _db_track(row)
        return track if _safe_track(track) else {}
    except Exception:
        return {}


def _catalog_tracks(query: str = "") -> list[dict]:
    seen = set()
    tracks = []
    for track in [*_load_db_tracks(query=query), *DEFAULT_TRACKS]:
        key = str(track.get("id") or f"{track.get('title')}:{track.get('artist')}")
        if key in seen or not _safe_track(track) or not _playable_track(track):
            continue
        seen.add(key)
        enriched = dict(track)
        enriched["is_creator_safe"] = True
        tracks.append(enriched)
    return tracks


def _score(track: dict, query: str = "", mood: str = "", genre: str = "", topic: str = "", length: int | float = 0) -> float:
    haystack = " ".join([
        track.get("title", ""),
        track.get("artist", ""),
        track.get("language", ""),
        track.get("mood", ""),
        track.get("genre", ""),
        " ".join(track.get("tags") or []),
    ]).lower()
    score = 1.0
    for text in [query, topic]:
        if text:
            score += sum(1 for part in str(text).lower().split() if part in haystack) * 2.5
    if mood and mood.lower() == str(track.get("mood", "")).lower():
        score += 2.1
    if genre and genre.lower() == str(track.get("genre", "")).lower():
        score += 1.8
    if length:
        delta = abs(float(track.get("duration_seconds") or 0) - float(length))
        score += max(0, 2.0 - delta / 20)
    score += math.log1p(int(track.get("bpm") or 80)) / 10
    score += int(track.get("trend_score") or 0) / 100
    return round(score, 3)


def search_tracks(query: str = "", mood: str = "", genre: str = "", topic: str = "", length: int | float = 0, limit: int = 12) -> list[dict]:
    tracks = []
    db_query = " ".join(part for part in [query, topic] if part).strip()
    catalog = _catalog_tracks(query=db_query)
    if not catalog and db_query:
        catalog = _catalog_tracks()
    for track in catalog:
        enriched = dict(track)
        enriched["score"] = _score(track, query, mood, genre, topic, length)
        enriched["is_creator_safe"] = True
        tracks.append(enriched)
    tracks.sort(key=lambda item: item["score"], reverse=True)
    return tracks[: max(1, min(int(limit or 12), 40))]


def ai_suggest_tracks(mood: str = "", genre: str = "", topic: str = "", length: int | float = 0, limit: int = 8) -> dict:
    query = " ".join(part for part in [mood, genre, topic] if part)
    tracks = search_tracks(query=query, mood=mood, genre=genre, topic=topic, length=length, limit=limit)
    return {
        "ok": True,
        "items": tracks,
        "assistant_note": "Suggestions are limited to admin-approved tracks with verified commercial/edit rights.",
        "blocked_policy": "Unapproved, unclear, noncommercial, or no-derivatives tracks are not returned.",
        "provider": provider_status(),
    }


def trending_tracks(limit: int = 10) -> list[dict]:
    tracks = search_tracks(limit=limit)
    for index, track in enumerate(tracks, 1):
        track["momentum_score"] = max(10, 100 - index * 9)
        track["usage_hint"] = "Approved for Status, Reels, Videos, and Posts."
    return tracks


def public_track(track_id: str) -> dict:
    db_track = _load_db_track_by_id(str(track_id or ""))
    if db_track and _playable_track(db_track):
        db_track["is_creator_safe"] = True
        return db_track
    return next((track for track in _catalog_tracks() if str(track.get("id")) == str(track_id)), {})


def waveform_for_track(track_id: str) -> list[float]:
    for track in _catalog_tracks():
        if str(track["id"]) == str(track_id):
            return list(track.get("waveform") or [])
    return [0.16, 0.24, 0.38, 0.52, 0.46, 0.34, 0.28, 0.18]


def attach_music_payload(track_id: str, volume: float = 0.82) -> dict:
    match = public_track(str(track_id or ""))
    if not match:
        return {
            "track_id": str(track_id or ""),
            "is_creator_safe": False,
            "message": "Track is not approved for PulseSoc use.",
        }
    return {
        "track_id": match["id"],
        "title": match["title"],
        "artist": match["artist"],
        "preview_url": match.get("preview_url", ""),
        "audio_url": match.get("audio_url") or match.get("preview_url") or "",
        "duration_seconds": float(match.get("duration_seconds") or 0),
        "waveform": waveform_for_track(match["id"]),
        "volume": max(0.0, min(float(volume or 0.82), 1.0)),
        "license": match.get("license_type") or match.get("license", "approved"),
        "license_type": match.get("license_type") or match.get("license", "approved"),
        "source": match.get("source") or match.get("source_provider") or "original_pulse_sound",
        "commercial_use_allowed": bool(match.get("commercial_use_allowed")),
        "remix_edit_allowed": bool(match.get("remix_edit_allowed")),
        "attribution_required": bool(match.get("attribution_required")),
        "proof_url": match.get("proof_url") or "",
        "approved_by_admin": bool(match.get("approved_by_admin")),
        "is_creator_safe": _safe_track(match),
    }


def license_inventory() -> list[dict]:
    return [
        {key: track.get(key) for key in LICENSE_REQUIRED_FIELDS}
        | {"id": track.get("id"), "duration_seconds": track.get("duration_seconds"), "is_creator_safe": _safe_track(track)}
        for track in _catalog_tracks()
    ]


def discovery_lanes(status_rows: Iterable[dict]) -> dict:
    rows = [dict(row) for row in status_rows]
    return {
        "for_you": rows[:40],
        "following": [row for row in rows if row.get("viewer_follows_author")][:24],
        "trending": sorted(rows, key=lambda row: int(row.get("reaction_count") or 0) + int(row.get("reply_count") or 0) * 2, reverse=True)[:24],
        "global": rows[:40],
        "music": [row for row in rows if str(row.get("status_type") or "").lower() == "music" or row.get("music_title")][:24],
        "ai_picks": sorted(rows, key=lambda row: int(row.get("ai_momentum_score") or 0), reverse=True)[:24],
        "live": [row for row in rows if str(row.get("status_type") or "").lower() == "live"][:24],
    }
