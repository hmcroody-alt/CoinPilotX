#!/usr/bin/env python3
"""Audit owner-only access for Pulse Labs and UNDX."""

from __future__ import annotations

import sys
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


OWNER_ID = 990101
REGULAR_ID = 990102
SECOND_ID = 990103


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def seed_users() -> None:
    bot.init_db()
    now = bot.datetime.now(UTC).isoformat(timespec="seconds")
    conn = bot.db()
    cur = conn.cursor()
    users = [
        (OWNER_ID, "owner-super-audit", "Owner Super Audit", bot.owner_email_value(), 1),
        (REGULAR_ID, "regular-super-audit", "Regular Super Audit", "regular-super-audit@example.test", 0),
        (SECOND_ID, "second-super-audit", "Second Super Audit", "second-super-audit@example.test", 0),
    ]
    for user_id, username, display_name, email, is_super in users:
        cur.execute(
            """
            INSERT INTO users (user_id, username, display_name, email, account_status, is_super_user, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,
              display_name=excluded.display_name,
              email=excluded.email,
              account_status='active',
              is_super_user=excluded.is_super_user,
              updated_at=excluded.updated_at
            """,
            (user_id, username, display_name, email, is_super, now, now),
        )
    conn.commit()
    conn.close()


def login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess.clear()
        sess["account_user_id"] = user_id
        sess["csrf_token"] = "audit-csrf"


def main() -> None:
    seed_users()
    client = bot.webhook_app.test_client()

    login(client, REGULAR_ID)
    pulse_html = client.get("/pulse").get_data(as_text=True)
    premium_html = client.get("/pulse/premium").get_data(as_text=True)
    labs_regular = client.get("/pulse/labs")
    undx_regular = client.get("/pulse/premium/undx")
    expect(labs_regular.status_code == 403, "/pulse/labs blocked for regular user", str(labs_regular.status_code))
    expect(undx_regular.status_code == 403, "/pulse/premium/undx blocked for regular user", str(undx_regular.status_code))
    expect("/pulse/labs" not in pulse_html, "Labs nav hidden for regular user")
    expect("/pulse/premium/undx" not in premium_html, "UNDX card hidden for regular user")
    for route, method, payload in [
        ("/api/undx/chat", "POST", {"message": "audit"}),
        ("/api/undx/agent-council", "GET", None),
        ("/api/undx/kernel/scan", "POST", {}),
        ("/api/undx/desktop-connector/health", "GET", None),
    ]:
        response = client.open(route, method=method, json=payload)
        expect(response.status_code == 403, f"{route} blocked for regular user", response.get_data(as_text=True)[:240])

    regular_admin_post = client.post(
        "/admin/super-users",
        data={"csrf_token": "audit-csrf", "user_id": SECOND_ID, "action": "enable"},
    )
    expect(regular_admin_post.status_code == 403, "regular user cannot assign super user", str(regular_admin_post.status_code))

    login(client, OWNER_ID)
    labs_owner = client.get("/pulse/labs")
    undx_owner = client.get("/pulse/premium/undx")
    admin_page = client.get("/admin/super-users")
    expect(labs_owner.status_code == 200, "owner can access Pulse Labs", str(labs_owner.status_code))
    expect(undx_owner.status_code == 200, "owner can access UNDX", str(undx_owner.status_code))
    expect(admin_page.status_code == 200 and "Owner Super User Access" in admin_page.get_data(as_text=True), "admin super-user backend exists")

    enable_response = client.post(
        "/admin/super-users",
        data={"csrf_token": "audit-csrf", "user_id": SECOND_ID, "action": "enable"},
    )
    expect(enable_response.status_code == 200, "owner can enable one super user", str(enable_response.status_code))
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_super_user,0)=1 AND user_id IN (?, ?, ?)", (OWNER_ID, REGULAR_ID, SECOND_ID))
    count = int((cur.fetchone() or [0])[0])
    conn.close()
    if not bot.owner_allows_multiple_super_users():
        expect(count == 1, "max one database super user enforced", str(count))
    else:
        expect(count >= 1, "multiple super users allowed by environment")

    print("super user access audit ok")


if __name__ == "__main__":
    main()
