"""Creator-safe Pulse music discovery and attachment helpers.

The service is intentionally provider-first. Production can connect licensed
catalog providers, while local/dev keeps a small royalty-free/original-sound
catalog so Status, Reels, and camera flows stay functional without scraping.
"""

from __future__ import annotations

import math
import os
from typing import Iterable


SAFE_MUSIC_PROVIDERS = {
    "soundstripe",
    "sounds",
    "mubert",
    "uppbeat",
    "custom_licensed_catalog",
    "original_pulse_sound",
}

DEFAULT_TRACKS = [
    {
        "id": "pulse-original-rise",
        "title": "Pulse Rise",
        "artist": "CoinPilotXAI Originals",
        "duration_seconds": 28,
        "license": "original_pulse_sound",
        "mood": "cinematic",
        "bpm": 92,
        "preview_url": "",
        "waveform": [0.12, 0.22, 0.35, 0.42, 0.38, 0.58, 0.66, 0.48, 0.72, 0.64, 0.4, 0.3],
        "tags": ["story", "cinematic", "creator"],
    },
    {
        "id": "pulse-neon-motion",
        "title": "Neon Motion",
        "artist": "Pulse Studio",
        "duration_seconds": 31,
        "license": "original_pulse_sound",
        "mood": "energetic",
        "bpm": 118,
        "preview_url": "",
        "waveform": [0.18, 0.3, 0.46, 0.7, 0.54, 0.78, 0.82, 0.62, 0.9, 0.72, 0.52, 0.34],
        "tags": ["reels", "status", "neon"],
    },
    {
        "id": "pulse-soft-signal",
        "title": "Soft Signal",
        "artist": "Pulse Studio",
        "duration_seconds": 24,
        "license": "original_pulse_sound",
        "mood": "warm",
        "bpm": 76,
        "preview_url": "",
        "waveform": [0.1, 0.15, 0.24, 0.32, 0.3, 0.36, 0.42, 0.4, 0.35, 0.28, 0.2, 0.12],
        "tags": ["photo", "family", "warm"],
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
        "policy": "Use original, user-uploaded, or explicitly licensed catalog audio only.",
        "required_env": {
            "PULSE_MUSIC_PROVIDERS": "comma-separated provider keys",
            "PULSE_MUSIC_LICENSE_KEY": "required by external licensed catalog providers",
        },
    }


def _score(track: dict, query: str = "", mood: str = "") -> float:
    haystack = " ".join([track.get("title", ""), track.get("artist", ""), track.get("mood", ""), " ".join(track.get("tags") or [])]).lower()
    score = 1.0
    if query:
        score += sum(1 for part in query.lower().split() if part in haystack) * 2.5
    if mood and mood.lower() == str(track.get("mood", "")).lower():
        score += 1.7
    score += math.log1p(int(track.get("bpm") or 80)) / 10
    return round(score, 3)


def search_tracks(query: str = "", mood: str = "", limit: int = 12) -> list[dict]:
    tracks = []
    for track in DEFAULT_TRACKS:
        enriched = dict(track)
        enriched["score"] = _score(track, query, mood)
        enriched["source_provider"] = "original_pulse_sound"
        enriched["is_creator_safe"] = True
        tracks.append(enriched)
    tracks.sort(key=lambda item: item["score"], reverse=True)
    return tracks[: max(1, min(int(limit or 12), 40))]


def trending_tracks(limit: int = 10) -> list[dict]:
    tracks = search_tracks(limit=limit)
    for index, track in enumerate(tracks, 1):
        track["momentum_score"] = max(10, 100 - index * 9)
        track["usage_hint"] = "Great for Status, Reels, and camera captures."
    return tracks


def waveform_for_track(track_id: str) -> list[float]:
    for track in DEFAULT_TRACKS:
        if track["id"] == track_id:
            return list(track.get("waveform") or [])
    return [0.16, 0.24, 0.38, 0.52, 0.46, 0.34, 0.28, 0.18]


def attach_music_payload(track_id: str, volume: float = 0.82) -> dict:
    match = next((track for track in DEFAULT_TRACKS if track["id"] == track_id), DEFAULT_TRACKS[0])
    return {
        "track_id": match["id"],
        "title": match["title"],
        "artist": match["artist"],
        "preview_url": match.get("preview_url", ""),
        "waveform": waveform_for_track(match["id"]),
        "volume": max(0.0, min(float(volume or 0.82), 1.0)),
        "license": match.get("license", "original_pulse_sound"),
        "is_creator_safe": True,
    }


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
