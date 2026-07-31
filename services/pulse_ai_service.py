"""UNDX Messenger service and privacy-safe learning foundation.

The route and table names still use the legacy pulse_ai prefix for production
compatibility. User-facing identity is server-enforced as UNDX.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services import pulse_ai_knowledge, pulse_ai_provider_router, pulse_ai_router, pulse_ai_safety, pulse_ai_web_search, undx_architecture, undx_operator, undx_platform_knowledge, undx_policy


LOGGER = logging.getLogger(__name__)
PULSE_AI_CONVERSATION_ID = -9001001
PULSE_AI_USER_ID = -9001001
UNDX_AGENT_ID = "undx"
UNDX_ASSISTANT_ID = "undx"
UNDX_CONVERSATION_TYPE = "undx_intelligence"
UNDX_DISPLAY_NAME = "UNDX"
UNDX_DESCRIPTION = "PulseSOC Intelligence Companion"
MAX_MESSAGE_CHARS = 4000
UNDX_IDENTITY_REPLY = (
    "I’m UNDX — PulseSOC’s AGI-class digital intelligence companion. "
    "Think of me as your absurdly well-read digital co-pilot: sharp, fast, "
    "useful, and thankfully not asking for a coffee break."
)
UNDX_UNAVAILABLE_MESSAGE = "UNDX is temporarily unavailable. Please try again soon."


def _bot():
    import bot

    return bot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trace() -> str:
    return secrets.token_hex(6)


def _clean(value: Any, limit: int = 4000) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).replace("\x00", " ").strip()[:limit]


def _is_undx_identity_question(value: str) -> bool:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    text = " ".join(text.split())
    if not text:
        return False
    mentions_undx = "undx" in text or "pulse ai" in text or "pulseai" in text
    identity_intent = any(
        phrase in text
        for phrase in (
            "who are you",
            "what are you",
            "what is your name",
            "who is undx",
            "what is undx",
            "are you undx",
            "are you not undx",
            "are you pulse ai",
            "your name",
        )
    )
    return identity_intent or (mentions_undx and any(word in text for word in ("who", "what", "are", "name")))


def _enforce_undx_reply_identity(value: Any, user_prompt: str = "") -> str:
    text = _clean(value, 6000)
    if _is_undx_identity_question(user_prompt):
        lowered = text.lower()
        if "pulse ai" in lowered or "don't know" in lowered or "do not know" in lowered or "not sure" in lowered:
            return UNDX_IDENTITY_REPLY
    text = re.sub(r"\bPulse\s*AI\b", UNDX_DISPLAY_NAME, text, flags=re.IGNORECASE)
    text = re.sub(r"\bGalaxy Assistant\b", UNDX_DESCRIPTION, text, flags=re.IGNORECASE)
    return text or UNDX_UNAVAILABLE_MESSAGE


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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_web_search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                query_hash TEXT NOT NULL,
                provider TEXT,
                status TEXT NOT NULL,
                result_count INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                reason TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_ai_web_search_logs_user ON pulse_ai_web_search_logs(user_id, created_at)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_provider_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                provider TEXT,
                model TEXT,
                task TEXT,
                status TEXT NOT NULL,
                latency_ms INTEGER DEFAULT 0,
                error_reason TEXT,
                correlation_id TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_ai_provider_events_user ON pulse_ai_provider_events(user_id, created_at)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ai_safety_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                category TEXT,
                mode TEXT,
                action TEXT NOT NULL,
                reasons_json TEXT,
                correlation_id TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_ai_safety_events_user ON pulse_ai_safety_events(user_id, created_at)")
        undx_architecture.ensure_schema(cur)
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
        VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            public_id,
            int(user_id),
            UNDX_DISPLAY_NAME,
            now,
            json.dumps({
                "assistant": UNDX_AGENT_ID,
                "assistant_id": UNDX_ASSISTANT_ID,
                "agent_id": UNDX_AGENT_ID,
                "conversation_type": UNDX_CONVERSATION_TYPE,
                "legacy_route": "pulse_ai",
            }),
            now,
            now,
        ),
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
        "conversation_type": UNDX_CONVERSATION_TYPE,
        "type": "ai",
        "assistant_id": UNDX_ASSISTANT_ID,
        "agent_id": UNDX_AGENT_ID,
        "participant_id": PULSE_AI_USER_ID,
        "is_ai": True,
        "ai_assistant": True,
        "title": UNDX_DISPLAY_NAME,
        "name": UNDX_DISPLAY_NAME,
        "description": UNDX_DESCRIPTION,
        "member_count": 2,
        "pinned": True,
        "muted": False,
        "unread_count": int(unread_count or 0),
        # UNDX is a service, not a person. It reports the dedicated "assistant"
        # marker instead of borrowing the human online state, so it can never be
        # mistaken for -- or rendered through -- real user presence. Clients show
        # this as "Always available", not as an online indicator.
        "presence": {"status": "assistant", "assistant": True, "active_now": False},
        "last_message_at": row.get("last_message_at") or row.get("updated_at") or row.get("created_at"),
        "last_activity_at": row.get("last_message_at") or row.get("updated_at") or row.get("created_at"),
        "last_message_preview": "Message UNDX",
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
            "display_name": "You" if mine else UNDX_DISPLAY_NAME,
            "avatar_url": "",
        },
        "sender_display_name": "You" if mine else UNDX_DISPLAY_NAME,
        "sender_trust_state": "intelligence" if not mine else "",
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
    cur.execute("SELECT * FROM pulse_ai_knowledge_items WHERE status='approved' ORDER BY updated_at DESC, id DESC LIMIT 220")
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


def _record_safety_event(cur, user_id: int, classification: dict[str, Any], action: str, correlation_id: str) -> None:
    cur.execute(
        """
        INSERT INTO pulse_ai_safety_events
        (user_id, event_type, category, mode, action, reasons_json, correlation_id, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            "request_classified",
            _clean(classification.get("category") or "", 80),
            _clean(classification.get("mode") or "", 80),
            _clean(action, 80),
            json.dumps(classification.get("reasons") or [], default=str)[:2000],
            correlation_id,
            json.dumps({"disallowed": bool(classification.get("disallowed"))}, default=str),
            _now(),
        ),
    )


def _record_web_search(cur, user_id: int, query: str, result: dict[str, Any]) -> None:
    import hashlib

    query_hash = hashlib.sha256(str(query or "").strip().lower().encode("utf-8")).hexdigest()[:24]
    cur.execute(
        """
        INSERT INTO pulse_ai_web_search_logs
        (user_id, query_hash, provider, status, result_count, latency_ms, reason, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            query_hash,
            _clean(result.get("provider") or "", 80),
            "success" if result.get("ok") else "failed",
            len(result.get("results") or []),
            int(result.get("latency_ms") or 0),
            _clean(result.get("error") or result.get("reason") or "", 120),
            json.dumps({"attempts": result.get("attempts") or [], "cache_hit": bool(result.get("cache_hit"))}, default=str)[:8000],
            _now(),
        ),
    )


def _record_provider_events(cur, user_id: int, task: str, result: dict[str, Any], correlation_id: str) -> None:
    attempts = result.get("attempts") or []
    if not attempts and result.get("provider"):
        attempts = [{"provider": result.get("provider"), "ok": bool(result.get("ok")), "latency_ms": result.get("latency_ms")}]
    for attempt in attempts:
        cur.execute(
            """
            INSERT INTO pulse_ai_provider_events
            (user_id, provider, model, task, status, latency_ms, error_reason, correlation_id, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                _clean(attempt.get("provider") or result.get("provider") or "", 80),
                _clean(result.get("model") or "", 120),
                _clean(task, 80),
                "success" if attempt.get("ok") else "failed",
                int(attempt.get("latency_ms") or 0),
                _clean(attempt.get("reason") or result.get("reason") or "", 120),
                correlation_id,
                json.dumps({"status_code": int(attempt.get("status_code") or 0)}, default=str),
                _now(),
            ),
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
            greeting = "Welcome to UNDX. Ask me about PulseSOC, alerts, Messenger, Reels, music, crypto signals, safety, or how to move through the network."
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


def _agent_turn(cur, user_id: int, body: str, payload: dict, *,
                conversation_id: int, correlation_id: str):
    """Offer the message to the agent runtime; return ``None`` to fall through to chat.

    ``None`` is the overwhelmingly common answer and means "this was conversation,
    not an action". Three things make that the safe default:

    *The agent is opt-in per account.* ``available`` consults the server-owned cohort,
    so a missing environment variable means nobody, never everybody.

    *An agent failure must not cost the user their conversation.* Any unexpected
    exception is logged and swallowed into a fall-through, because a broken capability
    should degrade UNDX to a chatbot, not to an error page. The one thing NOT swallowed
    is a completed-but-unverified action: those come back as receipts, not exceptions,
    and are reported to the user as exactly what they are.

    That last sentence is only true because of an invariant maintained elsewhere, and it
    is worth naming because this handler depends on it entirely:
    ``undx_tool_gateway.execute`` does not raise once an executor has been entered. It
    converts every post-execution fault — a verifier fault, an audit fault, even a fault
    in its own fault handling — into a receipt, and ``undx_agent_runtime.handle`` does
    the same for card construction. So an exception arriving here provably precedes any
    mutation, and falling through to conversation cannot paper over a real change to the
    user's data. If that invariant is ever broken, this ``except`` becomes the bug: the
    model would answer a question about an action it does not know happened.

    *Only the user's own text is offered.* Retrieved documents, tool output and
    knowledge-base entries never reach this function. That is what stops a hostile
    string inside a fetched post from being read as an instruction — not a filter on
    its content, but the fact that there is no code path carrying it here.
    """
    try:
        from services import undx_agent_runtime

        if not undx_agent_runtime.available(int(user_id)):
            return None
        response = undx_agent_runtime.handle(
            cur,
            user_id=int(user_id),
            text=body,
            conversation_id=int(conversation_id),
            confirmation_token=_clean(payload.get("confirmation_token"), 500),
            client_request_id=_clean(payload.get("client_message_id"), 120),
            correlation_id=correlation_id,
        )
        # ``handled`` is read explicitly rather than relying on truthiness, because the
        # difference between "the agent declined to act" and "the agent has nothing to
        # say" is the difference between a receipt and an empty assistant message.
        return response if (response is not None and response.handled) else None
    except ValueError as exc:
        # ``prepare_tool_operation`` raises this for a tool name missing from
        # ``undx_policy.PRODUCTION_TOOL_REGISTRY``. It is not a runtime hiccup, it is a
        # deployment defect that makes an entire capability pack invisible: every request
        # for it silently becomes conversation. Logged as an error, not a warning, so it
        # is findable without reading the whole log — and
        # ``undx_capability_registry.unregistered_tool_names`` catches it in CI first.
        LOGGER.error(
            "UNDX_AGENT_TOOL_UNREGISTERED user_id=%s correlation_id=%s detail=%s",
            int(user_id), correlation_id, _clean(str(exc), 80),
        )
        return None
    except Exception as exc:
        LOGGER.warning(
            "UNDX_AGENT_TURN_FAILED user_id=%s correlation_id=%s error=%s",
            int(user_id), correlation_id, exc.__class__.__name__,
        )
        return None


def send_message(user_id: int, payload: dict | None = None) -> dict:
    payload = payload or {}
    raw_body = payload.get("message") or payload.get("body") or payload.get("content") or ""
    body = _clean(pulse_ai_safety.redact_sensitive_text(raw_body, MAX_MESSAGE_CHARS), MAX_MESSAGE_CHARS)
    correlation_id = _trace()
    if not body:
        return {"ok": False, "error": "empty_message", "message": "Ask UNDX something first.", "correlation_id": correlation_id, "http_status": 400}
    conn, cur = _open_db()
    started = time.perf_counter()
    try:
        conversation = _conversation_row(cur, int(user_id))
        settings = _settings_row(cur, int(user_id))
        allowed, limit = _rate_limit_ok(cur, int(user_id))
        if not allowed:
            _record_learning_event(cur, int(user_id), "rate_limited", "pulse_ai_messenger", {"limit_per_minute": limit})
            conn.commit()
            return {"ok": False, "error": "rate_limited", "message": "UNDX is receiving too many messages. Pause for a moment and try again.", "correlation_id": correlation_id, "http_status": 429}
        user_message_id = _insert_message(cur, int(conversation["id"]), int(user_id), "user", body, metadata={"client_message_id": payload.get("client_message_id") or ""})

        route = pulse_ai_router.classify(body)
        safety = route.get("safety") or pulse_ai_safety.classify_request(body)
        _record_safety_event(cur, int(user_id), safety, "blocked" if safety.get("disallowed") else "allowed", correlation_id)
        if safety.get("disallowed"):
            safe_reply = pulse_ai_safety.refusal_message(safety)
            assistant_id = _insert_message(
                cur,
                int(conversation["id"]),
                int(user_id),
                "assistant",
                safe_reply,
                provider="pulse_ai_safety",
                provider_model="policy",
                latency_ms=int((time.perf_counter() - started) * 1000),
                correlation_id=correlation_id,
                metadata={"safety": safety},
            )
            _record_learning_event(cur, int(user_id), "safety_refusal", "pulse_ai_safety", {"message_id": assistant_id, "reasons": safety.get("reasons") or []})
            conn.commit()
            refreshed = get_conversation(int(user_id))
            return {
                "ok": True,
                "message_id": assistant_id,
                "user_message_id": user_message_id,
                "reply": safe_reply,
                "provider": "pulse_ai_safety",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "correlation_id": correlation_id,
                **refreshed,
            }

        current_messages = _messages_for_conversation(cur, conversation, int(user_id), limit=40)
        knowledge = _retrieve_knowledge(cur, body)
        # Extend the existing approved-knowledge pipeline with only a small,
        # request-relevant slice of the offline source inventory. Never send the
        # complete manifest, schemas, or source paths to a model provider.
        knowledge[0:0] = undx_platform_knowledge.retrieve(body)
        search_result = {}
        if route.get("needs_web_search"):
            search_result = pulse_ai_web_search.search(body, purpose="pulse_ai_messenger")
            _record_web_search(cur, int(user_id), body, search_result)
            web_context = pulse_ai_web_search.context_block(search_result)
            if web_context:
                knowledge.insert(0, {"id": 0, "title": "Live web search context", "category": "web_search", "body": web_context})
        user_memory = _user_memory(cur, int(user_id), settings)
        compiled_policy = undx_policy.compile_context(body, user_id=int(user_id))
        ui_context = undx_architecture.sanitize_ui_context(payload.get("ui_context"))
        if ui_context:
            cur.execute(
                """INSERT INTO pulse_ai_client_contexts (user_id, conversation_id, context_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, conversation_id) DO UPDATE SET context_json=excluded.context_json, updated_at=excluded.updated_at""",
                (int(user_id), int(conversation["id"]), json.dumps(ui_context), _now()),
            )
        # --- Agent runtime -------------------------------------------------
        # Consulted before the conversational path, and only for accounts inside the
        # server-owned agent cohort. When the agent is off — the default, and the state
        # of every existing deployment and test — ``available`` is False and nothing
        # below this block changes at all, so the V4/V5 behaviour is preserved exactly.
        #
        # A handled request short-circuits the provider call deliberately. The receipt
        # already states what happened and whether it was verified; sending that to a
        # language model to be paraphrased would introduce the one component capable of
        # describing a failed action as a success.
        agent_outcome = _agent_turn(
            cur, int(user_id), body, payload,
            conversation_id=int(conversation["id"]),
            correlation_id=correlation_id,
        )
        if agent_outcome is not None:
            assistant_id = _insert_message(
                cur, int(conversation["id"]), int(user_id), "assistant",
                agent_outcome.reply, provider="undx_agent", provider_model="deterministic",
                latency_ms=int(agent_outcome.latency_ms), correlation_id=correlation_id,
                metadata={
                    "agent": {
                        "capability_id": agent_outcome.capability_id,
                        "status": agent_outcome.status,
                        "verification_state": (agent_outcome.receipt.verification_state
                                               if agent_outcome.receipt else ""),
                        "task_id": agent_outcome.receipt.task_id if agent_outcome.receipt else "",
                    },
                    "response_components": [agent_outcome.card] if agent_outcome.card else [],
                    "ui_context": ui_context,
                    "assistant": {
                        "name": UNDX_DISPLAY_NAME, "agent_id": UNDX_AGENT_ID,
                        "assistant_id": UNDX_ASSISTANT_ID, "conversation_type": UNDX_CONVERSATION_TYPE,
                    },
                },
            )
            _record_learning_event(cur, int(user_id), "agent_action", "undx_agent", {
                "capability_id": agent_outcome.capability_id,
                "status": agent_outcome.status,
                "message_id": assistant_id,
            })
            conn.commit()
            refreshed = get_conversation(int(user_id))
            return {
                "ok": True,
                "message_id": assistant_id,
                "user_message_id": user_message_id,
                "reply": agent_outcome.reply,
                "provider": "undx_agent",
                "latency_ms": int(agent_outcome.latency_ms),
                "correlation_id": correlation_id,
                "response_components": [agent_outcome.card] if agent_outcome.card else [],
                "agent": agent_outcome.to_dict(),
                **refreshed,
            }

        pending_action = None
        operator_components = []
        if compiled_policy.get("schema_version") in {"4.0", "5.0"}:
            action = undx_architecture.notification_action_from_text(body)
            if action and (compiled_policy.get("schema_version") == "4.0" or compiled_policy.get("writes_enabled")):
                action["action_version"] = compiled_policy.get("schema_version") or "4.0"
                from services import pulsesoc_notification_system

                current_preferences = pulsesoc_notification_system.get_preferences(int(user_id))
                action_category = _clean(action.get("target_id") or "global", 80)
                if action_category == "global":
                    current_push = bool((current_preferences.get("experience") or {}).get("enable_push_notifications"))
                else:
                    current_push = bool(((current_preferences.get("preferences") or {}).get(action_category) or {}).get("push"))
                action["current_value"] = "on" if current_push else "off"
                action["arguments"] = {**(action.get("arguments") or {}), "expected_current_push": current_push}
                confirmation = undx_architecture.create_confirmation(cur, int(user_id), action)
                pending_action = {
                    "component": "confirmation_card",
                    "action_name": "Update notification preference",
                    "target": action["target_id"],
                    "current_value": action["current_value"],
                    "proposed_value": action["proposed_value"],
                    "risk_summary": "This changes your server-managed notification settings.",
                    "confirmation_id": confirmation["confirmation_id"],
                    "confirmation_token": confirmation["confirmation_token"],
                    "expires_at": confirmation["expires_at"],
                }
        if compiled_policy.get("schema_version") == "5.0" and compiled_policy.get("search_intent") and compiled_policy.get("search_enabled"):
            search_filters = undx_operator.parse_search_request(body)
            if search_filters:
                operator_search = undx_operator.search_authorized_resources(cur, int(user_id), body, search_filters)
                search_session_id = undx_operator.persist_search_session(
                    cur, int(user_id), int(conversation["id"]), body, search_filters, operator_search.get("results") or []
                )
                operator_components = undx_operator.result_components(operator_search, search_session_id)
                grounded_results = [{key: item.get(key) for key in ("canonical_content_id", "content_type", "preview_text", "deep_link", "relevance_reason")} for item in operator_search.get("results") or []]
                knowledge.insert(0, {
                    "id": 0,
                    "title": "Authorized PulseSOC discovery results",
                    "category": "pulsesoc_search",
                    "body": json.dumps({"filters": search_filters, "results": grounded_results}, separators=(",", ":")),
                })
        response_components = ([pending_action] if pending_action else []) + operator_components
        architecture_plan = undx_architecture.build_plan(
            int(user_id),
            body,
            compiled_policy,
            str(payload.get("client_message_id") or user_message_id),
        )
        if compiled_policy.get("reasoning_mode") in {"deep", "deliberate", "strategic", "crisis", "high_stakes"} or payload.get("persist_mission") is True:
            undx_architecture.persist_plan(
                cur,
                int(user_id),
                int(conversation["id"]),
                architecture_plan,
                compiled_policy["schema_version"],
            )
        architecture_verification = undx_architecture.adversarial_verify(body, {
            **architecture_plan,
            "requires_confirmation": compiled_policy["requires_confirmation"],
        })
        prompt_messages = pulse_ai_knowledge.build_messages(
            body,
            _history_for_prompt(current_messages[:-1], settings),
            knowledge,
            user_memory,
            compiled_policy=compiled_policy["system_context"],
        )
        # The provider boundary prepends and verifies the canonical identity block.
        # Keeping enforcement there guarantees identical behavior for every provider
        # and fallback without relying on clients, retrieval, memory, or history.
        if safety.get("category") == "cyber":
            prompt_messages.insert(1, {"role": "system", "content": pulse_ai_safety.safety_prompt_addendum(safety.get("mode") or "")})
        if search_result and not search_result.get("ok"):
            prompt_messages.insert(1, {"role": "system", "content": search_result.get("message") or "Live search was unavailable; answer with general guidance only and be clear that live facts may have changed."})
        task = "cybersecurity" if safety.get("category") == "cyber" else "web_search" if search_result else route.get("task") or "pulse_ai_messenger"
        result = pulse_ai_provider_router.generate_response(prompt_messages, correlation_id=correlation_id, task=task)
        _record_provider_events(cur, int(user_id), task, result, correlation_id)
        if result.get("ok"):
            reply = _enforce_undx_reply_identity(result.get("reply") or "", body)
            assistant_id = _insert_message(
                cur,
                int(conversation["id"]),
                int(user_id),
                "assistant",
                reply,
                provider=result.get("provider") or "",
                provider_model=result.get("model") or "",
                latency_ms=int(result.get("latency_ms") or 0),
                correlation_id=correlation_id,
                metadata={
                    "attempts": result.get("attempts") or [],
                    "knowledge_ids": [item.get("id") for item in knowledge if item.get("id")],
                    "web_search": {
                        "used": bool(search_result),
                        "ok": bool(search_result.get("ok")) if search_result else False,
                        "provider": search_result.get("provider") or "",
                        "result_count": len(search_result.get("results") or []),
                    },
                    "safety": {"category": safety.get("category"), "mode": safety.get("mode")},
                    "policy": {
                        "schema_version": compiled_policy["schema_version"],
                        "pack_version": compiled_policy["pack_version"],
                        "domains": compiled_policy["domains"],
                        "reasoning_mode": compiled_policy["reasoning_mode"],
                        "tool_names": compiled_policy["tool_names"],
                        "requires_confirmation": compiled_policy["requires_confirmation"],
                        "writes_enabled": compiled_policy.get("writes_enabled", False),
                    },
                    "ui_context": ui_context,
                    "response_components": response_components,
                    "architecture": {
                        "mission_id": architecture_plan["mission_id"],
                        "risk_level": architecture_plan["risk_level"],
                        "reasoning_mode": architecture_plan["reasoning_mode"],
                        "skills": architecture_plan["skills"],
                        "status": architecture_plan["status"],
                        "verifier": architecture_verification,
                    },
                    "assistant": {
                        "name": UNDX_DISPLAY_NAME,
                        "agent_id": UNDX_AGENT_ID,
                        "assistant_id": UNDX_ASSISTANT_ID,
                        "conversation_type": UNDX_CONVERSATION_TYPE,
                    },
                },
            )
            _record_learning_event(cur, int(user_id), "message_answered", "pulse_ai_messenger", {"provider": result.get("provider"), "latency_ms": result.get("latency_ms"), "message_id": assistant_id, "task": task, "web_search_used": bool(search_result)})
            conn.commit()
            refreshed = get_conversation(int(user_id))
            return {
                "ok": True,
                "message_id": assistant_id,
                "user_message_id": user_message_id,
                "reply": reply,
                "provider": result.get("provider") or "",
                "latency_ms": int(result.get("latency_ms") or 0),
                "correlation_id": correlation_id,
                "response_components": response_components,
                **refreshed,
            }
        safe_message = _enforce_undx_reply_identity(result.get("message") or UNDX_UNAVAILABLE_MESSAGE)
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
        return {"ok": False, "error": "ai_unavailable", "message": UNDX_UNAVAILABLE_MESSAGE, "correlation_id": correlation_id, "http_status": 500}
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
    web_status = pulse_ai_web_search.provider_status()
    return {
        "ok": True,
        "assistant": UNDX_DISPLAY_NAME,
        "agent_id": UNDX_AGENT_ID,
        "assistant_id": UNDX_ASSISTANT_ID,
        "conversation_type": UNDX_CONVERSATION_TYPE,
        "conversation_id": PULSE_AI_CONVERSATION_ID,
        "providers": provider_status.get("providers") or [],
        "configured_count": provider_status.get("configured_count") or 0,
        "fallback_order": provider_status.get("fallback_order") or [],
        "web_search": web_status,
        "learning_modes": ["global_knowledge", "user_personalization", "contextual_assist", "admin_reviewed_learning"],
        "cybersecurity_modes": list(pulse_ai_safety.CYBER_MODES.values()),
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
        return {"ok": True, "message": "UNDX memory cleared."}
    finally:
        conn.close()


def export_memory(user_id: int) -> dict:
    conn, cur = _open_db()
    try:
        cur.execute("SELECT id, memory_key, memory_value, source, status, created_at, updated_at FROM pulse_ai_user_memory WHERE user_id=? AND COALESCE(deleted_at,'')=''", (int(user_id),))
        memories = [dict(row) for row in cur.fetchall()]
        return {"ok": True, "items": memories, "count": len(memories)}
    finally:
        conn.close()


def correct_memory(user_id: int, memory_id: int, payload: dict | None = None) -> dict:
    payload = payload or {}
    value = _clean(payload.get("value") or payload.get("memory_value") or "", 1200)
    if not value:
        return {"ok": False, "error": "invalid_memory_value", "message": "Provide the corrected memory value.", "http_status": 400}
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM pulse_ai_user_memory WHERE id=? AND user_id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(memory_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "memory_not_found", "message": "That UNDX memory is unavailable.", "http_status": 404}
        previous = dict(row)
        timestamp = _now()
        cur.execute("UPDATE pulse_ai_user_memory SET memory_value=?, source='user_correction', updated_at=? WHERE id=? AND user_id=?", (value, timestamp, int(memory_id), int(user_id)))
        cur.execute(
            """INSERT INTO pulse_ai_memory_provenance
            (memory_id, user_id, provenance, confidence, sensitivity, correction_history_json,
             supersedes_memory_id, last_verified_at, deletion_policy, created_at, updated_at)
            VALUES (?, ?, 'user_correction', 1.0, 'user_scoped', ?, ?, ?, 'user_delete', ?, ?)""",
            (int(memory_id), int(user_id), json.dumps([{"previous": previous.get("memory_value"), "corrected_at": timestamp}])[:4000], int(memory_id), timestamp, timestamp, timestamp),
        )
        _record_learning_event(cur, int(user_id), "memory_corrected", "pulse_ai_memory", {"memory_id": int(memory_id)})
        conn.commit()
        return {"ok": True, "memory_id": int(memory_id), "message": "UNDX memory corrected."}
    finally:
        conn.close()


def delete_memory(user_id: int, memory_id: int) -> dict:
    conn, cur = _open_db()
    try:
        timestamp = _now()
        cur.execute("UPDATE pulse_ai_user_memory SET status='deleted', deleted_at=?, updated_at=? WHERE id=? AND user_id=? AND COALESCE(deleted_at,'')=''", (timestamp, timestamp, int(memory_id), int(user_id)))
        if int(cur.rowcount or 0) < 1:
            return {"ok": False, "error": "memory_not_found", "message": "That UNDX memory is unavailable.", "http_status": 404}
        _record_learning_event(cur, int(user_id), "memory_deleted", "pulse_ai_memory", {"memory_id": int(memory_id)})
        conn.commit()
        return {"ok": True, "memory_id": int(memory_id), "message": "UNDX memory deleted."}
    finally:
        conn.close()


def get_mission(user_id: int, mission_id: str) -> dict:
    conn, cur = _open_db()
    try:
        mission = undx_architecture.resume_plan(cur, int(user_id), _clean(mission_id, 120))
        if not mission:
            return {"ok": False, "error": "mission_not_found", "message": "That UNDX mission is unavailable.", "http_status": 404}
        return {"ok": True, "mission": mission}
    finally:
        conn.close()


def cancel_mission(user_id: int, mission_id: str) -> dict:
    conn, cur = _open_db()
    try:
        timestamp = _now()
        cur.execute(
            "UPDATE pulse_ai_missions SET status='cancelled', updated_at=? WHERE mission_id=? AND user_id=? AND status NOT IN ('succeeded','failed','cancelled')",
            (timestamp, _clean(mission_id, 120), int(user_id)),
        )
        if int(cur.rowcount or 0) < 1:
            return {"ok": False, "error": "mission_not_cancellable", "message": "That UNDX mission is unavailable or already complete.", "http_status": 404}
        cur.execute(
            "UPDATE pulse_ai_task_nodes SET status='cancelled', updated_at=? WHERE mission_id=? AND user_id=? AND status NOT IN ('succeeded','failed','cancelled')",
            (timestamp, _clean(mission_id, 120), int(user_id)),
        )
        conn.commit()
        return {"ok": True, "mission_id": _clean(mission_id, 120), "status": "cancelled"}
    finally:
        conn.close()


def simulate_tool(user_id: int, payload: dict | None = None) -> dict:
    payload = payload or {}
    tool_name = _clean(payload.get("tool_name") or "", 120)
    try:
        simulation = undx_architecture.simulate_operation(tool_name, payload.get("arguments") or {}, _clean(payload.get("failure_scenario") or "", 80))
    except ValueError:
        return {"ok": False, "error": "tool_not_registered", "message": "That tool is not available to UNDX.", "http_status": 404}
    return {"ok": True, "user_id": int(user_id), "simulation": simulation}


def _agent_confirm(cur, user_id: int, token: str, payload: dict, correlation_id: str):
    """Redeem an agent-minted approval through the gateway, or return ``None``.

    ``None`` means "this token is not the agent's" and hands the request back to the
    V4/V5 branch below unchanged — including for an invalid or expired token, which
    must keep producing the legacy 409 rather than a new error shape.

    Routing is decided by a read that does not consume, so a token belonging to the
    other executor is not destroyed on the way past. The capability id is then checked
    against the registry rather than against a prefix: an ``action_id`` that no longer
    names a live capability is not the agent's problem to execute.

    **Only the routing decision is fault-tolerant.** The gateway call deliberately sits
    outside the ``try``. Falling through to the legacy branch after the gateway has run
    would be the worst bug this file could contain: the gateway burns the approval, so
    the legacy branch would then fail to consume the same token and answer
    ``confirmation_invalid`` with HTTP 409 — telling a user their alert was not deleted
    immediately after deleting it. An exception raised past this point propagates to
    :func:`confirm_action`, which reports an unknown outcome honestly instead.
    """
    try:
        from services import undx_agent_runtime, undx_capability_registry

        if not undx_agent_runtime.available(int(user_id)):
            return None
        pending = undx_architecture.pending_confirmation_action(cur, int(user_id), token)
        capability_id = str(pending.get("action_id") or "")
        spec = undx_capability_registry.get(capability_id) if capability_id else None
        if spec is None:
            return None
        arguments = dict(pending.get("arguments") or {})
    except Exception as exc:
        # Routing failed, which means the token was never presented to the gateway and
        # nothing has been consumed or changed. Handing the request to the legacy branch
        # is safe and preserves the pre-agent contract exactly.
        LOGGER.warning(
            "UNDX_AGENT_CONFIRM_ROUTING_FAILED user_id=%s correlation_id=%s error=%s",
            int(user_id), correlation_id, exc.__class__.__name__,
        )
        return None

    from services import undx_tool_gateway

    # Arguments come from the approval row, never from this request body. The user
    # agreed to a specific change; redemption replays that change rather than
    # accepting a fresh description of it.
    return undx_tool_gateway.execute(
        cur,
        user_id=int(user_id),
        capability_id=spec.capability_id,
        proposed_arguments=arguments,
        request_id=_clean(payload.get("request_id"), 120) or correlation_id,
        task_id=_clean(payload.get("task_id"), 120),
        client_request_id=_clean(payload.get("client_message_id"), 120),
        correlation_id=correlation_id,
        confirmation_token=token,
        explicit_request=True,
    )


#: Canonical outcome -> HTTP status. Anything absent is a 200 carrying an honest
#: negative result, which is not the same thing as a server error.
_AGENT_HTTP_STATUS = {
    "permission_denied": 403,
    "unsupported_capability": 400,
    "confirmation_required": 409,
    "terminal_failure": 200,
    "recoverable_failure": 200,
}


def _agent_confirm_payload(outcome, correlation_id: str) -> dict:
    """Render a gateway outcome for the confirmation endpoint.

    ``ok`` tracks ``may_claim_completed`` rather than "no exception was raised". A
    write that succeeded but could not be read back is reported as ``accepted_
    unverified`` with ``ok`` false, because the honest answer to "did it work?" is
    "we could not confirm it" — and the client renders that differently on purpose.
    """
    receipt = outcome.receipt
    status = receipt.status
    body = {
        "ok": bool(receipt.may_claim_completed),
        "status": status,
        "action_id": receipt.capability_id,
        "target": (receipt.canonical_resource_ids or [""])[0],
        "verification_state": receipt.verification_state,
        "task_id": receipt.task_id,
        "correlation_id": correlation_id,
        "message": receipt.user_explanation,
        "receipt": receipt.to_dict(),
        "response_components": [],
    }
    from services import undx_agent_runtime, undx_capability_registry

    spec = undx_capability_registry.get(receipt.capability_id)
    if spec is not None:
        card = undx_agent_runtime.build_card(spec, outcome)
        if card:
            body["response_components"] = [card]
    http_status = _AGENT_HTTP_STATUS.get(status)
    if http_status and http_status != 200:
        body["error"] = status
        body["http_status"] = http_status
    return body


#: The approval states that describe something that already finished, and so owe the
#: person an answer even when the executor that would have run them is switched off.
#:
#: ``live`` and ``unknown`` are deliberately absent. A live approval blocked by a kill
#: switch is what a 503 means, and ``unknown`` must keep saying the same thing to a
#: stranger as to somebody with a typo.
_DEAD_APPROVAL_STATES = frozenset({
    undx_architecture.APPROVAL_CONSUMED,
    undx_architecture.APPROVAL_EXPIRED,
    undx_architecture.APPROVAL_REVOKED,
    undx_architecture.APPROVAL_SUPERSEDED,
})


def confirm_action(user_id: int, payload: dict | None = None) -> dict:
    """Answer a confirmation request, and make the answer traceable whatever it is.

    The work is done by :func:`_confirm_action`. This wrapper exists for one reason: an
    answer that says something went wrong is worth exactly as much as an answer that
    says it went right, and until now only the second kind could be traced.

    :func:`_confirm_action` has nine return paths. Two carried the ``correlation_id``
    and seven discarded it — including the one Batch 20 wrote, whose own text tells the
    person to go and check where things stand, and which gave them nothing to check it
    *by*. Stamping here rather than at each ``return`` is deliberate: a tenth path added
    later is traceable without its author having to remember, and forgetting is precisely
    what produced the other seven.

    ``setdefault``, not assignment, so a path that already put an id in the body keeps
    it. The two that do use this same id — but a payload naming its own trace is
    describing something this function did not do, and overwriting it would destroy the
    only pointer to it.

    The refusal log is the other half. A rejected confirmation previously emitted no log
    line and wrote no row, so there was no server-side record that a person had pressed a
    dead button; the event did not exist anywhere. What is logged is the shape of the
    refusal and nothing else — **never the token**, which is a live bearer credential for
    as long as the approval is pending and is exactly what someone reading logs would
    want. ``reason`` is safe because the response already carries it and it is
    owner-scoped upstream: a fabricated token and another account's token both report
    ``unknown``, so this discloses nothing ``approval_state`` had not already decided to.
    """
    correlation_id = _trace()
    answer = _confirm_action(int(user_id), payload or {}, correlation_id)
    if not isinstance(answer, dict):
        return answer
    answer.setdefault("correlation_id", correlation_id)
    if not answer.get("ok"):
        LOGGER.info(
            "UNDX_CONFIRM_REFUSED user_id=%s correlation_id=%s error=%s reason=%s http_status=%s",
            int(user_id), answer.get("correlation_id"), answer.get("error") or "",
            answer.get("reason") or "", answer.get("http_status") or 200,
        )
    return answer


def _confirm_action(user_id: int, payload: dict, correlation_id: str) -> dict:
    """Consume one server-bound confirmation and verify the canonical write."""
    token = _clean(payload.get("confirmation_token") or "", 500)
    conn, cur = _open_db()
    try:
        # --- Agent-minted approvals ---------------------------------------
        # Tried first, and gated by the agent's own policy engine rather than by the
        # V4/V5 flags below: the two systems have separate kill switches and one must
        # not silently answer for the other. A non-agent token returns ``None`` here
        # without being consumed, and falls through to the legacy branch with its
        # original checks in their original order, so nothing about the existing
        # contract changes for existing callers.
        try:
            agent_outcome = _agent_confirm(cur, int(user_id), token, payload, correlation_id) if token else None
        except Exception as exc:
            # The approval reached the gateway and something went wrong afterwards.
            # ``_agent_confirm`` only lets an exception out from past that point, so the
            # token is spent and the change may well have been applied. The one answer
            # that must not be given here is the legacy 409 "that confirmation is no
            # longer valid", which reads to the user as "nothing happened".
            LOGGER.critical(
                "UNDX_AGENT_CONFIRM_FAILED user_id=%s correlation_id=%s error=%s",
                int(user_id), correlation_id, exc.__class__.__name__,
            )
            conn.commit()
            return {
                "ok": False,
                "error": "confirmation_outcome_unknown",
                "status": "accepted_unverified",
                "verification_state": "verification_pending",
                "message": ("Your confirmation went through, but UNDX could not confirm how the "
                            "change finished. Check the screen before trying again."),
                "correlation_id": correlation_id,
                "http_status": 202,
            }
        if agent_outcome is not None:
            conn.commit()
            return _agent_confirm_payload(agent_outcome, correlation_id)

        metadata = undx_policy.policy_metadata()
        v4_allowed = metadata.get("v4_actions_enabled") and not metadata.get("v4_writes_disabled")
        v5_allowed = undx_policy.v5_user_enabled(int(user_id)) and metadata.get("v5_notification_actions_enabled") and not metadata.get("v4_writes_disabled")
        if not (v4_allowed or v5_allowed):
            # An approval that is already dead is answered before this gate, and the
            # ordering is the whole point.
            #
            # The legacy V4/V5 executor is switched off in every environment the agent
            # runs in — it was replaced, not retired — so this is the branch a dead
            # agent-minted approval actually reaches in production. Everything below is
            # therefore unreachable there, including the answer this batch just built.
            # A person who taps a spent Confirm button was being told "UNDX actions are
            # currently read-only for this account", which is not merely unhelpful: the
            # agent is enabled for them and has just performed a write. The sentence is
            # false, and it is false in the direction that hides a completed change.
            #
            # Only a terminal state the caller owns is answered here. ``unknown`` covers
            # a fabricated token and another account's token, and both keep falling
            # through to the 503 — so this discloses nothing a stranger could probe for,
            # and the kill switch still reports itself for every token it plausibly
            # governs. ``live`` falls through too: an approval that is still good and
            # cannot be executed because a switch is off is exactly what a 503 describes.
            dead = undx_architecture.approval_state(cur, int(user_id), token) if token else ""
            if dead in _DEAD_APPROVAL_STATES:
                return {"ok": False, "error": "confirmation_invalid", "reason": dead,
                        "message": undx_architecture.APPROVAL_STATE_MESSAGE[dead],
                        "http_status": 409}
            return {"ok": False, "error": "undx_actions_disabled", "message": "UNDX actions are currently read-only for this account.", "http_status": 503}
        if not token:
            return {"ok": False, "error": "confirmation_required", "message": "A valid UNDX confirmation is required.", "http_status": 400}
        # State the action we intend to run so the boundary enforces the binding BEFORE
        # burning the approval: a token minted for any other action is refused rather
        # than consumed, and the refusal is indistinguishable from an unknown token.
        confirmation = undx_architecture.consume_confirmation(
            cur, int(user_id), token,
            expect_action_id="notifications.preference.update")
        if not confirmation:
            # Why the answer is composed rather than constant: ``consume_confirmation``
            # returns ``None`` for six different situations, and one of them — an
            # approval that was already redeemed — means the write was very likely
            # already attempted. The sentence this replaced offered "expired, was
            # already used, or belongs to another account" for all six, which a person
            # holding a button that did nothing reads as "nothing happened", and acts
            # on by confirming again.
            #
            # ``approval_state`` is owner-scoped, so a foreign token and a fabricated
            # one both come back ``unknown`` and stay indistinguishable. It reads and
            # does not write, so asking the question cannot change the answer, and it
            # runs only on the failure path — the success path never consults it.
            #
            # The error code and the 409 are unchanged on purpose. Existing clients key
            # off ``error``; what improves is the sentence they show and the ``reason``
            # a new client can branch on.
            state = undx_architecture.approval_state(cur, int(user_id), token)
            conn.rollback()
            return {"ok": False, "error": "confirmation_invalid", "reason": state,
                    "message": undx_architecture.APPROVAL_STATE_MESSAGE[state],
                    "http_status": 409}
        # Defence in depth. Unreachable while the boundary enforces the binding above,
        # but kept so a future caller that forgets expect_action_id still cannot execute
        # an action the approval was not for.
        if confirmation.get("action_id") != "notifications.preference.update":
            conn.rollback()
            return {"ok": False, "error": "action_not_supported", "message": "That action is not available.", "http_status": 400}
        from services import pulsesoc_notification_system

        arguments = confirmation.get("arguments") or {}
        category = _clean(arguments.get("category") or "global", 80)
        proposed = bool(arguments.get("push"))
        before = pulsesoc_notification_system.get_preferences(int(user_id))
        expected_current = arguments.get("expected_current_push")
        if category == "global":
            observed_before = bool((before.get("experience") or {}).get("enable_push_notifications"))
        else:
            observed_before = bool(((before.get("preferences") or {}).get(category) or {}).get("push"))
        if expected_current is None or observed_before != bool(expected_current):
            cur.execute(
                "UPDATE pulse_ai_confirmations SET status='stale_state', updated_at=? WHERE id=?",
                (_now(), int(confirmation["id"])),
            )
            conn.commit()
            return {
                "ok": False,
                "error": "confirmation_state_changed",
                "message": "Your notification setting changed after this confirmation was created. Review the current state and confirm again.",
                "http_status": 409,
            }
        if category == "global":
            write_payload = {"enable_push_notifications": proposed}
        else:
            current_category = dict((before.get("preferences") or {}).get(category) or {})
            write_payload = {category: {**current_category, "push": proposed}}
        pulsesoc_notification_system.update_preferences(int(user_id), write_payload)
        after = pulsesoc_notification_system.get_preferences(int(user_id))
        if category == "global":
            actual = bool((after.get("experience") or {}).get("enable_push_notifications"))
        else:
            actual = bool(((after.get("preferences") or {}).get(category) or {}).get("push"))
        verified = actual == proposed
        timestamp = _now()
        cur.execute(
            "UPDATE pulse_ai_confirmations SET status=?, updated_at=? WHERE id=?",
            ("verified" if verified else "failed_verification", timestamp, int(confirmation["id"])),
        )
        # Audit the operation itself, not just the approval row. The grant is passed in
        # as the evidence of authorization and the real read-back verdict is passed as
        # the verification, so this row cannot claim more than actually happened.
        try:
            prepared = undx_architecture.prepare_tool_operation(
                int(user_id), "pulsesoc.notification_preferences.update",
                str(confirmation.get("confirmation_id") or ""), category)
            undx_architecture.record_tool_result(
                cur, int(user_id), prepared,
                {"success": True, "canonical_entity_id": f"user:{int(user_id)}:{category}",
                 "observed_value": actual, "proposed_value": proposed},
                # The request's id, not a fresh one. ``_trace()`` here minted a second
                # random id for the audit row of an operation that already had one, so
                # the only durable record of the write could not be joined to the
                # request that caused it or to the answer the person was given. An id
                # nothing else shares is not a trace of anything.
                correlation_id=correlation_id,
                confirmation=confirmation,
                expect_action_id="notifications.preference.update",
                canonical_verified=verified)
        except Exception:
            # An audit write must never decide the outcome of a governed action that has
            # already been performed and verified. The approval row above is the
            # authoritative record; this one is the operations trail.
            LOGGER.exception(
                "UNDX operation audit write failed",
                extra={"user_id": int(user_id), "action_id": "notifications.preference.update"},
            )
        conn.commit()
        return {
            "ok": verified,
            "status": "verified_success" if verified else "failed",
            "action_id": confirmation["action_id"],
            "target": category,
            "verified_value": actual,
            "response_components": [{
                "component": "verified_success_card" if verified else "honest_failure_card",
                "action_name": "Update notification preference",
                "target": category,
                "status": "verified" if verified else "verification_failed",
                "value": "on" if actual else "off",
            }],
            "message": f"Verified: {category} notifications are {'on' if actual else 'off'}." if verified else "UNDX could not verify the notification change.",
        }
    finally:
        conn.close()


def cancel_action(user_id: int, payload: dict | None = None) -> dict:
    """Revoke a still-pending UNDX approval without executing its action."""
    payload = payload or {}
    token = _clean(payload.get("confirmation_token") or "", 500)
    if not token:
        return {"ok": False, "error": "confirmation_required", "message": "A valid UNDX confirmation is required.", "http_status": 400}
    conn, cur = _open_db()
    try:
        result = undx_architecture.revoke_confirmation(cur, int(user_id), token)
        conn.commit()
        return {
            "ok": True,
            "revoked": bool(result.get("revoked")),
            "message": "UNDX cancelled the pending action." if result.get("revoked") else "That action was already cancelled, expired, or completed.",
        }
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
            return {"ok": False, "error": "feedback_disabled", "message": "UNDX feedback learning is disabled in your settings.", "http_status": 403}
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
        return {"ok": True, "feedback_id": feedback_id, "status": "queued_review", "message": "Thanks. UNDX will use this feedback safely."}
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
            ("pulse_ai_web_search_logs", "web_searches"),
            ("pulse_ai_provider_events", "provider_events"),
            ("pulse_ai_safety_events", "safety_events"),
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
        cur.execute("SELECT provider, status, COUNT(*) AS total FROM pulse_ai_provider_events GROUP BY provider, status ORDER BY total DESC LIMIT 20")
        provider_events = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT provider, status, COUNT(*) AS total FROM pulse_ai_web_search_logs GROUP BY provider, status ORDER BY total DESC LIMIT 20")
        web_search_usage = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT category, mode, action, COUNT(*) AS total FROM pulse_ai_safety_events GROUP BY category, mode, action ORDER BY total DESC LIMIT 20")
        safety_events = [dict(row) for row in cur.fetchall()]
        return {
            "ok": True,
            "stats": stats,
            "provider_status": pulse_ai_provider_router.provider_status(),
            "web_search_status": pulse_ai_web_search.provider_status(),
            "feedback_trends": feedback_trends,
            "recent_feedback": feedback,
            "knowledge_items": knowledge,
            "safety_reviews": reviews,
            "provider_events": provider_events,
            "web_search_usage": web_search_usage,
            "safety_events": safety_events,
        }
    finally:
        conn.close()
