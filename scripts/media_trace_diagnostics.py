#!/usr/bin/env python3
"""Print diagnostics for a Pulse media trace such as media-54."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, media_service, media_storage  # noqa: E402


def head(url: str) -> dict:
    if not url or not url.startswith(("http://", "https://")):
        return {"checked": False, "reason": "not_remote"}
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "CoinPilotX-MediaTrace/1.0"})
        with urlopen(req, timeout=8) as resp:
            return {
                "checked": True,
                "status": int(getattr(resp, "status", 0) or 0),
                "content_type": resp.headers.get("content-type", ""),
                "content_length": resp.headers.get("content-length", ""),
                "accept_ranges": resp.headers.get("accept-ranges", ""),
                "cache_control": resp.headers.get("cache-control", ""),
            }
    except Exception as exc:
        return {"checked": True, "error": exc.__class__.__name__, "message": str(exc)}


def main() -> None:
    trace = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("MEDIA_TRACE_ID") or "54").strip()
    media_id = int(trace.replace("media-", ""))
    bot.init_db()
    conn = db_service.connect()
    cur = conn.cursor()
    output = {"trace": f"media-{media_id}", "storage": media_storage.storage_status(), "tables": {}}
    for table in ("chat_media_uploads", "pulse_media_assets"):
        try:
            row = cur.execute(f"SELECT * FROM {table} WHERE id=?", (media_id,)).fetchone()
        except Exception as exc:
            output["tables"][table] = {"error": str(exc)}
            continue
        if not row:
            output["tables"][table] = {"found": False}
            continue
        item = dict(row)
        resolved = media_service.resolve_media(item)
        url = resolved.get("valid_url") or resolved.get("media_url") or ""
        output["tables"][table] = {
            "found": True,
            "row": item,
            "resolved": resolved,
            "cdn_head": head(url),
            "private_r2_url": "r2.cloudflarestorage.com" in url,
            "cdn_url_ok": not url.startswith("http") or url.startswith((os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/") + "/", "https://cdn.coinpilotx.app/")),
        }
    conn.close()
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
