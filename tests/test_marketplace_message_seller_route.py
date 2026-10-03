"""Message seller, driven through the real HTTP layer, end to end.

The buyer's journey is two requests, and the defect lived in the seam between
them:

1. ``POST /api/pulse/messages/start`` -- what the **Message seller** button calls
   (``mobile-native/src/api/marketplace.ts`` ``startMarketplaceSellerChat``).
2. ``GET /api/pulse/communications/v2/conversations/<id>/messages`` -- what the
   chat screen then calls with the id request 1 handed back
   (``mobile-native/src/api/messenger.ts``).

Step 1 answered ``200`` with a **legacy** ``pulse_conversations`` id. Step 2
resolves ids against ``comm_v2_conversations``, so it answered ``404
Conversation not found`` -- which is the string the buyer actually saw. Both
requests behaved exactly as written; the contract between them was wrong.

The companion unit suite
(``tests/test_marketplace_seller_conversation_bridge.py``) pins the resolver
semantics. This file exists because that is not sufficient: the bug was a wire
contract, and only a test that takes the id out of response 1 and puts it into
request 2 -- without ever looking inside -- can prove the seam is closed. So
``test_the_buyer_opens_the_conversation_the_button_returned`` deliberately treats
the id as opaque, the way the client does.

``test_the_legacy_id_the_old_build_returned_still_404s`` asserts the inverse
against the same two endpoints, so a pass here distinguishes the fixed build
from the broken one instead of merely tolerating both.

Why ``legacy_conversation_id`` is on the wire
--------------------------------------------
Rooms and Groups already solved this: ``POST /api/pulse/groups/<slug>/chat/open``
returns the v2 id as ``conversation_id`` and the legacy id as
``group_conversation_id`` (pinned in
``tests/test_app_review_rooms_groups_messages.py``). Direct messages now follow
the same shape rather than inventing a second convention -- ``conversation_id``
is always the id a client opens, on every surface.

Harness notes
-------------
Modelled on ``tests/test_app_review_rooms_groups_messages.py``: a temp sqlite
file bound before ``import bot`` so nothing touches ``coinpilotx.db``, and
authentication injected by patching ``bot.require_account`` rather than minting
sessions, because ``api_account_user`` resolves it on the module at call time.
Everything below that patch -- routing, parsing, the block and privacy gates,
SQL, persistence -- is the real code path.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="marketplace_message_seller_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

V2 = "/api/pulse/communications/v2"
START = "/api/pulse/messages/start"

# Fresh accounts per test: `pulse_message_threads` and the legacy conversation
# are keyed on the user pair, so a reused buyer would make "opens a new thread"
# and "reopens the existing one" the same test.
_USER_ID_SEQUENCE = iter(range(80001, 89000))


def _next_account(role):
    user_id = next(_USER_ID_SEQUENCE)
    return {
        "user_id": user_id,
        "username": f"ccb_{role}_{user_id}",
        "display_name": f"CCB {role.title()} {user_id}",
        "email": f"ccb_{role}_{user_id}@example.com",
    }


def _use_module_database():
    """Re-point the process at this module's temp database and rebuild schema.

    ``services.db`` resolves ``DATABASE_URL`` per connection and pytest imports
    every selected module during collection, so a module collected after this
    one leaves the environment pointing at its database. Both schema builders
    also short-circuit on module flags once anything else has run them. Clearing
    all three per test is cheap (both passes are ``IF NOT EXISTS``) and buys
    independence from collection order.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.PULSE_MESSENGER_SCHEMA_READY = False
    bot.init_db()


class MessageSellerRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        self.buyer = _next_account("buyer")
        self.seller = _next_account("seller")       # stands in for the M&W Store owner
        self.outsider = _next_account("outsider")
        for account in (self.buyer, self.seller, self.outsider):
            self._register(account)
        self._real_require_account = bot.require_account
        # Push/email/socket fan-out runs after the commit and is not what this
        # defect was about, so stubbing it keeps the test hermetic without
        # weakening any assertion about what was persisted or returned.
        self._real_finalize = bot.pulse_finalize_message_delivery
        self._real_emit = bot.pulse_emit_event
        bot.pulse_finalize_message_delivery = lambda *a, **k: None
        bot.pulse_emit_event = lambda *a, **k: None

    def tearDown(self):
        bot.require_account = self._real_require_account
        bot.pulse_finalize_message_delivery = self._real_finalize
        bot.pulse_emit_event = self._real_emit

    # ---- helpers ---------------------------------------------------------

    def _register(self, account, account_status="active"):
        conn = bot.db()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, display_name, email, account_status) "
            "VALUES (?, ?, ?, ?, ?)",
            (account["user_id"], account["username"], account["display_name"], account["email"], account_status),
        )
        conn.commit()
        conn.close()

    def _write(self, sql, params=()):
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        conn.close()

    @contextmanager
    def acting_as(self, user):
        previous = bot.require_account
        bot.require_account = lambda: dict(user)
        try:
            yield
        finally:
            bot.require_account = previous

    def message_seller(self, buyer=None, seller=None):
        """Exactly what the Message seller button sends."""
        buyer = buyer or self.buyer
        seller = seller or self.seller
        with self.acting_as(buyer):
            return self.client.post(START, json={"user_id": seller["user_id"]})

    def open_conversation(self, user, conversation_id):
        """Exactly what the chat screen sends next."""
        with self.acting_as(user):
            return self.client.get(f"{V2}/conversations/{conversation_id}/messages")

    def send(self, user, conversation_id, body):
        with self.acting_as(user):
            return self.client.post(f"{V2}/conversations/{conversation_id}/messages", json={"body": body})

    def started_ok(self, resp):
        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data.get("ok"), data)
        return data

    # ---- the fix ---------------------------------------------------------

    def test_the_buyer_opens_the_conversation_the_button_returned(self):
        """The id is treated as opaque here, exactly as the client treats it."""
        data = self.started_ok(self.message_seller())
        conversation_id = data.get("conversation_id")
        self.assertTrue(conversation_id, f"no conversation to open: {data}")

        opened = self.open_conversation(self.buyer, conversation_id)
        self.assertEqual(200, opened.status_code, opened.get_data(as_text=True))

    def test_the_seller_can_open_it_too(self):
        conversation_id = self.started_ok(self.message_seller())["conversation_id"]
        opened = self.open_conversation(self.seller, conversation_id)
        self.assertEqual(200, opened.status_code, opened.get_data(as_text=True))

    def test_the_legacy_id_the_old_build_returned_still_404s(self):
        """Proves this file can fail.

        ``legacy_conversation_id`` is the value ``conversation_id`` used to
        carry. Feeding it to step 2 reproduces the original bug report, so if
        this ever passes with a 200 the two id spaces have collided and every
        other assertion here is worthless.
        """
        data = self.started_ok(self.message_seller())
        legacy_id = data.get("legacy_conversation_id")
        self.assertTrue(legacy_id, f"the legacy id must stay on the wire for tracing: {data}")

        opened = self.open_conversation(self.buyer, legacy_id)
        self.assertEqual(404, opened.status_code, opened.get_data(as_text=True))

    def test_the_two_ids_are_different_rows_in_different_stacks(self):
        data = self.started_ok(self.message_seller())
        self.assertNotEqual(
            str(data.get("conversation_id")),
            str(data.get("legacy_conversation_id")),
            "a client id that equals the legacy id means the swap never happened",
        )
        conn = bot.db()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT conversation_type, direct_key FROM comm_v2_conversations WHERE id=?",
            (int(data["conversation_id"]),),
        )
        v2_row = dict(cur.fetchone() or {})
        cur.execute(
            "SELECT conversation_type FROM pulse_conversations WHERE id=?",
            (int(data["legacy_conversation_id"]),),
        )
        legacy_row = dict(cur.fetchone() or {})
        conn.close()
        self.assertEqual("direct", v2_row.get("conversation_type"))
        self.assertEqual("direct", legacy_row.get("conversation_type"))
        low, high = sorted([int(self.buyer["user_id"]), int(self.seller["user_id"])])
        self.assertEqual(f"{low}:{high}", v2_row.get("direct_key"))

    def test_the_buyer_and_seller_actually_exchange_messages(self):
        """The acceptance bar is a working conversation, not a 200 on open."""
        conversation_id = self.started_ok(self.message_seller())["conversation_id"]

        sent = self.send(self.buyer, conversation_id, "Is the lid still available?")
        self.assertEqual(200, sent.status_code, sent.get_data(as_text=True))
        replied = self.send(self.seller, conversation_id, "Yes, shipping today.")
        self.assertEqual(200, replied.status_code, replied.get_data(as_text=True))

        read = self.open_conversation(self.buyer, conversation_id)
        self.assertEqual(200, read.status_code, read.get_data(as_text=True))
        bodies = [item.get("body") for item in read.get_json().get("messages") or []]
        self.assertIn("Is the lid still available?", bodies)
        self.assertIn("Yes, shipping today.", bodies)

    # ---- web parity ------------------------------------------------------

    def test_the_web_redirect_points_at_the_openable_conversation(self):
        """Five web buttons do ``location.href = d.next_url`` off this endpoint.

        ``/pulse/messages/<id>`` renders ``pulse_messages_v2.html`` with the id as
        ``initial_conversation_id``, so web resolved it against the same v2 stack
        and broke in exactly the same way. Both URLs must carry the client id, or
        the fix lands on native only.
        """
        data = self.started_ok(self.message_seller())
        conversation_id = data["conversation_id"]
        self.assertEqual(f"/pulse/messages/{conversation_id}", data.get("next_url"))
        self.assertEqual(f"/pulse/messages/{conversation_id}", data.get("redirect_url"))

    # ---- idempotency -----------------------------------------------------

    def test_tapping_message_seller_twice_returns_the_same_conversation(self):
        first = self.started_ok(self.message_seller())
        second = self.started_ok(self.message_seller())
        self.assertEqual(first["conversation_id"], second["conversation_id"])
        self.assertEqual(first["legacy_conversation_id"], second["legacy_conversation_id"])

    def test_the_seller_replying_from_their_side_lands_in_one_thread(self):
        buyer_side = self.started_ok(self.message_seller())["conversation_id"]
        with self.acting_as(self.seller):
            resp = self.client.post(START, json={"user_id": self.buyer["user_id"]})
        self.assertEqual(buyer_side, self.started_ok(resp)["conversation_id"])

    def test_repeated_taps_create_exactly_one_conversation_per_pair(self):
        for _ in range(5):
            self.message_seller()
        conn = bot.db()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        low, high = sorted([int(self.buyer["user_id"]), int(self.seller["user_id"])])
        cur.execute(
            "SELECT COUNT(*) AS n FROM comm_v2_conversations WHERE direct_key=?",
            (f"{low}:{high}",),
        )
        v2_count = int(dict(cur.fetchone())["n"])
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM pulse_conversations c
            JOIN pulse_conversation_participants a ON a.conversation_id=c.id AND a.user_id=?
            JOIN pulse_conversation_participants b ON b.conversation_id=c.id AND b.user_id=?
            WHERE c.conversation_type='direct'
            """,
            (int(self.buyer["user_id"]), int(self.seller["user_id"])),
        )
        legacy_count = int(dict(cur.fetchone())["n"])
        conn.close()
        self.assertEqual(1, v2_count)
        self.assertEqual(1, legacy_count)

    # ---- refusals --------------------------------------------------------

    def test_a_seller_cannot_message_their_own_listing(self):
        with self.acting_as(self.seller):
            resp = self.client.post(START, json={"user_id": self.seller["user_id"]})
        self.assertEqual(400, resp.status_code, resp.get_data(as_text=True))
        self.assertFalse(resp.get_json().get("ok"))

    def test_an_outsider_cannot_open_the_conversation(self):
        """IDOR: holding the id is not membership."""
        conversation_id = self.started_ok(self.message_seller())["conversation_id"]
        opened = self.open_conversation(self.outsider, conversation_id)
        self.assertIn(opened.status_code, (403, 404), opened.get_data(as_text=True))

    def test_a_blocked_buyer_is_refused_before_a_conversation_exists(self):
        self._write(
            "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, created_at) VALUES (?, ?, ?)",
            (self.seller["user_id"], self.buyer["user_id"], "2026-10-01T00:00:00"),
        )
        resp = self.message_seller()
        self.assertEqual(403, resp.status_code, resp.get_data(as_text=True))
        self.assertFalse(resp.get_json().get("ok"))

    def test_a_suspended_seller_cannot_be_messaged(self):
        self._write(
            "UPDATE users SET account_status='suspended' WHERE user_id=?",
            (self.seller["user_id"],),
        )
        resp = self.message_seller()
        self.assertEqual(403, resp.status_code, resp.get_data(as_text=True))

    def test_a_seller_who_does_not_exist_is_a_404_not_a_dead_conversation(self):
        with self.acting_as(self.buyer):
            resp = self.client.post(START, json={"user_id": 7654321})
        self.assertEqual(404, resp.status_code, resp.get_data(as_text=True))

    def test_an_anonymous_tap_is_told_to_log_in(self):
        previous = bot.require_account
        bot.require_account = lambda: None
        try:
            resp = self.client.post(START, json={"user_id": self.seller["user_id"]})
        finally:
            bot.require_account = previous
        self.assertEqual(401, resp.status_code, resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
