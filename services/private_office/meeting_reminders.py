"""Reminder plan for a scheduled private meeting, and the veto that guards it.

Two tables, two jobs. ``private_meeting_reminders`` records *intent* — this
person should hear about this meeting this far ahead of this version of its
schedule — and `failed_email_queue` carries the actual send, held back by a
future ``next_retry_at``. Nothing new runs on a timer: the outbox processors
already skip rows that are not due, so a row due in 2032 simply waits.

The split matters because the two answer different questions. The queue knows
whether an email left the building. The reminder row knows whether it still
*should*, which is a fact about the meeting and can change long after the
queue row was written. Keeping intent separate is what lets the guard refuse a
send without losing the record of why it was planned.

Versioned, not rewritten
------------------------
Reminders are keyed by ``schedule_version``. Moving a meeting bumps the
version on the meeting row, which orphans every reminder planned against the
old one rather than editing them. The orphans are marked CANCELLED and a fresh
set is planned. This is why a reschedule cannot half-apply: the new plan is
additive, and the old plan's rows are inert the moment the version moves,
whether or not the cancellation pass got to them.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from services import db as db_service
from services.private_office import meetings as pm

LOGGER = logging.getLogger("private_office.meeting_reminders")

#: The email_type the outbox rows carry. One type, one validator.
REMINDER_EMAIL_TYPE = "private_meeting_reminder"

#: Participant states that stop being a reason to send. A person who declined
#: or was removed does not get a nudge about a meeting they are not in.
DROPPED_STATES: frozenset[str] = frozenset({
    pm.P_REMOVED, pm.P_BLOCKED, pm.P_DECLINED, pm.P_EXPIRED,
})

#: Meeting statuses a reminder still makes sense for. Anything else — live,
#: ended, cancelled — makes the reminder either redundant or wrong.
REMINDABLE: frozenset[str] = frozenset({pm.ST_DRAFT, pm.ST_SCHEDULED})


def _now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def clean_offsets(values: object) -> tuple[int, ...]:
    """Normalise a caller-supplied reminder policy.

    Deduplicated and sorted far-to-near so the plan reads in the order the
    recipient experiences it. Out-of-range and unparseable entries are dropped
    rather than refused: a reminder policy is a preference, and one bad entry
    should not cost the host the meeting.
    """
    if values is None:
        return pm.DEFAULT_REMINDER_OFFSETS
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        values = [values]
    seen: set[int] = set()
    for raw in list(values)[: pm.MAX_REMINDER_OFFSETS * 4]:
        if isinstance(raw, bool):
            continue
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            continue
        if minutes <= 0 or minutes > pm.MAX_REMINDER_OFFSET_MINUTES:
            continue
        seen.add(minutes)
    return tuple(sorted(seen, reverse=True))[: pm.MAX_REMINDER_OFFSETS]


def recipients(cur, meeting_id: int) -> list[int]:
    """Everyone who should hear about this meeting: host plus live invitees."""
    cur.execute(
        f"SELECT owner_user_id FROM {pm.MEETINGS_TABLE} WHERE id=? LIMIT 1",
        (int(meeting_id),))
    owner_row = pm._row(cur.fetchone())
    out: list[int] = []
    owner = int(owner_row.get("owner_user_id") or 0)
    if owner > 0:
        out.append(owner)
    cur.execute(
        f"SELECT user_id, state FROM {pm.PARTICIPANTS_TABLE} "
        f"WHERE meeting_id=? ORDER BY id ASC",
        (int(meeting_id),))
    for row in cur.fetchall():
        row = pm._row(row)
        user_id = int(row.get("user_id") or 0)
        if user_id <= 0 or user_id in out:
            continue
        if str(row.get("state") or "") in DROPPED_STATES:
            continue
        out.append(user_id)
    return out


def plan_reminders(cur, *, meeting_id: int, offsets: object = None,
                   now: datetime | None = None) -> dict:
    """Write the reminder rows for the meeting's *current* schedule version.

    Offsets whose moment has already passed are recorded SKIPPED rather than
    omitted. The difference is legible: a missing row means nobody planned
    one, and a SKIPPED row means someone booked a meeting for twenty minutes
    from now and the day-before reminder never had anywhere to go. Only the
    second is a normal event, and only the second should be silent.
    """
    pm.ensure_meetings_schema(cur)
    meeting = pm._meeting_by_ref(cur, int(meeting_id))
    if not meeting:
        return {"planned": 0, "skipped": 0, "version": 0}
    start = pm._parse_iso(meeting.get("scheduled_start_at"))
    if start is None or str(meeting.get("status") or "") not in REMINDABLE:
        return {"planned": 0, "skipped": 0,
                "version": int(meeting.get("schedule_version") or 1)}
    version = int(meeting.get("schedule_version") or 1)
    moment = (now or _now()).replace(tzinfo=None, microsecond=0)
    start = start.replace(tzinfo=None)
    stamp = _iso(moment)
    planned = skipped = 0
    for user_id in recipients(cur, int(meeting["id"])):
        for offset in clean_offsets(offsets):
            send_at = start - timedelta(minutes=offset)
            status = pm.R_PENDING if send_at > moment else pm.R_SKIPPED
            detail = "" if status == pm.R_PENDING else "offset_already_elapsed"
            # The UNIQUE key is the whole point: replanning the same version is
            # a no-op, so a retried reschedule cannot double a recipient's mail.
            cur.execute(
                f"""INSERT OR IGNORE INTO {pm.REMINDERS_TABLE}
                (meeting_id, user_id, offset_minutes, schedule_version, send_at,
                 status, kind, idempotency_key, queued_at, resolved_at, detail,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)""",
                (int(meeting["id"]), int(user_id), int(offset), version,
                 _iso(send_at), status, pm.RK_REMINDER,
                 _reminder_key(int(meeting["id"]), user_id, offset, version),
                 stamp if status != pm.R_PENDING else "", detail, stamp, stamp))
            if int(getattr(cur, "rowcount", 0) or 0) <= 0:
                continue
            if status == pm.R_PENDING:
                planned += 1
            else:
                skipped += 1
    LOGGER.info("PM_REMINDERS_PLANNED meeting=%s version=%s planned=%s skipped=%s",
                meeting.get("public_id") or meeting_id, version, planned, skipped)
    return {"planned": planned, "skipped": skipped, "version": version}


def _reminder_key(meeting_id: int, user_id: int, offset: int,
                  version: int) -> str:
    return f"pm-reminder:{meeting_id}:{user_id}:{offset}:{version}"


def cancel_reminders(cur, *, meeting_id: int, user_id: int = 0,
                     before_version: int = 0, reason: str = "") -> int:
    """Retire un-sent reminders. Returns how many moved.

    ``before_version`` retires only plans older than a given version, which is
    what a reschedule wants; omitting it retires the lot, which is what a
    cancellation wants. Already-final rows are left alone — an email that
    has gone cannot be un-sent, and saying otherwise in the table would make
    the log lie.
    """
    pm.ensure_meetings_schema(cur)
    stamp = _iso(_now())
    clause = ["meeting_id=?", "status IN (?, ?)"]
    params: list[object] = [int(meeting_id), pm.R_PENDING, pm.R_SENDING]
    if int(user_id or 0) > 0:
        clause.append("user_id=?")
        params.append(int(user_id))
    if int(before_version or 0) > 0:
        clause.append("schedule_version<?")
        params.append(int(before_version))
    cur.execute(
        f"UPDATE {pm.REMINDERS_TABLE} SET status=?, resolved_at=?, detail=?, "
        f"updated_at=? WHERE {' AND '.join(clause)}",
        [pm.R_CANCELLED, stamp, str(reason or "")[:200], stamp] + params)
    moved = int(getattr(cur, "rowcount", 0) or 0)
    if moved:
        LOGGER.info("PM_REMINDERS_CANCELLED meeting=%s moved=%s reason=%s",
                    meeting_id, moved, reason)
    return moved


def due_reminders(cur, *, now: datetime | None = None,
                  limit: int = 100) -> list[dict]:
    pm.ensure_meetings_schema(cur)
    cur.execute(
        f"SELECT * FROM {pm.REMINDERS_TABLE} WHERE status=? AND send_at<=? "
        f"ORDER BY send_at ASC, id ASC LIMIT ?",
        (pm.R_PENDING, _iso(now or _now()), max(1, min(int(limit or 100), 500))))
    return [pm._row(row) for row in cur.fetchall()]


def mark_reminder(cur, reminder_id: int, status: str, detail: str = "") -> None:
    if status not in pm.REMINDER_STATUSES:
        raise ValueError(f"Unknown reminder status: {status!r}")
    stamp = _iso(_now())
    cur.execute(
        f"UPDATE {pm.REMINDERS_TABLE} SET status=?, detail=?, resolved_at=?, "
        f"updated_at=? WHERE id=?",
        (status, str(detail or "")[:200],
         stamp if status in pm.REMINDER_FINAL else "", stamp, int(reminder_id)))


# --- the send-time veto ------------------------------------------------------


def _reminder_metadata(row: dict) -> dict:
    raw = (row or {}).get("metadata")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def validate_queued_reminder(row: dict) -> tuple[bool, str]:
    """Is this months-old queued reminder still worth sending?

    Registered against :data:`REMINDER_EMAIL_TYPE`, so it runs after the outbox
    claims the row and before the provider is called. Every refusal here is a
    fact that changed after the email was written; none of them were knowable
    at enqueue time, which is why this is asked late rather than early.

    Raising is a refusal too — see the module docstring in
    ``services/email_send_guard.py``. That is deliberate: "I could not check"
    and "I checked and it is fine" must not produce the same email.
    """
    reminder_id = int(_reminder_metadata(row).get("reminder_id") or 0)
    if reminder_id <= 0:
        return False, "no_reminder_ref"
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        pm.ensure_meetings_schema(cur)
        cur.execute(
            f"SELECT * FROM {pm.REMINDERS_TABLE} WHERE id=? LIMIT 1",
            (reminder_id,))
        reminder = pm._row(cur.fetchone())
        if not reminder:
            return False, "reminder_missing"
        status = str(reminder.get("status") or "")
        if status in pm.REMINDER_FINAL:
            # These three are different stories and the reason lands in the
            # queue row's last_error, which is where someone asks "why did this
            # person not get their reminder?" months later. "already_resolved"
            # for all of them would answer the question with the question.
            return False, {
                pm.R_SENT: "already_sent",
                pm.R_CANCELLED: "reminder_cancelled",
                pm.R_SKIPPED: "reminder_skipped",
            }.get(status, "already_resolved")
        meeting = pm._meeting_by_ref(cur, int(reminder.get("meeting_id") or 0))
        if not meeting:
            _resolve(conn, cur, reminder_id, pm.R_CANCELLED, "meeting_missing")
            return False, "meeting_missing"
        meeting_status = str(meeting.get("status") or "")
        if meeting_status == pm.ST_CANCELLED:
            _resolve(conn, cur, reminder_id, pm.R_CANCELLED, "meeting_cancelled")
            return False, "meeting_cancelled"
        if meeting_status not in REMINDABLE:
            _resolve(conn, cur, reminder_id, pm.R_CANCELLED, "meeting_not_pending")
            return False, "meeting_not_pending"
        if int(meeting.get("schedule_version") or 1) != int(
                reminder.get("schedule_version") or 1):
            _resolve(conn, cur, reminder_id, pm.R_CANCELLED, "schedule_changed")
            return False, "schedule_changed"
        start = pm._parse_iso(meeting.get("scheduled_start_at"))
        if start is None:
            return False, "schedule_missing"
        if start.replace(tzinfo=None) <= _now():
            # A reminder that arrives after the meeting started is worse than
            # no reminder: it is an alarm for something already missed.
            _resolve(conn, cur, reminder_id, pm.R_SKIPPED, "meeting_started")
            return False, "meeting_started"
        user_id = int(reminder.get("user_id") or 0)
        if user_id != int(meeting.get("owner_user_id") or 0):
            participant = pm._participant_row(cur, int(meeting["id"]), user_id)
            if not participant:
                _resolve(conn, cur, reminder_id, pm.R_CANCELLED, "uninvited")
                return False, "uninvited"
            if str(participant.get("state") or "") in DROPPED_STATES:
                _resolve(conn, cur, reminder_id, pm.R_CANCELLED, "uninvited")
                return False, "uninvited"
        _resolve(conn, cur, reminder_id, pm.R_SENT, "")
        return True, ""
    finally:
        conn.close()


def _resolve(conn, cur, reminder_id: int, status: str, detail: str) -> None:
    mark_reminder(cur, reminder_id, status, detail)
    conn.commit()


def install() -> None:
    """Attach the validator. Safe to call repeatedly."""
    from services import email_send_guard

    email_send_guard.register(REMINDER_EMAIL_TYPE, validate_queued_reminder)


install()
