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

Ordering stops a cycle from forming. It does not shorten the window in which
one can, and that window was the other half of the incident. Both paths used to
walk their range one message at a time:

  * `mark_read` re-stamped every message from everyone else in the conversation,
    from id 1, on every read event -- two statements per message, so a
    10k-message conversation held ~20k row locks for the length of the
    transaction.
  * `list_messages` re-stamped every incoming message on the page on every
    fetch. That one is page-bounded rather than O(N), but `delivered_at` was
    already set on all of those rows, so the only column it changed was
    `updated_at`, which nothing reads. It took a row lock per message on the
    page to write nothing observable.

Each is now two set-based statements bounded to rows that still need writing,
so re-reading an already-read conversation takes no receipt row locks at all.

That rewrite changes what is observable here, so the guards below moved with it.
Neither path binds one id per INSERT any more, which takes away the thing the
original behavioural assertions read:

  * Ordering now lives entirely in the SQL, so it is checked against the
    statement text (`ORDER BY m.id ASC`) for both paths. On `mark_read` that was
    always the only meaningful check -- SQLite returns a scan in rowid order, so
    a behavioural assertion there passed with or without the fix.
  * The behavioural checks are replaced by ones that assert the per-row INSERT
    is *gone*, since a recorder reading bind slot 0 would now find
    `conversation_id` there and pass on anything.
  * One thing stays behavioural: `list_messages` must still call `mark_read`
    (which walks from id 1) *before* its page statement (the newest ~40 ids).
    That is pure statement sequencing, so it is observable on SQLite.

The volume guards are the load-bearing addition: a correctness-only test passes
against the old per-row loops and proves nothing about them.
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
# An INSERT ... SELECT carries no message id in its bind parameters; an
# INSERT ... VALUES binds it first. The two have to be told apart or the
# recorder logs a conversation id as though it were a message id.
RECEIPT_SET_INSERT_RE = re.compile(r"INSERT\s+OR\s+IGNORE\s+INTO\s+comm_v2_read_receipts.*?\bSELECT\b", re.I | re.S)
RECEIPT_WRITE_RE = re.compile(r"^\s*(INSERT|UPDATE)\b.*comm_v2_read_receipts", re.I | re.S)

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
    """Passes everything through to a real sqlite cursor, logging receipt writes."""

    def __init__(self, cursor, log, writes=None):
        self._cursor = cursor
        self._log = log
        self._writes = writes if writes is not None else []

    def execute(self, sql, params=()):
        if RECEIPT_WRITE_RE.match(str(sql).strip()):
            self._writes.append(str(sql))
        if RECEIPT_INSERT_RE.search(sql):
            if RECEIPT_SET_INSERT_RE.search(sql):
                self._log.append(("set", None))
            else:
                self._log.append(("row", int(params[0])))
        return self._cursor.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def _row_ids(log):
    """Message ids from per-row receipt INSERTs, in the order they were bound."""
    return [value for kind, value in log if kind == "row"]


def _insert_owner(sql: str) -> str:
    """Which path emitted a set-based receipt INSERT. `mark_read` stamps seen_at
    and read_at; the delivery statement in `list_messages` only stamps
    delivered_at, because delivery is recorded even with receipts switched off."""
    return "mark_read" if "read_at" in sql else "page"


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
        self.writes = []
        self.recording = _RecordingCursor(self.cur, self.log, self.writes)
        self.shared = _KeepOpenConnection(self.conn)
        self._real_open_db = service._open_db
        self._real_dispatch = service._dispatch_command_center_async
        service._open_db = lambda: (self.shared, self.recording)
        service._dispatch_command_center_async = lambda *a, **k: True

    def tearDown(self):
        service._open_db = self._real_open_db
        service._dispatch_command_center_async = self._real_dispatch
        self.conn.close()

    def test_mark_read_emits_no_per_row_receipt_insert(self):
        """Replaces the old behavioural ordering check, which no longer has ids to
        read: a set-based INSERT binds `conversation_id` first, so the recorder
        would log a conversation id and the assertion would pass on anything."""
        result = service.mark_read(VIEWER, CONVERSATION)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(self.log, "mark_read wrote no receipts; the fixture is not exercising it")
        self.assertEqual(
            _row_ids(self.log),
            [],
            "mark_read is binding message ids one INSERT at a time again -- that is the "
            "per-row loop whose lock count caused the incident",
        )

    def test_mark_read_query_pins_the_order_in_sql(self):
        """The Postgres guard. Ordering now lives entirely in the statement.

        Postgres is free to return an unordered SELECT in any order it likes, and
        does change order between concurrent scans of the same table. Row locks
        are taken in the order the executor emits rows, so the ORDER BY is what
        makes two concurrent transactions agree.
        """
        source = _function_source("mark_read")
        insert = source[source.index("INSERT OR IGNORE INTO comm_v2_read_receipts") :]
        insert = insert[: insert.index('"""')]
        self.assertIn("ORDER BY m.id ASC", insert)

    def test_list_messages_emits_no_per_row_receipt_insert(self):
        result = service.list_messages(VIEWER, CONVERSATION, {"limit": 40})
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(self.log, "list_messages wrote no receipts; the fixture is not exercising it")
        self.assertEqual(
            _row_ids(self.log),
            [],
            "list_messages is binding message ids one INSERT at a time again -- that is "
            "a row lock per message on the page on every fetch",
        )

    def test_list_messages_query_pins_the_order_in_sql(self):
        """Same Postgres guard as mark_read's, for the delivery statement."""
        source = _function_source("list_messages")
        insert = source[source.index("INSERT OR IGNORE INTO comm_v2_read_receipts") :]
        insert = insert[: insert.index('"""')]
        self.assertIn("ORDER BY m.id ASC", insert)

    def test_relisting_an_already_read_conversation_takes_no_receipt_row_locks(self):
        """The delivery loop's whole cost was invisible: `delivered_at` was already
        set on every page row, so it re-locked them to rewrite `updated_at`, which
        no query in the codebase reads."""
        service.list_messages(VIEWER, CONVERSATION, {"limit": 40})
        self.conn.commit()
        before = self.conn.total_changes
        service.list_messages(VIEWER, CONVERSATION, {"limit": 40})
        self.conn.commit()
        written = self.conn.total_changes - before
        self.assertEqual(
            written,
            1,
            "a second fetch of an unchanged conversation must write only the participants "
            f"watermark row; it wrote {written}",
        )

    def test_list_messages_runs_mark_read_before_its_own_page_statement(self):
        """The one guard here that is still behavioural, and the one SQLite can
        actually see: mark_read walks from id 1, the page statement covers the
        newest ~40 ids. Running the page first takes a lock on id 60 before id 1,
        inverting the first-acquisition order a concurrent /read uses.
        """
        service.list_messages(VIEWER, CONVERSATION, {"limit": 40})
        kinds = [_insert_owner(sql) for sql in self.writes if RECEIPT_SET_INSERT_RE.search(sql)]
        self.assertEqual(
            kinds,
            ["mark_read", "page"],
            "list_messages must stamp the whole conversation before the page it fetched",
        )

    def test_both_receipt_inserts_order_the_same_way(self):
        """Each statement being internally ascending is not enough -- the pair of
        them has to agree, or two requests can still lock the shared rows in
        opposing orders. Direction is what the two forms have in common."""
        service.list_messages(VIEWER, CONVERSATION, {"limit": 40})
        inserts = [sql for sql in self.writes if RECEIPT_SET_INSERT_RE.search(sql)]
        self.assertEqual(len(inserts), 2, inserts)
        directions = []
        for sql in inserts:
            match = re.search(r"ORDER\s+BY\s+m\.id\s+(ASC|DESC)", sql, re.I)
            self.assertIsNotNone(match, sql)
            directions.append(match.group(1).upper())
        self.assertEqual(directions, ["ASC", "ASC"], inserts)

    def test_order_check_rejects_a_descending_sequence(self):
        """Positive control: the assertions above can actually fail."""
        descending = _first_acquisition_order([30, 29, 28])
        self.assertNotEqual(descending, sorted(descending))
        interleaved = _first_acquisition_order([21, 22, 1, 2, 21])
        self.assertEqual(interleaved, [21, 22, 1, 2])
        self.assertNotEqual(interleaved, sorted(interleaved))


NOW = "2026-01-01T00:00:00+00:00"
READER = 1
AUTHOR = 2


class _CountingCursor:
    """Passes through to sqlite3 while recording statements by target table."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append(str(sql))
        return self._cursor.execute(sql, params)

    def receipt_writes(self):
        return [sql for sql in self.statements if RECEIPT_WRITE_RE.match(sql.strip())]

    def reset(self):
        self.statements = []

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class ReadReceiptWriteVolumeTest(unittest.TestCase):
    """The other half of the incident: how long the transaction holds its locks.

    Ordering stops a cycle forming; it does nothing about the window. These
    tests pin the *volume* of receipt writes, because a correctness-only test
    passes against the old per-row loop and proves nothing about it.
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.raw = self.conn.cursor()
        ensure_schema(self.raw)
        self.conn.commit()
        self.cur = _CountingCursor(self.raw)

    def tearDown(self):
        self.conn.close()

    def _conversation(self, conversation_id=10):
        self.raw.execute(
            "INSERT INTO comm_v2_conversations (id, public_id, conversation_type, created_at, updated_at) "
            "VALUES (?, ?, 'direct', ?, ?)",
            (conversation_id, f"conv-{conversation_id}", NOW, NOW),
        )
        for user_id in (READER, AUTHOR):
            self.raw.execute(
                "INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, created_at, updated_at) "
                "VALUES (?, ?, 'active', '', ?, ?)",
                (conversation_id, user_id, NOW, NOW),
            )
        self.conn.commit()
        return conversation_id

    def _message(self, conversation_id, sender=AUTHOR, message_id=None, deleted_at=""):
        if message_id is None:
            self.raw.execute(
                "INSERT INTO comm_v2_messages (conversation_id, sender_user_id, message_type, body, deleted_at, created_at, updated_at) "
                "VALUES (?, ?, 'text', 'hi', ?, ?, ?)",
                (conversation_id, sender, deleted_at, NOW, NOW),
            )
        else:
            self.raw.execute(
                "INSERT INTO comm_v2_messages (id, conversation_id, sender_user_id, message_type, body, deleted_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'text', 'hi', ?, ?, ?)",
                (message_id, conversation_id, sender, deleted_at, NOW, NOW),
            )
        self.conn.commit()
        return int(self.raw.lastrowid)

    def _receipts(self, conversation_id, user_id=READER):
        self.raw.execute(
            "SELECT message_id, delivered_at, seen_at, read_at FROM comm_v2_read_receipts "
            "WHERE conversation_id=? AND user_id=? ORDER BY message_id",
            (conversation_id, user_id),
        )
        return [dict(row) for row in self.raw.fetchall()]

    def _mark_read(self, conversation_id):
        self.cur.reset()
        result = service.mark_read(READER, conversation_id, existing_conn=(self.conn, self.cur), commit=False)
        self.conn.commit()
        return result

    # --- volume -----------------------------------------------------------

    def _steady_state_writes(self, message_count):
        conversation_id = self._conversation(conversation_id=100 + message_count)
        for _ in range(message_count):
            self._message(conversation_id)
        self._mark_read(conversation_id)  # first pass does the real work
        self._mark_read(conversation_id)  # second pass has nothing left to do
        return len(self.cur.receipt_writes())

    def test_rereading_a_fully_read_conversation_does_not_scale_with_its_length(self):
        few = self._steady_state_writes(5)
        many = self._steady_state_writes(200)
        self.assertEqual(
            few,
            many,
            "re-reading an already-read conversation must cost the same whether it holds "
            f"5 messages or 200; got {few} vs {many} receipt statements",
        )

    def test_a_fully_read_conversation_costs_a_constant_two_statements(self):
        # Pins the constant itself, so a regression that merely slows the growth
        # rate rather than removing it still fails.
        self.assertEqual(self._steady_state_writes(200), 2)

    def test_the_first_read_also_costs_a_constant_two_statements(self):
        conversation_id = self._conversation()
        for _ in range(50):
            self._message(conversation_id)
        self._mark_read(conversation_id)
        self.assertEqual(len(self.cur.receipt_writes()), 2)

    def test_a_reread_takes_no_receipt_row_locks_at_all(self):
        """Statement count is the lock-window proxy; row count is the lock count."""
        conversation_id = self._conversation()
        for _ in range(30):
            self._message(conversation_id)
        self._mark_read(conversation_id)
        before = self.conn.total_changes
        self._mark_read(conversation_id)
        self.assertEqual(
            self.conn.total_changes - before,
            1,
            "a re-read of an unchanged conversation must write only the participants "
            "watermark row and take no receipt row locks",
        )

    # --- ordering ---------------------------------------------------------

    def test_receipts_land_in_ascending_message_id_order(self):
        conversation_id = self._conversation()
        for _ in range(10):
            self._message(conversation_id)
        self._mark_read(conversation_id)
        stamped = [row["message_id"] for row in self._receipts(conversation_id)]
        self.assertEqual(stamped, sorted(stamped))

    # --- correctness ------------------------------------------------------

    def test_every_incoming_message_is_stamped_read(self):
        conversation_id = self._conversation()
        ids = [self._message(conversation_id) for _ in range(6)]
        self._mark_read(conversation_id)
        receipts = self._receipts(conversation_id)
        self.assertEqual([row["message_id"] for row in receipts], ids)
        for row in receipts:
            self.assertTrue(row["read_at"])
            self.assertTrue(row["seen_at"])
            self.assertTrue(row["delivered_at"])

    def test_the_readers_own_messages_never_get_a_receipt(self):
        conversation_id = self._conversation()
        self._message(conversation_id, sender=READER)
        incoming = self._message(conversation_id, sender=AUTHOR)
        self._mark_read(conversation_id)
        self.assertEqual([row["message_id"] for row in self._receipts(conversation_id)], [incoming])

    def test_a_delivered_only_receipt_is_upgraded_to_read(self):
        """The reason the UPDATE cannot be dropped: `INSERT OR IGNORE` skips this
        row, so without it a delivered receipt would never become a read one."""
        conversation_id = self._conversation()
        message_id = self._message(conversation_id)
        self.raw.execute(
            "INSERT INTO comm_v2_read_receipts (message_id, conversation_id, user_id, delivered_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, conversation_id, READER, NOW, NOW, NOW),
        )
        self.conn.commit()
        self._mark_read(conversation_id)
        receipt = self._receipts(conversation_id)[0]
        self.assertTrue(receipt["read_at"], "an existing delivered-only receipt must still get read-stamped")

    def test_deleted_messages_are_not_stamped(self):
        conversation_id = self._conversation()
        live = self._message(conversation_id)
        self._message(conversation_id, deleted_at=NOW)
        self._mark_read(conversation_id)
        self.assertEqual([row["message_id"] for row in self._receipts(conversation_id)], [live])

    def test_a_message_that_becomes_visible_below_the_high_water_mark_is_still_stamped(self):
        """The case that rules out bounding the scan by `last_read_message_id`.

        Message ids come from a sequence, and a sequence hands out ids before
        the inserting transaction commits. Two people typing at once can have
        id 100 commit *after* id 101, so a reader can stamp 101, advance its
        watermark to 101, and only then see 100 appear. A watermark-bounded
        scan (`id > last_read_message_id`) would skip 100 permanently. Anti-
        joining against the receipts table instead means the row is simply
        still missing, so the next read picks it up.
        """
        conversation_id = self._conversation()
        later = self._message(conversation_id, message_id=101)
        self._mark_read(conversation_id)
        self.assertEqual([row["message_id"] for row in self._receipts(conversation_id)], [later])

        earlier = self._message(conversation_id, message_id=100)
        self._mark_read(conversation_id)
        self.assertEqual(
            [row["message_id"] for row in self._receipts(conversation_id)],
            [earlier, later],
            "a message that commits out of sequence order must not be skipped forever",
        )

    def _disable_receipts(self):
        self.raw.execute(
            "INSERT INTO comm_v2_user_settings (user_id, read_receipts_enabled, updated_at) VALUES (?, 0, ?)",
            (READER, NOW),
        )
        self.conn.commit()

    def test_read_receipts_disabled_records_the_read_but_publishes_nothing(self):
        """The opt-out is a publishing setting, not a recording one.

        This used to assert that receipts-off wrote no receipt row at all.
        That conflated two different things: what the SENDER is shown, and what
        the server knows about its own reader. `seen_at` is the only column any
        sender-visible payload renders -- `_message_payload` builds the 'Seen'
        tick from it and nothing else -- so `seen_at` is what the opt-out has
        to suppress. `read_at` has no cross-user consumer anywhere, and it is
        the only sound per-message answer to 'has this user read this message?',
        which is what notification reconciliation needs before it may dismiss a
        read message's alert. Withholding it punished exactly the people who
        opted out, by leaving their Notification Center full of stale alerts
        naming who had messaged them.
        """
        conversation_id = self._conversation()
        message_id = self._message(conversation_id)
        self._disable_receipts()
        self._mark_read(conversation_id)

        receipts = self._receipts(conversation_id)
        self.assertEqual([row["message_id"] for row in receipts], [message_id])
        self.assertTrue(receipts[0]["read_at"], "the reader-private read stamp must be recorded")
        self.assertTrue(receipts[0]["delivered_at"])
        self.assertFalse(
            receipts[0]["seen_at"],
            "seen_at is the sender-visible column; an opt-out must leave it empty",
        )

    def test_the_sender_is_shown_nothing_when_receipts_are_disabled(self):
        """The column assertion above only means something if the payload agrees."""
        conversation_id = self._conversation()
        message_id = self._message(conversation_id)
        self._disable_receipts()
        self._mark_read(conversation_id)

        # `ensure_schema` owns the comm_v2_* tables only; the payload builder
        # joins the app-wide `users` table, so the fixture has to supply it.
        self.raw.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT)"
        )
        self.raw.execute(
            "INSERT OR IGNORE INTO users (user_id, username, display_name, avatar_url) VALUES (?, ?, ?, '')",
            (AUTHOR, "author", "Author"),
        )
        self.conn.commit()

        self.raw.execute("SELECT * FROM comm_v2_messages WHERE id=?", (message_id,))
        row = dict(self.raw.fetchone())
        payload = service._message_payload(self.raw, row, AUTHOR)
        self.assertNotEqual(
            payload.get("delivery_status"),
            "seen",
            "recording read_at must not leak a Seen tick to the sender",
        )

    def test_recording_the_read_still_costs_a_constant_two_statements(self):
        """Ungating `read_at` must not reintroduce the per-row loop."""
        conversation_id = self._conversation()
        for _ in range(200):
            self._message(conversation_id)
        self._disable_receipts()
        self._mark_read(conversation_id)
        self.assertEqual(len(self.cur.receipt_writes()), 2)

    def test_re_enabling_read_receipts_backfills_the_gap(self):
        """Documented, deliberate: the opt-out is not retroactive protection.

        With the gate split by column, rows from the quiet period already exist,
        carrying `read_at` and an empty `seen_at`. Re-enabling therefore has to
        stamp `seen_at` onto those existing rows -- the anti-join INSERT skips
        them, so only the UPDATE can reach them. Without that, a user who
        switched receipts off and back on would leave the sender staring at
        'Delivered' forever for every message read in between.

        Suppressing the backfill entirely is a product decision about whether an
        opt-out applies retroactively; it did not belong in the performance fix
        that wrote this file and it does not belong here either, so the
        pre-existing behaviour is preserved.
        """
        conversation_id = self._conversation()
        message_id = self._message(conversation_id)
        self._disable_receipts()
        self._mark_read(conversation_id)

        quiet = self._receipts(conversation_id)[0]
        self.assertFalse(quiet["seen_at"], "fixture did not establish the quiet period")
        first_read_at = quiet["read_at"]

        self.raw.execute("UPDATE comm_v2_user_settings SET read_receipts_enabled=1 WHERE user_id=?", (READER,))
        self.conn.commit()
        self._mark_read(conversation_id)

        backfilled = self._receipts(conversation_id)
        self.assertEqual([row["message_id"] for row in backfilled], [message_id])
        self.assertTrue(backfilled[0]["seen_at"], "re-enabling must backfill the sender-visible stamp")
        self.assertEqual(
            backfilled[0]["read_at"],
            first_read_at,
            "read_at records the first read; the backfill must not move it",
        )


if __name__ == "__main__":
    unittest.main()
