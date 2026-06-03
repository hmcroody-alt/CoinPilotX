#!/usr/bin/env python3
"""Audit the unified Pulse Communications Platform API."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_users() -> tuple[int, int, int, int]:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users = [
        (970101, "comm_one", "Comm One", "comm-one@example.test"),
        (970102, "comm_two", "Comm Two", "comm-two@example.test"),
        (970103, "comm_three", "Comm Three", "comm-three@example.test"),
        (970104, "comm_empty", "Comm Empty", "comm-empty@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE users SET username=?, display_name=?, email=?, account_status='active' WHERE user_id=?", (username, display_name, email, user_id))
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
                (user_id, username, display_name, email, now),
            )
    conn.commit()
    conn.close()
    return users[0][0], users[1][0], users[2][0], users[3][0]


def client_for(user_id: int):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def request_json(client, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    if method == "GET":
        response = client.get(path)
    else:
        response = client.open(path, method=method, data=json.dumps(payload or {}), content_type="application/json")
    return response.status_code, response.get_json(silent=True) or {}


def assert_ok(status: int, data: dict, label: str) -> None:
    expect(status == 200 and data.get("ok") is True, label, f"status={status} body={data}")
    expect(bool(data.get("trace_id")), f"{label} includes trace id", str(data))


def main() -> None:
    bot.init_db()
    bot.init_db()
    user_id, other_id, third_id, empty_id = ensure_users()
    client = client_for(user_id)

    status, data = request_json(client, "POST", "/api/pulse/messages/direct/open", {"target_user_id": other_id})
    assert_ok(status, data, "pulse direct opens through compatibility route")
    direct_id = str(int(data.get("conversation_id") or 0))
    status, data = request_json(client, "POST", f"/api/pulse/communications/conversations/{direct_id}/messages", {"body": "communications direct seed"})
    assert_ok(status, data, "communications direct send")
    status, data = request_json(client, "GET", f"/api/pulse/communications/conversations/{direct_id}/messages?limit=80")
    assert_ok(status, data, "communications direct messages load")
    expect((data.get("conversation") or {}).get("conversation_type") == "direct", "communications direct type", str(data))
    expect(any((m.get("body") or "") == "communications direct seed" for m in data.get("messages") or []), "communications direct body returned", str(data))

    legacy_id = bot.direct_conversation_between(user_id, third_id)
    status, data = request_json(client, "POST", f"/api/pulse/communications/conversations/legacy-{legacy_id}/messages", {"body": "communications legacy seed"})
    assert_ok(status, data, "communications legacy direct send")
    status, data = request_json(client, "GET", f"/api/pulse/communications/conversations/legacy-{legacy_id}/messages?limit=80")
    assert_ok(status, data, "communications legacy direct messages load")
    expect((data.get("conversation") or {}).get("source") == "legacy_dashboard", "legacy source declared", str(data))
    expect(any((m.get("body") or "") == "communications legacy seed" for m in data.get("messages") or []), "legacy body returned", str(data))

    status, data = request_json(client, "GET", "/api/pulse/communications/conversations?type=direct")
    assert_ok(status, data, "communications direct list loads")
    ids = {str(item.get("conversation_id") or item.get("id")) for item in data.get("items") or []}
    expect(direct_id in ids, "pulse direct appears in unified list", str(ids))
    expect(f"legacy-{legacy_id}" in ids, "legacy direct appears in unified list", str(ids))

    status, data = request_json(client, "GET", "/api/pulse/communications/rooms")
    assert_ok(status, data, "communications rooms list loads")
    rooms = data.get("rooms") or data.get("items") or []
    expect(bool(rooms), "communications rooms list has rooms", str(data))
    room_ref = str(rooms[0].get("conversation_id") or rooms[0].get("id"))
    status, data = request_json(client, "GET", f"/api/pulse/communications/conversations/{room_ref}/messages?limit=80")
    assert_ok(status, data, "communications room messages load")
    expect((data.get("conversation") or {}).get("conversation_type") == "room", "communications room type", str(data))
    room_slug = str(rooms[0].get("room_id") or rooms[0].get("key") or "general-pulse")
    status, data = request_json(client, "GET", f"/api/pulse/communications/conversations/room-{room_slug}/messages?limit=80")
    assert_ok(status, data, "communications room slug messages load")
    expect((data.get("conversation") or {}).get("conversation_type") == "room", "communications room slug type", str(data))
    status, data = request_json(client, "POST", f"/api/pulse/communications/conversations/room-{room_slug}/messages", {"body": "communications room slug seed"})
    assert_ok(status, data, "communications room slug send")

    status, data = request_json(client, "POST", "/api/pulse/messages/groups/create", {"title": "Communications Audit Group", "member_ids": [other_id, third_id]})
    assert_ok(status, data, "communications group creates through compatibility route")
    group_id = str(int(data.get("conversation_id") or (data.get("conversation") or {}).get("id") or 0))
    status, data = request_json(client, "POST", f"/api/pulse/communications/conversations/{group_id}/messages", {"body": "communications group seed"})
    assert_ok(status, data, "communications group send")
    status, data = request_json(client, "GET", "/api/pulse/communications/groups")
    assert_ok(status, data, "communications groups list loads")
    group_ids = {str(item.get("conversation_id") or item.get("id")) for item in data.get("items") or []}
    expect(group_id in group_ids, "communications group appears in list", str(data))
    status, data = request_json(client, "GET", f"/api/pulse/communications/conversations/{group_id}/messages?limit=80")
    assert_ok(status, data, "communications group messages load")
    expect((data.get("conversation") or {}).get("conversation_type") == "group", "communications group type", str(data))
    expect(any((m.get("body") or "") == "communications group seed" for m in data.get("messages") or []), "communications group body returned", str(data))

    intruder = client_for(empty_id)
    status, data = request_json(intruder, "GET", f"/api/pulse/communications/conversations/{direct_id}/messages?limit=80")
    expect(status == 403 and data.get("ok") is False, "communications 403 state", f"status={status} body={data}")
    expect("access" in (data.get("message") or "").lower(), "communications 403 copy", str(data))

    status, data = request_json(client, "GET", "/api/pulse/communications/conversations/999999999/messages?limit=80")
    expect(status == 404 and data.get("ok") is False, "communications 404 state", f"status={status} body={data}")
    expect("not found" in (data.get("message") or "").lower(), "communications 404 copy", str(data))

    status, data = request_json(client_for(empty_id), "GET", "/api/pulse/communications/conversations?type=direct")
    assert_ok(status, data, "empty communications direct list loads")
    expect(isinstance(data.get("items"), list), "empty communications list shape", str(data))

    status, data = request_json(client, "GET", "/api/pulse/comm/v2/health")
    expect(status == 200 and data.get("enabled") is True and data.get("status") == "ready", "communications v2 health ready", f"status={status} body={data}")

    status, data = request_json(client, "POST", "/api/pulse/comm/v2/direct/open", {"target_user_id": other_id})
    assert_ok(status, data, "communications v2 direct opens")
    v2_direct_id = str(data.get("conversation_id") or (data.get("conversation") or {}).get("conversation_id") or 0)

    status, data = request_json(client, "GET", "/api/pulse/comm/v2/conversations?type=direct")
    assert_ok(status, data, "communications v2 direct list loads")
    v2_ids = {str(item.get("conversation_id") or item.get("id")) for item in data.get("items") or []}
    expect(v2_direct_id in v2_ids, "communications v2 direct includes opened v2 direct", str(data))

    status, data = request_json(client, "POST", f"/api/pulse/comm/v2/conversations/{v2_direct_id}/messages", {"body": "communications v2 direct seed"})
    assert_ok(status, data, "communications v2 direct send")
    v2_message_id = int(data.get("message_id") or (data.get("message") or {}).get("id") or 0)
    expect(v2_message_id > 0, "communications v2 send returns message id", str(data))

    status, data = request_json(client, "GET", f"/api/pulse/comm/v2/conversations/{v2_direct_id}/messages?limit=80")
    assert_ok(status, data, "communications v2 direct messages load")
    expect(any((m.get("body") or "") == "communications v2 direct seed" for m in data.get("messages") or []), "communications v2 direct body returned", str(data))

    status, data = request_json(client, "GET", f"/api/pulse/comm/v2/conversations/{v2_direct_id}/members")
    assert_ok(status, data, "communications v2 members load")
    expect(len(data.get("members") or []) >= 2, "communications v2 direct has members", str(data))

    status, data = request_json(client, "POST", f"/api/pulse/comm/v2/messages/{v2_message_id}/reactions", {"reaction": "fire"})
    assert_ok(status, data, "communications v2 reaction works")

    status, data = request_json(client, "POST", f"/api/pulse/comm/v2/conversations/{v2_direct_id}/read", {})
    assert_ok(status, data, "communications v2 mark read works")

    status, data = request_json(client, "GET", "/api/pulse/comm/v2/search?q=communications%20v2%20direct%20seed")
    assert_ok(status, data, "communications v2 search works")
    expect(any((m.get("body") or "") == "communications v2 direct seed" for m in data.get("messages") or []), "communications v2 search returns seeded message", str(data))

    status, data = request_json(client, "GET", "/pulse/messages")
    expect(status == 200, "communications frontend route loads", f"status={status}")
    page_text = data if isinstance(data, str) else ""
    if not page_text:
        page_response = client.get("/pulse/messages")
        page_text = page_response.get_data(as_text=True)
    expect("Pulse Communications API" in page_text, "communications frontend logs endpoint diagnostics")
    expect("Pulse Communications messages failed" in page_text, "communications frontend logs message failures")
    expect("data-comm-room-id" in page_text, "communications frontend carries room ids")
    expect("pulse-comm-intel" in page_text, "communications frontend includes intelligence panel")
    expect("/api/pulse/comm/v2/conversations/" in page_text, "communications frontend uses v2 member intelligence endpoint")

    print("pulse communications audit ok")


if __name__ == "__main__":
    main()
