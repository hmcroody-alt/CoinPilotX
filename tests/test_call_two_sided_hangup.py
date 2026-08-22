"""Hanging up must end the call for BOTH participants, in either direction.

The native client treats the backend call status as authoritative: Agora's
`onUserOffline` is only a hint that triggers an immediate status re-fetch, and
the session is torn down only when that fetch comes back terminal. That design
is correct, but it means the backend is the single point where two-sided
termination is decided — and it used to decide it asymmetrically.

`end_call` ended the call when *everyone* had left (`active == 0`) or when the
creator hung up. In a 1:1 call those are not the same thing:

  * caller (the creator) hangs up  -> creator branch fires -> call ends -> the
    callee's next poll sees a terminal status and tears down. Works.
  * callee hangs up               -> one active participant remains (the
    caller), and the leaver is not the creator -> no transition. The call stays
    "connected" forever, so the caller sits in a room with nobody in it,
    polling an active status, until they press End Call themselves.

Only one of the two directions was automatic, which is exactly the "the remote
user still has to press End Call" defect. The rule is now "fewer than two
active participants means this is no longer a call", which fixes the callee
direction without ending a group call that still has people in it.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sys
import tempfile
import itertools
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="call_hangup_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

from services import pulsesoc_communications_engine as engine  # noqa: E402

CALLER_ID = 9101
CALLEE_ID = 9102
THIRD_ID = 9103

_CONVERSATION_IDS = itertools.count(7001)


def _use_module_database():
    """Re-point the process at this module's temp database.

    ``services.db`` resolves ``DATABASE_URL`` lazily on every connection and
    pytest imports every selected module during collection before running any
    test, so a module collected after this one leaves the environment pointing
    at *its* database by the time these tests execute.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


def _seed_call(conn, cur, participants, created_by=CALLER_ID, status="connected"):
    """Insert a connected call with the given participants all joined.

    Built directly rather than via ``start_call`` so the test does not depend on
    conversation creation, provider token minting or network access — the thing
    under test is the end-of-call decision, not call setup.
    """
    public_id = f"call_{uuid.uuid4().hex[:12]}"
    # Unique per call: comm_v2_participants is UNIQUE(conversation_id, user_id),
    # so reusing a conversation across seeded calls makes every test after the
    # first blow up on an integrity error rather than on the behaviour.
    conversation_id = next(_CONVERSATION_IDS)
    now = engine._now()
    for user_id in participants:
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
        (public_id, conversation_id, room_name, provider, call_type, call_scope, status, created_by_user_id, metadata_json, created_at, updated_at, answered_at)
        VALUES (?, ?, ?, 'agora', 'audio', ?, ?, ?, '{}', ?, ?, ?)
        """,
        (
            public_id,
            conversation_id,
            f"room_{public_id}",
            "direct" if len(participants) == 2 else "group",
            status,
            int(created_by),
            now,
            now,
            now,
        ),
    )
    call_id = engine._inserted_call_id(cur, public_id)
    for user_id in participants:
        cur.execute(
            """
            INSERT INTO communication_call_participants
            (call_id, user_id, role, status, muted_audio, muted_video, joined_at, last_seen_at, device_info_json, created_at, updated_at)
            VALUES (?, ?, ?, 'joined', 0, 1, ?, ?, '{}', ?, ?)
            """,
            (call_id, int(user_id), "caller" if user_id == created_by else "callee", now, now, now, now),
        )
    conn.commit()
    return public_id


def _status_of(public_id):
    conn, cur = engine._open_db()
    try:
        return str(engine._get_call(cur, public_id).get("status") or "")
    finally:
        conn.close()


class TwoSidedHangupTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        self.conn, self.cur = engine._open_db()

    def tearDown(self):
        self.conn.close()

    def test_caller_hangup_ends_the_call_for_both(self):
        public_id = _seed_call(self.conn, self.cur, [CALLER_ID, CALLEE_ID])
        result = engine.end_call(CALLER_ID, public_id, {"reason": "native_hangup"})
        self.assertTrue(result.get("ok"), result)
        self.assertIn(_status_of(public_id), engine.FINAL_STATUSES)

    def test_callee_hangup_also_ends_the_call_for_both(self):
        """The direction that used to leave the caller stranded."""
        public_id = _seed_call(self.conn, self.cur, [CALLER_ID, CALLEE_ID])
        result = engine.end_call(CALLEE_ID, public_id, {"reason": "native_hangup"})
        self.assertTrue(result.get("ok"), result)
        self.assertIn(
            _status_of(public_id),
            engine.FINAL_STATUSES,
            "a 1:1 call must end when the callee hangs up, not wait for the caller to press End too",
        )

    def test_the_stranded_side_sees_a_terminal_status_on_its_next_poll(self):
        """End-to-end for the client contract: the poll is what ends the call.

        The native session store only tears down on a terminal status from
        `call_status`, so proving the row changed is not enough — the caller's
        own status fetch has to report it.
        """
        public_id = _seed_call(self.conn, self.cur, [CALLER_ID, CALLEE_ID])
        engine.end_call(CALLEE_ID, public_id, {"reason": "native_hangup"})
        polled = engine.call_status(CALLER_ID, public_id)
        self.assertTrue(polled.get("ok"), polled)
        self.assertIn(str(polled["call"].get("status") or ""), engine.FINAL_STATUSES)

    def test_group_call_survives_one_participant_leaving(self):
        """The fix must not turn every group call into a 1:1 call."""
        public_id = _seed_call(self.conn, self.cur, [CALLER_ID, CALLEE_ID, THIRD_ID])
        engine.end_call(THIRD_ID, public_id, {"reason": "native_hangup"})
        self.assertNotIn(
            _status_of(public_id),
            engine.FINAL_STATUSES,
            "two people are still on this call",
        )

    def test_group_call_ends_once_only_one_participant_is_left(self):
        public_id = _seed_call(self.conn, self.cur, [CALLER_ID, CALLEE_ID, THIRD_ID])
        engine.end_call(THIRD_ID, public_id, {"reason": "native_hangup"})
        engine.end_call(CALLEE_ID, public_id, {"reason": "native_hangup"})
        self.assertIn(_status_of(public_id), engine.FINAL_STATUSES)

    def test_ending_an_already_ended_call_is_safe(self):
        """Both sides can race to end the same call; neither may error."""
        public_id = _seed_call(self.conn, self.cur, [CALLER_ID, CALLEE_ID])
        first = engine.end_call(CALLEE_ID, public_id, {"reason": "native_hangup"})
        second = engine.end_call(CALLER_ID, public_id, {"reason": "native_hangup"})
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertIn(_status_of(public_id), engine.FINAL_STATUSES)


if __name__ == "__main__":
    unittest.main()
