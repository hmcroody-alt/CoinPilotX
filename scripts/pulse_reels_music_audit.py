#!/usr/bin/env python3
"""Audit Pulse Reels music safety wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token, label in [
        ("/api/pulse/reels/sounds", "Reels sound picker API exists"),
        ("COALESCE(at.approved_by_admin,0)=1", "Reels picker requires admin approval"),
        ("COALESCE(at.commercial_use_allowed,0)=1", "Reels picker requires commercial rights"),
        ("COALESCE(at.remix_edit_allowed,0)=1", "Reels picker requires edit rights"),
        ("pulse_attach_music_to_content(cur, content_type=\"reel\"", "Reels snapshot approved license attachment"),
        ("user_upload_pending_rights", "uploaded sounds stay pending until reviewed"),
        ("ON CONFLICT(reel_id, audio_track_id) DO UPDATE", "Reel music attachment upsert is PostgreSQL compatible"),
        ("PULSE_REELS_PAYLOAD_NONCRITICAL_FAILED", "Reel response rendering failures are noncritical after DB create"),
    ]:
        require(token in source, label)
    require("INSERT OR REPLACE INTO pulse_reel_audio" not in source, "Reel music attachment avoids SQLite-only INSERT OR REPLACE")
    print("pulse reels music audit ok")


if __name__ == "__main__":
    main()
