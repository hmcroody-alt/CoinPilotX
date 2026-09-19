"""The edit window is the server's rule, not a number the client gets to pick.

`edit_message` once read its cutoff from the request body
(`timedelta(minutes=int(payload.get("edit_window_minutes") or 15))`), and that
payload is `request.get_json(silent=True) or {}` on
`PATCH /api/pulse/communications/v2/messages/<id>`. Sending
`{"body": "...", "edit_window_minutes": 999999}` therefore reopened a message of
any age. The sender check above it holds, so this was never cross-user access --
it let someone silently rewrite their own year-old messages in a conversation
the other person is still reading, which is a conversation-integrity problem.

These tests hold the window closed against a hostile payload while proving the
window itself still opens for a recent message, so a blanket 403 cannot pass
them.
"""

import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2 import service  # noqa: E402
from pulse_communications_v2.models import ensure_schema  # noqa: E402

SERVICE_SOURCE = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pulse_communications_v2", "service.py"),
    encoding="utf-8",
).read()

SENDER_ID = 7
HOSTILE_PAYLOAD = {"body": "rewritten", "edit_window_minutes": 999999}


def _edit_message_source() -> str:
    body = SERVICE_SOURCE[SERVICE_SOURCE.index("def edit_message(") :]
    return body[: body.index("\ndef ", 1)]


class _KeepOpenConnection:
    """The service closes its connection in a `finally`; the test database only
    exists for as long as that connection does."""

    def __init__(self, conn):
        self._conn = conn

    def commit(self):
        self._conn.commit()

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._conn, name)


class EditWindowIsServerSideTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        ensure_schema(self.cur)
        self.cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT)")
        self.cur.execute("INSERT INTO users (user_id, username, display_name, avatar_url) VALUES (?, 'sender', 'Sender', '')", (SENDER_ID,))
        self.conn.commit()
        self._real_open_db = service._open_db
        service._open_db = lambda: (_KeepOpenConnection(self.conn), self.cur)

    def tearDown(self):
        service._open_db = self._real_open_db
        self.conn.close()

    def _insert(self, age_minutes, sender=SENDER_ID):
        created = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat(timespec="seconds")
        self.cur.execute(
            "INSERT INTO comm_v2_messages (conversation_id, sender_user_id, message_type, body, created_at, updated_at) "
            "VALUES (1, ?, 'text', 'original', ?, ?)",
            (sender, created, created),
        )
        self.conn.commit()
        return int(self.cur.lastrowid)

    def _body(self, message_id):
        self.cur.execute("SELECT body FROM comm_v2_messages WHERE id=?", (message_id,))
        return self.cur.fetchone()["body"]

    def test_a_huge_edit_window_in_the_payload_does_not_reopen_an_old_message(self):
        message_id = self._insert(age_minutes=60 * 24 * 365)
        result = service.edit_message(SENDER_ID, message_id, dict(HOSTILE_PAYLOAD))
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["status"], "edit_window_expired")
        self.assertEqual(self._body(message_id), "original")

    def test_a_message_just_past_the_window_is_closed(self):
        message_id = self._insert(age_minutes=16)
        result = service.edit_message(SENDER_ID, message_id, dict(HOSTILE_PAYLOAD))
        self.assertEqual(result["status"], "edit_window_expired")
        self.assertEqual(self._body(message_id), "original")

    def test_a_recent_message_still_edits(self):
        """Without this the suite would pass just as well against a route that
        rejects every edit."""
        message_id = self._insert(age_minutes=1)
        result = service.edit_message(SENDER_ID, message_id, dict(HOSTILE_PAYLOAD))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._body(message_id), "rewritten")

    def test_the_window_is_not_read_from_the_payload_at_all(self):
        self.assertNotIn("edit_window_minutes", _edit_message_source())
        self.assertEqual(service.MESSAGE_EDIT_WINDOW, timedelta(minutes=15))
