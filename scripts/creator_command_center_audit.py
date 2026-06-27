#!/usr/bin/env python3
"""Audit the PulseSoc user/admin Creator Command Center routes."""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def configure_env() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="pulsesoc-creator-command-"))
    db_path = temp_dir / "audit.db"
    os.environ.setdefault("COINPILOTX_DISABLE_LOCAL_ENV", "1")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.setdefault("SECRET_KEY", "creator-command-audit-secret")
    os.environ.setdefault("SESSION_SECRET", "creator-command-audit-session")
    os.environ.setdefault("COMMAND_CENTER_ENABLED", "false")
    os.environ.setdefault("PULSE_AI_ENABLED", "false")
    return db_path


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row[1]) for row in rows}
    except Exception:
        return set()


def insert_if_possible(conn: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    cols = table_columns(conn, table)
    usable = {key: value for key, value in values.items() if key in cols}
    if not usable:
        return
    placeholders = ",".join("?" for _ in usable)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(usable.keys())}) VALUES ({placeholders})",
        tuple(usable.values()),
    )


def seed_data(bot_module) -> None:
    bot_module.init_db()
    conn = bot_module.db()
    conn.row_factory = sqlite3.Row
    now = "2026-06-27T00:00:00"
    conn.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (9101, "creatorqa", "Creator QA", "creatorqa@example.com", now, 1, 1, 0),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO admin_users
        (id, full_name, email, password_hash, role, status, created_at, updated_at, must_change_password, failed_login_count)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (1, "Creator Admin", "creator-admin@example.com", "audit-only", "owner", "active", now, now, 0, 0),
    )
    insert_if_possible(
        conn,
        "pulse_posts",
        {
            "id": 8101,
            "user_id": 9101,
            "title": "Creator QA Post",
            "body": "Owner-scoped post",
            "visibility": "public",
            "moderation_status": "approved",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        },
    )
    insert_if_possible(
        conn,
        "pulse_reels",
        {
            "id": 8201,
            "post_id": 8101,
            "user_id": 9101,
            "caption": "Creator QA Reel",
            "status": "active",
            "moderation_status": "approved",
            "processing_status": "ready",
            "completion_rate": 0.72,
            "created_at": now,
            "updated_at": now,
        },
    )
    insert_if_possible(
        conn,
        "pulse_videos",
        {
            "id": 8301,
            "owner_user_id": 9101,
            "source_type": "audit",
            "source_id": "creator-command-audit-video",
            "media_id": "creator-command-audit-media",
            "title": "Creator QA Video",
            "visibility": "public",
            "moderation_status": "approved",
            "processing_status": "ready",
            "mux_status": "ready",
            "status": "active",
            "view_count": 7,
            "created_at": now,
            "updated_at": now,
        },
    )
    insert_if_possible(
        conn,
        "pulse_statuses",
        {
            "id": 8401,
            "user_id": 9101,
            "caption": "Creator QA Status",
            "visibility": "public",
            "moderation_status": "approved",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        },
    )
    insert_if_possible(
        conn,
        "pulse_status_views",
        {
            "id": 8501,
            "status_id": 8401,
            "viewer_id": 1,
            "completion_ratio": 0.66,
            "created_at": now,
        },
    )
    insert_if_possible(
        conn,
        "pulse_live_streams",
        {
            "id": 8601,
            "user_id": 9101,
            "title": "Creator QA Live",
            "status": "scheduled",
            "created_at": now,
            "updated_at": now,
        },
    )
    conn.commit()
    conn.close()


def assert_status(label: str, response, expected: set[int]) -> None:
    if response.status_code not in expected:
        body = response.get_data(as_text=True)[:500]
        raise AssertionError(f"{label} returned {response.status_code}, expected {sorted(expected)}: {body}")


def assert_internal_admin_links_resolve(client, route: str, html: str) -> None:
    hrefs = set(re.findall(r"href=['\"](/admin/[^'\"#?]+)", html))
    for href in sorted(hrefs):
        if href in {"/admin/logout"} or href.startswith("/admin/api/"):
            continue
        response = client.get(href)
        if response.status_code == 404 or response.status_code >= 500:
            body = response.get_data(as_text=True)[:500]
            raise AssertionError(f"{route} exposed broken admin link {href}: {response.status_code} {body}")


def main() -> int:
    configure_env()
    import bot  # noqa: WPS433

    seed_data(bot)
    client = bot.webhook_app.test_client()

    user_routes = [
        "/dashboard/creator",
        "/dashboard/creator/posts",
        "/dashboard/creator/reels",
        "/dashboard/creator/videos",
        "/dashboard/creator/statuses",
        "/dashboard/creator/live-studio",
    ]
    admin_routes = [
        "/admin/creator-command-center",
        "/admin/creator-command-center/posts",
        "/admin/creator-command-center/reels",
        "/admin/creator-command-center/videos",
        "/admin/creator-command-center/statuses",
        "/admin/creator-command-center/live-studio",
        "/admin/creator-command-center/analytics",
        "/admin/creator-command-center/media-health",
        "/admin/creator-command-center/moderation",
        "/admin/creator-command-center/audit",
    ]

    for route in user_routes:
        assert_status(f"unauthenticated {route}", client.get(route), {302})
    assert_status("unauthenticated creator api", client.get("/api/dashboard/creator/state"), {401})
    for route in admin_routes:
        assert_status(f"non-admin {route}", client.get(route), {302})

    with client.session_transaction() as sess:
        sess["account_user_id"] = 9101

    for route in user_routes:
        response = client.get(route)
        assert_status(f"user {route}", response, {200})
        text = response.get_data(as_text=True)
        if "Creator" not in text and "PulseSoc Creator" not in text:
            raise AssertionError(f"{route} did not render Creator command content")
        if "LogiNexus" in text:
            raise AssertionError(f"{route} leaked internal LogiNexus terminology")

    state_response = client.get("/api/dashboard/creator/state")
    assert_status("authenticated creator api", state_response, {200})
    state_json = state_response.get_json() or {}
    privacy = ((state_json.get("creator") or {}).get("privacy") or {})
    for key in ("owner_scoped", "raw_media_urls_hidden", "moderation_notes_hidden", "viewer_identity_protected"):
        if privacy.get(key) is not True:
            raise AssertionError(f"privacy flag missing or false: {key}")

    with client.session_transaction() as sess:
        sess["admin_user_id"] = 1

    for route in admin_routes:
        response = client.get(route)
        assert_status(f"admin {route}", response, {200})
        text = response.get_data(as_text=True)
        if "Creator Command Center" not in text and "Creator" not in text:
            raise AssertionError(f"{route} did not render admin Creator command content")
        forbidden_terms = ("DATABASE_URL", "COMMAND_CENTER_INTERNAL_TOKEN", "APNS_PRIVATE_KEY", "VAPID_PRIVATE_KEY", "raw token")
        if any(term in text for term in forbidden_terms):
            raise AssertionError(f"{route} exposed forbidden diagnostic text")
        assert_internal_admin_links_resolve(client, route, text)

    print("creator_command_center_audit: PASS")
    print(f"user_routes={len(user_routes)} admin_routes={len(admin_routes)} privacy_flags=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
