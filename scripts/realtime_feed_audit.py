#!/usr/bin/env python3
"""Audit realtime homepage surfaces: Live Now, SSE fallback, and feed discovery contracts."""

from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import live_ranking_engine  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def ensure_user() -> int:
    user_id = 973000 + int(time.time()) % 10000
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            f"realtime_audit_{user_id}",
            "Realtime Audit",
            f"realtime-audit-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def main():
    bot.init_db()
    ranked = live_ranking_engine.ranked_live_cards(
        [
            {"id": 1, "viewer_count": 3, "reaction_count": 2, "chat_count": 1, "creator_trust": 75},
            {"id": 2, "viewer_count": 30, "reaction_count": 14, "chat_count": 9, "creator_trust": 80},
        ]
    )
    require(ranked[0]["id"] == 2 and ranked[0].get("ai_rating"), "live ranking prioritizes active streams")
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    html = client.get("/pulse").get_data(as_text=True)
    require("data-live-now-hub" in html and "Realtime Pulse Discovery" in html, "homepage renders Live Now discovery hub")
    require("pulse_environment_engine.js" in html, "homepage loads ambient environment engine")
    live_now = client.get("/api/pulse/live-now")
    live_payload = live_now.get_json() or {}
    require(live_now.status_code == 200 and live_payload.get("ok") and isinstance(live_payload.get("items"), list), "Live Now endpoint returns stable JSON", live_now.get_data(as_text=True)[:300])
    live_events = client.get("/api/pulse/live?since_id=0")
    require(live_events.status_code == 200 and (live_events.get_json() or {}).get("ok"), "realtime event polling endpoint returns ok")
    source = (ROOT / "bot.py").read_text()
    require("EventSource(`/api/pulse/live/stream" in source and "setInterval(()=>{if(!document.hidden)pollLive()}" in source, "SSE has polling fallback for realtime feed")
    print("realtime feed audit ok")


if __name__ == "__main__":
    main()
