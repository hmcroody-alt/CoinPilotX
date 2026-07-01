#!/usr/bin/env python3
"""Audit Reel-to-feed audio propagation, live Status previews, and PulseSoc nav cleanup."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///coinpilotx.db")

import bot  # noqa: E402
from services import pulse_feed_engine  # noqa: E402


REPORT = ROOT / "reports" / "reel_feed_audio_status_nav_audit.json"


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def check(self, condition: bool, name: str, details: str = "") -> None:
        self.checks.append({"name": name, "ok": bool(condition), "details": details})
        print(("PASS " if condition else "FAIL ") + name + (f": {details}" if details else ""))
        if not condition:
            raise AssertionError(f"{name}: {details}")

    def write(self) -> None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"ok": all(item["ok"] for item in self.checks), "checks": self.checks}, indent=2), encoding="utf-8")


def client_for(user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def seed_owner_media_and_audio() -> dict:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    suffix = int(time.time() * 1000) % 1_000_000_000
    user_id = 9_990_000_000 + suffix
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, account_status)
        VALUES (?, ?, ?, ?, ?, 1, 'active')
        """,
        (user_id, f"reel_audio_owner_{suffix}", f"Reel Audio Owner {suffix}", f"reel-audio-{suffix}@example.test", now),
    )
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, context_type, context_id, original_filename, stored_filename, media_type, media_url, public_url,
         playback_url, thumbnail_url, poster_url, mime_type, playback_mime_type, file_size_bytes, duration_seconds,
         width, height, moderation_status, processing_status, is_available, created_at, updated_at)
        VALUES (?, 'pulse', 'audit-reel-feed-audio', 'video-with-original-audio.mp4', 'video-with-original-audio.mp4',
                'video', '/static/audit/video-with-original-audio.mp4', '/static/audit/video-with-original-audio.mp4',
                '/static/audit/video-with-original-audio.mp4', '', '', 'video/mp4', 'video/mp4', 2048, 5.7,
                1280, 720, 'approved', 'ready', 1, ?, ?)
        """,
        (user_id, now, now),
    )
    media_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_audio_tracks
        (title, artist, uploader_user_id, audio_url, duration_seconds, waveform_json, bpm, usage_count, trend_score,
         source_type, safety_status, source_provider, license_type, commercial_use_allowed, remix_edit_allowed,
         attribution_required, proof_url, proof_file, approved_by_admin, active, rights_confirmed, rights_confirmed_at,
         rights_statement, created_at, updated_at)
        VALUES ('QA Attached PulseSoc Music', 'PulseSoc QA', ?, '/static/audit/attached-pulsesoc-music.wav', 5.0,
                '[0.2,0.8,0.4,0.7]', 120, 0, 0, 'original', 'approved', 'original_pulse_sound',
                'PulseSoc original work', 1, 1, 0, 'audit-proof', '', 1, 1, 1, ?,
                'Audit-owned test music for attached audio propagation.', ?, ?)
        """,
        (user_id, now, now, now),
    )
    track_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return {"user_id": user_id, "media_id": media_id, "track_id": track_id}


def main() -> int:
    audit = Audit()
    bot.init_db()
    data = seed_owner_media_and_audio()
    owner = client_for(data["user_id"])
    response = owner.post(
        "/api/pulse/reels/create",
        json={
            "title": "QA Reel Shared To Feed With Music",
            "caption": "Attached music must survive the Reel-to-feed share.",
            "category": "Music",
            "visibility": "public",
            "privacy": "public",
            "share_to_feed": True,
            "post_type": "video",
            "media_id": data["media_id"],
            "media_ids": [data["media_id"]],
            "audio_track_id": data["track_id"],
            "music_track_id": data["track_id"],
            "sound_start_seconds": 0,
            "audio_volume": 0.8,
            "tags": ["audit", "music"],
        },
    )
    payload = response.get_json(silent=True) or {}
    audit.check(response.status_code == 200 and payload.get("ok"), "Reel create with feed share succeeds", str(payload)[:300])
    reel = payload.get("reel") or {}
    post_id = int(payload.get("post_id") or reel.get("post_id") or 0)
    reel_id = int(payload.get("reel_id") or reel.get("reel_id") or 0)
    audit.check(bool(post_id and reel_id), "Created Reel has post and reel ids", f"post_id={post_id} reel_id={reel_id}")

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT content_type, content_id, audio_track_id, original_audio_muted, audio_start_time, audio_volume FROM pulse_content_music WHERE audio_track_id=? ORDER BY content_type",
            (str(data["track_id"]),),
        ).fetchall()
    ]
    conn.close()
    content_keys = {(row["content_type"], int(row["content_id"])) for row in rows}
    audit.check(("reel", reel_id) in content_keys, "Reel music attachment persisted", str(rows))
    audit.check(("post", post_id) in content_keys, "Feed post music attachment persisted", str(rows))
    audit.check(("video", post_id) in content_keys, "Feed video music attachment persisted", str(rows))
    audit.check(all(int(row.get("original_audio_muted") or 0) == 1 for row in rows), "All music attachments mute original audio", str(rows))

    feed_post = pulse_feed_engine.get_post(post_id, viewer_user_id=data["user_id"], include_private=True)
    music = (feed_post or {}).get("music") or {}
    media = ((feed_post or {}).get("media") or [{}])[0] or {}
    audit.check(music.get("track_id") == str(data["track_id"]), "Feed serializer exposes same attached audio track", json.dumps(music, default=str)[:300])
    audit.check(bool(music.get("attached_audio_url")), "Feed serializer exposes attached audio URL", json.dumps(music, default=str)[:300])
    audit.check(media.get("attached_audio_url") == music.get("attached_audio_url"), "Feed media carries attached audio URL", json.dumps(media, default=str)[:300])
    audit.check(media.get("original_audio_muted") is True, "Feed media marks original audio muted", json.dumps(media, default=str)[:300])

    hydrated_reel = bot.pulse_reel_payload(reel_id=reel_id, viewer_user_id=data["user_id"])
    reel_audio = (hydrated_reel or {}).get("audio") or {}
    audit.check(reel_audio.get("track_id") == data["track_id"], "Reel payload exposes same attached audio track", json.dumps(reel_audio, default=str)[:300])
    audit.check(reel_audio.get("attached_audio_url") == music.get("attached_audio_url"), "Reel and feed use same attached audio URL", json.dumps(reel_audio, default=str)[:300])

    home = (ROOT / "static/js/pulse_home_core.js").read_text(encoding="utf-8")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")
    audit.check("observeStatusPreviewVideo" in home and "IntersectionObserver" in home, "Home Status previews use visibility observer")
    audit.check(
        "data-status-preview-seconds=\"10\"" in bot_source
        and "dataset.statusPreviewSeconds = \"10\"" in home
        and "Math.min(10" in home,
        "Status previews declare first-10-second loop",
    )
    audit.check("status-preview-image" in css and "statusKenBurns" in css, "Image Status previews animate")
    audit.check("status-preview-text" in css and "statusTextPulse" in css, "Text Status previews animate")
    audit.check("pulse-bell-icon" in bot_source and "aria-label=\"Notifications\"" in bot_source, "Header uses global notification bell")
    audit.check("__SHELL_AVATAR__" in bot_source and "pulse-topnav-avatar" in bot_source, "Top nav uses server-rendered avatar slot")
    audit.check("href=\"/pulse/notifications\"" in bot_source and "href=\"/pulse/messages\"" in bot_source and "href=\"/pulse/search\"" in bot_source, "Top nav routes are real PulseSoc routes")
    audit.write()
    print(f"report={REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
