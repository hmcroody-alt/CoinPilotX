#!/usr/bin/env python3
"""Mark loop-era ``crypto_alert_triggered`` notifications read. Reversible.

Context: between 2026-07-01 and 2026-07-27 an unlatched alert rule re-fired on
every worker sweep, producing 4,798 notifications for 3 users -- 4,776 of which
are still unread for the owner alone, 64% of their entire unread badge. The
loop was fixed on 2026-07-28; since then notifications match alert_events 1:1
on every day. See docs/briefings/ALERT_DUPLICATE_AUDIT.md.

This script does NOT delete. The rows are the only evidence of the loop's shape
and a future regression is diagnosed against them. It flips ``is_read`` and
writes an undo file first, so the exact prior state can be restored.

Dry run (default -- reports scope, writes nothing):

    railway run --service Postgres -- bash -c \
      'DATABASE_URL="$DATABASE_PUBLIC_URL" python scripts/briefings/mark_loop_era_alerts_read.py'

Apply:

    ... mark_loop_era_alerts_read.py --apply --undo-file /tmp/undo.json

Revert:

    ... mark_loop_era_alerts_read.py --revert /tmp/undo.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# The loop's last row is 2026-07-27T02:14:27Z; the fix landed 2026-07-28. Rows
# at or after this boundary are correct and must not be touched.
LOOP_ERA_END = "2026-07-28"
TARGET_TYPE = "crypto_alert_triggered"


def _connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    if url.startswith("sqlite"):
        sys.exit("This script targets production Postgres, not sqlite")
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url)
    return conn, psycopg2.extras.RealDictCursor


def _scope(cur):
    cur.execute(
        "SELECT id, user_id, is_read, created_at FROM pulse_notifications "
        "WHERE type=%s AND created_at < %s AND COALESCE(is_read,0)=0 "
        "ORDER BY id",
        (TARGET_TYPE, LOOP_ERA_END),
    )
    return [dict(r) for r in cur.fetchall()]


def _report(rows):
    by_user: dict[int, int] = {}
    for r in rows:
        by_user[r["user_id"]] = by_user.get(r["user_id"], 0) + 1
    print(f"  rows in scope: {len(rows)}")
    for uid in sorted(by_user):
        print(f"    user {uid}: {by_user[uid]}")
    if rows:
        print(f"    range: {rows[0]['created_at']} .. {rows[-1]['created_at']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform the update")
    ap.add_argument("--undo-file", help="where to write the undo snapshot (required with --apply)")
    ap.add_argument("--revert", metavar="UNDO_FILE", help="restore is_read from an undo snapshot")
    args = ap.parse_args()

    conn, dict_cursor = _connect()
    cur = conn.cursor(cursor_factory=dict_cursor)

    if args.revert:
        with open(args.revert) as fh:
            snap = json.load(fh)
        rows = snap["rows"]
        print(f"reverting {len(rows)} rows from {args.revert} (taken {snap['taken_at']})")
        for r in rows:
            cur.execute(
                "UPDATE pulse_notifications SET is_read=%s WHERE id=%s",
                (r["is_read"], r["id"]),
            )
        conn.commit()
        print(f"reverted {len(rows)} rows")
        conn.close()
        return 0

    rows = _scope(cur)
    print(f"loop-era {TARGET_TYPE} rows, unread, created_at < {LOOP_ERA_END}:")
    _report(rows)

    # Confirm the post-loop rows we are deliberately NOT touching.
    cur.execute(
        "SELECT COUNT(*) AS n FROM pulse_notifications WHERE type=%s AND created_at >= %s",
        (TARGET_TYPE, LOOP_ERA_END),
    )
    print(f"  post-loop rows left untouched: {cur.fetchone()['n']}")

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply --undo-file PATH")
        conn.close()
        return 0

    if not args.undo_file:
        sys.exit("--apply requires --undo-file so the change can be reverted")
    if not rows:
        print("nothing to do")
        conn.close()
        return 0

    snapshot = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "type": TARGET_TYPE,
        "loop_era_end": LOOP_ERA_END,
        "rows": [{"id": r["id"], "is_read": r["is_read"]} for r in rows],
    }
    with open(args.undo_file, "w") as fh:
        json.dump(snapshot, fh, indent=2)
    print(f"\nundo snapshot written to {args.undo_file} ({len(rows)} rows)")

    # Bounded by the same predicate AND the exact id set, so a row that changed
    # between the scope read and now cannot be swept up.
    ids = [r["id"] for r in rows]
    cur.execute(
        "UPDATE pulse_notifications SET is_read=1 "
        "WHERE id = ANY(%s) AND type=%s AND created_at < %s",
        (ids, TARGET_TYPE, LOOP_ERA_END),
    )
    updated = cur.rowcount
    conn.commit()
    print(f"marked {updated} rows read")

    cur.execute(
        "SELECT COUNT(*) AS n FROM pulse_notifications "
        "WHERE type=%s AND created_at < %s AND COALESCE(is_read,0)=0",
        (TARGET_TYPE, LOOP_ERA_END),
    )
    print(f"remaining unread loop-era rows: {cur.fetchone()['n']} (expected 0)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
