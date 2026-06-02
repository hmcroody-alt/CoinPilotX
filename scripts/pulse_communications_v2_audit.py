#!/usr/bin/env python3
"""End-to-end local audit for Pulse Communications 2.0."""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from pulse_communications_v2.models import table_names  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_users() -> tuple[int, int, int, int]:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    users = [
        (982001, "comm_v2_one", "Comm V2 One", "comm-v2-one@example.test"),
        (982002, "comm_v2_two", "Comm V2 Two", "comm-v2-two@example.test"),
        (982003, "comm_v2_three", "Comm V2 Three", "comm-v2-three@example.test"),
        (982004, "comm_v2_intruder", "Comm V2 Intruder", "comm-v2-intruder@example.test"),
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
    ids = [item[0] for item in users]
    placeholders = ",".join(["?"] * len(ids))
    for table, columns in {
        "comm_v2_blocks": ("blocker_user_id", "blocked_user_id"),
        "comm_v2_reports": ("reporter_user_id", "reported_user_id"),
        "comm_v2_moderation_events": ("actor_user_id", "target_user_id"),
    }.items():
        try:
            cur.execute(
                f"DELETE FROM {table} WHERE {columns[0]} IN ({placeholders}) OR {columns[1]} IN ({placeholders})",
                (*ids, *ids),
            )
        except Exception:
            pass
    cur.execute("SELECT id FROM admin_users WHERE email=? LIMIT 1", ("comm-v2-admin@example.test",))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO admin_users (full_name, email, password_hash, role, status, created_at, updated_at) VALUES (?, ?, ?, 'owner', 'active', ?, ?)",
            ("Comm V2 Admin", "comm-v2-admin@example.test", bot.generate_password_hash("not-used"), now, now),
        )
    conn.commit()
    conn.close()
    return users[0][0], users[1][0], users[2][0], users[3][0]


def client_for(user_id: int, admin: bool = False):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        if admin:
            conn = bot.db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM admin_users WHERE email=? LIMIT 1", ("comm-v2-admin@example.test",))
            session["admin_user_id"] = int(cur.fetchone()[0])
            conn.close()
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


def upload_media(client) -> int:
    response = client.post(
        "/api/pulse/media/upload",
        data={"context_type": "pulse_comm_v2", "context_id": "audit", "file": (io.BytesIO(PNG_1X1), "comm-v2.png")},
        content_type="multipart/form-data",
    )
    data = response.get_json(silent=True) or {}
    expect(response.status_code == 200 and data.get("ok"), "existing media upload endpoint accepts v2 attachment", f"status={response.status_code} body={data}")
    media_id = int((data.get("media") or {}).get("id") or 0)
    expect(media_id > 0, "media upload returns media id", str(data))
    return media_id


def main() -> None:
    user_id, other_id, third_id, intruder_id = ensure_users()
    client = client_for(user_id)

    status, data = request_json(client, "GET", "/api/pulse/communications/v2/health")
    expect(status == 200 and data == {"enabled": True, "status": "ready"}, "v2 health ready when audit enables flag", str(data))
    expect(len(table_names()) == 12, "v2 schema table count")

    status, data = request_json(client, "POST", "/api/pulse/communications/v2/direct/open", {"target_user_id": other_id})
    assert_ok(status, data, "create DM")
    direct_id = int(data["conversation_id"])
    status, data = request_json(client, "POST", f"/api/pulse/communications/v2/conversations/{direct_id}/messages", {"body": "v2 dm audit"})
    assert_ok(status, data, "send DM")
    dm_message_id = int(data["message_id"])

    status, data = request_json(client, "POST", "/api/pulse/communications/v2/groups", {"title": "V2 Audit Group", "member_ids": [other_id, third_id]})
    assert_ok(status, data, "create group")
    group_id = int(data["conversation_id"])
    status, data = request_json(client, "POST", f"/api/pulse/communications/v2/conversations/{group_id}/messages", {"body": "v2 group audit"})
    assert_ok(status, data, "send group message")

    status, data = request_json(client, "POST", "/api/pulse/communications/v2/rooms", {"title": "V2 Public Audit Room", "privacy": "public"})
    assert_ok(status, data, "create public room")
    room_id = int(data["conversation_id"])
    status, data = request_json(client, "POST", f"/api/pulse/communications/v2/conversations/{room_id}/messages", {"body": "v2 room audit"})
    assert_ok(status, data, "send room message")

    status, data = request_json(client, "POST", "/api/pulse/communications/v2/rooms", {"title": "V2 Private Audit Room", "privacy": "private"})
    assert_ok(status, data, "create private room")
    private_room_id = int(data["conversation_id"])

    status, data = request_json(client, "POST", "/api/pulse/communications/v2/communities", {"name": "V2 Audit Community"})
    assert_ok(status, data, "create community")
    community_id = int((data.get("community") or {}).get("id") or 0)
    status, data = request_json(client, "POST", f"/api/pulse/communications/v2/communities/{community_id}/channels", {"name": "audit-channel"})
    assert_ok(status, data, "create community channel")

    media_id = upload_media(client)
    status, data = request_json(client, "POST", f"/api/pulse/communications/v2/conversations/{direct_id}/messages", {"body": "v2 media audit", "media_ids": [media_id]})
    assert_ok(status, data, "send media message")
    expect(bool((data.get("message") or {}).get("attachments")), "media message includes attachment", str(data))

    status, data = request_json(client, "GET", f"/api/pulse/communications/v2/conversations/{direct_id}/messages?limit=80")
    assert_ok(status, data, "load history")
    expect(any((m.get("body") or "") == "v2 dm audit" for m in data.get("messages") or []), "DM history contains sent message", str(data))
    expect(any((m.get("body") or "") == "v2 media audit" and m.get("attachments") for m in data.get("messages") or []), "media history persists", str(data))

    reloaded = client_for(user_id)
    status, data = request_json(reloaded, "GET", f"/api/pulse/communications/v2/conversations/{direct_id}/messages?limit=80")
    assert_ok(status, data, "reload page data persists")
    expect(any((m.get("body") or "") == "v2 dm audit" for m in data.get("messages") or []), "reloaded history contains DM", str(data))

    intruder = client_for(intruder_id)
    status, data = request_json(intruder, "GET", f"/api/pulse/communications/v2/conversations/{direct_id}/messages?limit=80")
    expect(status == 403 and data.get("ok") is False, "DM permissions enforced", f"status={status} body={data}")
    status, data = request_json(intruder, "GET", f"/api/pulse/communications/v2/conversations/{private_room_id}/messages?limit=80")
    expect(status == 403 and data.get("ok") is False, "private room permissions enforced", f"status={status} body={data}")
    status, data = request_json(intruder, "POST", f"/api/pulse/communications/v2/conversations/{room_id}/messages", {"body": "public room join audit"})
    assert_ok(status, data, "public room permits send with auto-join")

    other = client_for(other_id)
    status, data = request_json(other, "POST", f"/api/pulse/communications/v2/conversations/{direct_id}/typing", {"is_typing": True})
    assert_ok(status, data, "typing indicator set")
    status, data = request_json(client, "GET", f"/api/pulse/communications/v2/conversations/{direct_id}/presence")
    assert_ok(status, data, "typing indicator loads")
    expect(any(int(t.get("user_id") or 0) == other_id for t in data.get("typing") or []), "typing state includes other user", str(data))

    status, data = request_json(other, "POST", f"/api/pulse/communications/v2/conversations/{direct_id}/read", {})
    assert_ok(status, data, "read receipt records")
    expect(int(data.get("last_read_message_id") or 0) >= dm_message_id, "read receipt advances", str(data))

    status, data = request_json(client, "POST", f"/api/pulse/communications/v2/messages/{dm_message_id}/reactions", {"reaction": "fire"})
    assert_ok(status, data, "message reaction works")
    expect((data.get("message") or {}).get("my_reaction") == "fire", "reaction echoed", str(data))

    status, data = request_json(client, "POST", f"/api/pulse/communications/v2/messages/{dm_message_id}/report", {"reason": "audit report"})
    assert_ok(status, data, "report safety works")
    status, data = request_json(client, "POST", "/api/pulse/communications/v2/blocks", {"blocked_user_id": third_id, "reason": "audit block"})
    assert_ok(status, data, "block safety works")

    admin = client_for(user_id, admin=True)
    status, data = request_json(admin, "GET", "/api/pulse/communications/v2/moderation")
    assert_ok(status, data, "admin moderation summary works")
    expect((data.get("moderation") or {}).get("open_reports", 0) >= 1, "moderation summary sees report", str(data))
    status, data = request_json(admin, "POST", f"/api/pulse/communications/v2/moderation/messages/{dm_message_id}", {"action": "approve", "reason": "audit approve"})
    assert_ok(status, data, "admin moderation action works")

    page = client.get("/pulse/messages-v2")
    text = page.get_data(as_text=True)
    expect(page.status_code == 200, "messages-v2 page loads", f"status={page.status_code}")
    expect("data-comm-v2-enabled=\"true\"" in text, "messages-v2 page reflects enabled audit flag")
    expect("/static/js/pulse_messages_v2.js" in text, "messages-v2 JavaScript included")

    print("pulse communications v2 audit ok")


if __name__ == "__main__":
    main()
