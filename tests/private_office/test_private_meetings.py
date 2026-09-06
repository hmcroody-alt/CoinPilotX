"""Private Meetings model — the mission §60 backend matrix, model layer.

Run either way::

    python -m pytest tests/private_office/test_private_meetings.py
    python tests/private_office/test_private_meetings.py

What these tests defend
-----------------------
* **Fail closed.** ``PRIVATE_MEETINGS_ENABLED`` absent means every write and
  read refuses — the flag defaults OFF, the opposite of the feature matrix's
  usual absent-means-on convention.
* **The waiting room is structural.** An unadmitted joiner has no
  ``communication_call_participants`` row, so the *engine itself* refuses
  ``_require_call_access`` — no token can be minted, which is stronger than a
  UI that hides a button.
* **Admission and revocation are row operations.** Admit inserts the call row;
  remove marks it terminal, and the engine's room-scope branch refuses a
  revoked row rather than letting ``join_token`` re-arm it.
* **One logical participant, forever.** Leave + rejoin mutates the same row —
  same primary key — so a duplicate tile is a constraint violation, not a bug
  class.
* **Both blocking systems bind.** A block in ``comm_v2_blocks`` OR the legacy
  ``blocked_users`` table, in either direction, refuses without revealing the
  meeting exists.
* **Lock means no one new.** Link, code, and invite all refuse while locked;
  a participant who was already admitted may re-enter (reconnect is not new).
* **Truthfulness.** TRANSCRIPT_DERIVED artifacts are refused while no
  transcription provider exists; screen share reports not_implemented;
  recording refuses while its flag is off.
* **No zombie meetings.** The sweep ends empty meetings, expired transports,
  over-long meetings, and host-disconnect windows that lapsed with no
  moderator present.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import pulsesoc_communications_engine as eng  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import meetings  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402

HOST = 101
GUEST = 202
OTHER = 303
STRANGER = 404


@pytest.fixture()
def cur(monkeypatch):
    """Fresh in-memory DB per test, flag ON, Agora 'configured'."""
    monkeypatch.setenv("PRIVATE_MEETINGS_ENABLED", "1")
    monkeypatch.delenv("PRIVATE_MEETINGS_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(eng, "agora_config_status", lambda: {"configured": True})
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    meetings.reset_meetings_schema_cache()
    meetings.ensure_meetings_schema(cursor, force=True)
    cursor.execute(po_schema.AUDIT_TABLE_DDL)
    cursor.execute(
        "CREATE TABLE blocked_users (blocker_user_id INT, blocked_user_id INT)")
    cursor.execute(
        "CREATE TABLE comm_v2_blocks (id INTEGER PRIMARY KEY, "
        "blocker_user_id INT, blocked_user_id INT, status TEXT)")
    yield cursor
    conn.close()
    meetings.reset_meetings_schema_cache()


def _instant(cursor, owner=HOST, **kwargs):
    return meetings.create_meeting(
        cursor, owner_user_id=owner, title="Standup", instant=True, **kwargs)


def _call_id(cursor, meeting_payload):
    cursor.execute("SELECT id FROM communication_calls WHERE public_id=?",
                   (meeting_payload["call_public_id"],))
    return int(cursor.fetchone()["id"])


def _reject(fn, *, status=None, code=None):
    with pytest.raises(meetings.PrivateMeetingRejected) as excinfo:
        fn()
    if status is not None:
        assert excinfo.value.status == status, excinfo.value.code
    if code is not None:
        assert excinfo.value.code == code
    return excinfo.value


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------

def test_flag_defaults_off_and_gates_every_entry(cur, monkeypatch):
    monkeypatch.delenv("PRIVATE_MEETINGS_ENABLED", raising=False)
    assert meetings.meetings_enabled() is False
    assert meetings.recording_enabled() is False
    _reject(lambda: meetings.create_meeting(cur, owner_user_id=HOST, instant=True),
            status=403, code="flag_disabled")
    _reject(lambda: meetings.join_meeting(cur, user_id=GUEST, meeting_ref="mtg_x"),
            status=403, code="flag_disabled")
    _reject(lambda: meetings.list_meetings(cur, user_id=HOST),
            status=403, code="flag_disabled")


def test_start_refuses_when_rtc_unconfigured(cur, monkeypatch):
    monkeypatch.setattr(eng, "agora_config_status", lambda: {"configured": False})
    _reject(lambda: _instant(cur), status=503, code="rtc_unavailable")
    # Fail closed, not FAILED: nothing to sweep, nothing half-created.
    cur.execute(f"SELECT status FROM {meetings.MEETINGS_TABLE}")
    rows = [r["status"] for r in cur.fetchall()]
    assert meetings.ST_LIVE not in rows


# ---------------------------------------------------------------------------
# Create / schedule / codes
# ---------------------------------------------------------------------------

def test_instant_create_goes_live_on_room_scope_call(cur):
    m = _instant(cur)
    assert m["status"] == meetings.ST_LIVE
    assert m["call_public_id"].startswith("call_")
    assert m["channel_name"] == f"pulsesoc-{m['call_public_id']}"
    cur.execute("SELECT call_scope, conversation_id, status FROM communication_calls")
    call = cur.fetchone()
    assert call["call_scope"] == "room"
    assert int(call["conversation_id"] or 0) == 0
    assert call["status"] == "connecting"


def test_scheduled_create_requires_valid_time(cur):
    _reject(lambda: meetings.create_meeting(
        cur, owner_user_id=HOST, scheduled_start_at="not-a-time"),
        status=400, code="invalid_schedule")
    m = meetings.create_meeting(
        cur, owner_user_id=HOST, title="Board",
        scheduled_start_at="2026-09-06T10:00:00+00:00", duration_minutes=30)
    assert m["status"] == meetings.ST_SCHEDULED
    assert m["call_public_id"] == "" if "call_public_id" in m else True


def test_meeting_code_shape_and_rotation_revokes(cur):
    m = _instant(cur)
    code = m["meeting_code"]
    assert len(code) == 11 and code.count("-") == 2
    rotated = meetings.rotate_code(cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    assert rotated["meeting_code"] != code
    # The old code stops resolving for a stranger — indistinguishable from
    # a code that never existed.
    _reject(lambda: meetings.join_meeting(cur, user_id=STRANGER, meeting_ref=code),
            status=404, code="not_found")
    joined = meetings.join_meeting(
        cur, user_id=STRANGER, meeting_ref=rotated["meeting_code"])
    assert joined["me"]["state"] == meetings.P_WAITING_ROOM


def test_only_host_starts_and_rotates(cur):
    m = meetings.create_meeting(
        cur, owner_user_id=HOST, scheduled_start_at="2026-09-06T10:00:00+00:00")
    _reject(lambda: meetings.start_meeting(
        cur, actor_user_id=GUEST, meeting_ref=m["public_id"]),
        status=403, code="forbidden")
    _reject(lambda: meetings.rotate_code(
        cur, actor_user_id=GUEST, meeting_ref=m["public_id"]),
        status=403, code="forbidden")
    started = meetings.start_meeting(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    assert started["status"] == meetings.ST_LIVE


# ---------------------------------------------------------------------------
# Waiting room — structural least privilege
# ---------------------------------------------------------------------------

def test_waiting_room_has_no_call_row_and_engine_refuses(cur):
    m = _instant(cur)
    g = meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    assert g["me"]["state"] == meetings.P_WAITING_ROOM
    assert "call_public_id" not in g
    assert "meeting_code" not in g  # code is host-only
    cur.execute("SELECT 1 FROM communication_call_participants WHERE user_id=?",
                (GUEST,))
    assert cur.fetchone() is None
    _, _, denied = eng._require_call_access(cur, GUEST, _call_id(cur, m))
    assert denied is not None


def test_admit_opens_engine_access_and_deny_is_final(cur):
    m = _instant(cur)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    meetings.join_meeting(cur, user_id=OTHER, meeting_ref=m["meeting_code"])
    # Non-moderator cannot admit.
    _reject(lambda: meetings.admit_participant(
        cur, actor_user_id=GUEST, meeting_ref=m["public_id"], user_id=OTHER),
        status=403, code="forbidden")
    admitted = meetings.admit_participant(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=GUEST)
    assert admitted["state"] == meetings.P_ADMITTED
    _, participant, denied = eng._require_call_access(cur, GUEST, _call_id(cur, m))
    assert denied is None and participant
    view = meetings.get_meeting(cur, user_id=GUEST, meeting_ref=m["public_id"])
    assert view["call_public_id"] == m["call_public_id"]
    denied_p = meetings.deny_participant(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=OTHER)
    assert denied_p["state"] == meetings.P_REMOVED
    _reject(lambda: meetings.join_meeting(
        cur, user_id=OTHER, meeting_ref=m["meeting_code"]),
        status=404, code="not_found")


def test_waiting_room_disabled_admits_directly(cur):
    m = _instant(cur, waiting_room_enabled=False)
    g = meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    assert g["me"]["state"] == meetings.P_ADMITTED
    _, _, denied = eng._require_call_access(cur, GUEST, _call_id(cur, m))
    assert denied is None


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

def test_lock_refuses_everyone_new_but_not_returning(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    meetings.set_locked(cur, actor_user_id=HOST, meeting_ref=m["public_id"],
                        locked=True)
    _reject(lambda: meetings.join_meeting(
        cur, user_id=STRANGER, meeting_ref=m["meeting_code"]),
        status=403, code="locked")
    # Already-admitted GUEST re-enters — reconnect is not "new".
    back = meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["public_id"])
    assert back["me"]["state"] in {meetings.P_ADMITTED, meetings.P_JOINED}
    meetings.set_locked(cur, actor_user_id=HOST, meeting_ref=m["public_id"],
                        locked=False)
    ok = meetings.join_meeting(cur, user_id=STRANGER, meeting_ref=m["meeting_code"])
    assert ok["me"]["state"] == meetings.P_ADMITTED


# ---------------------------------------------------------------------------
# Blocking — BOTH systems, either direction, no existence leak
# ---------------------------------------------------------------------------

def test_legacy_block_refuses_as_not_found(cur):
    m = _instant(cur)
    cur.execute("INSERT INTO blocked_users VALUES (?, ?)", (HOST, GUEST))
    _reject(lambda: meetings.join_meeting(
        cur, user_id=GUEST, meeting_ref=m["meeting_code"]),
        status=404, code="not_found")


def test_comm_v2_block_reverse_direction_also_refuses(cur):
    m = _instant(cur)
    cur.execute(
        "INSERT INTO comm_v2_blocks (blocker_user_id, blocked_user_id, status) "
        "VALUES (?, ?, 'active')", (GUEST, HOST))  # guest blocked the host
    _reject(lambda: meetings.join_meeting(
        cur, user_id=GUEST, meeting_ref=m["meeting_code"]),
        status=404, code="not_found")


def test_invites_skip_blocked_without_saying_why(cur):
    m = _instant(cur)
    cur.execute("INSERT INTO blocked_users VALUES (?, ?)", (OTHER, HOST))
    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"],
        user_ids=[GUEST, OTHER])
    assert result["invited"] == [GUEST]
    assert result["skipped"] == [{"user_id": OTHER, "reason": "unavailable"}]


# ---------------------------------------------------------------------------
# One logical participant — reconnect, rejoin, no duplicate rows
# ---------------------------------------------------------------------------

def test_leave_and_rejoin_reuses_the_same_row(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    cur.execute(f"SELECT id FROM {meetings.PARTICIPANTS_TABLE} WHERE user_id=?",
                (GUEST,))
    first_id = cur.fetchone()["id"]
    meetings.mark_joined(cur, user_id=GUEST, meeting_ref=m["public_id"])
    meetings.leave_meeting(cur, user_id=GUEST, meeting_ref=m["public_id"])
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    cur.execute(f"SELECT id, state FROM {meetings.PARTICIPANTS_TABLE} "
                f"WHERE user_id=?", (GUEST,))
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == first_id
    assert rows[0]["state"] == meetings.P_ADMITTED


def test_reconnect_marks_and_joined_at_is_set_once(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    joined = meetings.mark_joined(cur, user_id=GUEST, meeting_ref=m["public_id"])
    stamp = joined["joined_at"]
    assert stamp
    meetings.mark_reconnecting(cur, user_id=GUEST, meeting_ref=m["public_id"])
    again = meetings.mark_joined(cur, user_id=GUEST, meeting_ref=m["public_id"])
    assert again["joined_at"] == stamp
    assert again["state"] == meetings.P_JOINED


def test_removed_is_final_and_revokes_engine_access(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    _, _, before = eng._require_call_access(cur, GUEST, _call_id(cur, m))
    assert before is None
    meetings.remove_participant(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=GUEST)
    _, _, after = eng._require_call_access(cur, GUEST, _call_id(cur, m))
    assert after is not None
    _reject(lambda: meetings.join_meeting(
        cur, user_id=GUEST, meeting_ref=m["meeting_code"]),
        status=404, code="not_found")
    _reject(lambda: meetings.get_meeting(
        cur, user_id=GUEST, meeting_ref=m["public_id"]),
        status=404, code="not_found")


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def test_host_is_immutable_and_role_changes_are_host_only(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    _reject(lambda: meetings.set_role(
        cur, actor_user_id=GUEST, meeting_ref=m["public_id"], user_id=GUEST,
        role=meetings.ROLE_CO_HOST), status=403, code="forbidden")
    _reject(lambda: meetings.remove_participant(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=HOST),
        status=403, code="host_immutable")
    _reject(lambda: meetings.set_role(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=HOST,
        role=meetings.ROLE_PARTICIPANT), status=403, code="host_immutable")
    promoted = meetings.set_role(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=GUEST,
        role=meetings.ROLE_CO_HOST)
    assert promoted["role"] == meetings.ROLE_CO_HOST
    # Co-host can now admit.
    meetings.join_meeting(cur, user_id=OTHER, meeting_ref=m["meeting_code"])
    admitted = meetings.admit_participant(
        cur, actor_user_id=GUEST, meeting_ref=m["public_id"], user_id=OTHER)
    assert admitted["state"] == meetings.P_ADMITTED


def test_participant_cap(cur, monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_MAX_PARTICIPANTS", "2")
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    _reject(lambda: meetings.join_meeting(
        cur, user_id=OTHER, meeting_ref=m["meeting_code"]),
        status=409, code="meeting_full")


# ---------------------------------------------------------------------------
# Leave vs end-for-everyone; host disconnect window; sweep
# ---------------------------------------------------------------------------

def test_end_for_everyone_closes_call_participants_and_recordings(cur, monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_RECORDING_ENABLED", "1")
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    meetings.mark_joined(cur, user_id=GUEST, meeting_ref=m["public_id"])
    rec = meetings.start_recording(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    meetings.mark_recording_active(
        cur, meeting_ref=m["public_id"], recording_id=rec["id"],
        provider_sid="sid1")
    # A waiting-room occupant to expire.
    cur2_user = 505
    meetings.set_locked(cur, actor_user_id=HOST, meeting_ref=m["public_id"],
                        locked=False)
    cur.execute(
        f"UPDATE {meetings.MEETINGS_TABLE} SET waiting_room_enabled=1 WHERE id="
        f"(SELECT id FROM {meetings.MEETINGS_TABLE} WHERE public_id=?)",
        (m["public_id"],))
    meetings.join_meeting(cur, user_id=cur2_user, meeting_ref=m["meeting_code"])
    ended = meetings.end_meeting(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    assert ended["status"] == meetings.ST_ENDED
    cur.execute("SELECT status FROM communication_calls WHERE public_id=?",
                (m["call_public_id"],))
    assert cur.fetchone()["status"] == "ended"
    cur.execute("SELECT status FROM communication_call_participants")
    assert {r["status"] for r in cur.fetchall()} <= {"left", "removed"}
    cur.execute(f"SELECT state FROM {meetings.PARTICIPANTS_TABLE} WHERE user_id=?",
                (cur2_user,))
    assert cur.fetchone()["state"] == meetings.P_EXPIRED
    cur.execute(f"SELECT status FROM {meetings.RECORDINGS_TABLE}")
    assert cur.fetchone()["status"] == meetings.REC_COMPLETED
    # Idempotent: ending an ended meeting is a no-op, not an error.
    again = meetings.end_meeting(cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    assert again["status"] == meetings.ST_ENDED


def test_only_moderator_ends(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    _reject(lambda: meetings.end_meeting(
        cur, actor_user_id=GUEST, meeting_ref=m["public_id"]),
        status=403, code="forbidden")


def test_host_leave_arms_window_and_sweep_ends_without_moderator(cur, monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_HOST_RECONNECT_SECONDS", "15")
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    meetings.mark_joined(cur, user_id=GUEST, meeting_ref=m["public_id"])
    meetings.leave_meeting(cur, user_id=HOST, meeting_ref=m["public_id"])
    cur.execute(f"SELECT status, host_disconnect_deadline FROM "
                f"{meetings.MEETINGS_TABLE}")
    row = cur.fetchone()
    assert row["status"] == meetings.ST_LIVE  # leave is not end
    assert row["host_disconnect_deadline"]
    # Sweep before the deadline: nothing happens.
    assert meetings.sweep_meetings(cur, now=meetings._now_dt()) == 0
    # Past the deadline with no co-host: the meeting ends honestly.
    from datetime import timedelta
    later = meetings._now_dt() + timedelta(seconds=60)
    assert meetings.sweep_meetings(cur, now=later) == 1
    cur.execute(f"SELECT status, end_reason FROM {meetings.MEETINGS_TABLE}")
    row = cur.fetchone()
    assert row["status"] == meetings.ST_ENDED
    assert row["end_reason"] == "host_disconnected"


def test_host_rejoin_clears_window_and_cohost_keeps_meeting_alive(cur, monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_HOST_RECONNECT_SECONDS", "15")
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    meetings.set_role(cur, actor_user_id=HOST, meeting_ref=m["public_id"],
                      user_id=GUEST, role=meetings.ROLE_CO_HOST)
    meetings.mark_joined(cur, user_id=GUEST, meeting_ref=m["public_id"])
    meetings.leave_meeting(cur, user_id=HOST, meeting_ref=m["public_id"])
    from datetime import timedelta
    later = meetings._now_dt() + timedelta(seconds=60)
    # Co-host JOINED: the sweep clears the window instead of ending.
    assert meetings.sweep_meetings(cur, now=later) == 0
    cur.execute(f"SELECT status, host_disconnect_deadline FROM "
                f"{meetings.MEETINGS_TABLE}")
    row = cur.fetchone()
    assert row["status"] == meetings.ST_LIVE
    assert row["host_disconnect_deadline"] == ""
    # Host rejoin also clears an armed window.
    meetings.leave_meeting(cur, user_id=HOST, meeting_ref=m["public_id"])
    meetings.join_meeting(cur, user_id=HOST, meeting_ref=m["public_id"])
    cur.execute(f"SELECT host_disconnect_deadline FROM {meetings.MEETINGS_TABLE}")
    assert cur.fetchone()["host_disconnect_deadline"] == ""


def test_sweep_kills_zombies(cur, monkeypatch):
    from datetime import timedelta
    monkeypatch.setenv("PRIVATE_MEETINGS_EMPTY_TIMEOUT", "60")
    # 1) LIVE with nobody present past the empty timeout.
    m1 = _instant(cur, waiting_room_enabled=False)
    # 2) LIVE whose engine call was expired out from under it.
    m2 = _instant(cur, waiting_room_enabled=False)
    meetings.mark_joined(cur, user_id=HOST, meeting_ref=m2["public_id"])
    cur.execute("UPDATE communication_calls SET status='expired' WHERE public_id=?",
                (m2["call_public_id"],))
    # 3) SCHEDULED that never started, >24h past its slot.
    meetings.create_meeting(cur, owner_user_id=HOST, title="Stale",
                            scheduled_start_at="2026-09-01T10:00:00+00:00")
    later = meetings._now_dt() + timedelta(seconds=120)
    swept = meetings.sweep_meetings(cur, now=later)
    assert swept == 3
    cur.execute(f"SELECT public_id, status, end_reason FROM "
                f"{meetings.MEETINGS_TABLE} ORDER BY id")
    by_id = {r["public_id"]: (r["status"], r["end_reason"]) for r in cur.fetchall()}
    assert by_id[m1["public_id"]][1] == "empty_timeout"
    assert by_id[m2["public_id"]] == (meetings.ST_ENDED, "call_expired")
    stale = [v for k, v in by_id.items()
             if k not in (m1["public_id"], m2["public_id"])]
    assert stale == [(meetings.ST_CANCELLED, "never_started")]


def test_sweep_ends_overlong_meeting(cur, monkeypatch):
    from datetime import timedelta
    m = _instant(cur, waiting_room_enabled=False)
    meetings.mark_joined(cur, user_id=HOST, meeting_ref=m["public_id"])
    later = meetings._now_dt() + timedelta(seconds=meetings.max_meeting_seconds() + 60)
    assert meetings.sweep_meetings(cur, now=later) == 1
    cur.execute(f"SELECT end_reason FROM {meetings.MEETINGS_TABLE}")
    assert cur.fetchone()["end_reason"] == "max_duration"


# ---------------------------------------------------------------------------
# Chat, reactions, raise hand
# ---------------------------------------------------------------------------

def test_chat_is_persisted_scoped_and_denied_to_waiting_room(cur):
    m = _instant(cur)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    _reject(lambda: meetings.post_message(
        cur, user_id=GUEST, meeting_ref=m["public_id"], body="hi"),
        status=403, code="not_admitted")
    _reject(lambda: meetings.list_messages(
        cur, user_id=GUEST, meeting_ref=m["public_id"]),
        status=403, code="not_admitted")
    meetings.admit_participant(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=GUEST)
    sent = meetings.post_message(
        cur, user_id=GUEST, meeting_ref=m["public_id"], body="  hello all  ")
    assert sent["body"] == "hello all"
    react = meetings.post_message(
        cur, user_id=HOST, meeting_ref=m["public_id"], body="🎉",
        kind=meetings.KIND_REACTION)
    assert react["kind"] == meetings.KIND_REACTION
    since = meetings.list_messages(
        cur, user_id=HOST, meeting_ref=m["public_id"], since_id=sent["id"])
    assert [item["id"] for item in since] == [react["id"]]
    _reject(lambda: meetings.post_message(
        cur, user_id=HOST, meeting_ref=m["public_id"], body="",
        kind=meetings.KIND_TEXT), status=400, code="empty_body")
    _reject(lambda: meetings.post_message(
        cur, user_id=HOST, meeting_ref=m["public_id"], body="x",
        kind=meetings.KIND_SYSTEM), status=400, code="invalid_kind")


def test_raise_hand_set_and_cleared_on_leave(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    raised = meetings.set_raised_hand(
        cur, user_id=GUEST, meeting_ref=m["public_id"], raised=True)
    assert raised["raised_hand"] is True
    lowered = meetings.set_raised_hand(
        cur, user_id=GUEST, meeting_ref=m["public_id"], raised=False)
    assert lowered["raised_hand"] is False
    meetings.set_raised_hand(cur, user_id=GUEST, meeting_ref=m["public_id"],
                             raised=True)
    meetings.leave_meeting(cur, user_id=GUEST, meeting_ref=m["public_id"])
    cur.execute(f"SELECT raised_hand_at FROM {meetings.PARTICIPANTS_TABLE} "
                f"WHERE user_id=?", (GUEST,))
    assert cur.fetchone()["raised_hand_at"] == ""


# ---------------------------------------------------------------------------
# Recording + truthfulness
# ---------------------------------------------------------------------------

def test_recording_gated_by_flag_and_single_active(cur, monkeypatch):
    m = _instant(cur)
    _reject(lambda: meetings.start_recording(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"]),
        status=403, code="recording_disabled")
    monkeypatch.setenv("PRIVATE_MEETINGS_RECORDING_ENABLED", "1")
    rec = meetings.start_recording(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    assert rec["status"] == meetings.REC_REQUESTED
    _reject(lambda: meetings.start_recording(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"]),
        status=409, code="already_recording")
    # A recording that never reached ACTIVE stops as FAILED, not COMPLETED —
    # the table never claims media that was never captured.
    stopped = meetings.stop_recording(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    assert stopped["status"] == meetings.REC_FAILED
    view = meetings.get_meeting(cur, user_id=HOST, meeting_ref=m["public_id"])
    assert view["recording_active"] is False


def test_recording_active_shows_in_projection_for_everyone(cur, monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_RECORDING_ENABLED", "1")
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    rec = meetings.start_recording(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    meetings.mark_recording_active(
        cur, meeting_ref=m["public_id"], recording_id=rec["id"])
    view = meetings.get_meeting(cur, user_id=GUEST, meeting_ref=m["public_id"])
    assert view["recording_active"] is True  # the visible-indicator source


def test_capabilities_are_truthful(cur):
    m = _instant(cur)
    caps = meetings.get_meeting(
        cur, user_id=HOST, meeting_ref=m["public_id"])["capabilities"]
    assert caps["screen_share"] == {"available": False,
                                    "reason": "not_implemented"}
    assert caps["captions"]["available"] is False
    assert caps["captions"]["reason"] == "provider_required"
    assert caps["recording"]["available"] is False


def test_transcript_derived_artifacts_refused_user_confirmed_saved(cur):
    m = _instant(cur)
    _reject(lambda: meetings.save_artifact(
        cur, user_id=HOST, meeting_ref=m["public_id"], artifact_type="SUMMARY",
        provenance=meetings.PROV_TRANSCRIPT, title="t", content="c"),
        status=409, code="transcript_unavailable")
    saved = meetings.save_artifact(
        cur, user_id=HOST, meeting_ref=m["public_id"], artifact_type="DECISION",
        provenance=meetings.PROV_USER, title="Ship it",
        content="Agreed to ship build 14.")
    assert saved["provenance"] == meetings.PROV_USER
    # Artifacts are owner-scoped: another participant sees their own, not mine.
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    meetings.admit_participant(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"], user_id=GUEST)
    assert meetings.list_artifacts(
        cur, user_id=GUEST, meeting_ref=m["public_id"]) == []
    assert len(meetings.list_artifacts(
        cur, user_id=HOST, meeting_ref=m["public_id"])) == 1


# ---------------------------------------------------------------------------
# Isolation, invites, audit hygiene
# ---------------------------------------------------------------------------

def test_non_participant_reads_are_not_found(cur):
    m = _instant(cur)
    _reject(lambda: meetings.get_meeting(
        cur, user_id=STRANGER, meeting_ref=m["public_id"]),
        status=404, code="not_found")
    listing = meetings.list_meetings(cur, user_id=STRANGER)
    assert listing == {"live": [], "upcoming": [], "recent": []}


def test_invite_accept_lands_in_waiting_room_and_decline_records(cur):
    m = _instant(cur)
    meetings.invite_users(cur, actor_user_id=HOST, meeting_ref=m["public_id"],
                          user_ids=[GUEST, OTHER])
    accepted = meetings.respond_invite(
        cur, user_id=GUEST, meeting_ref=m["public_id"], accept=True)
    assert accepted["invite_status"] == meetings.INVITE_ACCEPTED
    assert accepted["participant"]["state"] == meetings.P_WAITING_ROOM
    declined = meetings.respond_invite(
        cur, user_id=OTHER, meeting_ref=m["public_id"], accept=False)
    assert declined["invite_status"] == meetings.INVITE_DECLINED
    assert declined["participant"]["state"] == meetings.P_DECLINED
    # No pending invite left → responding again refuses.
    _reject(lambda: meetings.respond_invite(
        cur, user_id=GUEST, meeting_ref=m["public_id"], accept=True),
        status=404, code="no_invite")


def test_list_meetings_buckets(cur):
    live = _instant(cur, waiting_room_enabled=False)
    meetings.create_meeting(
        cur, owner_user_id=HOST, title="Later",
        scheduled_start_at="2027-01-01T10:00:00+00:00")
    done = _instant(cur, waiting_room_enabled=False)
    meetings.end_meeting(cur, actor_user_id=HOST, meeting_ref=done["public_id"])
    listing = meetings.list_meetings(cur, user_id=HOST)
    assert [x["public_id"] for x in listing["live"]] == [live["public_id"]]
    assert len(listing["upcoming"]) == 1
    assert [x["public_id"] for x in listing["recent"]] == [done["public_id"]]


def test_audit_rows_are_metadata_only(cur):
    m = _instant(cur, waiting_room_enabled=False)
    meetings.join_meeting(cur, user_id=GUEST, meeting_ref=m["meeting_code"])
    meetings.end_meeting(cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    cur.execute(f"SELECT action, object_type, object_id FROM "
                f"{po_schema.AUDIT_TABLE}")
    rows = [dict(r) for r in cur.fetchall()]
    assert rows, "audit must land"
    for row in rows:
        assert row["action"] in audit.ACTIONS
        assert row["object_type"] == "PRIVATE_MEETING"
        assert row["object_id"].startswith("MEETING:")
        # Never the code, the title, or the channel.
        assert m["meeting_code"] not in row["object_id"]
        assert "pulsesoc-" not in row["object_id"]


def test_invalid_transitions_are_409(cur):
    m = meetings.create_meeting(
        cur, owner_user_id=HOST, scheduled_start_at="2026-09-06T10:00:00+00:00")
    meetings.cancel_meeting(cur, actor_user_id=HOST, meeting_ref=m["public_id"])
    _reject(lambda: meetings.start_meeting(
        cur, actor_user_id=HOST, meeting_ref=m["public_id"]),
        status=410, code="meeting_over")
    _reject(lambda: meetings.join_meeting(
        cur, user_id=HOST, meeting_ref=m["public_id"]),
        status=410, code="meeting_over")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
