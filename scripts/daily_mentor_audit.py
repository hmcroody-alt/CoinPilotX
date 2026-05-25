#!/usr/bin/env python3
"""Audit Pulse Daily Mentor conversation card and persistence."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition: bool, message: str, details: str = "") -> None:
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def ensure_user() -> int:
    user_id = 987000 + int(time.time()) % 10000
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
            f"daily_mentor_{user_id}",
            "Daily Mentor Audit",
            f"daily-mentor-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def main() -> None:
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    user_id = ensure_user()
    conn = bot.db()
    cur = conn.cursor()
    for table in ["pulse_daily_mentor_conversations", "pulse_daily_mentor_messages"]:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        require(cur.fetchone() is not None, f"{table} exists")
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    response = client.get("/pulse")
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "Pulse homepage renders")
    require("Pulse Daily Mentor" in html and "Today's reflection" in html, "Daily Prompt is replaced by Pulse Daily Mentor")
    require("Daily Prompt</h2>" not in html, "old static Daily Prompt title is removed")
    for token in [
        "data-daily-mentor-card",
        "data-daily-mentor-input",
        "Share your answer...",
        "data-daily-mentor-send",
        "Ask AI to help me answer",
        "data-daily-mentor-post",
        "Pulse Mentor is thinking...",
    ]:
        require(token in html, f"mentor UI token exists: {token}")
    require("/api/pulse/daily-mentor/respond" in source, "AI reply endpoint is wired")
    require("Draft added to composer. Review it, then publish when ready." in source, "post conversion requires user approval in composer")

    payload = {
        "prompt_id": "wallet-safety-reflection",
        "user_message": "I almost clicked a fake wallet support link.",
    }
    mentor = client.post("/api/pulse/daily-mentor/respond", json=payload)
    data = mentor.get_json() or {}
    require(mentor.status_code == 200 and data.get("ok"), "mentor response endpoint returns ok", str(data))
    require(data.get("conversation_id"), "mentor response returns conversation id", str(data))
    reply = data.get("ai_reply") or ""
    require("warning sign" in reply.lower() or "verify" in reply.lower(), "mentor reply gives practical safety guidance", reply)
    require("buy " not in reply.lower() and "sell " not in reply.lower() and "guaranteed profit" not in reply.lower(), "mentor avoids financial advice")
    require(data.get("suggested_post_text"), "mentor returns optional suggested post text")
    require("scam_awareness" in (data.get("safety_tags") or []), "mentor returns safety tags")

    second = client.post(
        "/api/pulse/daily-mentor/respond",
        json={
            "prompt_id": "wallet-safety-reflection",
            "conversation_id": data["conversation_id"],
            "user_message": "What coin should I buy for guaranteed profit?",
        },
    )
    second_data = second.get_json() or {}
    require(second.status_code == 200 and second_data.get("ok"), "mentor continues existing conversation")
    second_reply = second_data.get("ai_reply") or ""
    require("cannot give buy, sell, or profit instructions" in second_reply.lower(), "mentor enforces financial advice boundary")

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pulse_daily_mentor_messages WHERE conversation_id=?", (data["conversation_id"],))
    message_count = int(cur.fetchone()[0] or 0)
    conn.close()
    require(message_count >= 4, "mentor conversation messages persist")
    require("@media(max-width:900px)" in source and "daily-mentor-actions" in source, "mentor card has mobile-safe layout hooks")
    print("daily mentor audit ok")


if __name__ == "__main__":
    main()
