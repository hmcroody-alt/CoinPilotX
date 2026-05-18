"""Lightweight security event helpers for platform hardening."""

from __future__ import annotations

import json
from datetime import datetime

from . import user_context


def record(event_type, severity="low", user_id=0, ip_hash="", path="", details=None):
    conn = user_context.connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO security_events (event_type, user_id, ip_address, path, status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event_type)[:120],
                int(user_id or 0),
                str(ip_hash or "")[:160],
                str(path or "")[:500],
                str(severity or "low")[:40],
                json.dumps(details or {}),
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
