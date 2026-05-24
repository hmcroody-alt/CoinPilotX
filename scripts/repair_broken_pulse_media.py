#!/usr/bin/env python3
"""Repair stale Pulse media URLs and mark unrecoverable media unavailable."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service  # noqa: E402


def main() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM chat_media_uploads
        WHERE deleted_at IS NULL
          AND context_type IN ('pulse','pulse_post','pulse_status','pulse_reel','pulse_camera','pulse_message','pulse_group')
        ORDER BY id DESC
        LIMIT 2000
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    repaired = unavailable = checked = 0
    for row in rows:
        checked += 1
        result = media_service.repair_media_row(row)
        if result.get("status") == "marked_unavailable":
            unavailable += 1
            print(f"media {row.get('id')} unavailable: {result.get('media_url') or row.get('media_url') or ''}")
        elif result.get("ok"):
            repaired += 1
    print(f"checked={checked} repaired={repaired} unavailable_marked={unavailable}")
    print("pulse broken media repair ok")


if __name__ == "__main__":
    main()
