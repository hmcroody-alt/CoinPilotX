#!/usr/bin/env python3
"""Publish one immediate Pulse Intelligence post per enabled Space."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import pulse_feed_engine  # noqa: E402
from services.pulse_ai.space_post_scheduler import publish_space_ai_post  # noqa: E402


def today():
    return datetime.now(UTC).strftime("%Y-%m-%d")


def enabled_space_slugs(cur):
    cur.execute("SELECT DISTINCT space_slug FROM pulse_ai_schedules WHERE enabled=1")
    return {dict(row).get("space_slug") for row in cur.fetchall()}


def already_launched_today(cur, slug):
    cur.execute(
        """
        SELECT COUNT(*) AS total
        FROM pulse_ai_posts
        WHERE space_slug=? AND schedule_slot='immediate' AND substr(created_at, 1, 10)=? AND status!='rejected'
        """,
        (slug, today()),
    )
    if int(dict(cur.fetchone() or {}).get("total") or 0) > 0:
        return True
    cur.execute(
        """
        SELECT tags_json
        FROM pulse_posts
        WHERE substr(created_at, 1, 10)=?
          AND deleted_at IS NULL
        """,
        (today(),),
    )
    for row in cur.fetchall():
        try:
            tags = json.loads(dict(row).get("tags_json") or "[]")
        except Exception:
            tags = []
        if slug in tags:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Publish immediate Pulse Space AI posts.")
    parser.add_argument("--force", action="store_true", help="Publish even if an immediate post already exists today.")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum spaces to publish.")
    parser.add_argument("--space", action="append", default=[], help="Publish only a specific space slug. Can be repeated.")
    args = parser.parse_args()

    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    enabled = enabled_space_slugs(cur)
    created = []
    skipped = []
    try:
        only_spaces = set(args.space or [])
        for space in bot.PULSE_SPACES:
            slug = space.get("slug")
            if only_spaces and slug not in only_spaces:
                continue
            if slug not in enabled:
                skipped.append((slug, "paused"))
                continue
            if not args.force and already_launched_today(cur, slug):
                skipped.append((slug, "already_posted_today"))
                continue
            result = publish_space_ai_post(
                cur,
                space,
                "immediate",
                pulse_create_post=pulse_feed_engine.create_post,
                approve_only=False,
            )
            if result.get("ok"):
                created.append((slug, result.get("ai_post_id"), result.get("pulse_post_id"), result.get("status")))
                print(f"CREATED {slug} ai_post={result.get('ai_post_id')} pulse_post={result.get('pulse_post_id')} status={result.get('status')}")
                conn.commit()
            else:
                skipped.append((slug, result.get("status") or "failed"))
                print(f"SKIPPED {slug} reason={result.get('status') or 'failed'}")
            if args.limit and len(created) >= args.limit:
                break
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Space AI immediate run complete: created={len(created)} skipped={len(skipped)} date={today()}")
    if skipped:
        print("Skipped:", ", ".join(f"{slug}:{reason}" for slug, reason in skipped[:30]))


if __name__ == "__main__":
    main()
