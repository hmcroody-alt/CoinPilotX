#!/usr/bin/env python3
"""Repair normalizable Pulse media URLs and mark missing assets unavailable."""

from pathlib import Path
from datetime import UTC, datetime
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service  # noqa: E402


def main():
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM chat_media_uploads WHERE deleted_at IS NULL ORDER BY id ASC")
    repaired = unavailable = checked = 0
    for row in cur.fetchall():
        checked += 1
        item = dict(row)
        resolved = media_service.resolve_media(item)
        media_url = resolved.get("media_url") or ""
        thumb = resolved.get("thumbnail_url") or media_url
        poster = resolved.get("poster_url") or thumb
        is_available = 1 if resolved.get("is_available") else 0
        if not is_available:
            unavailable += 1
        try:
            cur.execute(
                """
                UPDATE chat_media_uploads
                SET media_url=?, thumbnail_url=?, poster_url=?, is_available=?,
                    storage_provider=COALESCE(NULLIF(storage_provider,''), ?),
                    storage_key=COALESCE(NULLIF(storage_key,''), ?),
                    availability_checked_at=?,
                    availability_error=?
                WHERE id=?
                """,
                (
                    media_url,
                    thumb,
                    poster,
                    is_available,
                    resolved.get("storage_provider") or "",
                    resolved.get("storage_key") or "",
                    datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
                    "" if is_available else "media file missing or unreachable",
                    item.get("id"),
                ),
            )
            repaired += 1
        except Exception as exc:
            print(f"repair_failed id={item.get('id')} error={exc}")
    conn.commit()
    conn.close()
    print(f"checked={checked}")
    print(f"repaired={repaired}")
    print(f"unavailable_marked={unavailable}")
    print("pulse media repair ok")


if __name__ == "__main__":
    main()
