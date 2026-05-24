#!/usr/bin/env python3
"""Audit Pulse-only live survives failed external multistream destinations."""

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
        ("livemultiaudit", "Roody Cherie", "coinpilotxai@gmail.com", "/static/Coinpilot%20Logo/NewLogo.png", "Multistream audit creator", now, now),
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
            "category": "Creator QA",
            "destinations": [{"platform": "pulse"}, {"platform": "facebook"}, {"platform": "youtube"}, {"platform": "custom_rtmp"}],
            "custom_rtmp_url": "https://not-rtmp.example/live",
        },
    )
    data = response.get_json() or {}
    require(response.status_code == 200 and data.get("ok"), "Pulse Live starts even when external targets fail")
    destinations = data.get("destinations") or []
    require(any(d.get("platform") == "pulse" and d.get("status") == "live" for d in destinations), "Pulse destination remains live")
    require(any(d.get("platform") in {"facebook", "youtube", "custom_rtmp"} and d.get("status") == "failed" for d in destinations), "external destination failures are isolated")
    require({"facebook", "youtube", "twitch", "kick", "tiktok", "x_twitter", "linkedin", "custom_rtmp"}.issubset(set(multistream_service.supported_platforms())), "multistream service supports major creator platforms")
    require(multistream_service.health_summary(destinations)["pulse_safe"], "multistream health confirms Pulse is safe")
    require(data.get("live_url"), "live URL is exposed after multistream setup")
    print("live multistream audit ok")


if __name__ == "__main__":
    main()
