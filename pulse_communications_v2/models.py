"""Inactive Pulse Communications 2.0 model and table contracts.

These dataclasses describe the v2 data foundation without binding the current
application to a new ORM or modifying legacy messaging tables.
"""

from __future__ import annotations

from dataclasses import dataclass


COMM_V2_TABLE_PREFIX = "comm_v2"


@dataclass(frozen=True)
class TableSpec:
    name: str
    create_sql: str


@dataclass(frozen=True)
class CommV2Conversation:
    table_name: str = "comm_v2_conversations"


@dataclass(frozen=True)
class CommV2Message:
    table_name: str = "comm_v2_messages"


@dataclass(frozen=True)
class CommV2Participant:
    table_name: str = "comm_v2_participants"


@dataclass(frozen=True)
class CommV2Community:
    table_name: str = "comm_v2_communities"


@dataclass(frozen=True)
class CommV2Channel:
    table_name: str = "comm_v2_channels"


@dataclass(frozen=True)
class CommV2Attachment:
    table_name: str = "comm_v2_attachments"


@dataclass(frozen=True)
class CommV2MessageReaction:
    table_name: str = "comm_v2_message_reactions"


@dataclass(frozen=True)
class CommV2ReadReceipt:
    table_name: str = "comm_v2_read_receipts"


COMM_V2_TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "comm_v2_conversations",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT UNIQUE,
            conversation_type TEXT NOT NULL DEFAULT 'direct',
            title TEXT,
            description TEXT,
            avatar_url TEXT,
            owner_user_id INTEGER,
            created_by_user_id INTEGER,
            linked_group_id INTEGER,
            linked_community_id INTEGER,
            linked_channel_id INTEGER,
            linked_live_id INTEGER,
            linked_project_id INTEGER,
            privacy TEXT DEFAULT 'private',
            visibility TEXT DEFAULT 'members',
            status TEXT DEFAULT 'inactive',
            is_discoverable INTEGER DEFAULT 0,
            participant_limit INTEGER DEFAULT 250,
            member_count INTEGER DEFAULT 0,
            last_message_id INTEGER,
            last_message_at TEXT,
            last_activity_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            archived_at TEXT,
            deleted_at TEXT
        )
        """,
    ),
    TableSpec(
        "comm_v2_participants",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            user_id INTEGER,
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
            pinned_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """,
    ),
    TableSpec(
        "comm_v2_messages",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT UNIQUE,
            conversation_id INTEGER,
            sender_user_id INTEGER,
            message_type TEXT DEFAULT 'text',
            body TEXT,
            rich_body_json TEXT,
            media_id INTEGER,
            reply_to_message_id INTEGER,
            thread_root_message_id INTEGER,
            client_message_id TEXT,
            delivery_status TEXT DEFAULT 'queued',
            moderation_status TEXT DEFAULT 'pending',
            wallet_guardian_status TEXT DEFAULT 'not_scanned',
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            edited_at TEXT,
            deleted_at TEXT
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
            owner_user_id INTEGER,
            privacy TEXT DEFAULT 'public',
            status TEXT DEFAULT 'inactive',
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
            community_id INTEGER,
            conversation_id INTEGER,
            name TEXT NOT NULL,
            slug TEXT,
            description TEXT,
            channel_type TEXT DEFAULT 'text',
            visibility TEXT DEFAULT 'members',
            status TEXT DEFAULT 'inactive',
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
        """,
    ),
    TableSpec(
        "comm_v2_attachments",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            media_type TEXT,
            storage_provider TEXT,
            r2_key TEXT,
            mux_asset_id TEXT,
            url TEXT,
            thumbnail_url TEXT,
            mime_type TEXT,
            file_size INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            scan_status TEXT DEFAULT 'pending',
            created_at TEXT
        )
        """,
    ),
    TableSpec(
        "comm_v2_message_reactions",
        """
        CREATE TABLE IF NOT EXISTS comm_v2_message_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            conversation_id INTEGER,
            user_id INTEGER,
            reaction_type TEXT,
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
            message_id INTEGER,
            conversation_id INTEGER,
            user_id INTEGER,
            delivered_at TEXT,
            seen_at TEXT,
            read_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(message_id, user_id)
        )
        """,
    ),
)


def table_names() -> tuple[str, ...]:
    return tuple(table.name for table in COMM_V2_TABLES)


def ensure_schema(cur) -> tuple[str, ...]:
    """Create only v2-prefixed tables when a future migration explicitly calls it."""
    for table in COMM_V2_TABLES:
        cur.execute(table.create_sql)
    return table_names()
