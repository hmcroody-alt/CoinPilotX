"""Pulse AI Messenger service and privacy-safe learning foundation."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services import pulse_ai_knowledge, pulse_ai_provider_router


LOGGER = logging.getLogger(__name__)
PULSE_AI_CONVERSATION_ID = -9001001
PULSE_AI_USER_ID = -9001001
MAX_MESSAGE_CHARS = 4000


def _bot():
    import bot

    return bot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trace() -> str:
    return secrets.token_hex(6)


def _clean(value: Any, limit: int = 4000) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).replace("\x00", " ").strip()[:limit]


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _open_db():
    bot = _bot()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    ensure_schema(cur, conn)
    return conn, cur


def ensure_schema(cur=None, conn=None) -> None:
    own = cur is None
    if own:
        conn, cur = _open_db_without_schema()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                user_id INTEGER NOT NULL,
                title TEXT,
                status TEXT DEFAULT 'active',
                pinned_at TEXT,
                last_message_id INTEGER DEFAULT 0,
                last_message_at TEXT,
                reset_at TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_ai_conversations_user ON pulse_ai_conversations(user_id)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                body TEXT NOT NULL,
                provider TEXT,
                provider_model TEXT,
                latency_ms INTEGER DEFAULT 0,
                error_code TEXT,
                correlation_id TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_ai_messages_conversation ON pulse_ai_messages(conversation_id, id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_ai_messages_user_created ON pulse_ai_messages(user_id, created_at)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_knowledge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                title TEXT NOT NULL,
                category TEXT,
                body TEXT NOT NULL,
                source TEXT DEFAULT 'admin_seed',
                status TEXT DEFAULT 'approved',
                approved_by_user_id INTEGER DEFAULT 0,
                approved_at TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_key TEXT NOT NULL,
                memory_value TEXT NOT NULL,
                source TEXT DEFAULT 'user_opt_in',
                status TEXT DEFAULT 'active',
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_ai_user_memory_user ON pulse_ai_user_memory(user_id, status)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                user_id INTEGER NOT NULL,
                message_id INTEGER,
                rating TEXT NOT NULL,
                comment TEXT,
                status TEXT DEFAULT 'queued_review',
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                source TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_safety_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                feedback_id INTEGER,
                knowledge_item_id INTEGER,
                review_status TEXT DEFAULT 'queued',
                reviewer_user_id INTEGER DEFAULT 0,
                notes TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_feature_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_key TEXT UNIQUE,
                name TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                updated_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_conversation_context_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                remember_preferences INTEGER DEFAULT 0,
                use_pulse_ai_chat_history INTEGER DEFAULT 1,
                assist_with_messages_when_asked INTEGER DEFAULT 1,
                improve_from_feedback INTEGER DEFAULT 1,
                private_context_opt_in INTEGER DEFAULT 0,
                updated_at TEXT,
                created_at TEXT
            )
            """
        )
        _seed_foundation(cur)
        if conn:
            conn.commit()
    finally:
        if own and conn:
            conn.close()


def _open_db_without_schema():
    bot = _bot()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    return conn, conn.cursor()


def _seed_foundation(cur) -> None:
    now = _now()
    for item in pulse_ai_knowledge.DEFAULT_FEATURE_REGISTRY:
        cur.execute(
            """
            INSERT OR IGNORE INTO pulse_ai_feature_registry (feature_key, name, summary, status, updated_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (item["key"], item["name"], item["summary"], now),
        )
    for index, item in enumerate(pulse_ai_knowledge.DEFAULT_KNOWLEDGE_ITEMS, start=1):
        public_id = f"pai_knowledge_seed_{index}"
        cur.execute(
            """
            INSERT OR IGNORE INTO pulse_ai_knowledge_items
            (public_id, title, category, body, source, status, approved_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'system_seed', 'approved', ?, ?, ?)
            """,
            (public_id, item["title"], item["category"], item["body"], now, now, now),
        )


def _conversation_row(cur, user_id: int) -> dict:
    cur.execute("SELECT * FROM pulse_ai_conversations WHERE user_id=? LIMIT 1", (int(user_id),))
    row = cur.fetchone()
    if row:
        return dict(row)
    now = _now()
    public_id = f"pulse-ai-{int(user_id)}"
    cur.execute(
        """
        INSERT INTO pulse_ai_conversations
        (public_id, user_id, title, status, pinned_at, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'Pulse AI', 'active', ?, ?, ?, ?)
        """,
        (public_id, int(user_id), now, json.dumps({"assistant": "galaxy_assistant"}), now, now),
    )
    cur.execute("SELECT * FROM pulse_ai_conversations WHERE user_id=? LIMIT 1", (int(user_id),))
    return dict(cur.fetchone())


def _settings_row(cur, user_id: int) -> dict:
    now = _now()
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_ai_conversation_context_permissions
        (user_id, remember_preferences, use_pulse_ai_chat_history, assist_with_messages_when_asked, improve_from_feedback, private_context_opt_in, created_at, updated_at)
        VALUES (?, 0, 1, 1, 1, 0, ?, ?)
        """,
        (int(user_id), now, now),
    )
    cur.execute("SELECT * FROM pulse_ai_conversation_context_permissions WHERE user_id=? LIMIT 1", (int(user_id),))
    return dict(cur.fetchone() or {})


def _conversation_payload(row: dict, unread_count: int = 0) -> dict:
    return {
        "conversation_id": PULSE_AI_CONVERSATION_ID,
        "id": PULSE_AI_CONVERSATION_ID,
        "public_id": row.get("public_id") or "pulse-ai",
        "conversation_type": "assistant",
        "type": "ai",
        "is_ai": True,
        "ai_assistant": True,
        "title": "Pulse AI",
        "description": "Galaxy Assistant",
        "member_count": 2,
        "pinned": True,
        "muted": False,
        "unread_count": int(unread_count or 0),
        "presence": {"status": "online", "active_now": True, "presence_visible": True},
        "last_message_at": row.get("last_message_at") or row.get("updated_at") or row.get("created_at"),
        "last_activity_at": row.get("last_message_at") or row.get("updated_at") or row.get("created_at"),
        "last_message_preview": "Ask me anything about PulseSoc.",
        "verified": True,
    }


def _message_payload(row: dict, current_user_id: int) -> dict:
    role = str(row.get("role") or "").lower()
    mine = role == "user"
    metadata = _json_loads(row.get("metadata_json"), {}) or {}
    return {
        "id": int(row.get("id") or 0),
        "message_id": int(row.get("id") or 0),
        "conversation_id": PULSE_AI_CONVERSATION_ID,
        "sender_user_id": int(current_user_id) if mine else PULSE_AI_USER_ID,
        "sender_id": int(current_user_id) if mine else PULSE_AI_USER_ID,
        "sender": {
            "user_id": int(current_user_id) if mine else PULSE_AI_USER_ID,
            "display_name": "You" if mine else "Pulse AI",
            "avatar_url": "",
        },
        "sender_display_name": "You" if mine else "Pulse AI",
        "is_mine": mine,
        "is_ai": not mine,
        "message_type": "text",
        "body": row.get("body") or "",
        "delivery_status": "sent" if not row.get("error_code") else "failed",
        "delivery_state": "sent" if not row.get("error_code") else "failed",
        "created_at": row.get("created_at") or _now(),
        "provider": row.get("provider") or "",
        "provider_model": row.get("provider_model") or "",
        "latency_ms": int(row.get("latency_ms") or 0),
        "error_code": row.get("error_code") or "",
        "correlation_id": row.get("correlation_id") or "",
        "metadata": metadata,
        "reactions": [],
        "attachments": [],
    }


def _insert_message(cur, conversation_id: int, user_id: int, role: str, body: str, **extra: Any) -> int:
    now = _now()
    cur.execute(
        """
        INSERT INTO pulse_ai_messages
        (conversation_id, user_id, role, body, provider, provider_model, latency_ms, error_code, correlation_id, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(conversation_id),
            int(user_id),
            role,
            body,
            extra.get("provider") or "",
            extra.get("provider_model") or "",
            int(extra.get("latency_ms") or 0),
            extra.get("error_code") or "",
            extra.get("correlation_id") or "",
            json.dumps(extra.get("metadata") or {}, default=str)[:8000],
            now,
        ),
    )
    message_id = int(cur.lastrowid or 0)
    cur.execute(
        "UPDATE pulse_ai_conversations SET last_message_id=?, last_message_at=?, updated_at=? WHERE id=?",
        (message_id, now, now, int(conversation_id)),
    )
    return message_id


def _messages_for_conversation(cur, conversation: dict, user_id: int, limit: int = 80) -> list[dict]:
    reset_at = conversation.get("reset_at") or ""
    params: list[Any] = [int(conversation["id"]), int(user_id)]
    clause = ""
    if reset_at:
        clause = "AND created_at>=?"
        params.append(reset_at)
    params.append(max(1, min(int(limit or 80), 120)))
    cur.execute(
        f"""
        SELECT * FROM pulse_ai_messages
        WHERE conversation_id=? AND user_id=? {clause}
        ORDER BY id DESC LIMIT ?
        """,
        tuple(params),
    )
    return list(reversed([dict(row) for row in cur.fetchall()]))


def _retrieve_knowledge(cur, query: str, limit: int = 8) -> list[dict]:
    terms = [term for term in re.findall(r"[a-z0-9]{3,}", query.lower()) if term not in {"the", "and", "how", "what", "with", "pulse", "pulsesoc"}]
    cur.execute("SELECT * FROM pulse_ai_knowledge_items WHERE status='approved' ORDER BY updated_at DESC, id DESC LIMIT 80")
    rows = [dict(row) for row in cur.fetchall()]
    scored = []
    for row in rows:
        haystack = f"{row.get('title','')} {row.get('category','')} {row.get('body','')}".lower()
        score = sum(1 for term in terms if term in haystack)
        if score or not terms:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], item[1].get("updated_at") or ""), reverse=True)
    return [row for _, row in scored[:limit]]


def _user_memory(cur, user_id: int, settings: dict) -> list[dict]:
    if not int(settings.get("remember_preferences") or 0):
        return []
    cur.execute(
        """
        SELECT * FROM pulse_ai_user_memory
        WHERE user_id=? AND status='active' AND COALESCE(deleted_at,'')=''
        ORDER BY updated_at DESC, id DESC LIMIT 20
        """,
        (int(user_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def _history_for_prompt(messages: list[dict], settings: dict) -> list[dict]:
    if not int(settings.get("use_pulse_ai_chat_history") or 0):
        return []
    return [
        {"role": row.get("role"), "body": row.get("body")}
        for row in messages[-12:]
        if row.get("role") in {"user", "assistant"} and row.get("body")
    ]


def _record_learning_event(cur, user_id: int | None, event_type: str, source: str, metadata: dict | None = None) -> None:
    cur.execute(
        """
        INSERT INTO pulse_ai_learning_events (public_id, user_id, event_type, source, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (f"pai_evt_{secrets.token_urlsafe(10)}", int(user_id or 0) or None, _clean(event_type, 80), _clean(source, 80), json.dumps(metadata or {}, default=str)[:8000], _now()),
    )


def _rate_limit_ok(cur, user_id: int) -> tuple[bool, int]:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    cur.execute(
        "SELECT COUNT(*) AS total FROM pulse_ai_messages WHERE user_id=? AND role='user' AND created_at>=?",
        (int(user_id), cutoff),
    )
    count = int((dict(cur.fetchone() or {}) or {}).get("total") or 0)
    limit = 12
    try:
        import os

        limit = max(2, min(int(os.getenv("PULSE_AI_RATE_LIMIT_PER_MINUTE", "12") or 12), 60))
    except Exception:
        limit = 12
    return count < limit, limit


def get_conversation(user_id: int, limit: int = 80) -> dict:
    conn, cur = _open_db()
    try:
        conversation = _conversation_row(cur, int(user_id))
        settings = _settings_row(cur, int(user_id))
        messages = _messages_for_conversation(cur, conversation, int(user_id), limit)
        if not messages:
            greeting = "Welcome to Pulse AI. Ask me anything about PulseSoc, your alerts, Messenger, Reels, music, crypto signals, or how to explore the galaxy."
            _insert_message(cur, int(conversation["id"]), int(user_id), "assistant", greeting, metadata={"kind": "greeting"})
            conn.commit()
            cur.execute("SELECT * FROM pulse_ai_conversations WHERE id=? LIMIT 1", (int(conversation["id"]),))
            conversation = dict(cur.fetchone())
            messages = _messages_for_conversation(cur, conversation, int(user_id), limit)
        return {
            "ok": True,
            "conversation": _conversation_payload(conversation),
            "messages": [_message_payload(row, int(user_id)) for row in messages],
            "quick_prompts": pulse_ai_knowledge.quick_prompts(),
            "settings": _settings_payload(settings),
        }
    finally:
        conn.close()


def send_message(user_id: int, payload: dict | None = None) -> dict:
    payload = payload or {}
    body = _clean(payload.get("message") or payload.get("body") or payload.get("content") or "", MAX_MESSAGE_CHARS)
    correlation_id = _trace()
    if not body:
        return {"ok": False, "error": "empty_message", "message": "Ask Pulse AI something first.", "correlation_id": correlation_id, "http_status": 400}
    conn, cur = _open_db()
    started = time.perf_counter()
    try:
        conversation = _conversation_row(cur, int(user_id))
        settings = _settings_row(cur, int(user_id))
        allowed, limit = _rate_limit_ok(cur, int(user_id))
        if not allowed:
            _record_learning_event(cur, int(user_id), "rate_limited", "pulse_ai_messenger", {"limit_per_minute": limit})
            conn.commit()
            return {"ok": False, "error": "rate_limited", "message": "Pulse AI is receiving too many messages. Pause for a moment and try again.", "correlation_id": correlation_id, "http_status": 429}
        user_message_id = _insert_message(cur, int(conversation["id"]), int(user_id), "user", body, metadata={"client_message_id": payload.get("client_message_id") or ""})
        current_messages = _messages_for_conversation(cur, conversation, int(user_id), limit=40)
        knowledge = _retrieve_knowledge(cur, body)
        user_memory = _user_memory(cur, int(user_id), settings)
        prompt_messages = pulse_ai_knowledge.build_messages(body, _history_for_prompt(current_messages[:-1], settings), knowledge, user_memory)
        result = pulse_ai_provider_router.generate_response(prompt_messages, correlation_id=correlation_id, task="pulse_ai_messenger")
        if result.get("ok"):
            assistant_id = _insert_message(
                cur,
                int(conversation["id"]),
                int(user_id),
                "assistant",
                _clean(result.get("reply") or "", 6000),
                provider=result.get("provider") or "",
                provider_model=result.get("model") or "",
                latency_ms=int(result.get("latency_ms") or 0),
                correlation_id=correlation_id,
                metadata={"attempts": result.get("attempts") or [], "knowledge_ids": [item.get("id") for item in knowledge]},
            )
            _record_learning_event(cur, int(user_id), "message_answered", "pulse_ai_messenger", {"provider": result.get("provider"), "latency_ms": result.get("latency_ms"), "message_id": assistant_id})
            conn.commit()
            refreshed = get_conversation(int(user_id))
            return {
                "ok": True,
                "message_id": assistant_id,
                "user_message_id": user_message_id,
                "reply": result.get("reply") or "",
                "provider": result.get("provider") or "",
                "latency_ms": int(result.get("latency_ms") or 0),
                "correlation_id": correlation_id,
                **refreshed,
            }
        safe_message = result.get("message") or "Pulse AI is temporarily unavailable. Please try again soon."
        assistant_id = _insert_message(
            cur,
            int(conversation["id"]),
            int(user_id),
            "assistant",
            safe_message,
            error_code=result.get("error") or "ai_unavailable",
            correlation_id=correlation_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
            metadata={"attempts": result.get("attempts") or [], "reason": result.get("reason") or ""},
        )
        _record_learning_event(cur, int(user_id), "provider_failure", "pulse_ai_messenger", {"message_id": assistant_id, "reason": result.get("reason"), "attempts": result.get("attempts") or []})
        conn.commit()
        refreshed = get_conversation(int(user_id))
        return {
            "ok": False,
            "error": result.get("error") or "ai_unavailable",
            "message": safe_message,
            "correlation_id": correlation_id,
            "message_id": assistant_id,
            "user_message_id": user_message_id,
            "conversation": refreshed.get("conversation"),
            "messages": refreshed.get("messages"),
            "http_status": 503,
        }
    except Exception as exc:
        LOGGER.exception("PULSE_AI_MESSAGE_FAILED user_id=%s correlation_id=%s error=%s", int(user_id), correlation_id, exc.__class__.__name__)
        conn.rollback()
        return {"ok": False, "error": "ai_unavailable", "message": "Pulse AI is temporarily unavailable. Please try again soon.", "correlation_id": correlation_id, "http_status": 500}
    finally:
        conn.close()


def reset_conversation(user_id: int) -> dict:
    conn, cur = _open_db()
    try:
        conversation = _conversation_row(cur, int(user_id))
        now = _now()
        cur.execute("UPDATE pulse_ai_conversations SET reset_at=?, updated_at=? WHERE id=?", (now, now, int(conversation["id"])))
        _record_learning_event(cur, int(user_id), "conversation_reset", "pulse_ai_messenger")
        conn.commit()
        return get_conversation(int(user_id))
    finally:
        conn.close()


def status() -> dict:
    provider_status = pulse_ai_provider_router.provider_status()
    return {
        "ok": True,
        "assistant": "Pulse AI",
        "conversation_id": PULSE_AI_CONVERSATION_ID,
        "providers": provider_status.get("providers") or [],
        "configured_count": provider_status.get("configured_count") or 0,
        "fallback_order": provider_status.get("fallback_order") or [],
        "learning_modes": ["global_knowledge", "user_personalization", "contextual_assist", "admin_reviewed_learning"],
    }


def _settings_payload(row: dict) -> dict:
    return {
        "remember_preferences": bool(row.get("remember_preferences")),
        "use_pulse_ai_chat_history": bool(row.get("use_pulse_ai_chat_history")),
        "assist_with_messages_when_asked": bool(row.get("assist_with_messages_when_asked")),
        "improve_from_feedback": bool(row.get("improve_from_feedback")),
        "private_context_opt_in": bool(row.get("private_context_opt_in")),
    }


def get_settings(user_id: int) -> dict:
    conn, cur = _open_db()
    try:
        return {"ok": True, "settings": _settings_payload(_settings_row(cur, int(user_id)))}
    finally:
        conn.close()


def update_settings(user_id: int, payload: dict | None = None) -> dict:
    payload = payload or {}
    allowed = {
        "remember_preferences",
        "use_pulse_ai_chat_history",
        "assist_with_messages_when_asked",
        "improve_from_feedback",
        "private_context_opt_in",
    }
    conn, cur = _open_db()
    try:
        _settings_row(cur, int(user_id))
        updates = {key: 1 if bool(payload.get(key)) else 0 for key in allowed if key in payload}
        if updates:
            assignments = ", ".join(f"{key}=?" for key in updates)
            cur.execute(
                f"UPDATE pulse_ai_conversation_context_permissions SET {assignments}, updated_at=? WHERE user_id=?",
                tuple(updates.values()) + (_now(), int(user_id)),
            )
            _record_learning_event(cur, int(user_id), "settings_updated", "pulse_ai_settings", {"keys": sorted(updates)})
        conn.commit()
        return get_settings(int(user_id))
    finally:
        conn.close()


def clear_memory(user_id: int) -> dict:
    conn, cur = _open_db()
    try:
        now = _now()
        cur.execute("UPDATE pulse_ai_user_memory SET status='deleted', deleted_at=?, updated_at=? WHERE user_id=? AND COALESCE(deleted_at,'')=''", (now, now, int(user_id)))
        _record_learning_event(cur, int(user_id), "memory_cleared", "pulse_ai_settings")
        conn.commit()
        return {"ok": True, "message": "Pulse AI memory cleared."}
    finally:
        conn.close()


def export_memory(user_id: int) -> dict:
    conn, cur = _open_db()
    try:
        cur.execute("SELECT memory_key, memory_value, source, status, created_at, updated_at FROM pulse_ai_user_memory WHERE user_id=? AND COALESCE(deleted_at,'')=''", (int(user_id),))
        memories = [dict(row) for row in cur.fetchall()]
        return {"ok": True, "items": memories, "count": len(memories)}
    finally:
        conn.close()


def record_feedback(user_id: int, payload: dict | None = None) -> dict:
    payload = payload or {}
    rating = _clean(payload.get("rating") or "", 40).lower().replace(" ", "_")
    if rating not in {"helpful", "not_helpful", "wrong", "unsafe", "outdated"}:
        return {"ok": False, "error": "invalid_feedback", "message": "Choose a valid feedback option.", "http_status": 400}
    conn, cur = _open_db()
    try:
        settings = _settings_row(cur, int(user_id))
        if not int(settings.get("improve_from_feedback") or 0):
            return {"ok": False, "error": "feedback_disabled", "message": "Pulse AI feedback learning is disabled in your settings.", "http_status": 403}
        now = _now()
        public_id = f"pai_feedback_{secrets.token_urlsafe(10)}"
        message_id = int(payload.get("message_id") or 0)
        cur.execute(
            """
            INSERT INTO pulse_ai_feedback (public_id, user_id, message_id, rating, comment, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued_review', ?, ?)
            """,
            (public_id, int(user_id), message_id, rating, _clean(payload.get("comment") or "", 1000), now, now),
        )
        feedback_id = int(cur.lastrowid or 0)
        if rating in {"wrong", "unsafe", "outdated"}:
            cur.execute(
                """
                INSERT INTO pulse_ai_safety_reviews (public_id, feedback_id, review_status, notes, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (f"pai_review_{secrets.token_urlsafe(10)}", feedback_id, f"User marked answer {rating}", now, now),
            )
        _record_learning_event(cur, int(user_id), "feedback_recorded", "pulse_ai_feedback", {"rating": rating, "message_id": message_id})
        conn.commit()
        return {"ok": True, "feedback_id": feedback_id, "status": "queued_review", "message": "Thanks. Pulse AI will use this feedback safely."}
    finally:
        conn.close()


def admin_learning_dashboard() -> dict:
    conn, cur = _open_db()
    try:
        stats: dict[str, Any] = {}
        for table, key in [
            ("pulse_ai_knowledge_items", "knowledge_items"),
            ("pulse_ai_feedback", "feedback"),
            ("pulse_ai_learning_events", "learning_events"),
            ("pulse_ai_safety_reviews", "safety_reviews"),
            ("pulse_ai_feature_registry", "features"),
            ("pulse_ai_messages", "messages"),
        ]:
            cur.execute(f"SELECT COUNT(*) AS total FROM {table}")
            stats[key] = int(dict(cur.fetchone() or {}).get("total") or 0)
        cur.execute("SELECT rating, COUNT(*) AS total FROM pulse_ai_feedback GROUP BY rating ORDER BY total DESC")
        feedback_trends = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM pulse_ai_feedback ORDER BY id DESC LIMIT 30")
        feedback = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM pulse_ai_knowledge_items ORDER BY updated_at DESC, id DESC LIMIT 40")
        knowledge = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM pulse_ai_safety_reviews ORDER BY id DESC LIMIT 40")
        reviews = [dict(row) for row in cur.fetchall()]
        return {
            "ok": True,
            "stats": stats,
            "provider_status": pulse_ai_provider_router.provider_status(),
            "feedback_trends": feedback_trends,
            "recent_feedback": feedback,
            "knowledge_items": knowledge,
            "safety_reviews": reviews,
        }
    finally:
        conn.close()
