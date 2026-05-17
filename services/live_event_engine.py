"""Lightweight live event bus for command-center and Arena fanout."""

import json
import sqlite3
from datetime import datetime, timedelta

from . import user_context


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def publish(channel, event_type, payload=None, dedupe_key=None, cooldown_seconds=2):
    channel = str(channel or "global")[:120]
    event_type = str(event_type or "event")[:80]
    payload_json = json.dumps(payload or {}, separators=(",", ":"), default=str)
    dedupe_key = str(dedupe_key or "")[:180]
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if dedupe_key:
        cutoff = (datetime.utcnow() - timedelta(seconds=max(0, int(cooldown_seconds or 0)))).isoformat(timespec="seconds")
        cur.execute(
            "SELECT id FROM live_events WHERE channel=? AND dedupe_key=? AND created_at>=? ORDER BY id DESC LIMIT 1",
            (channel, dedupe_key, cutoff),
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            return {"ok": True, "deduped": True, "event_id": int(existing["id"])}
    cur.execute(
        "INSERT INTO live_events (channel, event_type, dedupe_key, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (channel, event_type, dedupe_key, payload_json, _now()),
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "event_id": int(event_id)}


def poll(channel, after_id=0, limit=50):
    conn = user_context.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, channel, event_type, payload_json, created_at
        FROM live_events
        WHERE channel=? AND id>?
        ORDER BY id ASC
        LIMIT ?
        """,
        (str(channel or "global")[:120], int(after_id or 0), max(1, min(100, int(limit or 50)))),
    )
    rows = cur.fetchall()
    conn.close()
    events = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        events.append(
            {
                "id": int(row["id"]),
                "channel": row["channel"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"],
            }
        )
    return events
