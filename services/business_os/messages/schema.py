"""Business OS — Section 6: Messages schema bridge.

This module owns NO new message table. It idempotently ensures the ONE canonical
message engine's tables exist (``pulse_conversations`` /
``pulse_conversation_participants`` / ``pulse_messages`` / ``pulse_message_reports``)
and adds a single additive ``business_id`` tag column to ``pulse_conversations`` so a
business inbox is just a tagged canonical conversation.

Every ``CREATE`` is ``IF NOT EXISTS`` and every column add is guarded, so this is a
no-op against a production database where bot.py already created the (richer) canonical
tables — it never clobbers or forks them. Against a fresh test database it materialises
a compatible subset so the facade can be exercised in isolation.
"""

from __future__ import annotations

from services import db


# The canonical message tables (same names the whole platform uses). A compatible
# subset of columns — bot.py's own ensure adds the rest via IF-NOT-EXISTS/add-column.
_CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS pulse_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_type TEXT DEFAULT 'direct',
    created_by_user_id INTEGER,
    owner_user_id INTEGER,
    business_id TEXT,
    title TEXT,
    status TEXT DEFAULT 'active',
    is_public INTEGER DEFAULT 0,
    member_count INTEGER DEFAULT 0,
    last_message_at TEXT,
    last_activity_at TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

_PARTICIPANTS = """
CREATE TABLE IF NOT EXISTS pulse_conversation_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    user_id INTEGER,
    role TEXT DEFAULT 'member',
    muted INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    last_read_at TEXT,
    last_read_message_id INTEGER DEFAULT 0,
    unread_count INTEGER DEFAULT 0,
    joined_at TEXT,
    left_at TEXT,
    created_at TEXT
)
"""

_MESSAGES = """
CREATE TABLE IF NOT EXISTS pulse_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER DEFAULT 0,
    conversation_id INTEGER,
    sender_user_id INTEGER,
    receiver_user_id INTEGER DEFAULT 0,
    body TEXT,
    message_type TEXT DEFAULT 'text',
    media_url TEXT,
    client_message_id TEXT,
    reply_to_id INTEGER,
    delivery_status TEXT DEFAULT 'sent',
    status TEXT DEFAULT 'sent',
    read_at TEXT,
    deleted_at TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

_REPORTS = """
CREATE TABLE IF NOT EXISTS pulse_message_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    conversation_id INTEGER,
    reporter_user_id INTEGER,
    reason TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT,
    updated_at TEXT
)
"""

# Indexes that make business-inbox / thread reads efficient without changing shape.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_pulse_conv_business "
    "ON pulse_conversations(business_id)",
    "CREATE INDEX IF NOT EXISTS idx_pulse_msg_conv "
    "ON pulse_messages(conversation_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_pulse_part_user "
    "ON pulse_conversation_participants(user_id)",
)


def _columns(conn, table: str) -> set:
    rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    out = set()
    for r in rows:
        try:
            out.add(r["name"])
        except (TypeError, KeyError, IndexError):
            out.add(r[1])
    return out


def _add_col_if_missing(conn, table: str, col: str, decl: str) -> None:
    if col not in _columns(conn, table):
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))


def ensure_schema(conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(_CONVERSATIONS)
        conn.execute(_PARTICIPANTS)
        conn.execute(_MESSAGES)
        conn.execute(_REPORTS)
        # Additive tag column — safe against pre-existing production tables.
        _add_col_if_missing(conn, "pulse_conversations", "business_id", "TEXT")
        for stmt in _INDEXES:
            conn.execute(stmt)
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
