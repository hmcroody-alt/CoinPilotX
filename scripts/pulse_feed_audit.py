#!/usr/bin/env python3
"""Audit core Pulse feed rendering, APIs, and canonical visibility."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import pulse_feed_engine  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (940002,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (940002, "pulse_feed_audit", "Pulse Feed Audit", "pulse-feed-audit@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    conn.commit(); conn.close()
    return 940002


def main():
    bot.init_db()
    user_id = ensure_user()
    feed = pulse_feed_engine.list_feed(user_id, "for_you", limit=5, offset=0)
    expect(feed.get("ok") is True, "service feed returns ok")
    expect(isinstance(feed.get("posts"), list), "service feed returns post list")
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get("/api/pulse/feed?limit=5")
    payload = response.get_json() or {}
    expect(response.status_code == 200 and payload.get("ok") is True, "feed API returns ok", response.get_data(as_text=True)[:300])
    expect("intelligence" in payload, "feed API returns intelligence panel")
    core_html = client.get("/pulse").get_data(as_text=True)
    expect("pulse_home_core.js" in core_html, "default feed uses the compact core controller")
    html = client.get("/pulse?boot_profile=normal").get_data(as_text=True)
    for token in ["newPulsesBanner", "visiblePosts", "rememberDeletedPost", "pulse-status-rail", "postHtml"]:
        expect(token in html, f"feed client contains {token}")
    print("pulse feed audit ok")


if __name__ == "__main__":
    main()
