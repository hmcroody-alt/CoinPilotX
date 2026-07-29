"""Executable evidence for side-effect-free, membership-scoped Messenger reads."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.messenger_intelligence_service import (
    list_conversation_messages,
    list_my_conversations,
)


class MessengerReadPack(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        handle.close()
        self.path = handle.name
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE comm_v2_conversations (
              id INTEGER PRIMARY KEY, public_id TEXT, conversation_type TEXT,
              title TEXT, status TEXT, deleted_at TEXT, last_message_id INTEGER,
              last_message_at TEXT, last_activity_at TEXT, updated_at TEXT,
              created_at TEXT, privacy TEXT, is_discoverable INTEGER
            );
            CREATE TABLE comm_v2_participants (
              id INTEGER PRIMARY KEY, conversation_id INTEGER, user_id INTEGER,
              membership_state TEXT, left_at TEXT, unread_count INTEGER,
              muted_until TEXT, pinned_at TEXT, last_read_message_id INTEGER
            );
            CREATE TABLE comm_v2_messages (
              id INTEGER PRIMARY KEY, public_id TEXT, conversation_id INTEGER,
              sender_user_id INTEGER, message_type TEXT, body TEXT,
              reply_to_message_id INTEGER, moderation_status TEXT,
              created_at TEXT, edited_at TEXT, deleted_at TEXT
            );
            INSERT INTO comm_v2_conversations VALUES
              (10,'c10','direct','QA Alice','active','',101,'2026-07-28','2026-07-28','2026-07-28','2026-07-28','private',0),
              (11,'c11','room','Public discovery','active','',102,'2026-07-27','2026-07-27','2026-07-27','2026-07-27','public',1),
              (12,'c12','direct','Other private','active','',103,'2026-07-26','2026-07-26','2026-07-26','2026-07-26','private',0);
            INSERT INTO comm_v2_participants VALUES
              (1,10,1,'active','',3,'','',90),
              (2,12,2,'active','',1,'','',80);
            INSERT INTO comm_v2_messages VALUES
              (101,'m101',10,2,'text','QA hello',0,'approved','2026-07-28','',''),
              (102,'m102',12,2,'text','Other account secret',0,'approved','2026-07-28','',''),
              (103,'m103',10,2,'text','Removed',0,'approved','2026-07-28','','2026-07-28');
            """
        )
        conn.commit()
        conn.close()

        def connect():
            database = sqlite3.connect(self.path)
            database.row_factory = sqlite3.Row
            return database

        self.connection_patch = patch(
            "services.messenger_intelligence_service.db_service.connect",
            side_effect=connect,
        )
        self.connection_patch.start()

    def tearDown(self) -> None:
        self.connection_patch.stop()
        os.unlink(self.path)

    def test_lists_only_active_memberships(self) -> None:
        rows = list_my_conversations(1)
        self.assertEqual([row["conversation_id"] for row in rows], [10])

    def test_public_discovery_room_is_not_misrepresented_as_my_chat(self) -> None:
        self.assertNotIn(11, {row["conversation_id"] for row in list_my_conversations(1)})

    def test_read_does_not_mark_conversation_read(self) -> None:
        list_my_conversations(1)
        conn = sqlite3.connect(self.path)
        unread, last_read = conn.execute(
            "SELECT unread_count,last_read_message_id FROM comm_v2_participants WHERE id=1"
        ).fetchone()
        conn.close()
        self.assertEqual((unread, last_read), (3, 90))

    def test_other_account_conversation_is_not_exposed(self) -> None:
        self.assertNotIn(12, {row["conversation_id"] for row in list_my_conversations(1)})

    def test_messages_are_returned_only_after_membership_check(self) -> None:
        rows = list_conversation_messages(1, 10)
        self.assertEqual([row["message_id"] for row in rows], [101])
        self.assertEqual(rows[0]["body"], "QA hello")

    def test_foreign_conversation_messages_are_not_exposed(self) -> None:
        self.assertEqual(list_conversation_messages(1, 12), [])
        self.assertEqual(list_conversation_messages(1, 9999), [])

    def test_message_read_does_not_change_unread_or_last_read_state(self) -> None:
        list_conversation_messages(1, 10)
        conn = sqlite3.connect(self.path)
        state = conn.execute(
            "SELECT unread_count,last_read_message_id FROM comm_v2_participants WHERE id=1"
        ).fetchone()
        conn.close()
        self.assertEqual(state, (3, 90))


if __name__ == "__main__":
    unittest.main()
