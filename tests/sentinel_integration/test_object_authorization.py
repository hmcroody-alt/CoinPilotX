"""Stage 5 — object-level authorization, against the real Flask app.

``tests/sentinel/test_tenant_isolation.py`` proves Sentinel can *detect* a
tenant-isolation bypass in storage. It cannot prove the thing that decides
whether any of this matters: **does the request path actually refuse a stranger
who names someone else's conversation id?**

The Stage 0 map's finding for the rate limiter applies here too, in a different
shape. The audit of the ``/api/pulse/**`` and ``/api/mobile/**`` families found
a genuinely consistent idiom — resolve the object from the client's id, resolve
the conversation it belongs to, require a ``pulse_conversation_participants``
row — with no traced gaps. That is a good result, and it changes what this
mission owes the codebase: not a new permission engine (Hard Rule #6 names one
as a duplicate risk, and ``sentinel/authority.py`` already exists for Sentinel's
own agents), but a **regression harness** so that the check which is there
today cannot quietly leave.

Nothing in this file adds a control. Every assertion describes behaviour that
``bot.py`` already has at commit d8aaf911. A failure here means a refactor
removed an access check, which is the only outcome worth an alarm.

The anti-vacuity rule this file follows
---------------------------------------

Every "the stranger is refused" test is paired with "the member is served".
Without the partner, a fixture that built the wrong data — or a route that 404s
for everybody — would satisfy every denial assertion and report a green suite
for an app with no authorization at all. That false green already happened once
in this mission, in the Stage 6 route tests, and it took a subject discovered
from real traffic to catch it.

Kept out of ``tests/sentinel/`` because importing ``bot`` costs a full boot and
sets a process-wide ``DATABASE_URL``.

Run: python3 -m pytest tests/sentinel_integration/test_object_authorization.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="sentinel_objauth_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
import services.pulse_security_core as pulse_security_core  # noqa: E402
import services.security_guard as security_guard  # noqa: E402
from services import cache_engine  # noqa: E402

MEMBER_ID = 8001
OUTSIDER_ID = 8002
CONVERSATION_ID = 9001
SECRET = "hummingbird-ledger-4417"  # must never appear in an outsider's response

# A refusal has to be one of these. 401/403 come from the Stage 1 client
# contract; 404 is added here because the routes below deliberately answer
# "not found" rather than "forbidden" for a conversation the caller is not in,
# and that choice is itself a property worth pinning (see
# test_a_refusal_does_not_confirm_the_object_exists).
REFUSALS = {401, 403, 404}


class ObjectAuthorizationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        bot.init_db()
        cls.client = bot.webhook_app.test_client()
        cls._seed()

    @classmethod
    def _seed(cls):
        """One conversation, one member, one message, one outsider.

        Written straight to storage rather than through the API on purpose: if
        the seed went through ``/send``, a bug that let anyone post anywhere
        would build the fixture *and* satisfy the tests. The fixture has to be
        independent of the thing under test.
        """
        conn = bot.db()
        cur = conn.cursor()
        for user_id, name in ((MEMBER_ID, "member"), (OUTSIDER_ID, "outsider")):
            cur.execute(
                "INSERT OR REPLACE INTO users (user_id, full_name, email, "
                "account_status, login_enabled, access_enabled) "
                "VALUES (?, ?, ?, 'active', 1, 1)",
                (user_id, name, f"{name}-objauth@example.com"))
        cur.execute(
            "INSERT OR REPLACE INTO pulse_conversations "
            "(id, conversation_type, created_by_user_id, status, is_public) "
            "VALUES (?, 'group', ?, 'active', 0)",
            (CONVERSATION_ID, MEMBER_ID))
        cur.execute("DELETE FROM pulse_conversation_participants WHERE conversation_id=?",
                    (CONVERSATION_ID,))
        cur.execute(
            "INSERT INTO pulse_conversation_participants "
            "(conversation_id, user_id, role, created_at) VALUES (?, ?, 'owner', '2026-01-01')",
            (CONVERSATION_ID, MEMBER_ID))
        cur.execute("DELETE FROM pulse_messages WHERE conversation_id=?", (CONVERSATION_ID,))
        cur.execute(
            "INSERT INTO pulse_messages (conversation_id, sender_user_id, body, "
            "message_type, status, created_at) VALUES (?, ?, ?, 'text', 'sent', '2026-01-01')",
            (CONVERSATION_ID, MEMBER_ID, SECRET))
        cur.execute("SELECT id FROM pulse_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
                    (CONVERSATION_ID,))
        cls.MESSAGE_ID = int(cur.fetchone()[0])
        conn.commit()
        conn.close()

    def setUp(self):
        # The rate limiters that sit in front of every route (Stage 6 found four
        # of them) keep process-wide state. A 429 from a previous test would
        # otherwise be indistinguishable from an authorization refusal, and this
        # file would pass while proving nothing about authorization.
        bot.RATE_LIMIT_BUCKETS.clear()
        pulse_security_core._RATE_BUCKETS.clear()
        security_guard.BUCKETS.clear()
        cache_engine._MEMORY.clear()

    # --- speaking as a given user -----------------------------------------

    def as_user(self, user_id):
        client = bot.webhook_app.test_client()
        with client.session_transaction() as sess:
            sess["account_user_id"] = user_id
        return client

    def assertRefused(self, response, where):
        self.assertIn(response.status_code, REFUSALS,
                      f"{where}: an outsider got {response.status_code}")
        self.assertNotIn(SECRET, response.get_data(as_text=True),
                         f"{where}: the refusal body leaked the message")

    # --- the member is served (the anti-vacuity half) ----------------------

    def test_a_member_can_read_the_conversation(self):
        response = self.as_user(MEMBER_ID).get(f"/api/pulse/messages/{CONVERSATION_ID}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn(SECRET, response.get_data(as_text=True),
                      "the fixture is wrong: the member cannot see their own message, "
                      "so the refusal tests below would pass for the wrong reason")

    def test_a_member_can_read_the_message_list(self):
        response = self.as_user(MEMBER_ID).get(
            f"/api/pulse/messages/{CONVERSATION_ID}/messages")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn(SECRET, response.get_data(as_text=True))

    def test_a_member_can_react_to_a_message_in_it(self):
        response = self.as_user(MEMBER_ID).post(
            f"/api/pulse/messages/{self.MESSAGE_ID}/react", json={"reaction_type": "heart"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_a_member_can_mark_it_seen(self):
        response = self.as_user(MEMBER_ID).post(
            f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    # --- the outsider is refused ------------------------------------------

    def test_an_outsider_cannot_read_the_conversation(self):
        self.assertRefused(
            self.as_user(OUTSIDER_ID).get(f"/api/pulse/messages/{CONVERSATION_ID}"),
            "GET /api/pulse/messages/<id>")

    def test_an_outsider_cannot_read_the_message_list(self):
        self.assertRefused(
            self.as_user(OUTSIDER_ID).get(f"/api/pulse/messages/{CONVERSATION_ID}/messages"),
            "GET /api/pulse/messages/<id>/messages")

    def test_an_outsider_cannot_react_to_a_message_in_it(self):
        self.assertRefused(
            self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{self.MESSAGE_ID}/react", json={"reaction_type": "heart"}),
            "POST /api/pulse/messages/<id>/react")

    def test_an_outsider_cannot_mark_it_seen(self):
        self.assertRefused(
            self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={}),
            "POST /api/pulse/messages/<id>/seen")

    def test_an_outsider_cannot_post_into_it(self):
        self.assertRefused(
            self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/send", json={"body": "let me in"}),
            "POST /api/pulse/messages/<id>/send")

    # --- a refusal must not become a write --------------------------------

    def test_a_refused_write_leaves_no_trace_in_the_conversation(self):
        """The strongest form of the property: not "the outsider saw an error"
        but "the outsider changed nothing". A route that refused with 403 and
        wrote the row anyway would pass every test above.

        This is also the assertion that ties the request path to
        ``INV_MESSAGE_SENDER_PARTICIPANT`` — the invariant reports exactly the
        rows this test proves are never created.
        """
        client = self.as_user(OUTSIDER_ID)
        client.post(f"/api/pulse/messages/{CONVERSATION_ID}/send", json={"body": "intrusion"})
        client.post(f"/api/pulse/messages/{self.MESSAGE_ID}/react", json={"reaction_type": "heart"})
        client.post(f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})

        conn = bot.db()
        cur = conn.cursor()
        counts = {}
        for table, column in (("pulse_messages", "sender_user_id"),
                              ("pulse_message_reactions", "user_id"),
                              ("pulse_message_receipts", "user_id")):
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE conversation_id=? AND {column}=?",
                        (CONVERSATION_ID, OUTSIDER_ID))
            counts[table] = int(cur.fetchone()[0])
        conn.close()
        self.assertEqual(counts, {"pulse_messages": 0, "pulse_message_reactions": 0,
                                  "pulse_message_receipts": 0},
                         "a refused request still wrote to the conversation")

    # --- the shape of the refusal -----------------------------------------

    def test_a_refusal_does_not_confirm_the_object_exists(self):
        """A real message id the caller may not see, and an id that does not
        exist, must be answered the same way.

        Two different statuses here would turn ``/react`` into an oracle for
        enumerating message ids — the attacker learns which of their guesses
        are real without ever reading one. The routes answer 404 for both,
        which is why 404 is in ``REFUSALS`` alongside 403.
        """
        client = self.as_user(OUTSIDER_ID)
        real = client.post(f"/api/pulse/messages/{self.MESSAGE_ID}/react",
                           json={"reaction_type": "heart"})
        imaginary = client.post("/api/pulse/messages/99999999/react",
                                json={"reaction_type": "heart"})
        self.assertEqual(real.status_code, imaginary.status_code,
                         "the refusal for a real object differs from the refusal for a "
                         "nonexistent one, which enumerates ids")

    def test_an_anonymous_caller_is_refused_before_anything_else(self):
        anonymous = bot.webhook_app.test_client()
        for path, method in ((f"/api/pulse/messages/{CONVERSATION_ID}", "get"),
                             (f"/api/pulse/messages/{CONVERSATION_ID}/messages", "get"),
                             (f"/api/pulse/messages/{self.MESSAGE_ID}/react", "post"),
                             (f"/api/pulse/messages/{CONVERSATION_ID}/seen", "post")):
            response = getattr(anonymous, method)(path, json={})
            self.assertEqual(response.status_code, 401, f"{method.upper()} {path}")
            self.assertNotIn(SECRET, response.get_data(as_text=True))

    def test_every_refusal_is_a_status_the_shipped_client_understands(self):
        """Hard Rule #3. The App Store binary is frozen; a refusal it cannot
        parse is a crash or a spinner, not a security improvement."""
        client = self.as_user(OUTSIDER_ID)
        for response in (client.get(f"/api/pulse/messages/{CONVERSATION_ID}"),
                         client.get(f"/api/pulse/messages/{CONVERSATION_ID}/messages"),
                         client.post(f"/api/pulse/messages/{self.MESSAGE_ID}/react",
                                     json={"reaction_type": "heart"}),
                         client.post(f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})):
            self.assertIn(response.status_code, REFUSALS | {429})
            self.assertTrue(response.is_json, "the client parses JSON from /api/")
            self.assertFalse(response.get_json().get("ok", False))

    # --- membership is what decides, not identity -------------------------

    def _set_membership(self, left_at):
        conn = bot.db()
        conn.execute("DELETE FROM pulse_conversation_participants "
                     "WHERE conversation_id=? AND user_id=?", (CONVERSATION_ID, OUTSIDER_ID))
        conn.execute(
            "INSERT INTO pulse_conversation_participants "
            "(conversation_id, user_id, role, created_at, left_at) "
            "VALUES (?, ?, 'member', '2026-01-02', ?)",
            (CONVERSATION_ID, OUTSIDER_ID, left_at))
        conn.commit()
        conn.close()

    def _drop_membership(self):
        conn = bot.db()
        conn.execute("DELETE FROM pulse_conversation_participants "
                     "WHERE conversation_id=? AND user_id=?", (CONVERSATION_ID, OUTSIDER_ID))
        conn.commit()
        conn.close()

    def test_joining_is_what_grants_access(self):
        """Proves the gate reads the roster rather than, say, the creator id or
        an is-admin flag that happens to be false for the outsider. Without
        this, every refusal above is also consistent with "only the creator can
        ever see a conversation", which is a different and much weaker rule.
        """
        self._set_membership(left_at=None)
        try:
            joined = self.as_user(OUTSIDER_ID).get(f"/api/pulse/messages/{CONVERSATION_ID}")
            self.assertEqual(joined.status_code, 200, joined.get_data(as_text=True))
            self.assertIn(SECRET, joined.get_data(as_text=True))
        finally:
            self._drop_membership()

    def test_the_seen_route_does_not_check_left_at_like_the_others_do(self):
        """A finding, pinned rather than fixed here. Deferred to Stage 21.

        Four of the five chat routes gate on
        ``AND COALESCE(left_at,'')=''``. ``/seen`` gates only on
        ``conversation_id`` and ``user_id``, so a member who has left a group
        can still write read receipts into it — other members keep seeing
        "read by" from someone who walked out months ago.

        Scope, stated honestly rather than dramatised: ``/seen`` returns
        counters, not message bodies, so this is not a disclosure of content.
        The route the departed member would need to actually read the thread
        (``GET /api/pulse/messages/<id>``) refuses them, and
        ``test_leaving_removes_access_on_every_route_that_grants_it`` proves it.

        Not fixed in this commit for the same reason the two disagreeing rate
        limits found in Stage 6 were left alone: this stage builds a regression
        harness, and quietly changing what a frozen App Store client
        experiences is not a testing change. Adding the clause is a one-line
        edit; it belongs behind the Stage 21 flags with the rest of the
        enforcement work, where it can be shadowed before it is enforced.

        When that fix lands, this test should fail. Replace it — do not delete
        it — with the assertion that ``/seen`` now refuses.
        """
        self._set_membership(left_at="2026-01-03")
        try:
            response = self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
            self.assertEqual(
                response.status_code, 200,
                "/seen now checks left_at — good. Update this test to assert the "
                "refusal and close the Stage 21 item.")
            self.assertNotIn(SECRET, response.get_data(as_text=True),
                             "/seen must not return message content to anyone")
        finally:
            self._drop_membership()

    def test_leaving_removes_access_on_every_route_that_grants_it(self):
        """Checked on all four routes rather than one.

        The first version of this test only covered the conversation-detail
        route, and a mutant that dropped ``COALESCE(left_at,'')=''`` from the
        *react* route survived it — a departed member could keep reacting to a
        group chat they had walked out of, forever, and the suite stayed green.
        One route's worth of evidence for a property that four routes are
        supposed to hold is one route's worth of evidence.
        """
        self._set_membership(left_at="2026-01-03")
        try:
            client = self.as_user(OUTSIDER_ID)
            self.assertRefused(client.get(f"/api/pulse/messages/{CONVERSATION_ID}"),
                               "detail after leaving")
            self.assertRefused(client.get(f"/api/pulse/messages/{CONVERSATION_ID}/messages"),
                               "message list after leaving")
            self.assertRefused(
                client.post(f"/api/pulse/messages/{self.MESSAGE_ID}/react",
                            json={"reaction_type": "heart"}),
                "react after leaving")
            self.assertRefused(
                client.post(f"/api/pulse/messages/{CONVERSATION_ID}/send",
                            json={"body": "still here"}),
                "send after leaving")
        finally:
            self._drop_membership()


if __name__ == "__main__":
    unittest.main()
