"""Optional AI messaging foundation for the Command Center worker.

AI is disabled by default. This module stores scrubbed audit records and
returns safe unavailable responses unless Pulse AI is explicitly configured.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from services import db as db_service


TRUE_VALUES = {"1", "true", "yes", "on"}
VALID_AI_TASK_TYPES = {
    "chat_summary",
    "smart_replies",
    "scam_explanation",
    "translation_prepare",
    "moderation_insight",
}
SECRET_KEY_MARKERS = ("token", "secret", "password", "credential", "private_key", "api_key", "authorization")
MAX_CONTEXT_MESSAGES_DEFAULT = 30
MAX_CONTEXT_MESSAGES_HARD_LIMIT = 60
MAX_INPUT_SUMMARY_CHARS = 4_000
MAX_OUTPUT_BYTES = 12_000


class AIMessagingValidationError(ValueError):
    pass


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _env_text(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else default


def _env_bool(key: str, default: bool = False) -> bool:
    if key in os.environ:
        return _env_text(key).lower() in TRUE_VALUES
    return default


def ai_enabled() -> bool:
    return _env_bool("PULSE_AI_ENABLED", False)


def ai_provider() -> str:
    return _clean_text(_env_text("PULSE_AI_PROVIDER"), 60).lower()


def ai_model() -> str:
    return _clean_text(_env_text("PULSE_AI_MODEL"), 120)


def internal_only() -> bool:
    return _env_bool("PULSE_AI_INTERNAL_ONLY", True)


def max_context_messages() -> int:
    try:
        value = int(_env_text("PULSE_AI_MAX_CONTEXT_MESSAGES", str(MAX_CONTEXT_MESSAGES_DEFAULT)))
    except (TypeError, ValueError):
        value = MAX_CONTEXT_MESSAGES_DEFAULT
    return max(1, min(value, MAX_CONTEXT_MESSAGES_HARD_LIMIT))


def _positive_int(value: Any, field: str, *, required: bool = False) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    if required and parsed <= 0:
        raise AIMessagingValidationError(f"invalid_{field}")
    return max(0, parsed)


def _clean_text(value: Any, limit: int) -> str:
    text_value = re.sub(r"<[^>]*>", "", str(value or ""))
    text_value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text_value)
    return text_value.strip()[:limit]


def _redact_text(value: Any, limit: int = 2000) -> str:
    text = _clean_text(value, limit)
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[redacted-email]", text, flags=re.I)
    text = re.sub(r"\b(?:sk|pk|rk|xoxb|ghp|eyJ)[A-Za-z0-9_\-]{16,}\b", "[redacted-token]", text)
    text = re.sub(r"\b(?:\d[ -]*?){13,19}\b", "[redacted-number]", text)
    return text[:limit]


def _sanitize_payload(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        output = {}
        for key, item in list(value.items())[:80]:
            safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "", str(key or ""))[:80]
            if not safe_key or any(marker in safe_key.lower() for marker in SECRET_KEY_MARKERS):
                continue
            output[safe_key] = _sanitize_payload(item, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, depth + 1) for item in list(value)[:80]]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return _redact_text(value, 2000)


def _json_dump(value: Any, limit: int = MAX_OUTPUT_BYTES) -> str:
    serialized = json.dumps(_sanitize_payload(value), separators=(",", ":"), ensure_ascii=True)
    if len(serialized) <= limit:
        return serialized
    return json.dumps({"truncated": True}, separators=(",", ":"), ensure_ascii=True)


def _disabled_response(task_type: str, reason: str = "ai_disabled") -> dict[str, Any]:
    return {
        "ok": True,
        "available": False,
        "status": "disabled",
        "ai_enabled": False,
        "task_type": task_type,
        "reason": reason,
    }


def _provider_unavailable_response(task_type: str, reason: str) -> dict[str, Any]:
    return {
        "ok": True,
        "available": False,
        "status": "unavailable",
        "ai_enabled": True,
        "task_type": task_type,
        "reason": reason,
    }


def _normalize_task_type(value: str) -> str:
    task_type = _clean_text(value, 80).lower().replace(" ", "_").replace("-", "_")
    if task_type not in VALID_AI_TASK_TYPES:
        raise AIMessagingValidationError("invalid_ai_task_type")
    return task_type


def _input_summary(task_type: str, payload: dict[str, Any] | None = None) -> str:
    payload = payload if isinstance(payload, dict) else {}
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    if messages:
        lines = []
        for item in messages[:max_context_messages()]:
            if not isinstance(item, dict):
                continue
            role = _clean_text(item.get("role") or item.get("sender_role") or "member", 24) or "member"
            body = _redact_text(item.get("body") or item.get("text") or item.get("preview") or "", 240)
            if body:
                lines.append(f"{role}: {body}")
        if lines:
            return "\n".join(lines)[:MAX_INPUT_SUMMARY_CHARS]
    for key in ("input_summary", "summary", "text", "body", "reason"):
        if payload.get(key):
            return _redact_text(payload.get(key), MAX_INPUT_SUMMARY_CHARS)
    return f"{task_type} requested with no raw message body stored"


def _open_db():
    conn = db_service.connect()
    cur = conn.cursor()
    ensure_ai_schema(cur, conn)
    return conn, cur


def ensure_ai_schema(cur=None, conn=None) -> bool:
    own_connection = cur is None
    if own_connection:
        conn = db_service.connect()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS command_center_ai_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                user_id INTEGER,
                conversation_id INTEGER,
                message_id INTEGER,
                ai_task_type TEXT NOT NULL,
                input_summary TEXT,
                output_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_reason TEXT,
                created_at TEXT,
                processed_at TEXT
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_cc_ai_events_user ON command_center_ai_events(user_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_ai_events_conversation ON command_center_ai_events(conversation_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_ai_events_message ON command_center_ai_events(message_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_ai_events_task ON command_center_ai_events(ai_task_type, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_ai_events_status ON command_center_ai_events(status, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_ai_events_created ON command_center_ai_events(created_at)",
        ):
            cur.execute(statement)
        if own_connection:
            conn.commit()
        return True
    finally:
        if own_connection:
            conn.close()


def _record_ai_event(
    task_type: str,
    payload: dict[str, Any] | None,
    output: dict[str, Any],
    status: str,
    error_reason: str = "",
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    event_id = re.sub(r"[^a-zA-Z0-9_.:-]", "", str(payload.get("event_id") or ""))[:160] or f"ai_evt_{secrets.token_urlsafe(18)}"
    now = iso_now()
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            INSERT OR IGNORE INTO command_center_ai_events
            (event_id, user_id, conversation_id, message_id, ai_task_type, input_summary, output_json, status, error_reason, created_at, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                _positive_int(payload.get("user_id"), "user_id") or None,
                _positive_int(payload.get("conversation_id"), "conversation_id") or None,
                _positive_int(payload.get("message_id"), "message_id") or None,
                task_type,
                _input_summary(task_type, payload),
                _json_dump(output),
                _clean_text(status or "pending", 40),
                _clean_text(error_reason, 240) or None,
                now,
                now if status in {"disabled", "unavailable", "completed", "failed"} else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"event_id": event_id, "created_at": now}


def _provider_adapter(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    provider = ai_provider()
    if not provider:
        return _provider_unavailable_response(task_type, "provider_not_configured")
    if internal_only():
        return _provider_unavailable_response(task_type, "internal_only_adapter_pending")
    return _provider_unavailable_response(task_type, "provider_adapter_pending")


def _run_ai_task(task_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = _normalize_task_type(task_type)
    payload = payload if isinstance(payload, dict) else {}
    if not ai_enabled():
        response = _disabled_response(normalized)
        response.update(_record_ai_event(normalized, payload, response, "disabled", "ai_disabled"))
        return response
    response = _provider_adapter(normalized, payload)
    response.setdefault("model", ai_model())
    response.update(_record_ai_event(normalized, payload, response, "unavailable", response.get("reason") or "unavailable"))
    return response


def summarize_conversation(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _run_ai_task("chat_summary", payload)


def suggest_replies(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _run_ai_task("smart_replies", payload)


def explain_scam_risk(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _run_ai_task("scam_explanation", payload)


def prepare_translation(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _run_ai_task("translation_prepare", payload)


def create_moderation_insight(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _run_ai_task("moderation_insight", payload)
