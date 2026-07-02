#!/usr/bin/env python3
"""Audit Live multi-destination setup blocks fake external starts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, multistream_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, display_name, email, email_verified, avatar_url, bio, signup_time, created_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
        ("livemultiaudit", "Roody Cherie", "coinpilotxai@gmail.com", "/static/brand/pulsesoc-logo-20260606.png", "Multistream audit creator", now, now),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.post(
        "/api/pulse/live/start",
        headers={"X-Trace-Id": "audit-multistream"},
        json={
            "title": "Multistream Audit",
            "category": "Crypto Education",
            "destinations": [{"platform": "pulse"}, {"platform": "facebook"}, {"platform": "youtube"}],
        },
    )
    data = response.get_json() or {}
    require(response.status_code == 400 and data.get("error") == "destination_setup_required", "unconnected external targets block Start Live")
    require("Facebook Live" in ",".join(data.get("setup_required") or []) and "YouTube Live" in ",".join(data.get("setup_required") or []), "setup-required platforms are named clearly")
    require({"facebook", "youtube", "twitch", "kick", "tiktok", "x_twitter", "linkedin", "custom_rtmp"}.issubset(set(multistream_service.supported_platforms())), "multistream service supports major creator platforms")
    destinations = [{"platform": "pulse", "status": "live"}, {"platform": "youtube", "status": "setup_required"}]
    require(multistream_service.health_summary(destinations)["pulse_safe"], "multistream health confirms Pulse is safe")
    require(multistream_service.health_summary(destinations)["setup_required"] == 1, "multistream health counts setup-required destinations")
    print("live multistream audit ok")


if __name__ == "__main__":
    main()
