#!/usr/bin/env python3
"""Audit PulseSoc Live category, destination, and host-control setup."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, live_destination_service, multistream_service  # noqa: E402


BOT_SOURCE = (ROOT / "bot.py").read_text()
RUNTIME_SOURCE = (ROOT / "static/js/pulse_live_studio_runtime.js").read_text()
CSS_SOURCE = (ROOT / "static/css/pulse_live_studio.css").read_text()


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def source_block(start_token: str, end_token: str) -> str:
    start = BOT_SOURCE.find(start_token)
    require(start >= 0, f"{start_token} exists")
    end = BOT_SOURCE.find(end_token, start)
    require(end > start, f"{end_token} follows {start_token}")
    return BOT_SOURCE[start:end]


def make_user() -> int:
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    email = f"live-destination-audit-{bot.secrets.token_hex(5)}@example.com"
    cur.execute(
        """
        INSERT INTO users (username, display_name, email, email_verified, signup_time, created_at)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (f"livedestaudit{bot.secrets.token_hex(3)}", "Live Destination Audit", email, now, now),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id


def post_start(client, payload):
    response = client.post("/api/pulse/live/start", headers={"X-Trace-Id": "audit-live-destination"}, json=payload)
    return response.status_code, response.get_json() or {}


def main():
    bot.init_db()
    live_setup_block = source_block("def pulse_live_page():", "@webhook_app.route(\"/pulse/live/studio/<int:stream_id>\"")
    live_start_block = source_block("@webhook_app.route(\"/api/pulse/live/start\"", "@webhook_app.route(\"/api/pulse/live/mux/create\"")
    studio_block = source_block("@webhook_app.route(\"/pulse/live/studio/<int:stream_id>\"", "@webhook_app.route(\"/api/pulse/live/start\"")

    require('"Other"' in live_setup_block and "liveCustomCategory" in live_setup_block, "Other category and custom category input exist")
    require("Custom category is required when Other is selected." in live_start_block, "server validates custom category for Other")
    require("custom_category" in live_start_block and "custom_category" in BOT_SOURCE, "custom category is persisted safely")

    platforms = {"pulse", "facebook", "youtube", "twitch", "kick", "tiktok", "x_twitter", "linkedin", "custom_rtmp"}
    require(platforms.issubset(set(multistream_service.supported_platforms())), "destination model includes all requested platforms")
    for platform in platforms:
        require(f'"{platform}"' in BOT_SOURCE or f"'{platform}'" in BOT_SOURCE, f"{platform} appears in destination setup")
    require("live-destination-card" in live_setup_block and "data-live-destination" in live_setup_block, "destination UX uses cards/toggles")
    require("destination_setup_required" in live_start_block, "external destinations require connection before Start Live")
    require("PULSE_LIVE_RESTREAM_ENABLED" in (ROOT / "services/live_destination_service.py").read_text(), "Custom RTMP restream flag exists")
    success_response_start = live_start_block.find('"ok": True')
    success_response = live_start_block[success_response_start:] if success_response_start >= 0 else ""
    require("custom_stream_key" not in success_response, "custom RTMP key is not returned in start response")

    valid, reason = live_destination_service.validate_rtmp_url("rtmps://example.com/live")
    require(valid and not reason, "valid RTMPS URL accepted")
    for bad in ("https://example.com/live", "javascript:alert(1)", "data:text/plain,abc"):
        invalid, invalid_reason = live_destination_service.validate_rtmp_url(bad)
        require(not invalid and invalid_reason, f"invalid RTMP URL rejected: {bad.split(':', 1)[0]}")

    require("Health Connection Issue" not in studio_block and "FPS 0 FPS" not in studio_block and "Bitrate 0 kbps" not in studio_block, "debug health text is not rendered on the video surface")
    require("data-live-stream-health" in studio_block and "live-settings-health-card" in CSS_SOURCE, "stream health lives in settings")
    for token in ("data-live-speaker", "data-live-flip", "data-live-effects", "data-live-unavailable"):
        require(token in studio_block and token in RUNTIME_SOURCE, f"{token} control is rendered and wired")
    require("Screen share is not supported on this device." in RUNTIME_SOURCE, "screen share has unsupported-device fallback")
    require("Effects coming soon" in RUNTIME_SOURCE, "effects uses safe unavailable feedback")
    require("data-live-runtime-toast" in RUNTIME_SOURCE and "live-runtime-toast" in CSS_SOURCE, "unavailable controls have visible fallback feedback")
    require("Waiting" in RUNTIME_SOURCE and "Camera ready" in RUNTIME_SOURCE and "Mic ready" in RUNTIME_SOURCE, "pre-live health language is creator-friendly")
    require('href="#"' not in studio_block and "javascript:void(0)" not in studio_block, "Live Studio has no dead href actions")

    user_id = make_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    status, data = post_start(client, {"title": "Other Audit", "category": "Other", "destinations": [{"platform": "pulse"}]})
    require(status == 400 and data.get("error") == "custom_category_required", "Other category is blocked until custom category is provided")

    status, data = post_start(client, {"title": "External Audit", "category": "Crypto Education", "destinations": [{"platform": "pulse"}, {"platform": "youtube"}]})
    require(status == 400 and data.get("error") == "destination_setup_required", "unconnected external destination is blocked")

    old_flag = os.environ.get("PULSE_LIVE_RESTREAM_ENABLED")
    os.environ["PULSE_LIVE_RESTREAM_ENABLED"] = "0"
    try:
        status, data = post_start(
            client,
            {
                "title": "Custom RTMP Audit",
                "category": "Crypto Education",
                "destinations": [{"platform": "pulse"}, {"platform": "custom_rtmp"}],
                "custom_rtmp_url": "rtmps://example.com/live",
                "custom_stream_key": "secret-key",
            },
        )
        require(status == 400 and data.get("error") == "custom_rtmp_setup_required", "Custom RTMP is safely blocked when restreaming is disabled")
    finally:
        if old_flag is None:
            os.environ.pop("PULSE_LIVE_RESTREAM_ENABLED", None)
        else:
            os.environ["PULSE_LIVE_RESTREAM_ENABLED"] = old_flag

    print("live destination setup audit ok")


if __name__ == "__main__":
    main()
