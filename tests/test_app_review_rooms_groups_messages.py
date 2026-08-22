"""App Review fixes — item 5 (rooms + groups + messages must actually work).

The acceptance bar for this item is behavioural, not structural: it is not enough
that routes exist, they have to carry a real two-account conversation. So these
tests drive the HTTP layer as two different accounts and assert the message
actually crosses between them and survives a reload.

The round trip under test mirrors the manual QA script exactly:

    A creates a room -> B sees it -> B joins -> A sends -> B receives
    -> B reloads (persisted) -> B replies -> A receives both

On top of that round trip we pin the permission rules that make a room a real
room rather than a shared bucket: private rooms are invisible and unjoinable to
outsiders, non-members cannot read them, only the owner can delete, and an owner
cannot walk out and leave the room ownerless.

Two ids, two stacks
-------------------
A room/group is created in the legacy ``pulse_conversations`` tables, which own
the entity: rate limits, invitees, roles, owner-cannot-orphan, admin audit. The
*messages* live in the canonical v2 stack (``comm_v2_*``) under
``/api/pulse/communications/v2``, which is the only messaging API any client
speaks — ``mobile-native``'s ChatScreen and ``pulse_messages_v2.html`` both read
it. ``services/pulse_chat_bridge`` pairs the two.

So these tests deliberately use **two different ids**: ``room_id`` (legacy) for
every lifecycle call, and ``chat_id`` (v2) for every message call — exactly the
split the mobile client uses. An earlier version of this file sent messages to
the legacy endpoint with the legacy id and passed, which proved nothing: no
client uses that endpoint, so a green suite sat on top of a room you could
create, list, join and manage but never actually talk in. Asserting the two ids
are distinct is therefore part of the test, not an incidental detail.

Authentication is injected by patching ``bot.require_account`` rather than by
minting session cookies. ``api_account_user`` looks the name up on the module at
call time, so the patch covers every route uniformly, and it keeps the test
focused on room/membership/message logic instead of on the login stack (which
has its own suites). Everything below the patch — routing, request parsing,
permission checks, SQL, persistence — is the real code path.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sys
import tempfile
import unittest
from collections import namedtuple
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="app_review_rooms_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

# Each test gets its own trio of accounts — an owner, a joiner, and an unrelated
# outsider used to prove private rooms stay private. Fresh ids per test are
# deliberate: room creation is capped at 5 per owner per hour by the route (a
# real anti-abuse guard we do not want to weaken), and reusing one owner across
# tests would also let membership from an earlier test leak into a later one.
_USER_ID_SEQUENCE = iter(range(90001, 99000))

# The canonical messaging API — the one every client actually speaks.
V2 = "/api/pulse/communications/v2"

# ``room_id`` addresses the room entity (join/leave/manage/roles); ``chat_id``
# addresses the v2 conversation that carries its messages. Keeping them in one
# object makes it impossible to accidentally send a message to a lifecycle id.
Room = namedtuple("Room", "room_id chat_id")


def _next_account(role):
    user_id = next(_USER_ID_SEQUENCE)
    return {
        "user_id": user_id,
        "username": f"review_{role}_{user_id}",
        "display_name": f"Review {role.title()} {user_id}",
        "email": f"review_{role}_{user_id}@example.com",
    }


def _use_module_database():
    """Re-point the process at this module's temp database and guarantee schema.

    Three separate pieces of global state conspire to make this file
    order-dependent unless all three are re-asserted:

    1. ``services.db`` resolves ``DATABASE_URL`` lazily on every connection, and
       pytest imports every selected module during collection, so a module
       collected after this one leaves the environment pointing at *its*
       database by the time these tests run.
    2. ``bot.init_db`` short-circuits on ``INIT_DB_COMPLETED``, so once any
       other module has built a schema somewhere else, ours never gets built.
    3. ``ensure_pulse_messenger_schema`` short-circuits on
       ``PULSE_MESSENGER_SCHEMA_READY``, so the messenger tables (and the
       additive columns such as ``pulse_conversations.deleted_at``) are skipped
       for the same reason.

    Leaving any one of them set produces a confusing half-built database —
    "no such table: pulse_conversations" or "no such column: deleted_at" —
    that depends on which files were passed to pytest and in what order.
    Both builds are idempotent ``CREATE TABLE IF NOT EXISTS`` / additive-column
    passes, so re-running them per test is cheap and buys full independence.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.PULSE_MESSENGER_SCHEMA_READY = False
    bot.init_db()


class RoomsGroupsMessagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        self.user_a = _next_account("owner")
        self.user_b = _next_account("member")
        self.user_c = _next_account("outsider")
        self._real_require_account = bot.require_account
        # Delivery fan-out (push/email/socket) is not what this item is about and
        # it runs after the DB commit, so stub it to keep the test hermetic
        # without weakening any assertion about what was actually persisted.
        self._real_finalize = bot.pulse_finalize_message_delivery
        self._real_emit = bot.pulse_emit_event
        bot.pulse_finalize_message_delivery = lambda *a, **k: None
        bot.pulse_emit_event = lambda *a, **k: None

    def tearDown(self):
        bot.require_account = self._real_require_account
        bot.pulse_finalize_message_delivery = self._real_finalize
        bot.pulse_emit_event = self._real_emit

    @contextmanager
    def acting_as(self, user):
        """Run the enclosed requests as ``user``."""
        previous = bot.require_account
        bot.require_account = lambda: dict(user)
        try:
            yield
        finally:
            bot.require_account = previous

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def create_room(self, user, title, privacy="public", invitees=None):
        payload = {"title": title, "description": "Created by the app review suite.", "privacy": privacy}
        if invitees:
            payload["invitee_user_ids"] = list(invitees)
        with self.acting_as(user):
            resp = self.client.post("/api/pulse/communications/rooms", json=payload)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data.get("ok"), data)
        room_id = str(data.get("room_id") or "")
        self.assertTrue(room_id.isdigit(), f"room_id should be a numeric conversation id, got {room_id!r}")
        chat_id = data.get("conversation_id")
        self.assertTrue(chat_id, f"room create must hand back a chat conversation id, got {data}")
        return Room(room_id, chat_id)

    def list_rooms(self, user):
        with self.acting_as(user):
            resp = self.client.get("/api/pulse/communications/rooms")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json().get("rooms") or []

    def room_ids(self, user):
        return {str(item.get("id") or "") for item in self.list_rooms(user)}

    def join_room(self, user, room_id):
        """Join and return the chat id the client would then navigate into."""
        with self.acting_as(user):
            resp = self.client.post(f"/api/pulse/communications/rooms/{room_id}/join", json={})
        return resp

    def send_message(self, user, chat_id, body, **extra):
        with self.acting_as(user):
            return self.client.post(f"{V2}/conversations/{chat_id}/messages", json={"body": body, **extra})

    def read_messages(self, user, chat_id):
        with self.acting_as(user):
            return self.client.get(f"{V2}/conversations/{chat_id}/messages")

    def bodies(self, resp):
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return [item.get("body") for item in resp.get_json().get("messages") or []]

    # ------------------------------------------------------------------
    # the round trip the mission asks for
    # ------------------------------------------------------------------
    def test_two_account_room_message_round_trip(self):
        room = self.create_room(self.user_a, "Review Round Trip")

        # B can discover the public room before joining it.
        self.assertIn(room.room_id, self.room_ids(self.user_b), "a public room must be discoverable by other accounts")

        join = self.join_room(self.user_b, room.room_id)
        self.assertEqual(join.status_code, 200, join.get_data(as_text=True))
        self.assertTrue(join.get_json().get("ok"))
        # Joining hands back the same chat thread the creator was given — the
        # client navigates straight into it, so a mismatch here is a dead room.
        self.assertEqual(join.get_json().get("conversation_id"), room.chat_id)

        # A sends, B receives.
        self.assertEqual(self.send_message(self.user_a, room.chat_id, "Hello from A").status_code, 200)
        self.assertIn("Hello from A", self.bodies(self.read_messages(self.user_b, room.chat_id)))

        # A reload returns the same message: it is persisted, not in-memory.
        self.assertIn("Hello from A", self.bodies(self.read_messages(self.user_b, room.chat_id)))

        # B replies, A receives the whole thread in order.
        self.assertEqual(self.send_message(self.user_b, room.chat_id, "Reply from B").status_code, 200)
        thread = self.bodies(self.read_messages(self.user_a, room.chat_id))
        self.assertEqual(thread[-2:], ["Hello from A", "Reply from B"])

    def test_room_chat_id_is_the_v2_conversation_not_the_room_id(self):
        """The whole defect this file exists to catch, pinned directly.

        The room entity and its chat thread live in different tables with
        independent id sequences. Handing the client the *room* id sends it to
        ``/api/pulse/communications/v2/conversations/<room id>`` — a different
        conversation or, more often, none at all. On a freshly created database
        the two sequences happen to line up, which is why this went unnoticed;
        creating a room after other conversations already exist pulls them apart.
        """
        # Push the two id spaces apart so an accidental legacy id cannot pass.
        first = self.create_room(self.user_a, "Review Id Space A")
        self.send_message(self.user_a, first.chat_id, "seed")
        room = self.create_room(self.user_a, "Review Id Space B")

        self.assertNotEqual(
            str(room.chat_id),
            room.room_id,
            "test is not proving anything unless the two id spaces have diverged",
        )
        # The id handed to the client must resolve on the API the client calls.
        self.assertEqual(self.send_message(self.user_a, room.chat_id, "reachable").status_code, 200)
        self.assertIn("reachable", self.bodies(self.read_messages(self.user_a, room.chat_id)))

        # ...and the lifecycle id must not, or the two have been confused again.
        self.assertNotEqual(self.read_messages(self.user_a, room.room_id).status_code, 200)

    def test_repeated_send_of_one_client_message_id_stores_one_message(self):
        """A retried send (flaky network, user double-tap) must not duplicate."""
        room = self.create_room(self.user_a, "Review Idempotency")
        first = self.send_message(self.user_a, room.chat_id, "only once", client_message_id="cid-review-1")
        second = self.send_message(self.user_a, room.chat_id, "only once", client_message_id="cid-review-1")
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        self.assertEqual(
            first.get_json().get("message_id"),
            second.get_json().get("message_id"),
            "a retry must resolve to the original message, not a new one",
        )
        self.assertEqual(self.bodies(self.read_messages(self.user_a, room.chat_id)).count("only once"), 1)

    def test_empty_message_is_rejected(self):
        room = self.create_room(self.user_a, "Review Empty Guard")
        resp = self.send_message(self.user_a, room.chat_id, "   ")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json().get("ok"))

    # ------------------------------------------------------------------
    # privacy and membership
    # ------------------------------------------------------------------
    def test_private_room_is_hidden_and_unjoinable_for_outsiders(self):
        room = self.create_room(self.user_a, "Review Private", privacy="private")

        self.assertIn(room.room_id, self.room_ids(self.user_a), "the owner must still see their own private room")
        self.assertNotIn(
            room.room_id, self.room_ids(self.user_c), "a private room must not appear in another account's list"
        )

        with self.acting_as(self.user_c):
            join = self.client.post(f"/api/pulse/communications/rooms/{room.room_id}/join", json={})
        self.assertEqual(join.status_code, 403, join.get_data(as_text=True))

        # The chat thread has to enforce this too, not just the room entity —
        # the outsider knows the chat id the moment they know the room exists.
        self.assertEqual(self.read_messages(self.user_c, room.chat_id).status_code, 403)
        self.assertEqual(self.send_message(self.user_c, room.chat_id, "let me in").status_code, 403)

    def test_invited_member_can_use_a_private_room(self):
        """Invitees named at creation are seeded as members, so the room works for them."""
        room = self.create_room(
            self.user_a, "Review Invite", privacy="private", invitees=[self.user_b["user_id"]]
        )

        self.assertIn(room.room_id, self.room_ids(self.user_b), "an invited member must see the private room")
        self.assertEqual(self.send_message(self.user_a, room.chat_id, "Private hello").status_code, 200)
        self.assertIn("Private hello", self.bodies(self.read_messages(self.user_b, room.chat_id)))

    def test_member_can_leave_but_owner_cannot_orphan_the_room(self):
        # A private room, because that is where losing access on the way out is
        # actually enforceable: a public room stays readable to anyone by design.
        room = self.create_room(
            self.user_a, "Review Leave", privacy="private", invitees=[self.user_b["user_id"]]
        )
        self.assertEqual(self.send_message(self.user_b, room.chat_id, "while a member").status_code, 200)

        with self.acting_as(self.user_a):
            owner_leave = self.client.post(f"/api/pulse/communications/rooms/{room.room_id}/leave", json={})
        self.assertEqual(owner_leave.status_code, 400, "an owner must archive or delete rather than leave")

        with self.acting_as(self.user_b):
            member_leave = self.client.post(f"/api/pulse/communications/rooms/{room.room_id}/leave", json={})
        self.assertEqual(member_leave.status_code, 200, member_leave.get_data(as_text=True))
        self.assertTrue(member_leave.get_json().get("left"))

        # Leaving the room has to revoke the chat thread as well, or the member
        # walked out of the door and stayed in the conversation.
        self.assertEqual(self.read_messages(self.user_b, room.chat_id).status_code, 403)
        self.assertEqual(self.send_message(self.user_b, room.chat_id, "after leaving").status_code, 403)

    # ------------------------------------------------------------------
    # owner / moderator controls
    # ------------------------------------------------------------------
    def test_only_the_owner_can_delete_a_room(self):
        room = self.create_room(self.user_a, "Review Delete")
        self.join_room(self.user_b, room.room_id)

        with self.acting_as(self.user_b):
            denied = self.client.delete(f"/api/pulse/communications/rooms/{room.room_id}")
        self.assertEqual(denied.status_code, 403, "a plain member must not be able to delete the room")
        self.assertIn(room.room_id, self.room_ids(self.user_a), "the failed delete must not have removed anything")
        self.assertEqual(self.send_message(self.user_b, room.chat_id, "still open").status_code, 200)

        with self.acting_as(self.user_a):
            deleted = self.client.delete(f"/api/pulse/communications/rooms/{room.room_id}")
        self.assertEqual(deleted.status_code, 200, deleted.get_data(as_text=True))
        self.assertNotIn(room.room_id, self.room_ids(self.user_a), "a deleted room must leave the list")
        self.assertNotIn(room.room_id, self.room_ids(self.user_b))

        # Deleting the room has to close its chat thread too, otherwise members
        # keep messaging a room that no longer exists anywhere else in the app.
        self.assertEqual(self.read_messages(self.user_a, room.chat_id).status_code, 404)
        self.assertEqual(self.send_message(self.user_b, room.chat_id, "after delete").status_code, 404)

    def test_owner_can_promote_a_moderator_who_can_then_manage(self):
        """The moderator tier has to be reachable, not just accepted by the checks.

        Room management accepts owner/admin/moderator, but before this route
        existed the only role ever written to ``pulse_conversation_participants``
        was ``owner``, so a room had exactly one person who could manage it and
        the moderator branch was dead code.
        """
        room = self.create_room(self.user_a, "Review Moderator")
        self.join_room(self.user_b, room.room_id)

        # A plain member cannot manage the room.
        with self.acting_as(self.user_b):
            before = self.client.patch(
                f"/api/pulse/communications/rooms/{room.room_id}", json={"action": "update", "title": "Nope"}
            )
        self.assertEqual(before.status_code, 403)

        # A non-owner cannot hand out roles either.
        with self.acting_as(self.user_b):
            self_promote = self.client.post(
                f"/api/pulse/communications/rooms/{room.room_id}/members/role",
                json={"user_id": self.user_b["user_id"], "role": "moderator"},
            )
        self.assertEqual(self_promote.status_code, 403, "a member must not be able to promote themselves")

        # Non-members cannot be promoted.
        with self.acting_as(self.user_a):
            stranger = self.client.post(
                f"/api/pulse/communications/rooms/{room.room_id}/members/role",
                json={"user_id": self.user_c["user_id"], "role": "moderator"},
            )
        self.assertEqual(stranger.status_code, 404)

        with self.acting_as(self.user_a):
            promoted = self.client.post(
                f"/api/pulse/communications/rooms/{room.room_id}/members/role",
                json={"user_id": self.user_b["user_id"], "role": "moderator"},
            )
        self.assertEqual(promoted.status_code, 200, promoted.get_data(as_text=True))
        self.assertEqual(promoted.get_json().get("role"), "moderator")

        # The promotion is what actually grants management.
        with self.acting_as(self.user_b):
            after = self.client.patch(
                f"/api/pulse/communications/rooms/{room.room_id}",
                json={"action": "update", "title": "Review Moderator Renamed"},
            )
        self.assertEqual(after.status_code, 200, after.get_data(as_text=True))
        self.assertIn("Review Moderator Renamed", {item.get("title") for item in self.list_rooms(self.user_a)})

        # A moderator still is not an owner: deletion stays owner-only.
        with self.acting_as(self.user_b):
            self.assertEqual(self.client.delete(f"/api/pulse/communications/rooms/{room.room_id}").status_code, 403)

        # Demotion takes the power back.
        with self.acting_as(self.user_a):
            self.client.post(
                f"/api/pulse/communications/rooms/{room.room_id}/members/role",
                json={"user_id": self.user_b["user_id"], "role": "member"},
            )
        with self.acting_as(self.user_b):
            demoted = self.client.patch(
                f"/api/pulse/communications/rooms/{room.room_id}", json={"action": "update", "title": "Nope again"}
            )
        self.assertEqual(demoted.status_code, 403)

    def test_owner_can_rename_and_archive(self):
        room = self.create_room(self.user_a, "Review Manage")

        with self.acting_as(self.user_a):
            renamed = self.client.patch(
                f"/api/pulse/communications/rooms/{room.room_id}",
                json={"action": "update", "title": "Review Manage Renamed", "privacy": "private"},
            )
        self.assertEqual(renamed.status_code, 200, renamed.get_data(as_text=True))
        titles = {item.get("title") for item in self.list_rooms(self.user_a)}
        self.assertIn("Review Manage Renamed", titles)

        with self.acting_as(self.user_a):
            archived = self.client.patch(f"/api/pulse/communications/rooms/{room.room_id}", json={"action": "archive"})
        self.assertEqual(archived.status_code, 200, archived.get_data(as_text=True))
        self.assertNotIn(room.room_id, self.room_ids(self.user_a), "an archived room must drop out of the active list")

    # ------------------------------------------------------------------
    # the built-in rooms and the group surface
    # ------------------------------------------------------------------
    def test_builtin_rooms_carry_messages_between_accounts(self):
        """The static lobby rooms are real conversations, not display cards."""
        with self.acting_as(self.user_a):
            listed = self.client.get("/api/pulse/messages/rooms")
        self.assertEqual(listed.status_code, 200, listed.get_data(as_text=True))
        rooms = listed.get_json().get("rooms") or []
        self.assertTrue(rooms, "there should be at least one built-in room")
        room_key = str(rooms[0].get("room_id") or rooms[0].get("id") or "")
        self.assertTrue(room_key)

        with self.acting_as(self.user_a):
            sent = self.client.post(f"/api/pulse/messages/rooms/{room_key}/messages", json={"body": "Lobby hello"})
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True))

        with self.acting_as(self.user_b):
            self.client.post(f"/api/pulse/messages/rooms/{room_key}/join", json={})
            read = self.client.get(f"/api/pulse/messages/rooms/{room_key}/messages")
        self.assertEqual(read.status_code, 200, read.get_data(as_text=True))
        self.assertIn("Lobby hello", [item.get("body") for item in read.get_json().get("messages") or []])

    def test_group_create_join_and_chat_are_wired(self):
        with self.acting_as(self.user_a):
            created = self.client.post(
                "/api/pulse/groups/create",
                json={
                    "name": "Review Group",
                    "category": "Community",
                    "description": "Created by the app review suite.",
                    "group_type": "public",
                },
            )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        payload = created.get_json()
        self.assertTrue(payload.get("ok"), payload)
        slug = payload.get("slug") or (payload.get("group") or {}).get("slug")
        self.assertTrue(slug, f"group create must return a slug, got {payload}")

        with self.acting_as(self.user_b):
            joined = self.client.post(f"/api/pulse/groups/{slug}/join", json={})
        self.assertEqual(joined.status_code, 200, joined.get_data(as_text=True))

        # The group chat surface resolves to a real conversation both can use.
        def open_chat(user):
            with self.acting_as(user):
                resp = self.client.post(f"/api/pulse/groups/{slug}/chat/open", json={})
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            return resp.get_json()

        opened_b = open_chat(self.user_b)
        chat_id = opened_b.get("conversation_id")
        self.assertTrue(chat_id, "opening group chat must return a conversation id")

        # Both members must land in the *same* thread, and it must be the v2
        # conversation rather than the legacy group conversation row.
        opened_a = open_chat(self.user_a)
        self.assertEqual(opened_a.get("conversation_id"), chat_id)
        self.assertNotEqual(
            chat_id,
            opened_b.get("group_conversation_id"),
            "the chat id handed to clients must be the v2 conversation, not the legacy group conversation",
        )

        self.assertEqual(self.send_message(self.user_b, chat_id, "Group hello").status_code, 200)
        self.assertIn("Group hello", self.bodies(self.read_messages(self.user_a, chat_id)))

        # A reply comes back the other way and the thread survives a reload.
        self.assertEqual(self.send_message(self.user_a, chat_id, "Group reply").status_code, 200)
        self.assertEqual(self.bodies(self.read_messages(self.user_b, chat_id))[-2:], ["Group hello", "Group reply"])

        # A public *group* still has a members-only chat: joining the group is
        # what buys the conversation, so an outsider must be turned away.
        self.assertEqual(self.read_messages(self.user_c, chat_id).status_code, 403)
        self.assertEqual(self.send_message(self.user_c, chat_id, "outsider").status_code, 403)


if __name__ == "__main__":
    unittest.main()
