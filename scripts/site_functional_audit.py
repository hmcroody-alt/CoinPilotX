#!/usr/bin/env python3
"""Safe local website/PWA functional audit.

This script uses Flask's test client. It does not call payment providers,
send external messages, or require public network access.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def ensure_smoke_accounts():
    bot.init_db()
    now = bot.datetime.now(UTC).isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    if row:
        user_id = int(row["user_id"])
    else:
        cur.execute(
            "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
            ("audituser", "Audit User", "audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute("SELECT id FROM admin_users WHERE role IN ('owner','super_admin') AND status='active' ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END LIMIT 1")
    row = cur.fetchone()
    if row:
        admin_id = int(row["id"])
    else:
        cur.execute(
            "INSERT INTO admin_users (email, password_hash, full_name, role, status, created_at, updated_at) VALUES (?, ?, ?, 'owner', 'active', ?, ?)",
            ("audit-owner@example.com", bot.generate_password_hash("not-used"), "Audit Owner", now, now),
        )
        admin_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id, admin_id


def emit(status, route, reason, fix=""):
    print(f"{status}\t{route}\t{reason}\t{fix}")


def main():
    user_id, admin_id = ensure_smoke_accounts()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        sess["admin_user_id"] = admin_id

    routes = [
        "/pulse",
        "/pulse/create",
        "/pulse/my-posts",
        "/pulse/reels",
        "/pulse/friends",
        "/pulse/labs",
        "/pulse/messages",
        "/pulse/notifications",
        "/pulse/profile",
        "/pulse/profile/edit",
        "/pulse/groups",
        "/pulse/groups/create",
        "/pulse/spaces",
        "/pulse/teachers",
        "/pulse/marketplace",
        "/pulse/merchant/apply",
        "/pulse/merchant/dashboard",
        "/pulse/marketplace/create",
        "/pulse/creator-monetization",
        "/pulse/live",
        "/pulse/assistant",
        "/pulse/camera",
        "/pulse/premium",
        "/pulse/premium/undx",
        "/admin/command-center",
        "/admin/global-command",
        "/admin/capability-matrix",
        "/admin/reliability",
        "/admin/pulse-users",
        "/admin/realtime-grid",
        "/admin/intelligence-graph",
        "/admin/trust-map",
        "/admin/global-events",
        "/admin/monetization",
        "/admin/merchant-applications",
        "/admin/marketplace-command",
        "/admin/spaces-command",
        "/admin/media-studio",
        "/admin/groups-health",
        "/admin/group-chat-health",
        "/admin/messages-health",
        "/admin/reels-health",
        "/admin/performance",
        "/admin/system-audit",
    ]
    failed = 0
    for route in routes:
        try:
            res = client.get(route)
            body = res.get_data(as_text=True)
            if res.status_code >= 500:
                failed += 1
                emit("FAIL", route, f"HTTP {res.status_code}", "Inspect server exception and route dependencies")
            elif "Traceback" in body or "Internal Server Error" in body:
                failed += 1
                emit("FAIL", route, "raw error content detected", "Wrap with safe error card")
            elif res.status_code >= 400:
                emit("WARN", route, f"HTTP {res.status_code}", "Check auth/permission expectations")
            else:
                emit("PASS", route, f"HTTP {res.status_code}")
        except Exception as exc:
            failed += 1
            emit("FAIL", route, str(exc), "Route raised in test client")

    api_checks = [
        ("POST", "/api/pulse/groups/create", {"name": "Audit Group", "description": "Safe audit group", "group_type": "public", "category": "Community"}),
        ("POST", "/api/pulse/groups/join", {"group_slug": "audit-group"}),
        ("POST", "/api/pulse/marketplace/listings/create", {"title": "Audit Listing", "short_description": "Audit only", "category": "Education", "price": "10"}),
    ]
    for method, route, payload in api_checks:
        res = client.open(route, method=method, json=payload)
        content_type = res.headers.get("Content-Type", "")
        text = res.get_data(as_text=True)
        try:
            data = json.loads(text)
        except Exception:
            failed += 1
            emit("FAIL", route, "API did not return JSON", "Return {ok,message,trace_id} JSON")
            continue
        if "application/json" not in content_type:
            emit("WARN", route, f"unexpected content-type {content_type}", "Set JSON content type")
        if res.status_code >= 500 or data.get("ok") is False and not data.get("message"):
            failed += 1
            emit("FAIL", route, f"HTTP {res.status_code} {data}", "Return readable JSON error with trace_id")
        else:
            emit("PASS", route, f"HTTP {res.status_code} ok={data.get('ok')}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
