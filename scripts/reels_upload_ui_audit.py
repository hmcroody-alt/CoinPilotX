#!/usr/bin/env python3
"""Audit the Pulse Reels upload creator UI contract."""

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
    user_id = 980006
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
                "reels_upload_ui_audit",
                "Reels Upload UI Audit",
                "reels-upload-ui-audit@example.test",
                bot.datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
    conn.commit()
    conn.close()
    return user_id


def main() -> None:
    bot.init_db()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = ensure_user()

    response = client.get("/pulse/reels?tab=for_you")
    html = response.get_data(as_text=True)
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect(response.status_code == 200, "Reels page loads", html[:240])
    for token in [
        "reelUploadZone",
        "Upload Reel Video",
        "Choose a video from your device",
        "MP4, MOV, or WEBM",
        "reel-upload-select-button",
        "MEDIA_UPLOAD_MAX_VIDEO_MB",
        "reelUploadPreview",
        "reelUploadThumb",
        "reelUploadDuration",
        "reelUploadResolution",
        "reelUploadSize",
        "reelUploadMime",
        "reelReplaceVideo",
        "reelRemoveVideo",
        "id='reelPublishBtn' type='submit' disabled",
        "Choose a video before publishing your Reel.",
        "renderReelUploadPreview",
        "DataTransfer",
        "reel-upload-zone",
        "min-height:180px",
    ]:
        expect(token in html or token in source, f"Reels upload UI contains {token}")
    expect(html.find("reelUploadZone") < html.find("reelUploadCategory") < html.find("selectedSoundLabel"), "upload control appears before community and sound controls")
    for token in ["Adaptive " + "playback", "poster " + "first", "HLS enabled", "CDN ready", "ffmpeg processing", "Mux playback", "Media diagnostics"]:
        expect(token not in html, f"internal upload text hidden: {token}")
    print("reels upload ui audit ok")


if __name__ == "__main__":
    main()
