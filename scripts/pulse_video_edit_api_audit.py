#!/usr/bin/env python3
"""Verify video owners can edit metadata and non-owners cannot."""

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


def seed():
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for uid, name in [(985201, "video_owner"), (985202, "video_viewer")]:
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
        if not cur.fetchone():
            cur.execute("INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)", (uid, name, name, f"{name}@example.test", now))
    cur.execute("DELETE FROM pulse_videos WHERE source_type='audit_edit'")
    cur.execute("INSERT INTO pulse_videos (owner_user_id, source_type, source_id, title, description, visibility, status, created_at, updated_at) VALUES (985201, 'audit_edit', '1', 'Before', 'Before', 'public', 'active', ?, ?)", (now, now))
    video_id = cur.lastrowid
    conn.commit()
    conn.close()
    return video_id


video_id = seed()
response = client_for(985201).patch(f"/api/pulse/videos/{video_id}", json={"title": "After", "description": "Updated", "tags": "#pulse", "category": "Education", "visibility": "private"})
data = response.get_json() or {}
assert response.status_code == 200 and data.get("video", {}).get("title") == "After"
assert data["video"]["tags"] == "#pulse" and data["video"]["category"] == "Education"
print("PASS: owner edits video metadata")
response = client_for(985202).patch(f"/api/pulse/videos/{video_id}", json={"title": "Nope"})
assert response.status_code == 403
print("PASS: non-owner edit blocked")
print("pulse video edit API audit ok")
