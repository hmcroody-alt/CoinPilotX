"""Self-healing health helpers for Pulse Messenger infrastructure."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import secrets
import time


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def trace_id() -> str:
    return secrets.token_hex(6)


def safe_count(cur, sql: str, params=()) -> int:
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        if isinstance(row, dict):
            return int(row.get("total") or row.get("c") or 0)
        return int((row or [0])[0] or 0)
    except Exception:
        logging.exception("CHAT_HEALTH_COUNT_FAILED sql=%s", sql)
        return 0


def record_trace(cur, user_id: int, endpoint: str, status: str, trace: str, details=None) -> None:
    """Persist a lightweight diagnostic trace when the optional table exists."""
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_chat_health_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT,
                user_id INTEGER,
                endpoint TEXT,
                status TEXT,
                details_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            INSERT INTO pulse_chat_health_traces
            (trace_id, user_id, endpoint, status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (trace, int(user_id or 0), str(endpoint or "")[:180], str(status or "")[:40], json.dumps(details or {}, default=str)[:2000], _now()),
        )
    except Exception:
        logging.exception("CHAT_HEALTH_TRACE_FAILED trace_id=%s endpoint=%s", trace, endpoint)


def monitor_chat_tables(cur) -> dict:
    return {
        "conversations": safe_count(cur, "SELECT COUNT(*) AS total FROM pulse_conversations"),
        "participants": safe_count(cur, "SELECT COUNT(*) AS total FROM pulse_conversation_participants"),
        "messages": safe_count(cur, "SELECT COUNT(*) AS total FROM pulse_messages"),
        "rooms": safe_count(cur, "SELECT COUNT(*) AS total FROM pulse_chat_rooms"),
        "orphan_participants": safe_count(
            cur,
            """
            SELECT COUNT(*) AS total
            FROM pulse_conversation_participants p
            LEFT JOIN pulse_conversations c ON c.id=p.conversation_id
            WHERE c.id IS NULL
            """,
        ),
        "orphan_messages": safe_count(
            cur,
            """
            SELECT COUNT(*) AS total
            FROM pulse_messages m
            LEFT JOIN pulse_conversations c ON c.id=m.conversation_id
            WHERE COALESCE(m.conversation_id,0)!=0 AND c.id IS NULL
            """,
        ),
    }


def repair_stale_sessions(cur) -> dict:
    cutoff = (datetime.utcnow() - timedelta(minutes=20)).isoformat(timespec="seconds")
    repaired = 0
    try:
        cur.execute(
            """
            UPDATE pulse_conversation_typing
            SET typing_until=''
            WHERE COALESCE(typing_until,'')!='' AND typing_until<?
            """,
            (cutoff,),
        )
        repaired += int(getattr(cur, "rowcount", 0) or 0)
    except Exception:
        logging.exception("CHAT_HEALTH_TYPING_REPAIR_FAILED")
    return {"ok": True, "repaired": repaired}


def check_endpoint(client, method: str, path: str, payload=None) -> dict:
    start = time.perf_counter()
    if method.upper() == "GET":
        response = client.get(path)
    else:
        response = client.open(path, method=method.upper(), json=payload or {})
    latency_ms = int((time.perf_counter() - start) * 1000)
    try:
        data = response.get_json() or {}
    except Exception:
        data = {"ok": False, "message": response.get_data(as_text=True)[:240]}
    return {
        "ok": response.status_code < 500 and data.get("ok") is not False,
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "trace_id": data.get("trace_id"),
        "message": data.get("message") or "",
        "data": data,
    }


def health_summary(cur) -> dict:
    table_health = monitor_chat_tables(cur)
    broken = int(table_health.get("orphan_participants") or 0) + int(table_health.get("orphan_messages") or 0)
    return {
        "ok": broken == 0,
        "status": "healthy" if broken == 0 else "repair_needed",
        "tables": table_health,
        "recommendations": [] if broken == 0 else ["Run chat repair to remove orphan references and rebuild summaries."],
    }
