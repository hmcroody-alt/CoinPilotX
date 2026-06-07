"""Secure lightweight preview sessions for PulseSoc Camera publishing flows."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta

from . import user_context


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _expires(minutes: int = 45) -> str:
    return (datetime.utcnow() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def create_preview(user_id: int, destination: str, media: dict, metadata: dict | None = None) -> dict:
    """Persist a recoverable camera preview/draft before final publishing."""
    token = secrets.token_urlsafe(24)
    now = _now()
    expires_at = _expires()
    payload = {
        "destination": str(destination or "feed")[:40],
        "media": dict(media or {}),
        "metadata": dict(metadata or {}),
    }
    last_error = None
    for attempt in range(4):
        conn = user_context.connect()
        cur = conn.cursor()
        try:
            try:
                cur.execute("PRAGMA busy_timeout=5000")
            except Exception:
                pass
            cur.execute(
                """
                INSERT INTO pulse_camera_previews
                (preview_token, user_id, destination, media_id, media_url, thumbnail_url, payload_json, status, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
                """,
                (
                    token,
                    int(user_id),
                    payload["destination"],
                    int((media or {}).get("id") or 0),
                    str((media or {}).get("media_url") or "")[:1000],
                    str((media or {}).get("thumbnail_url") or (media or {}).get("media_url") or "")[:1000],
                    json.dumps(payload, separators=(",", ":"))[:12000],
                    now,
                    now,
                    expires_at,
                ),
            )
            conn.commit()
            conn.close()
            break
        except sqlite3.OperationalError as exc:
            conn.close()
            last_error = exc
            if "locked" not in str(exc).lower() or attempt == 3:
                raise
            time.sleep(0.12 * (attempt + 1))
    else:
        raise last_error or RuntimeError("preview could not be saved")
    return {
        "ok": True,
        "preview_token": token,
        "destination": payload["destination"],
        "expires_at": expires_at,
        "payload": payload,
    }


def mark_published(preview_token: str, user_id: int, entity_type: str, entity_id: int) -> dict:
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pulse_camera_previews
        SET status='published', published_entity_type=?, published_entity_id=?, updated_at=?
        WHERE preview_token=? AND user_id=?
        """,
        (str(entity_type or "")[:80], int(entity_id or 0), _now(), str(preview_token or ""), int(user_id)),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(changed), "preview_token": preview_token, "entity_type": entity_type, "entity_id": int(entity_id or 0)}


def latest_draft(user_id: int) -> dict:
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM pulse_camera_previews
        WHERE user_id=? AND status='draft' AND COALESCE(expires_at,'')>?
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id), _now()),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"ok": False, "message": "No active preview draft."}
    item = dict(row)
    try:
        item["payload"] = json.loads(item.get("payload_json") or "{}")
    except Exception:
        item["payload"] = {}
    return {"ok": True, "preview": item}
