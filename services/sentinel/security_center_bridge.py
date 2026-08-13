"""Bridge to the existing Security Center (Stage 20).

The platform already has canonical security primitives (`security_events`,
`auth_events`, admin audit, the command-center security engine). Sentinel
does NOT build a second security center: this bridge READS those stores and
maps rows into the canonical Sentinel envelope, deduplicated by source row
id, so correlation and evidence work over one unified stream.

Strictly read-only against the source tables.
"""

from __future__ import annotations

import json

from services.sentinel import events, store
from services.sentinel.identity import SENTINEL_INGEST

# security_events.event_type → (sentinel category, severity)
_SECURITY_EVENT_MAP = {
    "unusual_device": ("SECURITY", "medium"),
    "unusual_country": ("SECURITY", "medium"),
    "brute_force": ("SECURITY", "high"),
    "admin_action": ("ADMIN", "info"),
}
_AUTH_EVENT_MAP = {
    "login_failed": ("AUTH", "low"),
    "login_succeeded": ("AUTH", "info"),
    "password_reset_requested": ("AUTH", "low"),
    "password_reset_failed": ("AUTH", "medium"),
}


def _rows(cur, sql: str, params=()):
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception:
        return []  # source table absent on fresh DBs — bridge is best-effort


def sync_security_events(limit: int = 500, conn=None) -> dict:
    """Pull recent platform security/auth events into the Sentinel stream.
    Idempotent: dedupe_key is derived from the source table + row id, so
    re-running never duplicates."""
    limit = max(1, min(int(limit), 2000))
    ingested = deduped = 0
    with store.connection(conn) as c:
        cur = c.cursor()
        for table, mapping, default_cat in (
            ("security_events", _SECURITY_EVENT_MAP, "SECURITY"),
            ("auth_events", _AUTH_EVENT_MAP, "AUTH"),
        ):
            for row in _rows(cur, f"SELECT id, event_type, user_id, created_at, details "
                                  f"FROM {table} ORDER BY id DESC LIMIT ?", (limit,)):
                row_id = row[0]
                event_type = str(row[1] or "unknown")
                category, severity = mapping.get(event_type, (default_cat, "low"))
                detail_raw = row[4]
                try:
                    payload = json.loads(detail_raw) if isinstance(detail_raw, str) and detail_raw.strip().startswith("{") else {"details": str(detail_raw or "")[:500]}
                except Exception:
                    payload = {"details": str(detail_raw or "")[:500]}
                ev = events.Event(
                    category=category, event_type=event_type, severity=severity,
                    actor_id=SENTINEL_INGEST.actor_id,
                    source=f"bridge.{table}",
                    subject_type="user", subject_id=str(row[2] or ""),
                    occurred_at=str(row[3] or "")[:19] or events._utcnow(),
                    payload=payload,
                    dedupe_key=f"{table}:{row_id}")
                if events.ingest(ev, conn=c):
                    ingested += 1
                else:
                    deduped += 1
    return {"ingested": ingested, "deduped": deduped}
