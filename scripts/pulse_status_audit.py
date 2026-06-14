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


def ensure_other_user():
    conn = bot.db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (940005,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete, avatar_url) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (940005, "pulse_status_public", "Pulse Public Status", "pulse-status-public@example.test", bot.datetime.utcnow().isoformat(timespec="seconds"), "/static/brand/pulsesoc-logo-20260606.png"),
        )
    conn.commit(); conn.close()
    return 940005


def table_exists(cur, table):
    try:
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def main():
    bot.init_db()
    user_id = ensure_user()
    other_user_id = ensure_other_user()
    conn = bot.db(); cur = conn.cursor()
    for table in ["pulse_status", "pulse_statuses", "pulse_status_views", "pulse_status_reactions", "pulse_status_replies", "pulse_status_music", "pulse_status_media", "pulse_status_live"]:
        expect(table_exists(cur, table), f"{table} exists")
    conn.close()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get("/api/pulse/status/rail?lane=for_you")
    payload = response.get_json() or {}
    expect(response.status_code == 200 and payload.get("ok") is True, "status rail API returns ok", response.get_data(as_text=True)[:300])
    expect(payload.get("lanes") == ["for_you", "following", "trending", "global"], "status rail exposes mobile discovery lanes")
    empty_text = client.post("/api/pulse/status", json={"status_type": "text", "body": "   "})
    empty_text_payload = empty_text.get_json() or {}
    expect(empty_text.status_code == 400 and empty_text_payload.get("ok") is False, "empty text status validation works", empty_text.get_data(as_text=True)[:300])
    created = client.post("/api/pulse/status", json={"status_type": "text", "body": "Status audit"})
    data = created.get_json() or {}
    expect(created.status_code == 200 and data.get("ok") is True and data.get("status_id"), "status create API works", created.get_data(as_text=True)[:300])
    status_id = int(data["status_id"])
    first_view = client.post(f"/api/pulse/status/{status_id}/view").get_json() or {}
    second_view = client.post(f"/api/pulse/status/{status_id}/view").get_json() or {}
    expect(first_view.get("ok") is True and second_view.get("ok") is True, "status view API works")
    expect(first_view.get("view_count") == second_view.get("view_count"), "status view API deduplicates repeat viewer")
    first_reaction = client.post(f"/api/pulse/status/{status_id}/react", json={"reaction_type": "fire"}).get_json() or {}
    second_reaction = client.post(f"/api/pulse/status/{status_id}/react", json={"reaction_type": "love"}).get_json() or {}
    expect(first_reaction.get("ok") is True and second_reaction.get("ok") is True, "status reaction API works")
    expect(second_reaction.get("reaction_count") == first_reaction.get("reaction_count"), "status reaction API changes reaction without duplicate count")
    reply_payload = client.post(f"/api/pulse/status/{status_id}/reply", json={"body": "Reply from audit"}).get_json() or {}
    expect(reply_payload.get("ok") is True and reply_payload.get("reply", {}).get("id"), "status reply API works")
    now = bot.datetime.utcnow()
    conn = bot.db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO pulse_status (user_id, status_type, body, visibility, created_at, expires_at) VALUES (?, 'text', ?, 'public', ?, ?)",
        (other_user_id, "Other public status audit", now.isoformat(timespec="seconds"), (now + bot.timedelta(hours=24)).isoformat(timespec="seconds")),
    )
    other_public_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO pulse_status (user_id, status_type, body, visibility, created_at, expires_at) VALUES (?, 'text', ?, 'private', ?, ?)",
        (other_user_id, "Other private status audit", now.isoformat(timespec="seconds"), (now + bot.timedelta(hours=24)).isoformat(timespec="seconds")),
    )
    other_private_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO pulse_status (user_id, status_type, body, visibility, created_at, expires_at) VALUES (?, 'text', ?, 'public', ?, ?)",
        (other_user_id, "Expired status audit", (now - bot.timedelta(hours=48)).isoformat(timespec="seconds"), (now - bot.timedelta(hours=24)).isoformat(timespec="seconds")),
    )
    expired_id = int(cur.lastrowid)
    conn.commit(); conn.close()
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
    expect(f'data-open-status-id="{status_id}"' in html or f"data-open-status-id='{status_id}'" in html, "homepage server-renders created status in rail")
    expect("data-status-empty hidden" in html or "data-status-empty hidden=" in html, "homepage hides empty state when statuses exist")
    expect("pulse-status-avatar-ring" in html and ("<img src=" in html or "Pulse Status Audit" in html), "homepage status rail renders avatar ring")
    status_html = client.get("/pulse/status").get_data(as_text=True)
    for token in [
        "PulseSoc Status",
        "pulse-status-story-row",
        "data-status-open-create",
        "pulse-status-create-sheet",
        "pulse-status-floating-create",
        "data-status-full-tab='for_you'",
        "data-status-full-tab='following'",
        "data-status-full-tab='trending'",
        "data-status-full-tab='global'",
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
        "author_avatar_url",
        "view_count",
        "reaction_count",
        "reply_count",
        "data-status-viewer-react",
        "pulse-status-action-icon",
        "pointerdown",
        "touchstart",
    ]:
        expect(token in status_html, f"dedicated Status page contains {token}")
    for token in [
        "PulseSoc Social Ecosystem",
        "Create and view stories from your Pulse world.",
        "data-status-full-tab='ai_picks'",
        "data-status-full-tab='music'",
        "data-status-full-tab='live'",
        "10-second",
        "rewind",
    ]:
        expect(token not in status_html, f"dedicated mobile Status page omits {token}")
    rail_after = client.get("/api/pulse/status/rail?lane=for_you").get_json() or {}
    rail_ids = {int(item.get("id") or 0) for item in rail_after.get("items", [])}
    expect(status_id in rail_ids, "status API returns current user's active status")
    expect(other_public_id in rail_ids, "status API returns other user's public active status")
    expect(other_private_id not in rail_ids, "status API excludes another user's private status")
    expect(expired_id not in rail_ids, "status API excludes expired status")
    status_item = next((item for item in rail_after.get("items", []) if int(item.get("id") or 0) == status_id), {})
    for field in ["author_avatar_url", "view_count", "reaction_count", "reply_count"]:
        expect(field in status_item, f"status rail item includes {field}")
    print("pulse status audit ok")


if __name__ == "__main__":
    main()
