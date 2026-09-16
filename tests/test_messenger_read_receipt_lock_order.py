"""Read-receipt writes must take their row locks in one ascending id order.

Production symptom: `POST /api/pulse/communications/v2/conversations/<id>/read`
returned 500 with `DeadlockDetected ... while inserting index tuple in relation
"comm_v2_read_receipts"`. The receipts table is UNIQUE(message_id, user_id) and
`INSERT OR IGNORE` is translated to `ON CONFLICT DO NOTHING` on Postgres, so a
transaction inserting a key another open transaction already inserted must block
on that transaction. Two requests from the same signed-in user touch the exact
same keys, so whenever the two walk the id set in different orders they wait on
each other and Postgres kills one.

SQLite cannot reproduce the deadlock -- it has no row-level lock waits -- so
these tests assert on the thing that actually causes it: the order of the ids
bound to the receipt INSERTs. Two separate guards, because each covers a hole
the other leaves:

  * `mark_read`'s driving SELECT had no ORDER BY. SQLite happens to return a
    scan in rowid order, so a behavioural assertion here passes with or without
    the fix. Only the source-level check is meaningful for that one.
  * `list_messages` ran its page loop (the newest ~40 ids) *before* calling
    `mark_read` (which walks from id 1). That inverted first-acquisition order
    is pure statement sequencing, so it IS observable on SQLite.
"""

import os
import re
import sqlite3
import unittest

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2 import service  # noqa: E402
from pulse_communications_v2.models import ensure_schema  # noqa: E402

SERVICE_SOURCE = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pulse_communications_v2", "service.py"),
    encoding="utf-8",
).read()

RECEIPT_INSERT_RE = re.compile(r"INSERT\s+OR\s+IGNORE\s+INTO\s+comm_v2_read_receipts", re.I)

VIEWER = 7
SENDER = 8
CONVERSATION = 3


def _function_source(name: str) -> str:
    body = SERVICE_SOURCE[SERVICE_SOURCE.index(f"def {name}(") :]
    return body[: body.index("\ndef ", 1)]


def _first_acquisition_order(ids):
    """Order in which each id is locked for the first time; later re-touches of a
    row this transaction already holds cannot contribute to a deadlock cycle."""
    seen = set()
    order = []
    for value in ids:
        if value not in seen:
            seen.add(value)
            order.append(value)
    return order


class _KeepOpenConnection:
    """Service functions close the connection they were handed; the fixture needs
    to survive more than one call against the same in-memory database."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _RecordingCursor:
    """Passes everything through to a real sqlite cursor, logging receipt inserts."""

    def __init__(self, cursor, log):
        self._cursor = cursor
        self._log = log

    def execute(self, sql, params=()):
        if RECEIPT_INSERT_RE.search(sql):
            self._log.append(int(params[0]))
        return self._cursor.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class ReadReceiptLockOrderTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        ensure_schema(self.cur)
        self.cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT, email TEXT)")
        for user_id, name in ((VIEWER, "viewer"), (SENDER, "sender")):
            self.cur.execute("INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)", (user_id, name, name))
            self.cur.execute(
                "INSERT INTO comm_v2_user_settings (user_id, read_receipts_enabled, updated_at) VALUES (?, 1, '2026-01-01T00:00:00+00:00')",
                (user_id,),
            )
        self.cur.execute(
            "INSERT INTO comm_v2_conversations (id, public_id, conversation_type, privacy, created_at, updated_at) "
            "VALUES (?, 'conv-lock-order', 'direct', 'private', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (CONVERSATION,),
        )
        for user_id in (VIEWER, SENDER):
            self.cur.execute(
                "INSERT INTO comm_v2_participants (conversation_id, user_id, role, membership_state, joined_at, created_at, updated_at) "
                "VALUES (?, ?, 'member', 'active', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (CONVERSATION, user_id),
            )
        # Enough messages that list_messages' page is a strict subset of the full
        # range -- that gap is exactly where the inverted lock order lived.
        self.message_ids = []
        for index in range(60):
            self.cur.execute(
                "INSERT INTO comm_v2_messages (conversation_id, sender_user_id, message_type, body, created_at, updated_at) "
                "VALUES (?, ?, 'text', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (CONVERSATION, SENDER, f"message {index}"),
            )
            self.message_ids.append(int(self.cur.lastrowid))
        self.conn.commit()

        self.log = []
        self.recording = _RecordingCursor(self.cur, self.log)
        self.shared = _KeepOpenConnection(self.conn)
        self._real_open_db = service._open_db
        self._real_dispatch = service._dispatch_command_center_async
        service._open_db = lambda: (self.shared, self.recording)
        service._dispatch_command_center_async = lambda *a, **k: True

    def tearDown(self):
        service._open_db = self._real_open_db
        service._dispatch_command_center_async = self._real_dispatch
        self.conn.close()

    def test_mark_read_locks_receipts_in_ascending_id_order(self):
        result = service.mark_read(VIEWER, CONVERSATION)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(self.log, "mark_read wrote no receipts; the fixture is not exercising the loop")
        self.assertEqual(self.log, sorted(self.log))

    def test_mark_read_query_pins_the_order_in_sql(self):
        """The behavioural test above is vacuous on SQLite; this is the real guard.

        Postgres is free to return an unordered SELECT in any order it likes, and
        does change order between concurrent scans of the same table.
        """
        source = _function_source("mark_read")
        select = next(
            line for line in source.splitlines() if "SELECT id FROM comm_v2_messages" in line
        )
        self.assertIn("ORDER BY id", select)

    def test_list_messages_first_locks_each_receipt_in_ascending_order(self):
        result = service.list_messages(VIEWER, CONVERSATION, {"limit": 40})
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(self.log, "list_messages wrote no receipts; the fixture is not exercising the loop")
        order = _first_acquisition_order(self.log)
        self.assertEqual(
            order,
            sorted(order),
            "list_messages locked a high message id before a lower one it also needs, "
            "which is the inverted order that deadlocks against a concurrent /read",
        )

    def test_list_messages_and_mark_read_agree_on_order(self):
        """Both request paths must walk the shared keys the same way, or the pair
        of them can still form a cycle even though each is internally ascending."""
        service.list_messages(VIEWER, CONVERSATION, {"limit": 40})
        from_list = _first_acquisition_order(self.log)
        self.log.clear()
        service.mark_read(VIEWER, CONVERSATION)
        from_read = _first_acquisition_order(self.log)
        shared = [value for value in from_list if value in set(from_read)]
        self.assertEqual(shared, [value for value in from_read if value in set(from_list)])

    def test_order_check_rejects_a_descending_sequence(self):
        """Positive control: the assertions above can actually fail."""
        descending = _first_acquisition_order([30, 29, 28])
        self.assertNotEqual(descending, sorted(descending))
        interleaved = _first_acquisition_order([21, 22, 1, 2, 21])
        self.assertEqual(interleaved, [21, 22, 1, 2])
        self.assertNotEqual(interleaved, sorted(interleaved))


if __name__ == "__main__":
    unittest.main()
