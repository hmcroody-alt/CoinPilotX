#!/usr/bin/env python3
"""Report Pulse media storage and availability health."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage  # noqa: E402


def main():
    bot.init_db()
    status = media_storage.storage_status()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM chat_media_uploads
        WHERE deleted_at IS NULL
        ORDER BY id DESC LIMIT 500
        """
    )
    total = broken = missing_thumb = missing_poster = 0
    examples = []
    for row in cur.fetchall():
        total += 1
        item = dict(row)
        resolved = media_service.resolve_media(item)
        if not resolved.get("is_available"):
            broken += 1
            if len(examples) < 10:
                examples.append((item.get("id"), item.get("context_type"), resolved.get("media_url")))
        if not resolved.get("thumbnail_url"):
            missing_thumb += 1
        if resolved.get("media_type") == "video" and not resolved.get("poster_url"):
            missing_poster += 1
    conn.close()
    print("storage_provider=", status.get("provider"))
    print("storage_configured=", bool(status.get("configured")))
    print("total_active_media=", total)
    print("broken_or_restoring=", broken)
    print("missing_thumbnails=", missing_thumb)
    print("missing_posters=", missing_poster)
    for media_id, context, url in examples:
        print(f"broken_media id={media_id} context={context} url={url}")
    if status.get("provider") == "local":
        print("warning=local media storage is not durable for Railway production without a volume")
    print("pulse media health ok")


if __name__ == "__main__":
    main()
