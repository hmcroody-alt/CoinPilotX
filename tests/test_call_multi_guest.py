"""Multi-guest call backend: invites, limits, flags, creator-leave policy.

The room model is participant-based: ONE `communication_calls` row, ONE Agora
channel (`room_name` never changes), and N `communication_call_participants`
rows. These tests prove the additive multi-guest behavior without touching the
1:1 contract that `test_call_two_sided_hangup.py` locks down:

  * mid-call invite rings new people into the SAME channel, is backend-owned,
    idempotent, membership-checked, flag-gated and limit-capped;
  * a group call outlives its creator leaving (Stage 18) but the creator can
    still explicitly end it for everyone;
  * missed is per participant once a call is live — the group keeps talking;
  * capabilities report server-owned limits/flags so clients hardcode nothing.

Runs against the shared temp sqlite call-test database (see the memoized-schema
note in test_call_two_sided_hangup.py).
"""

import itertools
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB_PATH = os.environ.get("PULSESOC_CALL_TEST_DB", "")
if not _DB_PATH:
    _HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="call_tests_")
    os.close(_HANDLE)
    os.environ["PULSESOC_CALL_TEST_DB"] = _DB_PATH
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

from services import pulsesoc_communications_engine as engine  # noqa: E402

CREATOR_ID = 9201
GUEST_A = 9202
GUEST_B = 9203
GUEST_C = 9204
OUTSIDER = 9299

_CONVERSATION_IDS = itertools.count(8001)


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


def _seed_call(conn, cur, joined, members=None, created_by=CREATOR_ID, status="connected", call_type="audio"):
    """Insert a live call. `joined` are joined participants; `members` (defaults
    to joined) is the conversation membership available for invites."""
    public_id = f"call_{uuid.uuid4().hex[:12]}"
    conversation_id = next(_CONVERSATION_IDS)
    now = engine._now()
    for user_id in members if members is not None else joined:
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
        VALUES (?, ?, ?, 'agora', ?, 'group', ?, ?, '{}', ?, ?, ?)
        """,
        (public_id, conversation_id, f"room_{public_id}", call_type, status, int(created_by), now, now, now),
    )
    call_id = engine._inserted_call_id(cur, public_id)
    for user_id in joined:
        cur.execute(
            """
            INSERT INTO communication_call_participants
            (call_id, user_id, role, status, muted_audio, muted_video, joined_at, last_seen_at, device_info_json, created_at, updated_at)
            VALUES (?, ?, ?, 'joined', 0, 1, ?, ?, '{}', ?, ?)
            """,
            (call_id, int(user_id), "caller" if user_id == created_by else "callee", now, now, now, now),
        )
    conn.commit()
    return public_id, call_id


def _call_row(public_id):
    conn, cur = engine._open_db()
    try:
        return engine._get_call(cur, public_id) or {}
    finally:
        conn.close()


def _participant_rows(call_id):
    conn, cur = engine._open_db()
    try:
        cur.execute(
            "SELECT user_id, status FROM communication_call_participants WHERE call_id=? ORDER BY user_id",
            (int(call_id),),
        )
        return {int(row["user_id"]): str(row["status"] or "") for row in cur.fetchall()}
    finally:
        conn.close()


def _flag(enabled):
    return mock.patch.dict(os.environ, {"PULSE_GROUP_CALLS_ENABLED": "true" if enabled else "false"})


class InviteParticipantsTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        self.conn, self.cur = engine._open_db()

    def tearDown(self):
        self.conn.close()

    def test_invite_rings_new_participant_into_the_same_channel(self):
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A], members=[CREATOR_ID, GUEST_A, GUEST_B])
        room_before = _call_row(public_id).get("room_name")
        with _flag(True):
            result = engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [GUEST_B]})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("invited_user_ids"), [GUEST_B])
        self.assertEqual(_participant_rows(call_id).get(GUEST_B), "ringing")
        after = _call_row(public_id)
        self.assertEqual(after.get("room_name"), room_before, "an invite must NEVER move the call to a new channel")
        self.assertNotIn(str(after.get("status") or ""), engine.FINAL_STATUSES)

    def test_invite_is_gated_by_the_group_calls_flag(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A], members=[CREATOR_ID, GUEST_A, GUEST_B])
        with _flag(False):
            result = engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [GUEST_B]})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "group_calls_disabled")

    def test_invite_is_idempotent_for_already_active_participants(self):
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A])
        with _flag(True):
            result = engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [GUEST_A]})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("invited_user_ids"), [])
        self.assertEqual(result.get("already_in_call"), [GUEST_A])
        self.assertEqual(_participant_rows(call_id).get(GUEST_A), "joined", "re-inviting a joined participant must not demote them")

    def test_invite_reuses_the_row_of_someone_who_left(self):
        """UNIQUE(call_id, user_id): re-invite re-rings, never duplicates."""
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A, GUEST_B])
        conn, cur = engine._open_db()
        try:
            cur.execute(
                "UPDATE communication_call_participants SET status='left' WHERE call_id=? AND user_id=?",
                (call_id, GUEST_B),
            )
            conn.commit()
        finally:
            conn.close()
        with _flag(True):
            result = engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [GUEST_B]})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("invited_user_ids"), [GUEST_B])
        conn, cur = engine._open_db()
        try:
            cur.execute(
                "SELECT COUNT(*) AS n FROM communication_call_participants WHERE call_id=? AND user_id=?",
                (call_id, GUEST_B),
            )
            self.assertEqual(int(dict(cur.fetchone())["n"]), 1)
        finally:
            conn.close()
        self.assertEqual(_participant_rows(call_id).get(GUEST_B), "ringing")

    def test_invite_rejects_non_conversation_members(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A])
        with _flag(True):
            result = engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [OUTSIDER]})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "invalid_recipient")

    def test_invite_enforces_the_participant_limit(self):
        public_id, _ = _seed_call(
            self.conn, self.cur, [CREATOR_ID, GUEST_A, GUEST_B], members=[CREATOR_ID, GUEST_A, GUEST_B, GUEST_C]
        )
        with _flag(True), mock.patch.dict(os.environ, {"CALL_MAX_AUDIO_PARTICIPANTS": "3"}):
            result = engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [GUEST_C]})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "participant_limit_exceeded")

    def test_only_active_participants_can_invite(self):
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A, GUEST_B])
        conn, cur = engine._open_db()
        try:
            cur.execute(
                "UPDATE communication_call_participants SET status='left' WHERE call_id=? AND user_id=?",
                (call_id, GUEST_B),
            )
            conn.commit()
        finally:
            conn.close()
        with _flag(True):
            result = engine.invite_participants(GUEST_B, public_id, {"user_ids": [GUEST_C]})
        self.assertFalse(result.get("ok"))

    def test_invite_into_an_ended_call_is_rejected(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A], status="ended")
        with _flag(True):
            result = engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [GUEST_B]})
        self.assertFalse(result.get("ok"))


class CreatorLeavePolicyTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        self.conn, self.cur = engine._open_db()

    def tearDown(self):
        self.conn.close()

    def test_creator_leaving_a_group_call_does_not_end_it(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A, GUEST_B])
        result = engine.end_call(CREATOR_ID, public_id, {"reason": "native_hangup"})
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn(
            str(_call_row(public_id).get("status") or ""),
            engine.FINAL_STATUSES,
            "two guests are still talking; the creator leaving is just a leave",
        )

    def test_creator_can_end_a_group_call_for_everyone(self):
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A, GUEST_B])
        result = engine.end_call(CREATOR_ID, public_id, {"end_for_everyone": True})
        self.assertTrue(result.get("ok"), result)
        self.assertIn(str(_call_row(public_id).get("status") or ""), engine.FINAL_STATUSES)
        for user_id, status in _participant_rows(call_id).items():
            self.assertNotIn(status, {"joined", "ringing"}, f"user {user_id} left dangling as {status}")

    def test_non_creator_cannot_end_for_everyone(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A, GUEST_B])
        engine.end_call(GUEST_A, public_id, {"end_for_everyone": True})
        self.assertNotIn(str(_call_row(public_id).get("status") or ""), engine.FINAL_STATUSES)


class PerParticipantMissedTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        self.conn, self.cur = engine._open_db()

    def tearDown(self):
        self.conn.close()

    def test_unanswered_invitee_goes_missed_while_the_group_keeps_talking(self):
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A], members=[CREATOR_ID, GUEST_A, GUEST_B])
        stale = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat(timespec="seconds")
        conn, cur = engine._open_db()
        try:
            cur.execute(
                """
                INSERT INTO communication_call_participants
                (call_id, user_id, role, status, muted_audio, muted_video, device_info_json, created_at, updated_at)
                VALUES (?, ?, 'callee', 'ringing', 0, 0, '{}', ?, ?)
                """,
                (call_id, GUEST_B, stale, stale),
            )
            engine._mark_missed_stale_calls_cur(cur)
            conn.commit()
        finally:
            conn.close()
        rows = _participant_rows(call_id)
        self.assertEqual(rows.get(GUEST_B), "missed")
        self.assertEqual(rows.get(CREATOR_ID), "joined")
        self.assertNotIn(str(_call_row(public_id).get("status") or ""), engine.FINAL_STATUSES)

    def test_fresh_invitee_keeps_ringing(self):
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A], members=[CREATOR_ID, GUEST_A, GUEST_B])
        with _flag(True):
            engine.invite_participants(CREATOR_ID, public_id, {"user_ids": [GUEST_B]})
        conn, cur = engine._open_db()
        try:
            engine._mark_missed_stale_calls_cur(cur)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(_participant_rows(call_id).get(GUEST_B), "ringing")


class CapabilitiesAndLimitsTest(unittest.TestCase):
    def setUp(self):
        _use_module_database()

    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CALL_MAX_VIDEO_PARTICIPANTS", None)
            os.environ.pop("CALL_MAX_AUDIO_PARTICIPANTS", None)
            self.assertEqual(engine.call_participant_limit("video"), 6)
            self.assertEqual(engine.call_participant_limit("audio"), 12)

    def test_env_overrides_and_floor(self):
        with mock.patch.dict(os.environ, {"CALL_MAX_VIDEO_PARTICIPANTS": "4", "CALL_MAX_AUDIO_PARTICIPANTS": "1"}):
            self.assertEqual(engine.call_participant_limit("video"), 4)
            self.assertEqual(engine.call_participant_limit("audio"), 2, "a call needs at least two people")
        with mock.patch.dict(os.environ, {"CALL_MAX_VIDEO_PARTICIPANTS": "garbage"}):
            self.assertEqual(engine.call_participant_limit("video"), 6)

    def test_capabilities_payload_is_server_owned(self):
        with _flag(True), mock.patch.dict(os.environ, {"CALL_MAX_VIDEO_PARTICIPANTS": "6", "CALL_MAX_AUDIO_PARTICIPANTS": "12"}):
            result = engine.call_capabilities(CREATOR_ID)
        self.assertTrue(result.get("ok"), result)
        caps = result.get("capabilities") or {}
        self.assertTrue(caps.get("group_calls_enabled"))
        self.assertEqual(caps.get("max_video_participants"), 6)
        self.assertEqual(caps.get("max_audio_participants"), 12)
        with _flag(False):
            result = engine.call_capabilities(CREATOR_ID)
        self.assertFalse((result.get("capabilities") or {}).get("group_calls_enabled"))


class JoinSecurityTest(unittest.TestCase):
    """Stages 33-39: the server, not the client, decides who can be in the room.

    Token binding is the core invariant: `_agora_uid(user_id) == user_id`, and
    the uid minted into the RTC token comes from the AUTHENTICATED user id —
    never from anything in the request payload — so a forged uid cannot enter
    the channel as someone else.
    """

    def setUp(self):
        _use_module_database()
        self.conn, self.cur = engine._open_db()

    def tearDown(self):
        self.conn.close()

    @staticmethod
    def _token_ok(provider, room_name, user_id, call_type="audio", role="member"):
        return {
            "ok": True,
            "provider": "agora",
            "token": f"tok-{user_id}",
            "channel_name": room_name,
            "room_name": room_name,
            "uid": engine._agora_uid(int(user_id)),
            "expires_at": engine._now(),
        }

    def test_outsider_cannot_get_a_join_token(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A])
        result = engine.join_token(OUTSIDER, public_id)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "forbidden")

    def test_conversation_member_who_was_never_invited_is_denied(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A], members=[CREATOR_ID, GUEST_A, GUEST_B])
        result = engine.join_token(GUEST_B, public_id)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "not_participant")

    def test_no_join_token_for_an_ended_call(self):
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A], status="ended")
        result = engine.join_token(GUEST_A, public_id)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "call_final")

    def test_rtc_uid_is_the_authenticated_user_id_and_cannot_be_forged(self):
        self.assertEqual(engine._agora_uid(GUEST_A), GUEST_A)
        with self.assertRaises(ValueError):
            engine._agora_uid(0)
        with self.assertRaises(ValueError):
            engine._agora_uid(-5)
        public_id, _ = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A])
        with mock.patch.object(engine, "_generate_rtc_token", side_effect=self._token_ok) as minted:
            result = engine.join_token(GUEST_A, public_id)
        self.assertTrue(result.get("ok"), result)
        # Positional args: (provider, room_name, user_id, call_type, role)
        self.assertEqual(int(minted.call_args.args[2]), GUEST_A, "token must bind the authenticated user id")
        self.assertEqual((result.get("join") or {}).get("uid"), GUEST_A)

    def test_duplicate_join_is_idempotent_one_row_one_channel(self):
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A])
        room_before = _call_row(public_id).get("room_name")
        with mock.patch.object(engine, "_generate_rtc_token", side_effect=self._token_ok):
            first = engine.join_token(GUEST_A, public_id)
            second = engine.join_token(GUEST_A, public_id)
        self.assertTrue(first.get("ok") and second.get("ok"))
        conn, cur = engine._open_db()
        try:
            cur.execute(
                "SELECT COUNT(*) AS n FROM communication_call_participants WHERE call_id=? AND user_id=?",
                (call_id, GUEST_A),
            )
            self.assertEqual(int(dict(cur.fetchone())["n"]), 1, "UNIQUE(call_id, user_id): a second device re-issues, never duplicates")
        finally:
            conn.close()
        self.assertEqual(_call_row(public_id).get("room_name"), room_before)
        self.assertEqual(_participant_rows(call_id).get(GUEST_A), "joined")

    def test_second_device_join_keeps_the_original_joined_at(self):
        """Multi-device: re-issuing a token is an atomic replace on the same
        participant row — joined_at is preserved, last_seen advances."""
        public_id, call_id = _seed_call(self.conn, self.cur, [CREATOR_ID, GUEST_A])
        with mock.patch.object(engine, "_generate_rtc_token", side_effect=self._token_ok):
            engine.join_token(GUEST_A, public_id)
            conn, cur = engine._open_db()
            try:
                cur.execute(
                    "SELECT joined_at FROM communication_call_participants WHERE call_id=? AND user_id=?",
                    (call_id, GUEST_A),
                )
                joined_at_first = str(dict(cur.fetchone())["joined_at"])
            finally:
                conn.close()
            engine.join_token(GUEST_A, public_id)
        conn, cur = engine._open_db()
        try:
            cur.execute(
                "SELECT joined_at FROM communication_call_participants WHERE call_id=? AND user_id=?",
                (call_id, GUEST_A),
            )
            self.assertEqual(str(dict(cur.fetchone())["joined_at"]), joined_at_first)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
