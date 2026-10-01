#!/usr/bin/env python3
"""Audit the final Reels publish step after media upload completes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user() -> int:
    user_id = 980047
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
    if not cur.fetchone():
        cur.execute(
            """
            INSERT INTO users
            (user_id, username, display_name, email, signup_time, onboarding_complete)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                user_id,
                "reels_final_step_audit",
                "Reels Final Step Audit",
                "reels-final-step-audit@example.test",
                now,
            ),
        )
    conn.commit()
    conn.close()
    return user_id


def create_uploaded_video(user_id: int) -> int:
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, context_type, context_id, original_filename, stored_filename,
         media_url, cdn_url, playback_url, media_type, mime_type, file_size_bytes,
         width, height, moderation_status, storage_provider, storage_key,
         mux_asset_id, mux_playback_id, mux_status, processing_status,
         is_available, verification_status, created_at, updated_at)
        VALUES (?, 'pulse_reel', 'draft', 'audit-reel.mp4', 'audit-reel.mp4',
                ?, ?, ?, 'video', 'video/mp4', 1234567,
                1080, 1920, 'approved', 'r2', ?,
                'mux_asset_audit', 'mux_playback_audit', 'preparing', 'mux_processing',
                1, 'passed', ?, ?)
        """,
        (
            user_id,
            "https://cdn.coinpilotx.app/pulse_media/audit-reel.mp4",
            "https://cdn.coinpilotx.app/pulse_media/audit-reel.mp4",
            "https://stream.mux.com/mux_playback_audit.m3u8",
            "pulse_media/audit-reel.mp4",
            now,
            now,
        ),
    )
    media_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return media_id


def main() -> None:
    bot.init_db()
    user_id = ensure_user()
    media_id = create_uploaded_video(user_id)
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    payload = {
        "title": "Audit Reel Final Publish",
        "caption": "Final publish audit",
        "category": "Community",
        "visibility": "public",
        "post_type": "video",
        "media_id": media_id,
        "media_ids": [media_id],
    }
    response = client.post("/api/pulse/reels/create", json=payload)
    data = response.get_json(silent=True) or {}
    expect(response.status_code == 200, "Reels create endpoint returns 200", str(data)[:500])
    expect(data.get("ok") is True and data.get("success") is True, "Reels create returns success=true", str(data)[:500])
    expect(int(data.get("media_id") or 0) == media_id, "Reels create response includes uploaded media_id")
    expect(int(data.get("reel_id") or 0) > 0, "Reels create returns reel_id")
    expect(int(data.get("post_id") or 0) > 0, "Reels create returns post_id")
    expect(data.get("mux_playback_id") == "mux_playback_audit", "Reels create response returns mux_playback_id")
    expect(data.get("playback_url", "").startswith("https://stream.mux.com/"), "Reels create response prefers Mux playback URL")

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_reels WHERE id=? AND post_id=? LIMIT 1", (int(data["reel_id"]), int(data["post_id"])))
    reel = dict(cur.fetchone() or {})
    conn.close()
    expect(bool(reel), "Reel row is created")
    expect(reel.get("processing_status") == "mux_processing", "Reel row keeps processing state instead of blocking publish")
    expect((reel.get("video_url") or "").startswith("https://stream.mux.com/"), "Reel row stores playable URL")

    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token in [
        "/api/pulse/media/upload",
        "/api/pulse/reels/create",
        "media_id:mediaId",
        "media_ids:[mediaId]",
        "Reel create request",
        "publishFailed=true",
        "if(!publishFailed)updateReelPublishState()",
        "Video is processing.",
    ]:
        expect(token in source, f"frontend final publish contract contains {token}")
    expect("Upload completed but no media ID was returned." in source, "upload response missing media id is explicit")
    print("pulse reels publish final-step audit ok")


if __name__ == "__main__":
    main()
