#!/usr/bin/env python3
"""Audit Pulse Status rail, DB tables, and API scaffolding."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (940004,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (940004, "pulse_status_audit", "Pulse Status Audit", "pulse-status-audit@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    conn.commit(); conn.close()
    return 940004


def table_exists(cur, table):
    try:
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def main():
    bot.init_db()
    user_id = ensure_user()
    conn = bot.db(); cur = conn.cursor()
    for table in ["pulse_status", "pulse_statuses", "pulse_status_views", "pulse_status_reactions", "pulse_status_replies", "pulse_status_music", "pulse_status_media", "pulse_status_live"]:
        expect(table_exists(cur, table), f"{table} exists")
    conn.close()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get("/api/pulse/status/rail")
    payload = response.get_json() or {}
    expect(response.status_code == 200 and payload.get("ok") is True, "status rail API returns ok", response.get_data(as_text=True)[:300])
    empty_text = client.post("/api/pulse/status", json={"status_type": "text", "body": "   "})
    empty_text_payload = empty_text.get_json() or {}
    expect(empty_text.status_code == 400 and empty_text_payload.get("ok") is False, "empty text status validation works", empty_text.get_data(as_text=True)[:300])
    created = client.post("/api/pulse/status", json={"status_type": "text", "body": "Status audit"})
    data = created.get_json() or {}
    expect(created.status_code == 200 and data.get("ok") is True and data.get("status_id"), "status create API works", created.get_data(as_text=True)[:300])
    status_id = int(data["status_id"])
    expect((client.post(f"/api/pulse/status/{status_id}/view").get_json() or {}).get("ok") is True, "status view API works")
    expect((client.post(f"/api/pulse/status/{status_id}/react", json={"reaction_type": "fire"}).get_json() or {}).get("ok") is True, "status reaction API works")
    reply_payload = client.post(f"/api/pulse/status/{status_id}/reply", json={"body": "Reply from audit"}).get_json() or {}
    expect(reply_payload.get("ok") is True and reply_payload.get("reply", {}).get("id"), "status reply API works")
    html = client.get("/pulse").get_data(as_text=True)
    for token in [
        "pulse-status2",
        "data-pulse-status-version='entry-1'",
        "data-status2-strip",
        "pulse-status-home-entry",
        "pulse-status-tray-only",
        "href='/pulse/status'",
        "<strong>Create</strong><small>Status</small>",
    ]:
        expect(token in html, f"homepage Status entry contains {token}")
    for token in ["Stories from your Pulse world.", "Trending Status", "Quick updates, creator moments"]:
        expect(token not in html, f"homepage Status tray omits marketing token {token}")
    for token in [
        "data-status2-form",
        "data-status2-body",
        "data-status2-privacy",
        "data-status2-duration",
        "pulseStatus2Media",
        "Post Status",
    ]:
        expect(token not in html, f"homepage does not contain inline Status composer token {token}")
    status_html = client.get("/pulse/status").get_data(as_text=True)
    for token in [
        "Pulse Status",
        "Create and view stories from your Pulse world.",
        "data-status2-form",
        "data-status-create-form='dedicated'",
        "data-status2-preview",
        "data-status2-body",
        "data-status2-privacy",
        "data-status2-duration",
        "pulseStatus2Media",
        "Post Status",
        "/api/pulse/status",
        "/api/pulse/media/upload",
    ]:
        expect(token in status_html, f"dedicated Status page contains {token}")
    print("pulse status audit ok")


if __name__ == "__main__":
    main()
