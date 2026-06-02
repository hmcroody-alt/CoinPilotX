"""Pulse Communications 2.0 table contracts.

Every table is v2-prefixed so the old Rooms/Groups/Messenger schema can remain
in place until the new system is proven.
"""

from __future__ import annotations

from dataclasses import dataclass


COMM_V2_TABLE_PREFIX = "comm_v2"


@dataclass(frozen=True)
class TableSpec:
    name: str
    create_sql: str
    indexes: tuple[str, ...] = ()


COMM_V2_TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "comm_v2_conversations",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT UNIQUE,
            conversation_type TEXT NOT NULL,
            title TEXT,
            description TEXT,
            owner_user_id INTEGER,
            created_by_user_id INTEGER,
            direct_key TEXT UNIQUE,
            community_id INTEGER,
            channel_id INTEGER,
            privacy TEXT DEFAULT 'private',
            visibility TEXT DEFAULT 'members',
            status TEXT DEFAULT 'active',
            is_discoverable INTEGER DEFAULT 0,
            member_count INTEGER DEFAULT 0,
            last_message_id INTEGER DEFAULT 0,
            last_message_at TEXT,
            last_activity_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
        """,
        (
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_conversations_type ON comm_v2_conversations(conversation_type, status)",
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_conversations_activity ON comm_v2_conversations(last_activity_at)",
        ),
    ),
    TableSpec(
        "comm_v2_participants",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT DEFAULT 'member',
            membership_state TEXT DEFAULT 'active',
            joined_at TEXT,
            left_at TEXT,
            muted_until TEXT,
            notifications_level TEXT DEFAULT 'all',
            last_seen_at TEXT,
            last_read_message_id INTEGER DEFAULT 0,
            last_read_at TEXT,
            unread_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(conversation_id, user_id)
        )
        """,
        (
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_participants_user ON comm_v2_participants(user_id, membership_state)",
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_participants_convo ON comm_v2_participants(conversation_id, membership_state)",
        ),
    ),
    TableSpec(
        "comm_v2_messages",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT UNIQUE,
            conversation_id INTEGER NOT NULL,
            sender_user_id INTEGER NOT NULL,
            message_type TEXT DEFAULT 'text',
            body TEXT,
            reply_to_message_id INTEGER DEFAULT 0,
            thread_root_message_id INTEGER DEFAULT 0,
            client_message_id TEXT,
            delivery_status TEXT DEFAULT 'sent',
            moderation_status TEXT DEFAULT 'approved',
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            edited_at TEXT,
            deleted_at TEXT
        )
        """,
        (
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_messages_convo_id ON comm_v2_messages(conversation_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_messages_sender ON comm_v2_messages(sender_user_id, created_at)",
        ),
    ),
    TableSpec(
        "comm_v2_attachments",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            media_upload_id INTEGER,
            uploader_user_id INTEGER,
            media_type TEXT,
            storage_provider TEXT,
            storage_key TEXT,
            url TEXT,
            thumbnail_url TEXT,
            mime_type TEXT,
            file_size_bytes INTEGER DEFAULT 0,
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            scan_status TEXT DEFAULT 'approved',
            created_at TEXT
        )
        """,
        (
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_attachments_message ON comm_v2_attachments(message_id)",
        ),
    ),
    TableSpec(
        "comm_v2_message_reactions",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_message_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reaction_type TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(message_id, user_id)
        )
        """,
    ),
    TableSpec(
        "comm_v2_read_receipts",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_read_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            delivered_at TEXT,
            seen_at TEXT,
            read_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(message_id, user_id)
        )
        """,
        (
            "CREATE INDEX IF NOT EXISTS idx_comm_v2_receipts_convo_user ON comm_v2_read_receipts(conversation_id, user_id)",
        ),
    ),
    TableSpec(
        "comm_v2_typing",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_typing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            is_typing INTEGER DEFAULT 1,
            expires_at TEXT,
            updated_at TEXT,
            UNIQUE(conversation_id, user_id)
        )
        """,
    ),
    TableSpec(
        "comm_v2_reports",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            reporter_user_id INTEGER NOT NULL,
            reported_user_id INTEGER DEFAULT 0,
            reason TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            reviewed_at TEXT,
            reviewed_by_admin_id INTEGER DEFAULT 0
        )
        """,
    ),
    TableSpec(
        "comm_v2_blocks",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker_user_id INTEGER NOT NULL,
            blocked_user_id INTEGER NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(blocker_user_id, blocked_user_id)
        )
        """,
    ),
    TableSpec(
        "comm_v2_moderation_events",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_moderation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            actor_user_id INTEGER DEFAULT 0,
            admin_user_id INTEGER DEFAULT 0,
            target_user_id INTEGER DEFAULT 0,
            event_type TEXT,
            reason TEXT,
            metadata_json TEXT,
            created_at TEXT
        )
        """,
    ),
    TableSpec(
        "comm_v2_communities",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_communities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT UNIQUE,
            name TEXT NOT NULL,
            slug TEXT UNIQUE,
            description TEXT,
            owner_user_id INTEGER NOT NULL,
            privacy TEXT DEFAULT 'public',
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
        """,
    ),
    TableSpec(
        "comm_v2_channels",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT UNIQUE,
            community_id INTEGER NOT NULL,
            conversation_id INTEGER DEFAULT 0,
            name TEXT NOT NULL,
            slug TEXT,
            description TEXT,
            channel_type TEXT DEFAULT 'text',
            visibility TEXT DEFAULT 'members',
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT,
            UNIQUE(community_id, slug)
        )
        """,
    ),
)


def table_names() -> tuple[str, ...]:
    return tuple(table.name for table in COMM_V2_TABLES)


def ensure_schema(cur) -> tuple[str, ...]:
    for table in COMM_V2_TABLES:
        cur.execute(table.create_sql)
        for index_sql in table.indexes:
            cur.execute(index_sql)
    return table_names()
