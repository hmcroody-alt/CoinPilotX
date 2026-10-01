#!/usr/bin/env python3
"""End-to-end Pulse Groups, Messenger, and media stabilization audit."""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2&\xb5\x00\x00\x00\x00IEND\xaeB`\x82"
)


class Audit:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        self.passed += 1
        print(f"PASS\t{label}\t{detail}")

    def fail(self, label: str, detail: str) -> None:
        self.failed += 1
        self.failures.append(f"{label}: {detail}")
        print(f"FAIL\t{label}\t{detail}")

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.ok(label, detail)
        else:
            self.fail(label, detail or "condition failed")


def ensure_users() -> tuple[int, int, int, int]:
    bot.init_db()
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    users = [
        (910001, "groups_audit_owner@coinpilotx.test", "Groups Audit Owner", "groupsauditowner"),
        (910002, "groups_audit_member@coinpilotx.test", "Groups Audit Member", "groupsauditmember"),
        (910003, "groups_audit_guest@coinpilotx.test", "Groups Audit Guest", "groupsauditguest"),
    ]
    for user_id, email, display, username in users:
        cur.execute(
            """
            INSERT OR IGNORE INTO users
            (user_id, email, display_name, full_name, username, password_hash, created_at, updated_at, account_status)
            VALUES (?, ?, ?, ?, ?, 'audit', ?, ?, 'active')
            """,
            (user_id, email, display, display, username, now, now),
        )
    cur.execute(
        """
        UPDATE chat_media_uploads
        SET created_at='2000-01-01T00:00:00'
        WHERE uploader_user_id IN (?, ?, ?)
        """,
        tuple(user_id for user_id, *_ in users),
    )
    cur.execute("SELECT id FROM admin_users WHERE role IN ('owner','super_admin') AND status='active' ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row:
        admin_id = int(row["id"])
    else:
        cur.execute(
            "INSERT INTO admin_users (email, password_hash, full_name, role, status, created_at, updated_at) VALUES (?, ?, ?, 'owner', 'active', ?, ?)",
            ("groups-audit-owner@example.com", bot.generate_password_hash("not-used"), "Groups Audit Admin", now, now),
        )
        admin_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return 910001, 910002, 910003, admin_id


def login(client, user_id: int, admin_id: int | None = None) -> None:
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        if admin_id:
            sess["admin_user_id"] = admin_id


def json_post(client, route: str, payload: dict | None = None, method: str = "post"):
    if method.lower() == "get":
        response = client.get(route)
    else:
        response = getattr(client, method)(route, json=payload or {})
    data = response.get_json(silent=True) or {}
    return response, data


def require_json(audit: Audit, label: str, response, data: dict, *keys: str) -> bool:
    ok = response.status_code < 400 and data.get("ok") is True and all(key in data for key in keys)
    audit.check(label, ok, f"HTTP {response.status_code} {data}")
    return ok


def upload_media(client, route: str, field_name: str, filename: str, content: bytes, mime: str, extra: dict | None = None):
    payload = {field_name: (io.BytesIO(content), filename, mime)}
    if extra:
        payload.update(extra)
    return client.post(route, data=payload, content_type="multipart/form-data")


def db_scalar(sql: str, params: tuple = ()) -> int:
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    conn.close()
    return int((row or {"total": 0})["total"] or 0)


def main() -> int:
    audit = Audit()
    owner_id, member_id, guest_id, admin_id = ensure_users()
    app = bot.webhook_app
    owner = app.test_client()
    member = app.test_client()
    guest = app.test_client()
    admin = app.test_client()
    login(owner, owner_id)
    login(member, member_id)
    login(guest, guest_id)
    login(admin, owner_id, admin_id)

    suffix = int(time.time())
    group_name = f"Groups Full Audit {suffix}"
    response, data = json_post(owner, "/api/pulse/groups/create", {
        "name": group_name,
        "description": "Full stabilization audit group",
        "category": "QA",
        "group_type": "public",
        "rules": "Stay safe and test real flows.",
    })
    if not require_json(audit, "create group", response, data, "group_id", "slug"):
        return 1
    group_id = int(data["group_id"])
    slug = str(data["slug"])

    response, data = json_post(owner, f"/api/pulse/groups/{slug}/update", {"description": "Updated audit description", "group_type": "private"})
    require_json(audit, "edit group", response, data)

    response, data = json_post(member, f"/api/pulse/groups/{slug}/join")
    require_json(audit, "private join request", response, data)
    response, data = json_post(owner, f"/api/pulse/groups/{slug}/update", {"group_type": "public"})
    require_json(audit, "restore public group", response, data)
    response, data = json_post(member, f"/api/pulse/groups/{slug}/join")
    require_json(audit, "join group", response, data)
    response, data = json_post(guest, f"/api/pulse/groups/{slug}/join")
    require_json(audit, "second member join", response, data)
    response, data = json_post(member, f"/api/pulse/groups/{slug}/leave")
    require_json(audit, "leave group", response, data)
    response, data = json_post(member, f"/api/pulse/groups/{slug}/join")
    require_json(audit, "rejoin group", response, data)

    response, data = json_post(owner, f"/api/pulse/groups/{slug}/invite", {"invitee_user_id": guest_id})
    require_json(audit, "invite member", response, data)
    response, data = json_post(owner, f"/api/pulse/groups/{slug}/members/role", {"user_id": member_id, "role": "moderator"})
    require_json(audit, "promote moderator", response, data)
    response, data = json_post(owner, f"/api/pulse/groups/{slug}/members/role", {"user_id": member_id, "role": "member"})
    require_json(audit, "demote moderator", response, data)
    response, data = json_post(owner, f"/api/pulse/groups/{slug}/ban", {"user_id": guest_id, "reason": "audit ban"})
    require_json(audit, "ban member", response, data)
    response, data = json_post(guest, f"/api/pulse/groups/{slug}/join")
    audit.check("banned join blocked", response.status_code == 403 and data.get("ok") is False, f"HTTP {response.status_code} {data}")
    response, data = json_post(owner, f"/api/pulse/groups/{slug}/unban", {"user_id": guest_id})
    require_json(audit, "unban member", response, data)

    response = owner.post(f"/api/pulse/groups/{slug}/posts", data={"body": "Audit text post"})
    data = response.get_json(silent=True) or {}
    require_json(audit, "create text group post", response, data, "post")
    text_post_id = int((data.get("post") or {}).get("id") or 0)
    response, data = json_post(owner, f"/api/pulse/groups/posts/{text_post_id}", {"body": "Edited audit text post"}, method="patch")
    require_json(audit, "edit group post", response, data)

    response = upload_media(owner, f"/api/pulse/groups/{slug}/posts", "media", "audit.png", PNG_BYTES, "image/png", {"body": "Audit image post"})
    data = response.get_json(silent=True) or {}
    require_json(audit, "upload image group post", response, data, "post")
    image_post_id = int((data.get("post") or {}).get("id") or 0)
    image_url = str((data.get("post") or {}).get("media_url") or "")
    audit.check("image media URL generated", image_url.startswith("/static/"), image_url)
    audit.check("image media renders", owner.get(image_url).status_code == 200, image_url)

    response = upload_media(owner, f"/api/pulse/groups/{slug}/posts", "media", "audit.webm", b"\x1aE\xdf\xa3audit", "video/webm", {"body": "Audit video post"})
    data = response.get_json(silent=True) or {}
    require_json(audit, "upload video group post", response, data, "post")
    video_post_id = int((data.get("post") or {}).get("id") or 0)
    video_url = str((data.get("post") or {}).get("media_url") or "")
    audit.check("video media URL generated", video_url.startswith("/static/"), video_url)
    audit.check("video media serves", owner.get(video_url).status_code == 200, video_url)

    for label, route, payload in [
        ("react to post", f"/api/pulse/groups/posts/{image_post_id}/react", {"reaction_type": "fire"}),
        ("comment on post", f"/api/pulse/groups/posts/{image_post_id}/comments", {"body": "Audit comment"}),
        ("report post", f"/api/pulse/groups/posts/{image_post_id}/report", {"reason": "spam", "notes": "audit"}),
        ("pin post", f"/api/pulse/groups/posts/{image_post_id}/pin", {}),
    ]:
        response, data = json_post(member if "react" in label or "comment" in label or "report" in label else owner, route, payload)
        require_json(audit, label, response, data)

    response = owner.post(f"/api/pulse/groups/{slug}/chat/open", json={})
    data = response.get_json(silent=True) or {}
    require_json(audit, "open group chat", response, data, "conversation_id")
    conversation_id = int(data.get("conversation_id") or 0)
    response, data = json_post(owner, f"/api/pulse/messages/group/{conversation_id}/update", {"title": "Audit Group Chat"})
    require_json(audit, "rename group chat", response, data)
    response, data = json_post(owner, f"/api/pulse/messages/group/{conversation_id}/members", {"user_id": guest_id})
    require_json(audit, "add group chat member", response, data)

    response, data = json_post(owner, "/api/pulse/messages/send", {"conversation_id": conversation_id, "body": "Audit group text"})
    require_json(audit, "send group chat text", response, data, "message_id")
    text_message_id = int(data.get("message_id") or 0)

    response = upload_media(owner, "/api/pulse/messages/upload", "file", "chat.png", PNG_BYTES, "image/png", {"conversation_id": str(conversation_id)})
    data = response.get_json(silent=True) or {}
    require_json(audit, "upload chat image", response, data, "media_url", "message_type")
    response, data = json_post(owner, "/api/pulse/messages/send", {"conversation_id": conversation_id, "message_type": data.get("message_type"), "media_url": data.get("media_url"), "thumbnail_url": data.get("thumbnail_url"), "body": "chat image"})
    require_json(audit, "send chat image", response, data, "message_id")

    for label, filename, content, voice in [
        ("send chat gif", "chat.gif", b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;", False),
        ("send chat video", "chat.webm", b"\x1aE\xdf\xa3audit", False),
        ("send voice note", "voice.ogg", b"OggS\x00\x02audit", True),
    ]:
        mime = "audio/ogg" if voice else "video/webm" if filename.endswith(".webm") else "image/gif" if filename.endswith(".gif") else "application/octet-stream"
        response = upload_media(owner, "/api/pulse/messages/upload", "file", filename, content, mime, {"conversation_id": str(conversation_id), "voice": "1" if voice else "0"})
        data = response.get_json(silent=True) or {}
        if not require_json(audit, f"upload {label}", response, data, "media_url", "message_type"):
            continue
        audit.check(f"{label} media URL generated", bool(data.get("media_url")), str(data.get("media_url") or ""))
        response, send_data = json_post(owner, "/api/pulse/messages/send", {"conversation_id": conversation_id, "message_type": data.get("message_type"), "media_url": data.get("media_url"), "thumbnail_url": data.get("thumbnail_url"), "body": label})
        require_json(audit, label, response, send_data, "message_id")

    response, data = json_post(member, f"/api/pulse/messages/{conversation_id}/typing", {"typing": True})
    require_json(audit, "typing indicator", response, data)
    response, data = json_post(member, f"/api/pulse/messages/{conversation_id}/seen")
    require_json(audit, "seen receipt", response, data)
    response, data = json_post(member, f"/api/pulse/messages/{text_message_id}/react", {"reaction_type": "heart"})
    require_json(audit, "message reaction", response, data)
    response, data = json_post(owner, "/api/pulse/messages/search?q=Audit", {}, method="get")
    require_json(audit, "message search", response, data)
    response, data = json_post(owner, f"/api/pulse/messages/{text_message_id}/delete")
    require_json(audit, "delete message", response, data)
    response = owner.get(f"/api/pulse/messages/{conversation_id}/messages")
    data = response.get_json(silent=True) or {}
    require_json(audit, "load group chat messages", response, data, "messages")
    audit.check("deleted message hidden", all(int(m.get("id") or 0) != text_message_id for m in data.get("messages", [])), "message removed from load")

    response, data = json_post(owner, f"/api/pulse/groups/posts/{text_post_id}/delete", {"reason": "audit cleanup"})
    require_json(audit, "delete text group post", response, data)
    response, data = json_post(owner, f"/api/pulse/groups/posts/{image_post_id}/delete", {"reason": "audit cleanup"})
    require_json(audit, "delete image group post", response, data)
    audit.check("deleted post row removed", db_scalar("SELECT COUNT(*) AS total FROM pulse_group_posts WHERE id=?", (image_post_id,)) == 0)
    response, data = json_post(owner, f"/api/pulse/groups/posts/{video_post_id}/delete", {"reason": "audit cleanup"})
    require_json(audit, "delete video group post", response, data)

    response, data = json_post(owner, f"/api/pulse/groups/{slug}/delete", {"reason": "audit cleanup"})
    require_json(audit, "delete group", response, data)
    audit.check("group row removed", db_scalar("SELECT COUNT(*) AS total FROM pulse_groups WHERE id=?", (group_id,)) == 0)
    audit.check("group posts removed", db_scalar("SELECT COUNT(*) AS total FROM pulse_group_posts WHERE group_id=?", (group_id,)) == 0)
    audit.check("group chat removed", db_scalar("SELECT COUNT(*) AS total FROM pulse_conversations WHERE id=?", (conversation_id,)) == 0)
    audit.check("group route gone", owner.get(f"/pulse/groups/{slug}").status_code == 404)

    for label, route in [
        ("groups page", "/pulse/groups"),
        ("groups health", "/admin/groups-health"),
        ("messages health", "/admin/messages-health"),
    ]:
        client = admin if route.startswith("/admin") else owner
        response = client.get(route)
        audit.check(label, response.status_code == 200, f"HTTP {response.status_code}")

    print(f"SUMMARY\tPASS={audit.passed}\tFAIL={audit.failed}")
    if audit.failed:
        for failure in audit.failures:
            print(f"DETAIL\t{failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
