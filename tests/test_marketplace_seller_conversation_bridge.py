"""Message seller must hand the buyer a conversation the client can actually open.

The defect
----------
Tapping **Message seller** on a marketplace listing landed the buyer on a chat
screen reading *"Messages could not load / Conversation not found."*

``/api/pulse/messages/start`` returned ``pulse_start_conversation``'s
``conversation_id``, which is a **legacy** ``pulse_conversations`` row id. Every
client resolves ids against the **v2** stack instead:
``mobile-native/src/api/messenger.ts`` points at
``/api/pulse/communications/v2``, and ``/pulse/messages/<id>`` renders
``pulse_messages_v2.html`` with the id as ``initial_conversation_id``. So
``service._conversation_access`` looked the legacy id up in
``comm_v2_conversations``, found nothing, and answered ``missing`` -> HTTP 404.

The two id spaces are disjoint in production (legacy direct ids 70-378, v2 ids
1-34), so this failed for every buyer, every time. It was never intermittent and
it was never a transport fault.

``services/pulse_chat_bridge`` already says all of this in its own module
docstring -- the identical bug was found and fixed for community Rooms and Group
chats via ``sync_thread``. Direct messages were simply never given the same
treatment. ``direct_thread`` is that missing counterpart.

Why these tests assert through ``service._conversation_access``
--------------------------------------------------------------
The bug was never "no row was written" -- the legacy row was written correctly.
It was "the row we wrote is invisible to the resolver the client's request runs
through". A test that only checked ``direct_thread`` returned a non-zero id
would have passed against the broken build too, because the broken build also
returned a non-zero id. It just returned one from the wrong table.

So the assertions run the *real* ``_conversation_access`` -- the exact function
that produced the 404 -- and require ``ok``. ``test_the_legacy_id_is_what_the_old_
build_handed_out`` pins the inverse against the same resolver, so these tests
demonstrably distinguish the fixed build from the broken one rather than merely
passing on it.

Schema fidelity
---------------
The v2 tables come from the real ``pulse_communications_v2.models.ensure_schema``
rather than being hand-written here, because the behaviour under test *is*
``UNIQUE(direct_key)`` and ``UNIQUE(conversation_id, user_id)``. A hand-rolled
fixture that forgot either one would make the uniqueness tests pass vacuously --
``INSERT OR IGNORE`` with no constraint to violate inserts a duplicate happily.
Both constraints were confirmed present in production by direct introspection
(``comm_v2_conversations_direct_key_key``,
``comm_v2_participants_conversation_id_user_id_key``), as was
``legacy_conversation_id`` being nullable, which is what lets rows created here
leave it NULL under its UNIQUE index.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pulse_communications_v2 import models as comm_v2_models  # noqa: E402
from pulse_communications_v2 import service as comm_v2_service  # noqa: E402
from services import pulse_chat_bridge  # noqa: E402

# Distinct, non-adjacent ids: a crossed-wires bug then reads as the wrong number
# rather than as an off-by-one that could be a coincidence.
BUYER = 60101
SELLER = 60202          # stands in for the M&W Store owner
THIRD_PARTY = 60303     # party to neither side of the conversation
BLOCKED_BUYER = 60404

# Production's marketplace catalogue is entirely seller_user_id=1, and the legacy
# direct ids there run 70-378 while v2 ids run 1-34. 112 is inside the legacy
# band and outside the v2 band, which is what made the 404 deterministic.
LEGACY_CONVERSATION_ID = 112


class SellerConversationBridgeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        comm_v2_models.ensure_schema(self.cur)
        # Mirrors production: added by service._ensure_columns, nullable, no
        # default, carrying a UNIQUE index that tolerates many NULLs.
        self.cur.execute("ALTER TABLE comm_v2_conversations ADD COLUMN legacy_conversation_id INTEGER")
        self.cur.execute(
            "CREATE UNIQUE INDEX idx_comm_v2_conversations_legacy "
            "ON comm_v2_conversations(legacy_conversation_id)"
        )
        self.cur.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT)")
        for user_id, username in (
            (BUYER, "buyer_ada"),
            (SELLER, "mw_store_owner"),
            (THIRD_PARTY, "unrelated_mara"),
            (BLOCKED_BUYER, "blocked_rhea"),
        ):
            self.cur.execute(
                "INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
                (user_id, username, username.replace("_", " ").title()),
            )
        # direct_thread calls _ensure_v2_schema, which lazily imports bot to add
        # the legacy columns. Importing bot runs init_db() against DATABASE_URL at
        # module scope, so the schema is built above instead and the bootstrap is
        # stubbed out. What it would create is already present and asserted on.
        self._real_ensure = pulse_chat_bridge._ensure_v2_schema
        pulse_chat_bridge._ensure_v2_schema = lambda cur, conn: None
        self.addCleanup(self._restore_ensure)
        self.addCleanup(self.conn.close)

    def _restore_ensure(self):
        pulse_chat_bridge._ensure_v2_schema = self._real_ensure

    # ---- helpers ---------------------------------------------------------

    def _open(self, buyer=BUYER, seller=SELLER) -> int:
        return pulse_chat_bridge.direct_thread(self.cur, self.conn, buyer, seller)

    def _access(self, user_id: int, conversation_id) -> str:
        """The verdict the client's own request runs through."""
        _, state = comm_v2_service._conversation_access(self.cur, user_id, conversation_id)
        return state

    def _direct_rows(self) -> list[sqlite3.Row]:
        self.cur.execute("SELECT * FROM comm_v2_conversations WHERE conversation_type='direct'")
        return self.cur.fetchall()

    def _active_members(self, conversation_id: int) -> list[int]:
        self.cur.execute(
            "SELECT user_id FROM comm_v2_participants WHERE conversation_id=? "
            "AND membership_state='active' AND COALESCE(left_at,'')='' ORDER BY user_id",
            (conversation_id,),
        )
        return [int(row["user_id"]) for row in self.cur.fetchall()]

    # ---- the fix ---------------------------------------------------------

    def test_the_buyer_can_open_what_message_seller_returns(self):
        """The whole point: the id handed to the client resolves, not 404s."""
        conversation_id = self._open()
        self.assertTrue(conversation_id, "Message seller returned no conversation to open")
        self.assertEqual("ok", self._access(BUYER, conversation_id))
        self.assertEqual("ok", self._access(SELLER, conversation_id))

    def test_the_legacy_id_is_what_the_old_build_handed_out(self):
        """Proves these tests can fail -- the pre-fix return value still 404s.

        If this ever reports ``ok``, the fixture has drifted into a world where
        the two id spaces overlap and every other test here is meaningless.
        """
        self._open()
        self.assertEqual("missing", self._access(BUYER, LEGACY_CONVERSATION_ID))

    def test_both_parties_are_active_members(self):
        conversation_id = self._open()
        self.assertEqual([BUYER, SELLER], self._active_members(conversation_id))

    def test_the_conversation_is_a_private_direct_thread(self):
        conversation_id = self._open()
        self.cur.execute(
            "SELECT conversation_type, privacy, visibility, status, member_count, direct_key "
            "FROM comm_v2_conversations WHERE id=?",
            (conversation_id,),
        )
        row = self.cur.fetchone()
        self.assertEqual("direct", row["conversation_type"])
        self.assertEqual("private", row["privacy"])
        self.assertEqual("members", row["visibility"])
        self.assertEqual("active", row["status"])
        self.assertEqual(2, int(row["member_count"]))
        self.assertEqual(f"{BUYER}:{SELLER}", row["direct_key"])

    # ---- idempotency and concurrency ------------------------------------

    def test_tapping_message_seller_twice_opens_one_conversation(self):
        first = self._open()
        second = self._open()
        self.assertEqual(first, second)
        self.assertEqual(1, len(self._direct_rows()))

    def test_either_party_starting_it_converges_on_one_thread(self):
        """The key is the pair, not who tapped first."""
        buyer_side = self._open(BUYER, SELLER)
        seller_side = self._open(SELLER, BUYER)
        self.assertEqual(buyer_side, seller_side)
        self.assertEqual(1, len(self._direct_rows()))

    def test_a_simultaneous_duplicate_insert_cannot_fork_the_thread(self):
        """Stands in for two taps racing in separate requests.

        The losing writer's row is swallowed by ``UNIQUE(direct_key)`` -- which is
        why the id is read back with a SELECT and never from ``lastrowid``, whose
        value on the losing side belongs to no inserted row.
        """
        winner = self._open()
        self.cur.execute(
            "INSERT OR IGNORE INTO comm_v2_conversations "
            "(public_id, conversation_type, title, owner_user_id, created_by_user_id, direct_key, "
            " privacy, visibility, status, member_count, created_at, updated_at, last_activity_at) "
            "VALUES ('dm_racing_writer', 'direct', '', ?, ?, ?, 'private', 'members', 'active', 0, '', '', '')",
            (BUYER, BUYER, f"{BUYER}:{SELLER}"),
        )
        self.assertEqual(1, len(self._direct_rows()))
        self.assertEqual(winner, self._open())

    def test_repeat_taps_do_not_duplicate_participants_or_inflate_member_count(self):
        conversation_id = self._open()
        for _ in range(4):
            self._open()
        self.cur.execute(
            "SELECT COUNT(*) n FROM comm_v2_participants WHERE conversation_id=?",
            (conversation_id,),
        )
        self.assertEqual(2, int(self.cur.fetchone()["n"]))
        self.cur.execute("SELECT member_count FROM comm_v2_conversations WHERE id=?", (conversation_id,))
        self.assertEqual(2, int(self.cur.fetchone()["member_count"]))

    def test_an_existing_canonical_thread_is_joined_not_forked(self):
        """Every one of production's five legacy DM pairs already has a v2 thread.

        This is why the fix keys on ``direct_key`` instead of pairing the legacy
        row through ``sync_thread``: pairing would have minted a second thread for
        a pair that already had one, splitting the buyer's history in half.
        """
        self.cur.execute(
            "INSERT INTO comm_v2_conversations "
            "(public_id, conversation_type, title, owner_user_id, created_by_user_id, direct_key, "
            " privacy, visibility, status, member_count, created_at, updated_at, last_activity_at) "
            "VALUES ('dm_already_here', 'direct', '', ?, ?, ?, 'private', 'members', 'active', 2, '', '', '')",
            (SELLER, SELLER, f"{BUYER}:{SELLER}"),
        )
        existing_id = int(self.cur.lastrowid)
        comm_v2_service._add_participant(self.cur, existing_id, BUYER, "member")
        comm_v2_service._add_participant(self.cur, existing_id, SELLER, "member")

        self.assertEqual(existing_id, self._open())
        self.assertEqual(1, len(self._direct_rows()))

    # ---- reviving a thread that stopped resolving ------------------------

    def test_a_soft_deleted_thread_is_revived_rather_than_404ing(self):
        """``start or open`` has to mean the thread is usable afterwards.

        ``_conversation_access`` filters on ``deleted_at``, so a pair whose thread
        was soft-deleted would otherwise get the original defect back: a non-zero
        id for a row that exists and still resolves to ``missing``. A bare INSERT
        cannot escape it either -- ``UNIQUE(direct_key)`` already holds the row,
        and on Postgres that IntegrityError aborts the surrounding transaction.
        """
        conversation_id = self._open()
        self.cur.execute(
            "UPDATE comm_v2_conversations SET deleted_at='2026-10-01T00:00:00Z', status='deleted' WHERE id=?",
            (conversation_id,),
        )
        self.assertEqual("missing", self._access(BUYER, conversation_id))

        self.assertEqual(conversation_id, self._open())
        self.assertEqual("ok", self._access(BUYER, conversation_id))
        self.assertEqual(1, len(self._direct_rows()))

    def test_a_party_who_left_is_readmitted(self):
        """Active membership is the other half of what the resolver checks."""
        conversation_id = self._open()
        self.cur.execute(
            "UPDATE comm_v2_participants SET membership_state='left', left_at='2026-10-01T00:00:00Z' "
            "WHERE conversation_id=? AND user_id=?",
            (conversation_id, BUYER),
        )
        self.assertEqual("denied", self._access(BUYER, conversation_id))

        self.assertEqual(conversation_id, self._open())
        self.assertEqual("ok", self._access(BUYER, conversation_id))
        self.assertEqual([BUYER, SELLER], self._active_members(conversation_id))

    # ---- refusals --------------------------------------------------------

    def test_a_seller_messaging_their_own_listing_gets_no_thread(self):
        """BUYER == SELLER must not mint a self-conversation.

        ``MarketplaceProductScreen`` hides the button via ``isOwnListing``, but a
        hidden button is not a guard -- the request is reachable regardless.
        """
        self.assertEqual(0, self._open(SELLER, SELLER))
        self.assertEqual([], self._direct_rows())

    def test_a_missing_party_gets_no_thread(self):
        for buyer, seller in ((BUYER, 0), (0, SELLER), (0, 0), (BUYER, None)):
            with self.subTest(buyer=buyer, seller=seller):
                self.assertEqual(0, pulse_chat_bridge.direct_thread(self.cur, self.conn, buyer, seller))
        self.assertEqual([], self._direct_rows())

    def test_an_outsider_cannot_enter_the_conversation_by_guessing_its_id(self):
        """IDOR: the id is not a capability. Membership is."""
        conversation_id = self._open()
        self.assertEqual("denied", self._access(THIRD_PARTY, conversation_id))

    def test_a_block_closes_the_conversation_to_both_sides(self):
        """A thread opened before a block must stop resolving once one exists."""
        conversation_id = self._open(BLOCKED_BUYER, SELLER)
        self.assertEqual("ok", self._access(BLOCKED_BUYER, conversation_id))

        self.cur.execute(
            "INSERT INTO comm_v2_blocks (blocker_user_id, blocked_user_id, status, created_at) "
            "VALUES (?, ?, 'active', '2026-10-01T00:00:00Z')",
            (SELLER, BLOCKED_BUYER),
        )
        self.assertEqual("blocked", self._access(BLOCKED_BUYER, conversation_id))
        self.assertEqual("blocked", self._access(SELLER, conversation_id))

    def test_disabling_v2_declines_instead_of_half_writing(self):
        """``0`` means "fall back", the same contract ``sync_thread`` has.

        The caller keeps its legacy behaviour rather than being handed a v2 id
        pointing at nothing.
        """
        real_enabled = pulse_chat_bridge._v2_enabled
        pulse_chat_bridge._v2_enabled = lambda: False
        try:
            self.assertEqual(0, self._open())
        finally:
            pulse_chat_bridge._v2_enabled = real_enabled
        self.assertEqual([], self._direct_rows())


if __name__ == "__main__":
    unittest.main()
