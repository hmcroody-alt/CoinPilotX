#!/usr/bin/env python3
"""Verify video deletion is owner-safe and soft deletes related feed content."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///coinpilotx.db")
import bot  # noqa: E402


def client_for(user_id):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


bot.init_db()
conn = bot.db()
cur = conn.cursor()
now = datetime.now(UTC).isoformat(timespec="seconds")
for uid, name in [(985211, "delete_owner"), (985212, "delete_viewer")]:
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)", (uid, name, name, f"{name}@example.test", now))
cur.execute("INSERT INTO pulse_posts (user_id, post_type, title, body, visibility, moderation_status, created_at, updated_at) VALUES (985211, 'video', 'Delete audit', 'Delete audit', 'public', 'approved', ?, ?)", (now, now))
post_id = cur.lastrowid
cur.execute("INSERT INTO pulse_videos (owner_user_id, source_type, source_id, title, visibility, status, created_at, updated_at) VALUES (985211, 'feed_video', ?, 'Delete audit', 'public', 'active', ?, ?)", (str(post_id), now, now))
video_id = cur.lastrowid
conn.commit()
conn.close()

assert client_for(985212).delete(f"/api/pulse/videos/{video_id}").status_code == 403
print("PASS: non-owner delete blocked")
response = client_for(985211).delete(f"/api/pulse/videos/{video_id}")
assert response.status_code == 200
conn = bot.db()
cur = conn.cursor()
cur.execute("SELECT status FROM pulse_videos WHERE id=?", (video_id,))
assert cur.fetchone()[0] == "archived"
cur.execute("SELECT deleted_at FROM pulse_posts WHERE id=?", (post_id,))
assert cur.fetchone()[0]
conn.close()
print("PASS: owner soft delete hides video and related feed post")
print("pulse video delete API audit ok")
