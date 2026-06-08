#!/usr/bin/env python3
"""PulseSoc database query/index performance guardrails."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


REQUIRED_INDEXES = [
    "idx_pulse_posts_feed",
    "idx_pulse_posts_mobile_feed",
    "idx_pulse_posts_feed_author",
    "idx_pulse_reels_status_created",
    "idx_pulse_videos_visibility_status",
    "idx_pulse_notifications_user_read_created",
    "idx_pulse_notifications_user_created",
    "idx_premium_entitlements_user_key",
]


def index_names(cur):
    if bot.db_service.IS_POSTGRES:
        cur.execute("SELECT indexname AS name FROM pg_indexes WHERE schemaname='public'")
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
    return {str(row[0]) for row in cur.fetchall()}


def main() -> int:
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    names = index_names(cur)
    missing = [name for name in REQUIRED_INDEXES if name not in names]
    for name in REQUIRED_INDEXES:
        print(f"{'PASS' if name in names else 'FAIL'}\tindex\t{name}")
    if not bot.db_service.IS_POSTGRES:
        checks = [
            ("pulse_posts public feed", "SELECT id FROM pulse_posts WHERE deleted_at IS NULL AND COALESCE(visibility,'public')='public' AND COALESCE(moderation_status,'approved')='approved' AND COALESCE(status,'published') NOT IN ('deleted','removed','archived') ORDER BY created_at DESC, id DESC LIMIT 12"),
            ("pulse_videos library", "SELECT id FROM pulse_videos WHERE COALESCE(status,'active')='active' AND COALESCE(visibility,'public')='public' ORDER BY created_at DESC, id DESC LIMIT 12"),
            ("pulse_notifications unread", "SELECT COUNT(*) FROM pulse_notifications WHERE user_id=1 AND (is_read=0 OR read_at IS NULL)"),
        ]
        for label, sql in checks:
            cur.execute("EXPLAIN QUERY PLAN " + sql)
            plan = " | ".join(str(tuple(row)) for row in cur.fetchall())
            full_scan = (
                ("SCAN pulse_posts" in plan and "USING INDEX" not in plan)
                or ("SCAN pulse_videos" in plan and "USING" not in plan)
                or ("SCAN pulse_notifications" in plan and "USING" not in plan)
            )
            print(f"{'FAIL' if full_scan else 'PASS'}\tplan\t{label}\t{plan[:500]}")
            if full_scan:
                missing.append(f"query plan full scan: {label}")
    conn.close()
    if missing:
        raise SystemExit("database query audit failed:\n- " + "\n- ".join(missing))
    print("database query audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
