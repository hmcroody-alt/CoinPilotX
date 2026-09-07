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
        self._reset_conversation()

    @classmethod
    def _reset_conversation(cls):
        """Return the conversation to exactly what ``_seed`` built.

        ``test_a_refused_write_leaves_no_trace_in_the_conversation`` counts rows
        absolutely — it asserts the outsider owns *zero* messages, reactions and
        receipts — so it silently depends on no earlier test having written any.
        That held only while the outsider was never allowed to succeed at
        anything. The Stage 21 ``/seen`` tests break that assumption on purpose:
        proving the gate is off by default means proving a departed member still
        gets a 200, and a 200 from ``/seen`` writes a receipt.

        ``_drop_membership`` removes the roster row but not the writes made
        through it, so the leftover receipt made a passing test fail depending
        on alphabetical method order. Restoring the fixture per-test is the fix
        rather than teaching one helper to clean up after one route: any future
        test that legitimately writes would reintroduce the same coupling.
        """
        conn = bot.db()
        conn.execute("DELETE FROM pulse_conversation_participants "
                     "WHERE conversation_id=? AND user_id<>?", (CONVERSATION_ID, MEMBER_ID))
        conn.execute("DELETE FROM pulse_message_receipts WHERE conversation_id=?",
                     (CONVERSATION_ID,))
        conn.execute("DELETE FROM pulse_message_reactions WHERE conversation_id=?",
                     (CONVERSATION_ID,))
        conn.execute("DELETE FROM pulse_messages WHERE conversation_id=? AND id<>?",
                     (CONVERSATION_ID, cls.MESSAGE_ID))
        conn.commit()
        conn.close()

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

    def test_the_seen_route_still_accepts_a_departed_member_by_default(self):
        """The Stage 21 fix landed, and it is off by default. This is the
        replacement the previous version of this test asked for.

        Four of the five chat routes gate on ``AND COALESCE(left_at,'')=''``;
        ``/seen`` did not, so a member who left a group kept writing read
        receipts into it and the remaining members kept seeing "read by" from
        someone who walked out. It now checks, but behind
        ``SENTINEL_RECEIPT_PARTICIPATION_ENFORCED``, which defaults off.

        Asserting the default explicitly is the point. This is a behaviour change
        against a frozen App Store client, and the whole argument for shipping it
        dark is that deploying the code must not be the decision. A test that only
        covered the enforced path would let the default flip to on without
        anything failing.
        """
        self._set_membership(left_at="2026-01-03")
        try:
            response = self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
            self.assertEqual(
                response.status_code, 200,
                "with the gate off, /seen must behave exactly as it does in "
                "production today")
            self.assertNotIn(SECRET, response.get_data(as_text=True),
                             "/seen must not return message content to anyone")
        finally:
            self._drop_membership()

    def test_the_seen_route_refuses_a_departed_member_once_enforced(self):
        """Partner to the test above: proves the gate does something. Without
        this, 'off behaves as before' is satisfied by a fix that never works."""
        self._set_membership(left_at="2026-01-03")
        os.environ["SENTINEL_RECEIPT_PARTICIPATION_ENFORCED"] = "1"
        try:
            response = self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
            self.assertRefused(response, "seen after leaving, gate enforced")
            self.assertEqual(
                response.status_code, 404,
                "a conversation the caller may not touch must be "
                "indistinguishable from one that does not exist")
        finally:
            os.environ.pop("SENTINEL_RECEIPT_PARTICIPATION_ENFORCED", None)
            self._drop_membership()

    def test_enforcing_does_not_refuse_a_member_who_is_still_present(self):
        """The failure mode that would matter in production. A gate that refuses
        everyone is also a gate that 'correctly refuses departed members', and
        turning it on would break every read receipt in the product."""
        self._set_membership(left_at=None)
        os.environ["SENTINEL_RECEIPT_PARTICIPATION_ENFORCED"] = "1"
        try:
            response = self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
            self.assertEqual(response.status_code, 200,
                             response.get_data(as_text=True))
        finally:
            os.environ.pop("SENTINEL_RECEIPT_PARTICIPATION_ENFORCED", None)
            self._drop_membership()

    def test_the_emergency_switch_returns_seen_to_its_shipped_behaviour(self):
        """Enforcement gates are revocable, and this one is reached by the
        registry built in Stage 21 rather than by its own special case."""
        self._set_membership(left_at="2026-01-03")
        os.environ["SENTINEL_RECEIPT_PARTICIPATION_ENFORCED"] = "1"
        os.environ["SENTINEL_EMERGENCY_KILL_SWITCH"] = "1"
        try:
            response = self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
            self.assertEqual(response.status_code, 200,
                             "the emergency switch must revert enforcement")
        finally:
            os.environ.pop("SENTINEL_EMERGENCY_KILL_SWITCH", None)
            os.environ.pop("SENTINEL_RECEIPT_PARTICIPATION_ENFORCED", None)
            self._drop_membership()

    def test_shadow_mode_records_the_refusal_it_did_not_make(self):
        """The justification for shipping this dark is that the blast radius can
        be measured from real traffic first. That is only true if something is
        recorded, so a shadow gate that stays silent is indistinguishable from a
        gate nobody wired up — and 'off' would be the honest name for it.

        The route's call is what is checked here rather than the event reaching
        the database: the emit path has its own suite, and requiring the bridge
        and schema to be live would make this test fail for reasons that have
        nothing to do with /seen.
        """
        calls = []
        original = bot.sentinel_note_shadow_refusal
        bot.sentinel_note_shadow_refusal = lambda *a, **kw: calls.append((a, kw))
        self._set_membership(left_at="2026-01-03")
        try:
            response = self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(calls), 1, "shadow mode recorded nothing")
            self.assertEqual(calls[0][0][0], "receipt_from_departed_member")
            self.assertEqual(calls[0][0][2], CONVERSATION_ID)
        finally:
            bot.sentinel_note_shadow_refusal = original
            self._drop_membership()

    def test_a_present_member_produces_no_shadow_noise(self):
        """Partner. A recorder that fires for everyone would show a huge blast
        radius and argue against ever enabling the gate — the opposite of the
        decision the signal exists to support."""
        calls = []
        original = bot.sentinel_note_shadow_refusal
        bot.sentinel_note_shadow_refusal = lambda *a, **kw: calls.append((a, kw))
        self._set_membership(left_at=None)
        try:
            self.as_user(OUTSIDER_ID).post(
                f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
            self.assertEqual(calls, [], "a present member is not a would-be refusal")
        finally:
            bot.sentinel_note_shadow_refusal = original
            self._drop_membership()

    def test_a_stranger_is_still_refused_whatever_the_gate_says(self):
        """Someone who was never a participant is refused today and must stay
        refused with the gate off. The fix separates 'never joined' from
        'joined and left', and collapsing them the wrong way would open the
        route to strangers while looking like a tightening."""
        self._drop_membership()
        for enforced in ("0", "1"):
            os.environ["SENTINEL_RECEIPT_PARTICIPATION_ENFORCED"] = enforced
            try:
                response = self.as_user(OUTSIDER_ID).post(
                    f"/api/pulse/messages/{CONVERSATION_ID}/seen", json={})
                self.assertRefused(response, f"stranger, enforced={enforced}")
            finally:
                os.environ.pop("SENTINEL_RECEIPT_PARTICIPATION_ENFORCED", None)

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
