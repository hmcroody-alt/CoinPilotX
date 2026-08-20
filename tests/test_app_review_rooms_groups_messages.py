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
outsiders, non-members cannot read, only the owner can delete, and an owner
cannot walk out and leave the room ownerless.

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
    def create_room(self, user, title, privacy="public"):
        with self.acting_as(user):
            resp = self.client.post(
                "/api/pulse/communications/rooms",
                json={"title": title, "description": "Created by the app review suite.", "privacy": privacy},
            )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data.get("ok"), data)
        room_id = str(data.get("room_id") or "")
        self.assertTrue(room_id.isdigit(), f"room_id should be a numeric conversation id, got {room_id!r}")
        return room_id

    def list_rooms(self, user):
        with self.acting_as(user):
            resp = self.client.get("/api/pulse/communications/rooms")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json().get("rooms") or []

    def room_ids(self, user):
        return {str(room.get("id") or "") for room in self.list_rooms(user)}

    def send_message(self, user, room_id, body):
        with self.acting_as(user):
            return self.client.post(
                f"/api/pulse/communications/conversations/{room_id}/messages",
                json={"body": body},
            )

    def read_messages(self, user, room_id):
        with self.acting_as(user):
            return self.client.get(f"/api/pulse/communications/conversations/{room_id}/messages")

    def bodies(self, resp):
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return [item.get("body") for item in resp.get_json().get("messages") or []]

    # ------------------------------------------------------------------
    # the round trip the mission asks for
    # ------------------------------------------------------------------
    def test_two_account_room_message_round_trip(self):
        room_id = self.create_room(self.user_a, "Review Round Trip")

        # B can discover the public room before joining it.
        self.assertIn(room_id, self.room_ids(self.user_b), "a public room must be discoverable by other accounts")

        # ...but cannot read it until membership exists.
        self.assertEqual(self.read_messages(self.user_b, room_id).status_code, 403)

        with self.acting_as(self.user_b):
            join = self.client.post(f"/api/pulse/communications/rooms/{room_id}/join", json={})
        self.assertEqual(join.status_code, 200, join.get_data(as_text=True))
        self.assertTrue(join.get_json().get("ok"))

        # A sends, B receives.
        self.assertEqual(self.send_message(self.user_a, room_id, "Hello from A").status_code, 200)
        self.assertIn("Hello from A", self.bodies(self.read_messages(self.user_b, room_id)))

        # A reload returns the same message: it is persisted, not in-memory.
        self.assertIn("Hello from A", self.bodies(self.read_messages(self.user_b, room_id)))

        # B replies, A receives the whole thread in order.
        self.assertEqual(self.send_message(self.user_b, room_id, "Reply from B").status_code, 200)
        thread = self.bodies(self.read_messages(self.user_a, room_id))
        self.assertEqual(thread[-2:], ["Hello from A", "Reply from B"])

    def test_empty_message_is_rejected(self):
        room_id = self.create_room(self.user_a, "Review Empty Guard")
        resp = self.send_message(self.user_a, room_id, "   ")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json().get("ok"))

    # ------------------------------------------------------------------
    # privacy and membership
    # ------------------------------------------------------------------
    def test_private_room_is_hidden_and_unjoinable_for_outsiders(self):
        room_id = self.create_room(self.user_a, "Review Private", privacy="private")

        self.assertIn(room_id, self.room_ids(self.user_a), "the owner must still see their own private room")
        self.assertNotIn(room_id, self.room_ids(self.user_c), "a private room must not appear in another account's list")

        with self.acting_as(self.user_c):
            join = self.client.post(f"/api/pulse/communications/rooms/{room_id}/join", json={})
        self.assertEqual(join.status_code, 403, join.get_data(as_text=True))

        self.assertEqual(self.read_messages(self.user_c, room_id).status_code, 403)
        self.assertEqual(self.send_message(self.user_c, room_id, "let me in").status_code, 403)

    def test_invited_member_can_use_a_private_room(self):
        """Invitees named at creation are seeded as members, so the room works for them."""
        with self.acting_as(self.user_a):
            resp = self.client.post(
                "/api/pulse/communications/rooms",
                json={"title": "Review Invite", "privacy": "private", "invitee_user_ids": [self.user_b["user_id"]]},
            )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        room_id = str(resp.get_json().get("room_id"))

        self.assertIn(room_id, self.room_ids(self.user_b), "an invited member must see the private room")
        self.assertEqual(self.send_message(self.user_a, room_id, "Private hello").status_code, 200)
        self.assertIn("Private hello", self.bodies(self.read_messages(self.user_b, room_id)))

    def test_member_can_leave_but_owner_cannot_orphan_the_room(self):
        room_id = self.create_room(self.user_a, "Review Leave")
        with self.acting_as(self.user_b):
            self.client.post(f"/api/pulse/communications/rooms/{room_id}/join", json={})

        with self.acting_as(self.user_a):
            owner_leave = self.client.post(f"/api/pulse/communications/rooms/{room_id}/leave", json={})
        self.assertEqual(owner_leave.status_code, 400, "an owner must archive or delete rather than leave")

        with self.acting_as(self.user_b):
            member_leave = self.client.post(f"/api/pulse/communications/rooms/{room_id}/leave", json={})
        self.assertEqual(member_leave.status_code, 200, member_leave.get_data(as_text=True))
        self.assertTrue(member_leave.get_json().get("left"))

        # Having left, B loses read access again.
        self.assertEqual(self.read_messages(self.user_b, room_id).status_code, 403)

    # ------------------------------------------------------------------
    # owner / moderator controls
    # ------------------------------------------------------------------
    def test_only_the_owner_can_delete_a_room(self):
        room_id = self.create_room(self.user_a, "Review Delete")
        with self.acting_as(self.user_b):
            self.client.post(f"/api/pulse/communications/rooms/{room_id}/join", json={})

        with self.acting_as(self.user_b):
            denied = self.client.delete(f"/api/pulse/communications/rooms/{room_id}")
        self.assertEqual(denied.status_code, 403, "a plain member must not be able to delete the room")
        self.assertIn(room_id, self.room_ids(self.user_a), "the failed delete must not have removed anything")

        with self.acting_as(self.user_a):
            deleted = self.client.delete(f"/api/pulse/communications/rooms/{room_id}")
        self.assertEqual(deleted.status_code, 200, deleted.get_data(as_text=True))
        self.assertNotIn(room_id, self.room_ids(self.user_a), "a deleted room must leave the list")
        self.assertNotIn(room_id, self.room_ids(self.user_b))

    def test_owner_can_promote_a_moderator_who_can_then_manage(self):
        """The moderator tier has to be reachable, not just accepted by the checks.

        Room management accepts owner/admin/moderator, but before this route
        existed the only role ever written to ``pulse_conversation_participants``
        was ``owner``, so a room had exactly one person who could manage it and
        the moderator branch was dead code.
        """
        room_id = self.create_room(self.user_a, "Review Moderator")
        with self.acting_as(self.user_b):
            self.client.post(f"/api/pulse/communications/rooms/{room_id}/join", json={})

        # A plain member cannot manage the room.
        with self.acting_as(self.user_b):
            before = self.client.patch(
                f"/api/pulse/communications/rooms/{room_id}", json={"action": "update", "title": "Nope"}
            )
        self.assertEqual(before.status_code, 403)

        # A non-owner cannot hand out roles either.
        with self.acting_as(self.user_b):
            self_promote = self.client.post(
                f"/api/pulse/communications/rooms/{room_id}/members/role",
                json={"user_id": self.user_b["user_id"], "role": "moderator"},
            )
        self.assertEqual(self_promote.status_code, 403, "a member must not be able to promote themselves")

        # Non-members cannot be promoted.
        with self.acting_as(self.user_a):
            stranger = self.client.post(
                f"/api/pulse/communications/rooms/{room_id}/members/role",
                json={"user_id": self.user_c["user_id"], "role": "moderator"},
            )
        self.assertEqual(stranger.status_code, 404)

        with self.acting_as(self.user_a):
            promoted = self.client.post(
                f"/api/pulse/communications/rooms/{room_id}/members/role",
                json={"user_id": self.user_b["user_id"], "role": "moderator"},
            )
        self.assertEqual(promoted.status_code, 200, promoted.get_data(as_text=True))
        self.assertEqual(promoted.get_json().get("role"), "moderator")

        # The promotion is what actually grants management.
        with self.acting_as(self.user_b):
            after = self.client.patch(
                f"/api/pulse/communications/rooms/{room_id}",
                json={"action": "update", "title": "Review Moderator Renamed"},
            )
        self.assertEqual(after.status_code, 200, after.get_data(as_text=True))
        self.assertIn("Review Moderator Renamed", {room.get("title") for room in self.list_rooms(self.user_a)})

        # A moderator still is not an owner: deletion stays owner-only.
        with self.acting_as(self.user_b):
            self.assertEqual(self.client.delete(f"/api/pulse/communications/rooms/{room_id}").status_code, 403)

        # Demotion takes the power back.
        with self.acting_as(self.user_a):
            self.client.post(
                f"/api/pulse/communications/rooms/{room_id}/members/role",
                json={"user_id": self.user_b["user_id"], "role": "member"},
            )
        with self.acting_as(self.user_b):
            demoted = self.client.patch(
                f"/api/pulse/communications/rooms/{room_id}", json={"action": "update", "title": "Nope again"}
            )
        self.assertEqual(demoted.status_code, 403)

    def test_owner_can_rename_and_archive(self):
        room_id = self.create_room(self.user_a, "Review Manage")

        with self.acting_as(self.user_a):
            renamed = self.client.patch(
                f"/api/pulse/communications/rooms/{room_id}",
                json={"action": "update", "title": "Review Manage Renamed", "privacy": "private"},
            )
        self.assertEqual(renamed.status_code, 200, renamed.get_data(as_text=True))
        titles = {room.get("title") for room in self.list_rooms(self.user_a)}
        self.assertIn("Review Manage Renamed", titles)

        with self.acting_as(self.user_a):
            archived = self.client.patch(f"/api/pulse/communications/rooms/{room_id}", json={"action": "archive"})
        self.assertEqual(archived.status_code, 200, archived.get_data(as_text=True))
        self.assertNotIn(room_id, self.room_ids(self.user_a), "an archived room must drop out of the active list")

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
        with self.acting_as(self.user_b):
            opened = self.client.post(f"/api/pulse/groups/{slug}/chat/open", json={})
        self.assertEqual(opened.status_code, 200, opened.get_data(as_text=True))
        conversation_id = opened.get_json().get("conversation_id")
        self.assertTrue(conversation_id, "opening group chat must return a conversation id")

        self.assertEqual(self.send_message(self.user_b, conversation_id, "Group hello").status_code, 200)
        self.assertIn("Group hello", self.bodies(self.read_messages(self.user_a, conversation_id)))


if __name__ == "__main__":
    unittest.main()
