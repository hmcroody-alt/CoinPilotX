"""Accepting a call must become visible to the CALLER, not just to the callee.

The caller has exactly one way to learn that the far end answered: its own
`call_status` poll. While the call is ringing the caller has not joined the
media room, so no RTC event can reach it — there is no fast path, and no second
chance. Whatever `call_status` says is the entire truth available to the caller.

That makes acceptance durability a hard requirement rather than a nicety.
`accept_call` transitions `ringing -> accepted -> connecting` and commits at the
end, but the media-token mint sits *between* those two transitions:

    transition(accepted)
    token = _generate_rtc_token(...)
    if not token.get("ok"):
        return token            # <-- returned without conn.commit()
    transition(connecting)
    conn.commit()

The early return left the function through `finally: conn.close()` with an open
transaction, so sqlite/psycopg rolled it back and the row reverted to
`ringing`. The callee saw an error it could retry, but the caller saw nothing at
all: it kept polling `ringing` and kept playing ringback until the 45s stale
sweeper marked the call missed. That is the "callee accepted, caller keeps
ringing" defect, and it reproduces whenever the RTC provider is briefly
unavailable — precisely when the credentials are misconfigured or rate-limited.

Acceptance is a signaling fact. It must outlive a media failure.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import itertools
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Every schema bootstrap in the stack (bot.init_db, pulse_communications_v2's
# _SCHEMA_READY) is memoized per process, so only the FIRST temp database a test
# module opens ever gets its tables built. Two call-test modules each minting
# their own database therefore cannot coexist in one pytest run — whichever is
# collected second finds an empty file. They share one instead.
_DB_PATH = os.environ.get("PULSESOC_CALL_TEST_DB", "")
if not _DB_PATH:
    _HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="call_tests_")
    os.close(_HANDLE)
    os.environ["PULSESOC_CALL_TEST_DB"] = _DB_PATH
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

from services import pulsesoc_communications_engine as engine  # noqa: E402

CALLER_ID = 9301
CALLEE_ID = 9302

_CONVERSATION_IDS = itertools.count(8001)

_GOOD_TOKEN = {
    "ok": True,
    "token": "stub-rtc-token",
    "app_id": "stub-app",
    "channel_name": "stub-channel",
    "uid": 4242,
    "provider": "agora",
}


def _use_module_database():
    """Re-point the process at this module's temp database.

    ``services.db`` resolves ``DATABASE_URL`` lazily per connection and pytest
    imports every selected module during collection before running any test, so
    a module collected after this one otherwise leaves the environment pointing
    at *its* database by the time these tests execute.

    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


def _seed_ringing_call(conn, cur):
    """A 1:1 call that is ringing, with the callee not yet joined.

    Built directly rather than via ``start_call`` so the test does not depend on
    conversation creation or provider token minting during *setup* — token
    behaviour is the thing under test and must only be controlled per-test.
    """
    public_id = f"call_{uuid.uuid4().hex[:12]}"
    conversation_id = next(_CONVERSATION_IDS)
    now = engine._now()
    for user_id in (CALLER_ID, CALLEE_ID):
        cur.execute(
            """
            INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, created_at, updated_at)
            VALUES (?, ?, 'active', '', ?, ?)
            """,
            (conversation_id, int(user_id), now, now),
        )
    cur.execute(
        """
        INSERT INTO communication_calls
        (public_id, conversation_id, room_name, provider, call_type, call_scope, status, created_by_user_id, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, 'agora', 'audio', 'direct', 'ringing', ?, '{}', ?, ?)
        """,
        (public_id, conversation_id, f"room_{public_id}", CALLER_ID, now, now),
    )
    call_id = engine._inserted_call_id(cur, public_id)
    for user_id, role, status in ((CALLER_ID, "caller", "joined"), (CALLEE_ID, "callee", "ringing")):
        cur.execute(
            """
            INSERT INTO communication_call_participants
            (call_id, user_id, role, status, muted_audio, muted_video, joined_at, last_seen_at, device_info_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, 1, ?, ?, '{}', ?, ?)
            """,
            (call_id, int(user_id), role, status, now, now, now, now),
        )
    conn.commit()
    return public_id


def _status_of(public_id):
    conn, cur = engine._open_db()
    try:
        return str(engine._get_call(cur, public_id).get("status") or "")
    finally:
        conn.close()


def _caller_polled_status(public_id):
    """What the caller's own status poll reports — the only signal it has."""
    polled = engine.call_status(CALLER_ID, public_id)
    assert polled.get("ok"), polled
    return str(polled["call"].get("status") or "")


class CallAcceptanceSyncTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        self.conn, self.cur = engine._open_db()
        self._real_token = engine._generate_rtc_token
        engine._generate_rtc_token = lambda *a, **k: dict(_GOOD_TOKEN)

    def tearDown(self):
        engine._generate_rtc_token = self._real_token
        self.conn.close()

    # --- the happy path the product depends on -------------------------------

    def test_accept_moves_the_call_off_ringing(self):
        public_id = _seed_ringing_call(self.conn, self.cur)
        self.assertEqual(_status_of(public_id), "ringing")
        result = engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn(_status_of(public_id), ("ringing", "created"))

    def test_caller_poll_observes_the_acceptance(self):
        """The end-to-end client contract: the poll is what stops the ringback."""
        public_id = _seed_ringing_call(self.conn, self.cur)
        engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        polled = _caller_polled_status(public_id)
        # Mirrors MEDIA_CONNECTABLE_CALL_STATES in the native callToneLifecycle:
        # these are the statuses that stop ringback and start the media join.
        self.assertIn(
            polled,
            {"accepted", "connecting", "connected", "active", "reconnecting"},
            "the caller must see a connectable status, not 'ringing'",
        )

    def test_accepted_call_is_not_terminal(self):
        public_id = _seed_ringing_call(self.conn, self.cur)
        engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        self.assertNotIn(_status_of(public_id), engine.FINAL_STATUSES)

    # --- the regression: media failure must not erase the acceptance ---------

    def test_acceptance_survives_a_token_failure(self):
        """The exact defect. A failed token mint used to roll 'accepted' back."""
        public_id = _seed_ringing_call(self.conn, self.cur)
        engine._generate_rtc_token = lambda *a, **k: {"ok": False, "error": "AGORA_CONFIG_MISSING"}
        result = engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        self.assertFalse(result.get("ok"), "the token failure must still be reported to the callee")
        self.assertNotEqual(
            _status_of(public_id),
            "ringing",
            "acceptance is a signaling fact and must be committed even when the media token fails",
        )

    def test_caller_is_not_left_ringing_after_a_token_failure(self):
        """The user-visible half of the same bug: the caller must stop ringing."""
        public_id = _seed_ringing_call(self.conn, self.cur)
        engine._generate_rtc_token = lambda *a, **k: {"ok": False, "error": "AGORA_CONFIG_MISSING"}
        engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        self.assertNotEqual(
            _caller_polled_status(public_id),
            "ringing",
            "the caller polls this endpoint every 1.1s and has no other acceptance signal",
        )

    def test_callee_can_retry_accept_after_a_token_failure(self):
        """A transient provider outage must not permanently wedge the call."""
        public_id = _seed_ringing_call(self.conn, self.cur)
        engine._generate_rtc_token = lambda *a, **k: {"ok": False, "error": "AGORA_CONFIG_MISSING"}
        engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        engine._generate_rtc_token = lambda *a, **k: dict(_GOOD_TOKEN)
        retry = engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        self.assertTrue(retry.get("ok"), retry)
        self.assertNotIn(_status_of(public_id), engine.FINAL_STATUSES)

    # --- idempotence and adjacent-flow regressions --------------------------

    def test_accepting_twice_is_safe(self):
        """Overlapping CallKit and in-app accepts both land on the same call."""
        public_id = _seed_ringing_call(self.conn, self.cur)
        first = engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        second = engine.accept_call(CALLEE_ID, public_id, {"source": "callkit"})
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertNotIn(_status_of(public_id), engine.FINAL_STATUSES)

    def test_accept_after_the_call_ended_is_rejected(self):
        """Accept-then-immediate-hangup: the late accept must not revive the call."""
        public_id = _seed_ringing_call(self.conn, self.cur)
        engine.end_call(CALLER_ID, public_id, {"reason": "native_hangup"})
        result = engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        self.assertFalse(result.get("ok"))
        self.assertIn(_status_of(public_id), engine.FINAL_STATUSES)

    def test_decline_still_ends_the_call(self):
        """Regression guard: the accept fix must not affect the decline path."""
        public_id = _seed_ringing_call(self.conn, self.cur)
        result = engine.decline_call(CALLEE_ID, public_id, {"reason": "native_decline"})
        self.assertTrue(result.get("ok"), result)
        self.assertIn(_status_of(public_id), engine.FINAL_STATUSES)

    def test_hangup_after_acceptance_ends_the_call_for_both(self):
        public_id = _seed_ringing_call(self.conn, self.cur)
        engine.accept_call(CALLEE_ID, public_id, {"source": "native"})
        engine.end_call(CALLEE_ID, public_id, {"reason": "native_hangup"})
        self.assertIn(_caller_polled_status(public_id), engine.FINAL_STATUSES)


if __name__ == "__main__":
    unittest.main()
