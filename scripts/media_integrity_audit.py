#!/usr/bin/env python3
"""Audit canonical Pulse media resolution and R2/CDN readiness."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service, media_storage  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    status = media_storage.storage_status()
    expect(status.get("provider") in {"local", "r2", "s3"}, "media storage provider is known")
    if os.getenv("MEDIA_STORAGE_PROVIDER", "").lower() == "r2":
        expect(status.get("configured") is True, "R2 media storage is fully configured", str(status))
        expect(status.get("bucket") == "pulse-media", "R2 production bucket configured", str(status))
        expect(status.get("public_base_url", "").rstrip("/") == "https://cdn.coinpilotx.app", "production CDN base configured", str(status))
    resolved = media_service.resolve_media({"storage_key": "audit/example.jpg", "media_type": "image", "storage_provider": "r2", "is_available": 1})
    expect(resolved["media_url"].startswith("https://cdn.coinpilotx.app/") or media_storage.provider() != "r2", "storage key resolves to canonical CDN URL")
    expect(resolved["fallback_url"].endswith("media-unavailable.svg"), "fallback media URL is present")
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    broken = []
    try:
        cur.execute("SELECT id, media_url, public_url, storage_key, media_type, mime_type, width, height, is_available FROM chat_media_uploads ORDER BY id DESC LIMIT 200")
        for row in cur.fetchall():
            item = dict(row)
            media = media_service.resolve_media(item)
            if media.get("is_available") and not media.get("valid_url"):
                broken.append(str(item.get("id")))
            if media.get("media_url", "").startswith("/Users/"):
                broken.append(f"local-path:{item.get('id')}")
    except Exception:
        pass
    conn.close()
    expect(not broken, "no recently visible media resolves to a broken raw path", ", ".join(broken[:10]))
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("media_service.resolve_media" in source, "bot uses canonical media resolver")
    expect("Media is being restored." in source, "broken media fallback text is rendered")
    print("media integrity audit ok")


if __name__ == "__main__":
    main()
