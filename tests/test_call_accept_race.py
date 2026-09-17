"""Two devices answering one call must produce exactly one acceptance.

`accept_call` decides whether to run the `ringing -> accepted` transition by
reading the call row, checking the status in Python, and then writing:

    call, participant, denied = _require_call_access(cur, user_id, call_ref)   # SELECT
    if str(call.get("status") or "") in {"created", "ringing"}:                # check
        _transition(cur, call, "accepted", ...)                                # write

and `_transition` ends in an UPDATE keyed on the primary key alone:

    UPDATE communication_calls SET status=?, ... WHERE id=?

There is no status predicate in that WHERE, no rowcount check, no SELECT ...
FOR UPDATE on the read, and no isolation escalation anywhere in the module --
`_get_call` is a plain `SELECT * FROM communication_calls WHERE ... LIMIT 1`.
The status test is therefore performed against a snapshot that another
transaction is free to invalidate before the write lands.

This matters because the brief makes "first valid answer wins" a hard
requirement: one device answering must stop all the others. A user signed in on
an iPhone and an iPad gets both ringing, and the app's own accept path can fire
twice for a single tap -- `accept_call`'s own comment notes that "the CallKit
answer handler and the in-app incoming-call sheet can both fire for one tap".
Every extra winner re-runs the acceptance side effects: another `accepted`
event, another `call_accepted` sync emission to the caller, and another
answered-elsewhere cancel fan-out carrying a *different* excluded device id.

The two tests below prove two separate things, and both are needed:

`AcceptSnapshotGuardTest` proves the guard is MISSING. It needs no concurrency
-- it hands `_transition` a snapshot that has gone stale and shows the write
lands anyway. This runs on any backend, SQLite included.

`AcceptRaceTest` proves the gap is REACHABLE on the engine production runs. It
requires PostgreSQL and is skipped otherwise, deliberately: SQLite cannot show
this and would report a false pass, because its writer lock serialises the two
transactions outright. The second thread either blocks until the first fully
commits -- re-reading the *new* status and correctly skipping the transition --
or fails with "database is locked". Both outcomes look like correct behaviour.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
import threading
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PG_URL = os.environ.get("PULSESOC_RACE_TEST_DATABASE_URL", "")
if _PG_URL:
    os.environ["DATABASE_URL"] = _PG_URL
else:
    _HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="accept_race_")
    os.close(_HANDLE)
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

from services import pulsesoc_communications_engine as engine  # noqa: E402

CALLER_ID = 9401
CALLEE_ID = 9402

_STUB_TOKEN = {
    "ok": True,
    "token": "stub-rtc-token",
    "app_id": "stub-app",
    "channel_name": "stub-channel",
    "uid": 4242,
    "provider": "agora",
}

_SERIAL_PK = "SERIAL PRIMARY KEY" if _PG_URL.startswith("postgres") else "INTEGER PRIMARY KEY AUTOINCREMENT"


class _CallFixture(unittest.TestCase):
    """Seeds one ringing 1:1 call and stubs the media-token mint."""

    def setUp(self):
        self.conn, self.cur = engine._open_db()
        # `_participant_allowed` reads this for a `direct`-scope call. Owned by
        # pulse_communications_v2.models; created here only if that bootstrap
        # has not already run against this database.
        self.cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS comm_v2_participants (
                id {_SERIAL_PK},
                conversation_id BIGINT,
                user_id BIGINT,
                membership_state TEXT,
                left_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.commit()
        self.public_id = self._seed_ringing_call()
        self._real_token = engine._generate_rtc_token
        engine._generate_rtc_token = lambda *a, **k: dict(_STUB_TOKEN)

    def tearDown(self):
        engine._generate_rtc_token = self._real_token
        self.conn.close()

    def _seed_ringing_call(self):
        public_id = f"call_{uuid.uuid4().hex[:12]}"
        conversation_id = int(uuid.uuid4().int % 100000) + 8000
        now = engine._now()
        for user_id in (CALLER_ID, CALLEE_ID):
            self.cur.execute(
                """
                INSERT INTO comm_v2_participants
                (conversation_id, user_id, membership_state, left_at, created_at, updated_at)
                VALUES (?, ?, 'active', '', ?, ?)
                """,
                (conversation_id, int(user_id), now, now),
            )
        self.cur.execute(
            """
            INSERT INTO communication_calls
            (public_id, conversation_id, room_name, provider, call_type, call_scope,
             status, created_by_user_id, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, 'agora', 'audio', 'direct', 'ringing', ?, '{}', ?, ?)
            """,
            (public_id, conversation_id, f"room_{public_id}", CALLER_ID, now, now),
        )
        self.call_id = engine._inserted_call_id(self.cur, public_id)
        for user_id, role, status in (
            (CALLER_ID, "caller", "joined"),
            (CALLEE_ID, "callee", "ringing"),
        ):
            self.cur.execute(
                """
                INSERT INTO communication_call_participants
                (call_id, user_id, role, status, muted_audio, muted_video,
                 joined_at, last_seen_at, device_info_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, 1, ?, ?, '{}', ?, ?)
                """,
                (self.call_id, int(user_id), role, status, now, now, now, now),
            )
        self.conn.commit()
        return public_id

    def _status(self):
        conn, cur = engine._open_db()
        try:
            return str(engine._get_call(cur, self.public_id).get("status") or "")
        finally:
            conn.close()

    def _accepted_from_ringing_events(self):
        conn, cur = engine._open_db()
        try:
            cur.execute(
                "SELECT event_payload_json FROM communication_call_events "
                "WHERE call_id=? AND event_type='accepted'",
                (int(self.call_id),),
            )
            rows = cur.fetchall() or []
        finally:
            conn.close()
        transitions = []
        for row in rows:
            try:
                payload = json.loads(str(engine._row(row).get("event_payload_json") or "{}"))
            except ValueError:
                continue
            if payload.get("from") == "ringing" and payload.get("to") == "accepted":
                transitions.append(payload)
        return transitions


class AcceptSnapshotGuardTest(_CallFixture):
    """The write must be rejected when the snapshot it was authorised against is stale."""

    def test_positive_control_a_fresh_accept_is_allowed(self):
        # Without this, the test below would pass just as well against a
        # `_transition` that refused every write.
        result = engine.accept_call(CALLEE_ID, self.public_id, {"device_id": "iphone-a"})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(self._accepted_from_ringing_events()), 1)

    def test_a_stale_ringing_snapshot_cannot_still_accept(self):
        # The snapshot the SECOND device read while the call was still ringing.
        # In production this is what its transaction holds after `_get_call`,
        # before the first device's commit becomes visible to it.
        stale = engine._get_call(self.cur, self.public_id)
        self.assertEqual(str(stale.get("status") or ""), "ringing")

        # The first device answers and wins.
        first = engine.accept_call(CALLEE_ID, self.public_id, {"device_id": "iphone-a"})
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(self._status(), "connecting")

        # The second device now writes, authorised by its stale snapshot. The
        # call is no longer ringing, so this acceptance must not be honoured.
        conn, cur = engine._open_db()
        try:
            late = engine._transition(cur, stale, "accepted", CALLEE_ID, "accepted")
            conn.commit()
        finally:
            conn.close()

        self.assertFalse(
            late.get("ok"),
            "A second device accepted a call that had already left `ringing`. "
            "`_transition` validated against the caller's in-memory snapshot and "
            "then wrote `WHERE id=?` with no status predicate and no rowcount "
            "check, so the stored row -- already advanced to `connecting` by the "
            "winner -- was overwritten. First-answer-wins is not enforced.",
        )
        self.assertEqual(
            len(self._accepted_from_ringing_events()),
            1,
            "The ringing -> accepted transition was recorded more than once, so "
            "the acceptance side effects (caller sync emission, answered-elsewhere "
            "cancel fan-out) ran twice for one call.",
        )


_needs_postgres = unittest.skipUnless(
    _PG_URL.startswith("postgres"),
    "needs a real PostgreSQL DATABASE_URL in PULSESOC_RACE_TEST_DATABASE_URL; "
    "SQLite cannot host two concurrent writers and gives a misleading result",
)


class SweeperStompsAnsweredCallTest(_CallFixture):
    """The 45s ring sweeper must not mark a call missed after it was answered.

    `_mark_missed_stale_calls_cur` reads every ringing call up front:

        cur.execute("SELECT * FROM communication_calls WHERE status='ringing' ...")
        for row in cur.fetchall():
            ...
            _transition(cur, call, "missed", ..., "ring_timeout")

    and then transitions each one from that materialised snapshot. Processing a
    single call does real work between the read and the write -- a participant
    query, a user summary lookup, a missed-call notification per recipient, a
    sync emission -- so a call sitting later in the batch can be answered while
    the sweeper is still working through the ones before it. Its snapshot still
    says `ringing`, `ringing -> missed` is a legal edge, and the write lands.

    This is the same root cause as the accept race but strictly worse: it needs
    only ONE device. A user who answers near the timeout boundary is put into a
    call the server then records as missed -- the caller is told the call was
    missed, and a missed-call history row is written for a call that was in fact
    picked up.

    The answer is injected on the sweeper's OWN cursor, mid-batch. That is not a
    convenience to dodge SQLite's writer lock; it is the more faithful model. The
    defect is not about two connections contending, it is that the loop trusts
    rows it read before doing seconds of work. Driving it this way makes the
    interleaving exact rather than timing-dependent, and keeps the test running
    on the same backend as the rest of the suite.
    """

    def _age_call_past_the_ring_timeout(self, public_id):
        old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(timespec="seconds")
        self.cur.execute(
            "UPDATE communication_calls SET created_at=? WHERE public_id=?", (old, public_id)
        )
        self.conn.commit()

    def _sweep_answering_the_victim_midway(self, victim_public_id):
        """Run the sweeper, answering `victim_public_id` while it works."""
        real_notify = engine._notify_missed_call
        self.answered = {}

        def notify_then_answer(cur, call, actor_id, recipient_id, actor_name=""):
            result = real_notify(cur, call, actor_id, recipient_id, actor_name)
            # The decoy is being written off right now, and the victim's row has
            # already been read into the batch. Answering here reproduces a
            # callee picking up during that window.
            if not self.answered:
                self.answered["result"] = engine._transition(
                    cur,
                    engine._get_call(cur, victim_public_id),
                    "accepted",
                    CALLEE_ID,
                    "accepted",
                )
            return result

        engine._notify_missed_call = notify_then_answer
        try:
            engine.mark_missed_stale_calls()
        finally:
            engine._notify_missed_call = real_notify

    def _final_status(self, public_id):
        conn, cur = engine._open_db()
        try:
            return str(engine._get_call(cur, public_id).get("status") or "")
        finally:
            conn.close()

    def test_positive_control_an_unanswered_stale_call_is_still_swept(self):
        # The guard must reject only the answered call. Without this, a
        # `_transition` that refused every sweep write would pass the test below.
        self._age_call_past_the_ring_timeout(self.public_id)
        decoy_public_id = self.public_id

        victim_public_id = self._seed_ringing_call()
        self._age_call_past_the_ring_timeout(victim_public_id)

        self._sweep_answering_the_victim_midway(victim_public_id)

        self.assertEqual(
            self._final_status(decoy_public_id),
            "missed",
            "The sweeper stopped writing off genuinely unanswered calls.",
        )

    def test_a_call_answered_mid_sweep_is_not_marked_missed(self):
        # Two stale ringing calls. The sweeper walks them in `id ASC` order, so
        # the one seeded in setUp is written off first and the one seeded here
        # is still pending -- with its snapshot already loaded -- while that
        # first write-off runs. The second one is what gets answered.
        self._age_call_past_the_ring_timeout(self.public_id)
        decoy_public_id = self.public_id

        victim_public_id = self._seed_ringing_call()
        self._age_call_past_the_ring_timeout(victim_public_id)
        self.assertNotEqual(decoy_public_id, victim_public_id)

        self._sweep_answering_the_victim_midway(victim_public_id)
        self.assertTrue(self.answered.get("result", {}).get("ok"), self.answered)

        self.assertNotEqual(
            self._final_status(victim_public_id),
            "missed",
            "The ring sweeper marked a call missed that had already been "
            "answered. It transitioned from a snapshot read before the answer "
            "landed, and `_transition` writes `WHERE id=?` with no status "
            "predicate, so the answered call was overwritten. The callee is in "
            "a call the server records as missed, and the caller is notified of "
            "a missed call that was picked up.",
        )


@_needs_postgres
class AcceptRaceTest(_CallFixture):
    """The stale snapshot above is genuinely reachable under READ COMMITTED."""

    def setUp(self):
        super().setUp()
        self._real_participant_for_call = engine._participant_for_call

    def tearDown(self):
        engine._participant_for_call = self._real_participant_for_call
        super().tearDown()

    def _arm_toctou_barrier(self):
        """Hold both threads between the status read and the status write.

        `_require_call_access` calls `_get_call` (the read) and then
        `_participant_for_call`. Blocking the latter parks each thread with
        `status='ringing'` already loaded into its local `call` dict, which is
        exactly the window the production code leaves open. No transition logic
        is modified -- only the scheduling is made deterministic, so the test
        cannot pass by winning a timing lottery.
        """
        barrier = threading.Barrier(2, timeout=20)
        seen = set()
        real = self._real_participant_for_call

        def patched(cur, call_id, user_id):
            result = real(cur, call_id, user_id)
            ident = threading.get_ident()
            if ident not in seen:
                seen.add(ident)
                barrier.wait()
            return result

        engine._participant_for_call = patched

    def test_two_devices_answering_together_accept_once(self):
        self._arm_toctou_barrier()
        results = {}

        def answer(device_id):
            try:
                results[device_id] = engine.accept_call(
                    CALLEE_ID, self.public_id, {"device_id": device_id}
                )
            except Exception as exc:  # recorded, not raised, so both threads finish
                results[device_id] = {"ok": False, "exception": repr(exc)}

        threads = [
            threading.Thread(target=answer, args=(device,), daemon=True)
            for device in ("iphone-device-a", "ipad-device-b")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        transitions = self._accepted_from_ringing_events()
        self.assertEqual(
            len(transitions),
            1,
            f"The ringing -> accepted transition ran {len(transitions)} times for "
            "one call. Both devices read `ringing`, both passed the Python status "
            "check, and both wrote. Each run re-emits the acceptance side effects: "
            "a duplicate call_accepted sync event to the caller and a second "
            "answered-elsewhere cancel fan-out excluding a different device. "
            f"Per-device results: {results}",
        )


if __name__ == "__main__":
    unittest.main()
