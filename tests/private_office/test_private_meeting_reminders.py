"""Private Meetings — the reminder plan and the veto that guards it.

What these tests defend
-----------------------
* **A plan is per schedule version.** Moving a meeting orphans the old plan
  instead of editing it, so a reschedule cannot half-apply and a survivor of
  the previous version can never be sent.
* **Elapsed offsets are recorded, not dropped.** Booking a meeting for twenty
  minutes from now leaves a SKIPPED day-before row. "Nobody planned one" and
  "there was nowhere to put one" must not look the same in the table.
* **The veto runs late and fails closed.** A reminder queued in 2026 for 2035
  is re-checked at send time against the world as it is then: cancelled,
  moved, uninvited, already sent, already started. A validator that cannot
  answer refuses.
* **One recipient per email.** No body names another attendee and no send
  carries more than one address — a private meeting's guest list is private.
* **The far future is ordinary.** A 2035 reminder is held by the outbox's own
  next_retry_at, with nothing running in between.
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import email_send_guard  # noqa: E402
from services import pulsesoc_communications_engine as eng  # noqa: E402
from services.private_office import meeting_emails as pm_mail  # noqa: E402
from services.private_office import meeting_reminders as pm_rem  # noqa: E402
from services.private_office import meetings  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402

HOST = 101
GUEST = 202
STRANGER = 303


def _future(days=30, hour=10):
    moment = datetime.now(timezone.utc).replace(
        hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return moment.isoformat()


@pytest.fixture()
def cur(monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_ENABLED", "1")
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
    # The canonical shape, copied from bot.py's `CREATE TABLE users`: the key
    # is `user_id` and there is no `id`. This fixture used to declare one, and
    # that single invented column is why the suite could not see the defect
    # that lost two real bookings in production — the recipient lookup asked
    # for `id`, every test had one, and PostgreSQL did not.
    cursor.execute(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, "
        "display_name TEXT, email TEXT)")
    for user_id, email in ((HOST, "host@example.com"), (GUEST, "guest@example.com"),
                           (STRANGER, "stranger@example.com")):
        cursor.execute("INSERT INTO users (user_id, email) VALUES (?, ?)",
                       (user_id, email))
    yield cursor
    conn.close()
    meetings.reset_meetings_schema_cache()


@pytest.fixture()
def outbox(monkeypatch):
    """Capture every enqueue instead of writing the real queue."""
    captured = []

    def fake_queue(user_id, to_email, subject, html_body, text_body="",
                   email_type="transactional", metadata=None, notification_id=0,
                   send_after=""):
        captured.append({
            "user_id": user_id, "to_email": to_email, "subject": subject,
            "html": html_body, "text": text_body, "email_type": email_type,
            "metadata": dict(metadata or {}), "send_after": send_after,
        })
        return {"ok": True, "status": "queued", "queue_id": len(captured),
                "send_after": send_after}

    from services import notification_service

    monkeypatch.setattr(notification_service, "_queue_email_job", fake_queue)
    return captured


def _schedule(cur, *, days=30, hour=10, duration=60, offsets=None, guests=()):
    meeting = meetings.create_meeting(
        cur, owner_user_id=HOST, title="Board review",
        scheduled_start_at=_future(days=days, hour=hour),
        duration_minutes=duration, timezone_name="UTC",
        reminder_offsets=offsets)
    if guests:
        meetings.invite_users(cur, actor_user_id=HOST,
                              meeting_ref=meeting["public_id"],
                              user_ids=list(guests))
    return meeting


def _row_id(cur, meeting):
    """The internal id. Deliberately not in the projected payload — the wire
    identity is public_id — so tests that need the FK look it up."""
    cur.execute(f"SELECT id FROM {meetings.MEETINGS_TABLE} WHERE public_id=?",
                (meeting["public_id"],))
    return int(cur.fetchone()[0])


def _reminders(cur, meeting_public_id=None):
    cur.execute(f"SELECT * FROM {meetings.REMINDERS_TABLE} ORDER BY id ASC")
    return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def test_scheduling_plans_the_default_offsets_for_the_host(cur, outbox):
    _schedule(cur)
    rows = _reminders(cur)
    assert {int(r["offset_minutes"]) for r in rows} == set(
        meetings.DEFAULT_REMINDER_OFFSETS)
    assert {int(r["user_id"]) for r in rows} == {HOST}
    assert {r["status"] for r in rows} == {meetings.R_PENDING}


def test_a_guest_gets_their_own_plan(cur, outbox):
    _schedule(cur, guests=(GUEST,))
    rows = _reminders(cur)
    assert {int(r["user_id"]) for r in rows} == {HOST, GUEST}
    assert len(rows) == 2 * len(meetings.DEFAULT_REMINDER_OFFSETS)


def test_send_at_is_the_start_minus_the_offset(cur, outbox):
    meeting = _schedule(cur, days=40)
    start = meetings._parse_iso(meeting["scheduled_start_at"]).replace(tzinfo=None)
    for row in _reminders(cur):
        expected = start - timedelta(minutes=int(row["offset_minutes"]))
        assert row["send_at"] == expected.replace(microsecond=0).isoformat()


def test_an_offset_already_in_the_past_is_skipped_not_dropped(cur, outbox):
    """Booked for tomorrow, so the 24-hour reminder has nowhere to go.

    It must still leave a row. A missing row means nobody planned one; a
    SKIPPED row means the plan was made and one leg of it had already expired,
    which is a normal thing that should be legible rather than invisible.
    """
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(
        minute=0, second=0, microsecond=0)
    meetings.create_meeting(
        cur, owner_user_id=HOST, title="Soon",
        scheduled_start_at=soon.isoformat(), duration_minutes=30,
        timezone_name="UTC")
    rows = {int(r["offset_minutes"]): r for r in _reminders(cur)}
    assert rows[1440]["status"] == meetings.R_SKIPPED
    assert rows[1440]["detail"] == "offset_already_elapsed"
    assert rows[15]["status"] == meetings.R_PENDING


def test_replanning_the_same_version_is_a_no_op(cur, outbox):
    meeting = _schedule(cur)
    before = len(_reminders(cur))
    pm_rem.plan_reminders(cur, meeting_id=_row_id(cur, meeting))
    pm_rem.plan_reminders(cur, meeting_id=_row_id(cur, meeting))
    assert len(_reminders(cur)) == before


def test_a_custom_reminder_policy_is_honoured(cur, outbox):
    _schedule(cur, offsets=[2880, 30])
    assert {int(r["offset_minutes"]) for r in _reminders(cur)} == {2880, 30}


def test_a_junk_policy_entry_is_dropped_not_fatal(cur, outbox):
    _schedule(cur, offsets=[60, "45", -5, 0, "soon", True, None, 10**9])
    assert {int(r["offset_minutes"]) for r in _reminders(cur)} == {60, 45}


def test_the_policy_is_capped(cur, outbox):
    _schedule(cur, offsets=list(range(1, 200)))
    assert len({int(r["offset_minutes"]) for r in _reminders(cur)}) \
        == meetings.MAX_REMINDER_OFFSETS


def test_an_instant_meeting_plans_nothing(cur, outbox):
    meetings.create_meeting(cur, owner_user_id=HOST, instant=True)
    assert _reminders(cur) == []


# ---------------------------------------------------------------------------
# Version invalidation
# ---------------------------------------------------------------------------


def test_a_reschedule_retires_the_old_plan_and_lays_a_new_one(cur, outbox):
    meeting = _schedule(cur, days=30)
    meetings.reschedule_meeting(cur, actor_user_id=HOST,
                                meeting_ref=meeting["public_id"],
                                scheduled_start_at=_future(days=45),
                                timezone_name="UTC")
    rows = _reminders(cur)
    old = [r for r in rows if int(r["schedule_version"]) == 1]
    new = [r for r in rows if int(r["schedule_version"]) == 2]
    assert old and new
    assert {r["status"] for r in old} == {meetings.R_CANCELLED}
    assert {r["detail"] for r in old} == {"schedule_changed"}
    assert {r["status"] for r in new} == {meetings.R_PENDING}


def test_a_title_only_edit_leaves_the_plan_alone(cur, outbox):
    """Renaming a meeting does not move it, so nobody's reminder changes."""
    meeting = _schedule(cur)
    before = [(r["send_at"], r["status"]) for r in _reminders(cur)]
    meetings.reschedule_meeting(cur, actor_user_id=HOST,
                                meeting_ref=meeting["public_id"],
                                title="Renamed")
    assert [(r["send_at"], r["status"]) for r in _reminders(cur)] == before


def test_cancelling_the_meeting_cancels_every_pending_reminder(cur, outbox):
    meeting = _schedule(cur, guests=(GUEST,))
    meetings.cancel_meeting(cur, actor_user_id=HOST,
                            meeting_ref=meeting["public_id"])
    assert {r["status"] for r in _reminders(cur)} == {meetings.R_CANCELLED}


def test_removing_a_guest_cancels_only_their_reminders(cur, outbox):
    meeting = _schedule(cur, guests=(GUEST,))
    meetings.remove_participant(cur, actor_user_id=HOST,
                                meeting_ref=meeting["public_id"],
                                user_id=GUEST)
    rows = _reminders(cur)
    assert {r["status"] for r in rows if int(r["user_id"]) == GUEST} \
        == {meetings.R_CANCELLED}
    assert {r["status"] for r in rows if int(r["user_id"]) == HOST} \
        == {meetings.R_PENDING}


def test_a_late_invite_gets_the_reminders_that_have_not_gone(cur, outbox):
    meeting = _schedule(cur)
    meetings.invite_users(cur, actor_user_id=HOST,
                          meeting_ref=meeting["public_id"], user_ids=[GUEST])
    guest_rows = [r for r in _reminders(cur) if int(r["user_id"]) == GUEST]
    assert len(guest_rows) == len(meetings.DEFAULT_REMINDER_OFFSETS)


def test_cancel_reminders_leaves_already_sent_rows_alone(cur, outbox):
    """A sent email cannot be un-sent, so the table must not claim otherwise."""
    meeting = _schedule(cur)
    rows = _reminders(cur)
    pm_rem.mark_reminder(cur, int(rows[0]["id"]), meetings.R_SENT)
    pm_rem.cancel_reminders(cur, meeting_id=_row_id(cur, meeting), reason="test")
    after = {int(r["id"]): r["status"] for r in _reminders(cur)}
    assert after[int(rows[0]["id"])] == meetings.R_SENT
    assert after[int(rows[1]["id"])] == meetings.R_CANCELLED


# ---------------------------------------------------------------------------
# The outbox hand-off
# ---------------------------------------------------------------------------


def test_every_reminder_is_queued_held_until_its_own_moment(cur, outbox):
    _schedule(cur, days=30)
    reminders = {int(r["offset_minutes"]): r for r in _reminders(cur)}
    queued = [m for m in outbox if m["email_type"] == "private_meeting_reminder"]
    assert len(queued) == len(reminders)
    for message in queued:
        offset = int(message["metadata"]["offset_minutes"])
        assert message["send_after"] == reminders[offset]["send_at"]


def test_a_2035_reminder_is_queued_now_and_held_for_years(cur, outbox):
    """The durability claim, stated as an assertion.

    Nothing in this subsystem runs on a timer. The row goes in today with a
    next_retry_at nine years out, and the outbox's own due-filter is what
    waits.
    """
    far = datetime(2035, 7, 4, 15, 0, tzinfo=timezone.utc)
    meetings.create_meeting(
        cur, owner_user_id=HOST, title="Long game",
        scheduled_start_at=far.isoformat(), duration_minutes=60,
        timezone_name="UTC", reminder_offsets=[1440])
    message = [m for m in outbox
               if m["email_type"] == "private_meeting_reminder"][0]
    assert message["send_after"] == "2035-07-03T15:00:00"
    assert message["send_after"] > datetime.utcnow().isoformat()


def test_a_queued_reminder_is_not_queued_twice(cur, outbox):
    meeting = _schedule(cur)
    before = len(outbox)
    pm_mail.enqueue_reminders(cur, meeting_id=_row_id(cur, meeting))
    assert len(outbox) == before


def test_each_message_carries_exactly_one_address(cur, outbox):
    _schedule(cur, guests=(GUEST,))
    for message in outbox:
        assert "," not in message["to_email"]
        assert ";" not in message["to_email"]


def test_no_body_names_another_attendee(cur, outbox):
    """The guest list is private. A host who invited two strangers has not
    agreed to introduce them, and an email cannot be un-sent."""
    _schedule(cur, guests=(GUEST, STRANGER))
    for message in outbox:
        others = {"host@example.com", "guest@example.com",
                  "stranger@example.com"} - {message["to_email"]}
        for address in others:
            assert address not in message["text"]
            assert address not in message["html"]


def test_a_reminder_with_no_address_is_skipped_permanently(cur, outbox):
    """A missing address is a permanent answer, not a retryable failure."""
    meeting = _schedule(cur)
    cur.execute("UPDATE users SET email='' WHERE user_id=?", (HOST,))
    cur.execute(f"UPDATE {meetings.REMINDERS_TABLE} SET queued_at=''")
    pm_mail.enqueue_reminders(cur, meeting_id=_row_id(cur, meeting))
    assert {r["status"] for r in _reminders(cur)} == {meetings.R_SKIPPED}
    assert {r["detail"] for r in _reminders(cur)} == {"no_recipient_email"}


# ---------------------------------------------------------------------------
# The send-time veto
# ---------------------------------------------------------------------------


@pytest.fixture()
def guarded(cur, monkeypatch):
    """Point the validator at this test's in-memory database."""
    class _Conn:
        def cursor(self):
            return cur

        def commit(self):
            pass

        def close(self):
            pass

    from services import db as db_service

    monkeypatch.setattr(db_service, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(meetings, "ensure_meetings_schema",
                        lambda *a, **k: None)
    return cur


def _queue_row(reminder_id, email_type="private_meeting_reminder"):
    return {"email_type": email_type,
            "metadata": json.dumps({"reminder_id": int(reminder_id)})}


def test_a_still_valid_reminder_is_allowed_and_marked_sent(cur, outbox, guarded):
    _schedule(cur)
    reminder = _reminders(cur)[0]
    ok, reason = email_send_guard.may_send(_queue_row(reminder["id"]))
    assert (ok, reason) == (True, "")
    after = {int(r["id"]): r["status"] for r in _reminders(cur)}
    assert after[int(reminder["id"])] == meetings.R_SENT


def test_a_cancelled_meeting_refuses_its_reminder(cur, outbox, guarded):
    meeting = _schedule(cur)
    reminder = _reminders(cur)[0]
    meetings.cancel_meeting(cur, actor_user_id=HOST,
                            meeting_ref=meeting["public_id"])
    ok, reason = email_send_guard.may_send(_queue_row(reminder["id"]))
    assert (ok, reason) == (False, "reminder_cancelled")


def test_the_guard_still_refuses_when_the_cancel_pass_missed_the_row(cur, outbox,
                                                                    guarded):
    """The race the guard exists for.

    Cancellation retires the plan, so normally the row is already CANCELLED by
    the time the outbox looks at it. But a cancellation that commits while the
    row is in flight — claimed, mid-send — leaves a PENDING reminder against a
    cancelled meeting. Broadcast invalidation cannot close that window; asking
    at send time can.
    """
    meeting = _schedule(cur)
    reminder = _reminders(cur)[0]
    meetings.cancel_meeting(cur, actor_user_id=HOST,
                            meeting_ref=meeting["public_id"])
    cur.execute(f"UPDATE {meetings.REMINDERS_TABLE} SET status=? WHERE id=?",
                (meetings.R_PENDING, int(reminder["id"])))
    ok, reason = email_send_guard.may_send(_queue_row(reminder["id"]))
    assert (ok, reason) == (False, "meeting_cancelled")


def test_a_rescheduled_meeting_refuses_the_old_version(cur, outbox, guarded):
    """The exact race the version exists for: the row was claimed against a
    schedule that has since moved."""
    meeting = _schedule(cur)
    stale = _reminders(cur)[0]
    meetings.reschedule_meeting(cur, actor_user_id=HOST,
                                meeting_ref=meeting["public_id"],
                                scheduled_start_at=_future(days=60),
                                timezone_name="UTC")
    cur.execute(f"UPDATE {meetings.REMINDERS_TABLE} SET status=? WHERE id=?",
                (meetings.R_PENDING, int(stale["id"])))
    ok, reason = email_send_guard.may_send(_queue_row(stale["id"]))
    assert (ok, reason) == (False, "schedule_changed")


def test_an_uninvited_recipient_is_refused(cur, outbox, guarded):
    meeting = _schedule(cur, guests=(GUEST,))
    reminder = [r for r in _reminders(cur) if int(r["user_id"]) == GUEST][0]
    meetings.remove_participant(cur, actor_user_id=HOST,
                                meeting_ref=meeting["public_id"],
                                user_id=GUEST)
    cur.execute(f"UPDATE {meetings.REMINDERS_TABLE} SET status=? WHERE id=?",
                (meetings.R_PENDING, int(reminder["id"])))
    ok, reason = email_send_guard.may_send(_queue_row(reminder["id"]))
    assert (ok, reason) == (False, "uninvited")


def test_a_reminder_for_a_meeting_already_under_way_is_refused(cur, outbox, guarded):
    """Late is worse than never: an alarm for something already missed."""
    meeting = _schedule(cur)
    reminder = _reminders(cur)[0]
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cur.execute(f"UPDATE {meetings.MEETINGS_TABLE} SET scheduled_start_at=? "
                f"WHERE id=?", (past, _row_id(cur, meeting)))
    ok, reason = email_send_guard.may_send(_queue_row(reminder["id"]))
    assert (ok, reason) == (False, "meeting_started")
    assert _reminders(cur)[0]["status"] == meetings.R_SKIPPED


def test_an_already_sent_reminder_is_not_sent_again(cur, outbox, guarded):
    _schedule(cur)
    reminder = _reminders(cur)[0]
    pm_rem.mark_reminder(cur, int(reminder["id"]), meetings.R_SENT)
    ok, reason = email_send_guard.may_send(_queue_row(reminder["id"]))
    assert (ok, reason) == (False, "already_sent")


def test_a_vanished_meeting_refuses(cur, outbox, guarded):
    meeting = _schedule(cur)
    reminder = _reminders(cur)[0]
    cur.execute(f"DELETE FROM {meetings.MEETINGS_TABLE} WHERE id=?",
                (_row_id(cur, meeting),))
    ok, reason = email_send_guard.may_send(_queue_row(reminder["id"]))
    assert (ok, reason) == (False, "meeting_missing")


def test_a_queue_row_with_no_reminder_reference_refuses(cur, outbox, guarded):
    ok, reason = email_send_guard.may_send(
        {"email_type": "private_meeting_reminder", "metadata": "{}"})
    assert (ok, reason) == (False, "no_reminder_ref")


def test_a_validator_that_raises_refuses(monkeypatch):
    """Fail closed. "I could not check" must not produce the same email as
    "I checked and it is fine"."""
    email_send_guard.register("boom", lambda row: 1 / 0)
    try:
        assert email_send_guard.may_send({"email_type": "boom"}) == (
            False, "validator_error")
    finally:
        email_send_guard.unregister("boom")


def test_an_email_type_with_no_validator_is_untouched():
    """Everything the platform already sends must be unaffected by all this."""
    assert email_send_guard.may_send({"email_type": "welcome"}) == (True, "")
    assert email_send_guard.may_send({}) == (True, "")


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


def test_the_body_states_the_wall_clock_the_zone_and_the_utc_instant(cur, outbox):
    meetings.create_meeting(
        cur, owner_user_id=HOST, title="Tokyo sync",
        scheduled_start_at="2033-03-01T09:00:00",
        timezone_name="Asia/Tokyo", duration_minutes=45)
    body = [m for m in outbox
            if m["email_type"] == "private_meeting_confirmation"][0]["text"]
    assert "09:00" in body
    assert "Asia/Tokyo" in body
    assert "2033-03-01 00:00 UTC" in body


def test_the_deep_link_names_the_room_not_the_meeting(cur, outbox):
    """A link that named the meeting would put its identifier in an inbox, and
    it would still stop at the second lock anyway."""
    meeting = _schedule(cur)
    body = outbox[0]["text"]
    assert "/pulse/private-office" in body
    assert meeting["public_id"] not in body
    assert meeting.get("meeting_code", "") not in body or not meeting.get("meeting_code")


def test_no_link_in_any_email_carries_a_credential(cur, outbox):
    """The mutation: a "convenient" one-tap link.

    Anything that made the link work without the recipient proving who they
    are would have moved the meeting's access control into a forwarded email
    and past the Office second lock. The link is an address, not a key —
    whoever opens it still logs in and still gets gated.
    """
    meeting = _schedule(cur, guests=(GUEST,))
    outbox.clear()
    meetings.reschedule_meeting(cur, actor_user_id=HOST,
                                meeting_ref=meeting["public_id"],
                                scheduled_start_at=_future(days=60),
                                timezone_name="UTC")
    # An allowlist, not a blocklist. A blocklist of scary-sounding parameter
    # names passes the day someone adds `?j=` -- this fails on anything the
    # app-link builder did not put there on purpose.
    permitted = {"pulse_app", "pulse_src"}
    links = []
    for message in outbox:
        for field in ("text", "html"):
            links += re.findall(r"https?://[^\s\"'<>]+",
                                html_unescape(message[field]))
    assert links, "the emails carry no links at all -- test proves nothing"
    for link in links:
        names = {pair.split("=")[0]
                 for pair in urlparse(link).query.split("&") if pair}
        assert names <= permitted, (link, names - permitted)


def test_the_cancellation_email_carries_no_join_link(cur, outbox):
    meeting = _schedule(cur)
    outbox.clear()
    meetings.cancel_meeting(cur, actor_user_id=HOST,
                            meeting_ref=meeting["public_id"])
    body = outbox[0]
    assert body["email_type"] == "private_meeting_cancellation"
    assert "Open Private Office" not in body["text"]


def test_a_reschedule_mails_everyone_once(cur, outbox):
    meeting = _schedule(cur, guests=(GUEST,))
    outbox.clear()
    meetings.reschedule_meeting(cur, actor_user_id=HOST,
                                meeting_ref=meeting["public_id"],
                                scheduled_start_at=_future(days=50),
                                timezone_name="UTC")
    updates = [m for m in outbox
               if m["email_type"] == "private_meeting_reschedule"]
    assert sorted(m["to_email"] for m in updates) == [
        "guest@example.com", "host@example.com"]


def test_a_hostile_title_cannot_escape_the_html(cur, outbox):
    meetings.create_meeting(
        cur, owner_user_id=HOST, title="<img src=x onerror=alert(1)>",
        scheduled_start_at=_future(), duration_minutes=30, timezone_name="UTC")
    html = outbox[0]["html"]
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_the_host_and_a_guest_get_different_ledes(cur, outbox):
    _schedule(cur, guests=(GUEST,))
    by_address = {m["to_email"]: m["text"] for m in outbox
                  if m["email_type"] == "private_meeting_confirmation"}
    assert "Your meeting is scheduled" not in by_address["guest@example.com"]
    assert "invited you" in by_address["guest@example.com"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
