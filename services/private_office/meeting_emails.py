"""The four meeting emails: confirmation, reminder, reschedule, cancellation.

One recipient per send, always
------------------------------
Every function here enqueues one outbox row per person. Nothing takes a list of
addresses, and no body ever names the other attendees. A private meeting's
guest list is itself private — a host who invites two people who do not know
each other has not consented to introducing them, and a cc: field would do it
irreversibly. The cost is N rows instead of one; the benefit is that the leak
is not expressible.

Time is stated, never implied
-----------------------------
Bodies carry the wall clock *and* the named zone *and* the UTC instant. The
recipient may be in a different zone from the host, may read the mail months
later, and may be looking at it on a device whose clock is wrong. A bare
"3:00pm" is only unambiguous to the person who typed it.

Branding is borrowed, not forked
--------------------------------
``branded_email_html`` lives in ``bot`` and is resolved lazily, because a
service importing the monolith at module scope would be a cycle. If it cannot
be resolved the body still sends, unstyled — the meeting detail matters and
the chrome does not.
"""

from __future__ import annotations

import html as _html
import logging
from datetime import datetime, timedelta

from services import app_links
from services.private_office import meetings as pm

LOGGER = logging.getLogger("private_office.meeting_emails")

KIND_CONFIRMATION = "CONFIRMATION"
KIND_REMINDER = "REMINDER"
KIND_RESCHEDULE = "RESCHEDULE"
KIND_CANCELLATION = "CANCELLATION"

#: One email_type per kind so the guard can attach a validator to the one that
#: needs it (reminders) without touching the three that are sent immediately.
EMAIL_TYPES = {
    KIND_CONFIRMATION: "private_meeting_confirmation",
    KIND_REMINDER: "private_meeting_reminder",
    KIND_RESCHEDULE: "private_meeting_reschedule",
    KIND_CANCELLATION: "private_meeting_cancellation",
}


def _esc(value: object) -> str:
    return _html.escape(str(value or ""), quote=True)


def _brand(title: str, body_html: str) -> str:
    try:
        import bot

        return bot.branded_email_html(title, body_html)
    except Exception:  # noqa: BLE001
        LOGGER.warning("PM_EMAIL_BRANDING_UNAVAILABLE")
        return f"<h1>{_esc(title)}</h1>{body_html}"


def office_link() -> str:
    """The one deep link these emails carry.

    It names Private Office and nothing finer. The second lock lives inside
    the app, so a link that pointed straight at a meeting would still stop at
    the lock — but it would also put a meeting's identifier in an inbox, in a
    URL, in whatever scans that inbox. Naming the room is enough.
    """
    try:
        return app_links.build_app_link("private_office", source="email")
    except Exception:  # noqa: BLE001
        return "https://pulsesoc.com/pulse/private-office"


def describe_when(meeting: dict) -> dict:
    """Render one instant three ways, so no reader has to guess."""
    start = pm._parse_iso(meeting.get("scheduled_start_at"))
    zone_name = str(meeting.get("scheduled_timezone") or "UTC")
    if start is None:
        return {"local": "", "zone": zone_name, "utc": "", "duration": ""}
    if start.tzinfo is None:
        start = start.replace(tzinfo=pm.timezone.utc)
    try:
        local = start.astimezone(pm.ZoneInfo(zone_name))
    except Exception:  # noqa: BLE001
        local, zone_name = start, "UTC"
    minutes = int(meeting.get("duration_minutes") or 0)
    if minutes >= 60 and minutes % 60 == 0:
        duration = f"{minutes // 60} hour" + ("s" if minutes > 60 else "")
    elif minutes > 60:
        duration = f"{minutes // 60}h {minutes % 60}m"
    else:
        duration = f"{minutes} minutes"
    return {
        "local": local.strftime("%A, %d %B %Y at %H:%M"),
        "zone": zone_name,
        "utc": start.astimezone(pm.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "duration": duration if minutes > 0 else "",
    }


def _lede(kind: str, is_host: bool) -> tuple[str, str]:
    if kind == KIND_CONFIRMATION:
        return ("Your meeting is scheduled" if is_host else "You are invited",
                "This meeting is in your Private Office."
                if is_host else "The host has invited you to a private meeting.")
    if kind == KIND_REMINDER:
        return ("Your meeting is coming up",
                "A reminder about a meeting in your Private Office.")
    if kind == KIND_RESCHEDULE:
        return ("This meeting has moved",
                "The time below replaces the one you were sent before.")
    return ("This meeting was cancelled",
            "Nothing further is needed from you.")


def compose(kind: str, *, meeting: dict, is_host: bool = False) -> tuple[str, str, str]:
    """``(subject, text_body, html_body)`` for one recipient.

    Takes no recipient identity beyond "is this the host", because nothing in
    the body should vary by person except that. Personalising further would
    mean carrying names into an outbox row that may sit for months.
    """
    if kind not in EMAIL_TYPES:
        raise ValueError(f"Unknown meeting email kind: {kind!r}")
    title = str(meeting.get("title") or "").strip() or "Private meeting"
    when = describe_when(meeting)
    heading, lede = _lede(kind, is_host)
    verb = {
        KIND_CONFIRMATION: "Scheduled",
        KIND_REMINDER: "Reminder",
        KIND_RESCHEDULE: "Updated",
        KIND_CANCELLATION: "Cancelled",
    }[kind]
    subject = f"{verb}: {title}"[:240]
    link = office_link()
    agenda = str(meeting.get("agenda") or "").strip()

    lines = [lede, "", title]
    if when["local"]:
        lines.append(f"{when['local']} ({when['zone']})")
        lines.append(f"That is {when['utc']}.")
    if when["duration"] and kind != KIND_CANCELLATION:
        lines.append(f"Duration: {when['duration']}")
    if agenda and kind != KIND_CANCELLATION:
        lines += ["", "Agenda:", agenda]
    if kind != KIND_CANCELLATION:
        lines += ["", f"Open Private Office: {link}"]
    lines += ["", "You are receiving this because you are on this meeting.",
              "Support: support@pulsesoc.com"]
    text = "\n".join(lines)

    rows = [f"<p>{_esc(lede)}</p>",
            f"<p style=\"font-size:18px;color:#ffffff\"><strong>{_esc(title)}</strong></p>"]
    if when["local"]:
        rows.append(
            f"<p><strong>{_esc(when['local'])}</strong><br>"
            f"<span style=\"color:#9fb5c0\">{_esc(when['zone'])} &middot; "
            f"{_esc(when['utc'])}</span></p>")
    if when["duration"] and kind != KIND_CANCELLATION:
        rows.append(f"<p>Duration: {_esc(when['duration'])}</p>")
    if agenda and kind != KIND_CANCELLATION:
        rows.append(f"<p><strong>Agenda</strong><br>{_esc(agenda)}</p>")
    if kind != KIND_CANCELLATION:
        rows.append(
            f"<p><a href=\"{_esc(link)}\" style=\"color:#36e58f\">"
            f"Open Private Office</a></p>")
    rows.append("<p style=\"color:#9fb5c0;font-size:13px\">You are receiving "
                "this because you are on this meeting.</p>")
    return subject, text, _brand(heading, "".join(rows))


def _recipient_email(cur, user_id: int) -> str:
    try:
        cur.execute(
            "SELECT email FROM users WHERE user_id=? OR id=? LIMIT 1",
            (int(user_id), int(user_id)))
        row = pm._row(cur.fetchone())
    except Exception:  # noqa: BLE001
        LOGGER.exception("PM_EMAIL_RECIPIENT_LOOKUP_FAILED user=%s", user_id)
        return ""
    return str(row.get("email") or "").strip()


def enqueue(cur, *, kind: str, meeting: dict, user_id: int,
            send_after: str = "", idempotency_key: str = "",
            metadata: dict | None = None) -> dict:
    """Put one meeting email in the shared outbox for one person."""
    to_email = _recipient_email(cur, int(user_id))
    if not to_email:
        return {"ok": False, "status": "no_email"}
    is_host = int(user_id) == int(meeting.get("owner_user_id") or 0)
    subject, text, html = compose(kind, meeting=meeting, is_host=is_host)
    payload = dict(metadata or {})
    payload.update({
        "meeting_public_id": str(meeting.get("public_id") or ""),
        "schedule_version": int(meeting.get("schedule_version") or 1),
        "meeting_email_kind": kind,
    })
    if idempotency_key:
        payload["email_idempotency_key"] = idempotency_key[:240]
    from services import notification_service

    return notification_service._queue_email_job(
        int(user_id), to_email, subject, html, text_body=text,
        email_type=EMAIL_TYPES[kind], metadata=payload,
        send_after=send_after)


def announce(cur, *, kind: str, meeting: dict, user_ids=None) -> int:
    """Send one immediate meeting email to each person, separately."""
    meeting_id = int(meeting.get("id") or 0)
    if user_ids is None:
        from services.private_office import meeting_reminders

        user_ids = meeting_reminders.recipients(cur, meeting_id)
    version = int(meeting.get("schedule_version") or 1)
    sent = 0
    for user_id in user_ids or []:
        try:
            result = enqueue(
                cur, kind=kind, meeting=meeting, user_id=int(user_id),
                idempotency_key=f"pm-{kind.lower()}:{meeting_id}:{int(user_id)}:{version}")
        except Exception:  # noqa: BLE001
            LOGGER.exception("PM_EMAIL_ENQUEUE_FAILED kind=%s meeting=%s",
                             kind, meeting_id)
            continue
        if result.get("ok"):
            sent += 1
    LOGGER.info("PM_EMAIL_ANNOUNCED kind=%s meeting=%s recipients=%s",
                kind, meeting_id, sent)
    return sent


def enqueue_reminders(cur, *, meeting_id: int = 0, now: datetime | None = None,
                      due_within_minutes: int | None = None,
                      limit: int = 200) -> int:
    """Hand planned reminders to the outbox, each held until its own moment.

    The default enqueues every un-queued reminder regardless of how far off it
    is, and lets ``next_retry_at`` do the waiting. That is the whole durability
    argument: once this runs, a meeting booked for 2035 will remind its
    attendees in 2035 even though nothing in this subsystem runs on a timer and
    no process stays alive in between. The outbox is already durable and
    already retried; borrowing it beats standing up a scheduler that would need
    to be as reliable and would not be.

    ``due_within_minutes`` narrows to reminders coming up inside a window, for
    a caller that wants to drip rather than pre-load.
    """
    from services.private_office import meeting_reminders

    moment = (now or datetime.utcnow()).replace(microsecond=0)
    clause = ["status=?", "queued_at=''"]
    params: list[object] = [pm.R_PENDING]
    if due_within_minutes is not None:
        clause.append("send_at<=?")
        params.append(
            (moment + timedelta(minutes=max(0, int(due_within_minutes)))).isoformat())
    if int(meeting_id or 0) > 0:
        clause.append("meeting_id=?")
        params.append(int(meeting_id))
    params.append(max(1, min(int(limit or 200), 500)))
    cur.execute(
        f"SELECT * FROM {pm.REMINDERS_TABLE} WHERE {' AND '.join(clause)} "
        f"ORDER BY send_at ASC, id ASC LIMIT ?",
        tuple(params))
    rows = [pm._row(row) for row in cur.fetchall()]
    queued = 0
    for reminder in rows:
        meeting = pm._meeting_by_ref(cur, int(reminder.get("meeting_id") or 0))
        if not meeting:
            continue
        try:
            result = enqueue(
                cur, kind=KIND_REMINDER, meeting=meeting,
                user_id=int(reminder.get("user_id") or 0),
                send_after=str(reminder.get("send_at") or ""),
                idempotency_key=str(reminder.get("idempotency_key") or ""),
                metadata={"reminder_id": int(reminder.get("id") or 0),
                          "offset_minutes": int(reminder.get("offset_minutes") or 0)})
        except Exception:  # noqa: BLE001
            LOGGER.exception("PM_REMINDER_ENQUEUE_FAILED reminder=%s",
                             reminder.get("id"))
            continue
        if not result.get("ok"):
            # No address is not a failure to retry -- it is a permanent answer,
            # and leaving the row PENDING would re-ask it every sweep forever.
            meeting_reminders.mark_reminder(
                cur, int(reminder["id"]), pm.R_SKIPPED, "no_recipient_email")
            continue
        cur.execute(
            f"UPDATE {pm.REMINDERS_TABLE} SET queued_at=?, updated_at=? WHERE id=?",
            (moment.isoformat(), moment.isoformat(), int(reminder["id"])))
        queued += 1
    return queued
