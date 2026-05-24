#!/usr/bin/env python3
"""Backfill legacy local Pulse media into configured R2/S3 storage."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage  # noqa: E402


def main() -> None:
    bot.init_db()
    status = media_storage.storage_status()
    if status.get("provider") not in {"r2", "s3"} or not status.get("configured"):
        print("backfill skipped: durable storage is not fully configured")
        print(f"provider={status.get('provider')} configured={status.get('configured')} required={status.get('required') or {}}")
        return
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM chat_media_uploads
        WHERE deleted_at IS NULL
          AND (
            COALESCE(storage_provider,'local')='local'
            OR COALESCE(media_url,'') LIKE '/static/%'
            OR COALESCE(media_url,'') LIKE '/uploads/%'
          )
        ORDER BY id ASC
        LIMIT 1000
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    migrated = skipped = failed = 0
    for row in rows:
        result = media_service.migrate_local_media_row(row)
        if result.get("status") == "migrated":
            migrated += 1
        elif result.get("ok"):
            skipped += 1
        else:
            failed += 1
            print(f"media {row.get('id')} backfill failed: {result}")
    print(f"checked={len(rows)} migrated={migrated} skipped={skipped} failed={failed}")
    print("pulse media R2 backfill ok")


if __name__ == "__main__":
    main()
