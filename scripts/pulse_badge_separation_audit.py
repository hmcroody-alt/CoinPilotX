#!/usr/bin/env python3
"""Audit PulseSoc chat-vs-alert badge separation."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(name: str, condition: bool, failures: list[str]) -> None:
    print(f"{'PASS' if condition else 'FAIL'}: {name}")
    if not condition:
        failures.append(name)


def main() -> int:
    failures: list[str] = []
    notifications_js = read("static/notifications.js")
    service = read("services/notification_service.py")
    bot = read("bot.py")
    mobile_topbars = re.findall(r'<nav class="mobile-topbar".*?</nav>', bot, flags=re.DOTALL)
    desktop_top = bot[bot.find("def pulse_desktop_top_nav_html"):bot.find("def pulse_desktop_left_rail_html")]

    require("frontend alert count uses alert_unread_count only", "alert: Number(payload.alert_unread_count || 0)" in notifications_js, failures)
    require("frontend chat count uses chat_unread_count only", "chat: Number(payload.chat_unread_count || 0)" in notifications_js, failures)
    require("live chat events ignore generic unread_count", "payload?.chat_unread_count || 0" in notifications_js and "payload?.chat_unread_count || payload?.unread_count" not in notifications_js, failures)
    require("live alert events ignore generic unread_count", "payload?.alert_unread_count || 0" in notifications_js and "payload?.alert_unread_count || payload?.unread_count" not in notifications_js, failures)
    require("backend excludes message notifications from alert count", "AND NOT ({_message_notification_where_clause()})" in service, failures)
    require("backend sums conversation unread for chat count", "COALESCE(SUM(CASE WHEN COALESCE(unread_count,0) > 0 THEN unread_count ELSE 0 END),0)" in service, failures)
    require("backend includes Command Center V2 unread state", "comm_v2_participants" in service and "membership_state" in service, failures)
    require("desktop header exposes notification and chat badges without right-side message duplicate", "data-header-notifications" in desktop_top and "pulse-alert-radar" in desktop_top and "data-alert-unread data-notification-unread hidden" in desktop_top and '("Messages", "/pulse/messages")' in desktop_top and "data-chat-unread" in desktop_top and "pulse-topnav-messages" not in desktop_top, failures)
    require("mobile topbar exposes notification bell only", bool(mobile_topbars) and all('href="/pulse/notifications"' in bar and ("PULSE_NOTIFICATION_BELL_ICON" in bar or "pulse-bell-icon" in bar or "__NOTIFICATION_BELL_ICON__" in bar) and "pulse-alert-radar" not in bar and 'href="/pulse/messages"' not in bar and "data-chat-unread" not in bar for bar in mobile_topbars), failures)
    require("bottom nav has separate chat and alert badges", "data-alert-unread data-notification-unread hidden" in bot and "data-chat-unread hidden" in bot and "mobile_bottom_html" in bot, failures)
    require("Pulse shell badges keep urgent red styling", re.search(r"background\s*:\s*#ff335d", bot), failures)

    conn = sqlite3.connect(ROOT / "coinpilotx.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    user_id = 991337777
    now = datetime.now(UTC).isoformat(timespec="seconds")
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
        (user_id, "badge_audit_user", "Badge Audit User", "badge-audit@example.test", now),
    )
    cur.execute("DELETE FROM pulse_notifications WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM pulse_conversation_participants WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM comm_v2_participants WHERE user_id=?", (user_id,))
    cur.execute(
        "INSERT INTO pulse_notifications (user_id, type, title, body, deep_link, is_read, created_at) VALUES (?, 'like', 'Like', 'Someone liked your post.', '/pulse/post/1', 0, ?)",
        (user_id, now),
    )
    cur.execute(
        "INSERT INTO pulse_notifications (user_id, type, title, body, deep_link, is_read, created_at) VALUES (?, 'chat_message', 'Message', 'New chat.', '/pulse/messages/123', 0, ?)",
        (user_id, now),
    )
    cur.execute(
        "INSERT INTO pulse_conversation_participants (conversation_id, user_id, role, joined_at, unread_count) VALUES (123456, ?, 'member', ?, 3)",
        (user_id, now),
    )
    cur.execute(
        """
        INSERT INTO comm_v2_participants (conversation_id, user_id, role, membership_state, joined_at, unread_count)
        VALUES (654321, ?, 'member', 'active', ?, 2)
        """,
        (user_id, now),
    )
    conn.commit()
    conn.close()

    from services import notification_service

    class PostgresTableCursor:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=()):
            self.sql = sql

        def fetchone(self):
            return (1,)

    postgres_cursor = PostgresTableCursor()
    original_engine = notification_service.db_service.ENGINE_NAME
    notification_service.db_service.ENGINE_NAME = "postgresql"
    try:
        table_found = notification_service._table_exists(postgres_cursor, "pulse_conversation_participants")
    finally:
        notification_service.db_service.ENGINE_NAME = original_engine
    require("PostgreSQL table check succeeds", table_found, failures)
    require("PostgreSQL table check avoids sqlite_master", "information_schema.tables" in postgres_cursor.sql and "sqlite_master" not in postgres_cursor.sql, failures)

    counts = notification_service.pulse_badge_counts(user_id)
    require("backend count fixture separates alert from chat", counts.get("alert_unread_count") == 1 and counts.get("chat_unread_count") == 5, failures)
    require("legacy count aliases remain alert-only", counts.get("count") == counts.get("alert_unread_count") and counts.get("unread_count") == counts.get("alert_unread_count"), failures)

    conn = sqlite3.connect(ROOT / "coinpilotx.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_notifications WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM pulse_conversation_participants WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM comm_v2_participants WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

    if failures:
        print("\nBadge separation audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nBadge separation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
