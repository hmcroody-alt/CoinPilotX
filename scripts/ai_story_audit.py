#!/usr/bin/env python3
"""Audit Pulse AI Story generation, preview metadata, and publish path."""

from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import ai_story_service  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def ensure_user() -> int:
    user_id = 972000 + int(time.time()) % 10000
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
            f"ai_story_audit_{user_id}",
            "AI Story Audit",
            f"ai-story-audit-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def main():
    bot.init_db()
    story = ai_story_service.generate_story("Create a cyberpunk sunset over Haiti with floating crypto symbols", style="cyberpunk")
    require(story.get("ok") is True and story.get("status_type") == "ai", "AI Story service generates status-ready story")
    require((story.get("visual") or {}).get("background") and story.get("caption"), "AI Story includes preview visual and caption")
    require("Haiti" in story.get("tags", []), "AI Story preserves useful prompt context")

    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    generated = client.post("/api/pulse/status/ai-story", json={"prompt": story["prompt"], "style": "cyberpunk"})
    payload = generated.get_json() or {}
    require(generated.status_code == 200 and payload.get("ok") and (payload.get("story") or {}).get("visual"), "AI Story endpoint returns preview story", generated.get_data(as_text=True)[:300])
    created = client.post(
        "/api/pulse/status",
        json={
            "status_type": "ai",
            "body": payload["story"]["caption"],
            "visibility": "public",
            "ai_context": payload["story"],
        },
    )
    created_payload = created.get_json() or {}
    require(created.status_code == 200 and created_payload.get("ok"), "AI Story publishes as Pulse Status", created.get_data(as_text=True)[:300])
    require((created_payload.get("status") or {}).get("status_type") == "ai", "published status remains AI typed")
    print("ai story audit ok")


if __name__ == "__main__":
    main()
