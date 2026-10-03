"""A pair who deleted their DM must be able to open it again.

``comm_v2_conversations.direct_key`` is ``TEXT UNIQUE`` -- in production the
constraint is named ``comm_v2_conversations_direct_key_key``. The direct branch
of ``create_conversation`` looked for a live thread with

    WHERE direct_key=? AND COALESCE(deleted_at,'')=''

and, finding none, plain-``INSERT``ed that same ``direct_key``. The two
disagree about which rows exist: the SELECT hides a soft-deleted thread, the
unique index still holds its key. So every pair who had ever deleted their
conversation hit the constraint on the way back in, ``create_conversation``
re-raised, and the route 500'd -- permanently, because nothing about the
offending row ever changed.

The fix mirrors ``services.pulse_chat_bridge.direct_thread``, which resolves
this same key: a conflict-tolerant insert, an unconditional re-SELECT rather
than ``lastrowid``, and revival of the soft-deleted row instead of a collision
with it.

**What this file can and cannot prove.** SQLite enforces ``UNIQUE`` too, so the
raise reproduces here and these tests fail against the old code. What SQLite
cannot show is the engine-specific half: that ``INSERT OR IGNORE`` becomes
``ON CONFLICT DO NOTHING`` (SQLite runs it literally), and that it leaves
``lastrowid`` unusable on the conflict path (SQLite still populates it). Both of
those only exist on PostgreSQL, which is what production runs, and they are
proved by ``scripts/verify_comm_v2_direct_key_conflict_on_postgres.py`` against
a throwaway ``postgres:18``. A green run of this file alone is not the whole
proof, and that is deliberate rather than an omission.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Distinct ids, so a crossed-wires bug reads as a wrong number rather than a
# coincidence that happens to satisfy the assertion.
ALICE = 81101
BOB = 81202
CARA = 81303

DIRECT_KEY = f"{ALICE}:{BOB}"


def _db():
    """Only the tables this path touches, so a pass means the path ran."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            avatar_url TEXT,
            email TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            setting_key TEXT,
            setting_value TEXT
        )
        """
    )
    for uid, name in ((ALICE, "alice"), (BOB, "bob"), (CARA, "cara")):
        cur.execute(
            "INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
            (uid, name, name.title()),
        )
    conn.commit()
    return conn, cur


class _KeepOpen:
    """A connection whose ``close()`` does nothing.

    ``create_conversation`` closes its connection in a ``finally``; an in-memory
    SQLite database dies with it, taking the rows the assertions need to read.
    """

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


class DirectConversationReopen(unittest.TestCase):
    def setUp(self):
        from pulse_communications_v2 import models as comm_models
        from pulse_communications_v2 import service as comm_service

        self.conn, self.cur = _db()
        self.addCleanup(self.conn.close)
        comm_models.ensure_schema(self.cur)
        self.conn.commit()

        self.service = comm_service
        # Bypass the bot import (which would run init_db against the dev
        # database) and the feature flag, leaving the branch under test intact.
        self._restore = (comm_service._open_db, comm_service._disabled)
        comm_service._open_db = lambda: (_KeepOpen(self.conn), self.cur)
        comm_service._disabled = lambda action: None
        self.addCleanup(self._put_back)

    def _put_back(self):
        self.service._open_db, self.service._disabled = self._restore

    def _open(self, sender, target):
        return self.service.create_conversation(
            sender, {"conversation_type": "direct", "target_user_id": target}
        )

    def _rows_for_key(self, key=DIRECT_KEY):
        self.cur.execute(
            "SELECT id, COALESCE(deleted_at,'') AS deleted_at, status "
            "FROM comm_v2_conversations WHERE direct_key=? ORDER BY id",
            (key,),
        )
        return [dict(r) for r in self.cur.fetchall()]

    def _soft_delete(self, conversation_id):
        """What leaving/deleting a thread does: stamp ``deleted_at``, keep the row.

        The row survives and so does its ``direct_key``. That is the collision.
        """
        self.cur.execute(
            "UPDATE comm_v2_conversations SET deleted_at=?, status='deleted' WHERE id=?",
            (self.service._now(), int(conversation_id)),
        )
        self.conn.commit()

    def _active_members(self, conversation_id):
        self.cur.execute(
            "SELECT user_id FROM comm_v2_participants WHERE conversation_id=? "
            "AND membership_state='active' AND COALESCE(left_at,'')=''",
            (int(conversation_id),),
        )
        return {int(dict(r)["user_id"]) for r in self.cur.fetchall()}

    def test_a_first_direct_message_opens(self):
        # Control case. Without it, every assertion below could pass because
        # conversation creation is broken for some unrelated reason.
        result = self._open(ALICE, BOB)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(1, len(self._rows_for_key()))
        self.assertEqual({ALICE, BOB}, self._active_members(result["conversation_id"]))

    def test_reopening_a_live_thread_returns_the_same_one(self):
        first = self._open(ALICE, BOB)
        again = self._open(BOB, ALICE)
        self.assertTrue(again.get("ok"), again)
        self.assertEqual(first["conversation_id"], again["conversation_id"])
        self.assertEqual(1, len(self._rows_for_key()))

    def test_reopening_a_soft_deleted_thread_does_not_raise(self):
        """The defect, stated at its narrowest: it used to raise here.

        Asserted separately from the revival assertions below so that a
        regression reads as "it raises again" rather than as a confusing
        failure about row counts.
        """
        opened = self._open(ALICE, BOB)
        self._soft_delete(opened["conversation_id"])
        try:
            result = self._open(ALICE, BOB)
        except Exception as exc:  # noqa: BLE001 -- the raise *is* the regression
            self.fail(f"reopening a soft-deleted direct thread raised {exc!r}")
        self.assertTrue(result.get("ok"), result)

    def test_reopening_revives_the_same_row_rather_than_forking_it(self):
        opened = self._open(ALICE, BOB)
        conversation_id = opened["conversation_id"]
        self._soft_delete(conversation_id)

        result = self._open(ALICE, BOB)
        self.assertEqual(conversation_id, result["conversation_id"])
        rows = self._rows_for_key()
        self.assertEqual(1, len(rows), rows)
        # A revived row that stays deleted is worse than the raise: the caller
        # is handed an id, and `list_conversations` / `_conversation_access`
        # both filter on `deleted_at`, so opening it 404s.
        self.assertEqual("", rows[0]["deleted_at"])
        self.assertEqual("active", rows[0]["status"])
        self.assertEqual({ALICE, BOB}, self._active_members(conversation_id))

    def test_the_revived_thread_is_listable_by_both_members(self):
        """Reopened has to mean usable, not merely present.

        The id being correct is not the product promise; the thread showing up
        when either member looks for it is.
        """
        opened = self._open(ALICE, BOB)
        conversation_id = opened["conversation_id"]
        self._soft_delete(conversation_id)
        self._open(ALICE, BOB)
        self.conn.commit()

        for viewer in (ALICE, BOB):
            listed = self.service.list_conversations(viewer, {"type": "direct"})
            self.assertTrue(listed.get("ok"), listed)
            ids = [int(c.get("id") or 0) for c in listed.get("conversations") or []]
            self.assertIn(conversation_id, ids, f"viewer={viewer} ids={ids}")

    def test_a_deleted_then_reopened_thread_survives_a_third_round(self):
        # Once, because the first delete was special. Twice, because a fix that
        # only cleared `deleted_at` on the way in -- without the row being
        # re-deletable -- would pass the test above and fail the second time.
        opened = self._open(ALICE, BOB)
        conversation_id = opened["conversation_id"]
        for _ in range(3):
            self._soft_delete(conversation_id)
            result = self._open(ALICE, BOB)
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(conversation_id, result["conversation_id"])
        self.assertEqual(1, len(self._rows_for_key()))

    def test_a_closed_inbox_still_refuses_to_reopen_a_deleted_thread(self):
        """Revival must not become a hole in the "Message requests" gate.

        The gate sits below the live-thread branch deliberately: tightening the
        setting must not sever a conversation already underway. But a *deleted*
        thread is not underway -- reopening it is opening a new one, which is
        exactly what the preference governs. The conflict-tolerant insert must
        not quietly turn a refusal into an admission.
        """
        opened = self._open(ALICE, BOB)
        conversation_id = opened["conversation_id"]
        self._soft_delete(conversation_id)
        self.cur.execute(
            "INSERT INTO user_settings (user_id, setting_key, setting_value) VALUES (?, 'message_requests', 'none')",
            (BOB,),
        )
        self.conn.commit()

        refused = self._open(ALICE, BOB)
        self.assertFalse(refused.get("ok"), refused)
        self.assertEqual("message_requests_closed", refused.get("status"))
        # The refusal has to leave the row alone. Reviving it and *then*
        # refusing would hand BOB's closed inbox a live thread anyway.
        rows = self._rows_for_key()
        self.assertEqual(1, len(rows), rows)
        self.assertNotEqual("", rows[0]["deleted_at"])

    def test_an_unrelated_pair_is_untouched(self):
        """The key is per-pair; reviving one must not disturb another.

        A fix that re-SELECTed without the `direct_key` filter -- or took the
        first row of the table -- would pass every assertion above and hand
        CARA someone else's conversation.
        """
        alice_bob = self._open(ALICE, BOB)["conversation_id"]
        alice_cara = self._open(ALICE, CARA)["conversation_id"]
        self.assertNotEqual(alice_bob, alice_cara)

        self._soft_delete(alice_bob)
        revived = self._open(ALICE, BOB)
        self.assertEqual(alice_bob, revived["conversation_id"])

        other = self._rows_for_key(f"{ALICE}:{CARA}")
        self.assertEqual(1, len(other), other)
        self.assertEqual(alice_cara, other[0]["id"])
        self.assertEqual("", other[0]["deleted_at"])
        self.assertEqual({ALICE, CARA}, self._active_members(alice_cara))


if __name__ == "__main__":
    unittest.main()
