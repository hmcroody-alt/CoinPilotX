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


def record_recovery_event(cur, user_id: int, conversation_id: int = 0, event_type: str = "recovery", details=None) -> dict:
    event_trace = trace_id()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_chat_recovery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT,
                user_id INTEGER,
                conversation_id INTEGER,
                event_type TEXT,
                details_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            INSERT INTO pulse_chat_recovery_events
            (trace_id, user_id, conversation_id, event_type, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_trace,
                int(user_id or 0),
                int(conversation_id or 0),
                str(event_type or "recovery")[:80],
                json.dumps(details or {}, default=str)[:2000],
                _now(),
            ),
        )
        return {"ok": True, "trace_id": event_trace}
    except Exception:
        logging.exception("CHAT_RECOVERY_EVENT_FAILED user_id=%s conversation_id=%s", user_id, conversation_id)
        return {"ok": False, "trace_id": event_trace}


def chat_recovery_payload(trace: str | None = None, mode: str = "syncing", message: str | None = None) -> dict:
    copy = {
        "loading": "Loading conversation...",
        "syncing": "Loading conversation...",
        "reconnecting": "Reconnecting securely...",
        "offline": "Offline temporarily.",
        "retrying": "Retrying connection...",
    }
    return {
        "trace_id": trace or trace_id(),
        "recovery_mode": mode,
        "message": message or copy.get(mode, "Loading conversation..."),
        "retryable": True,
        "fallback_polling": True,
    }


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
        "recent_failures": safe_count(
            cur,
            """
            SELECT COUNT(*) AS total
            FROM pulse_chat_health_traces
            WHERE status IN ('error','failed','exception')
            """,
        ),
        "recovery_events": safe_count(cur, "SELECT COUNT(*) AS total FROM pulse_chat_recovery_events"),
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
    try:
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat(timespec="seconds")
        cur.execute(
            """
            UPDATE pulse_messages
            SET deleted_at=COALESCE(NULLIF(deleted_at,''), ?), status='hidden', delivery_status='hidden'
            WHERE COALESCE(deleted_at,'')=''
              AND COALESCE(message_type,'') IN ('system','system_join','chat_event')
              AND lower(COALESCE(body,'')) LIKE '%% joined'
            """,
            (_now(),),
        )
        repaired += int(getattr(cur, "rowcount", 0) or 0)
        cur.execute(
            """
            DELETE FROM pulse_chat_recovery_events
            WHERE COALESCE(created_at,'') < ?
            """,
            (cutoff,),
        )
    except Exception:
        logging.exception("CHAT_HEALTH_LEGACY_REPAIR_FAILED")
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
