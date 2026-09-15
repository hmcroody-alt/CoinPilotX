"""Private Meetings — Zoom-class multi-guest meetings on the canonical call engine.

Authority model
---------------
PulseSoc owns the meeting: identity, admission, roles, lock state, lifecycle.
Agora is transport only, and ``rtc_uid`` is never an identity authority — it is
derived from ``user_id`` and verified against the participant row on every
token mint. The meeting rides ONE ``communication_calls`` row of scope
``"room"`` owned by this module; RTC presence is the engine's
``communication_call_participants`` table, and **admission is the act of
inserting that row**. A waiting-room occupant has no participant row, so the
engine's ``_require_call_access`` refuses a token before Agora is ever
consulted — least privilege by construction, not by check.

New Agora engine owners introduced: 0. New microphone / camera / audio-session
owners: 0. The native side reuses ``callSessionStore`` untouched, keyed by the
meeting's ``call_public_id``.

Truthfulness is structural. Transcription has no configured provider, so
``TRANSCRIPT_DERIVED`` artifacts are refused while
:func:`transcription_configured` is False. Screen share requires a third
engine owner (a ReplayKit broadcast-upload extension) and was escalated per
mission rule "if a new owner is required, STOP AND REPORT" — both surface as
truthful capability states in :func:`get_meeting`, never as fake UI.

One logical participant per (meeting, user), forever. Reconnects, rejoins
after leaving, and re-admissions all mutate the same row —
``UNIQUE(meeting_id, user_id)`` makes a duplicate tile a constraint violation
rather than a code review comment.

Fail-closed: ``PRIVATE_MEETINGS_ENABLED`` defaults to **off**, and every write
path re-checks it. Blocking consults BOTH systems (``comm_v2_blocks`` and
``blocked_users``); a block in either direction refuses without leaking that
the meeting exists.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pulse_communications_v2 import service as comm_service
from services import pulsesoc_communications_engine as call_engine
from services.private_office import audit
from services.private_office import schema
from services.private_office import telemetry as _telemetry

LOGGER = logging.getLogger("private_office.meetings")

MEETINGS_TABLE = "private_meetings"
PARTICIPANTS_TABLE = "private_meeting_participants"
INVITES_TABLE = "private_meeting_invites"
MESSAGES_TABLE = "private_meeting_messages"
RECORDINGS_TABLE = "private_meeting_recordings"
MEETING_ARTIFACT_TABLE = "private_meeting_artifacts"
REMINDERS_TABLE = "private_meeting_reminders"

# ---------------------------------------------------------------------------
# Vocabulary — closed sets, same discipline as audit.ACTIONS
# ---------------------------------------------------------------------------

ST_DRAFT = "DRAFT"
ST_SCHEDULED = "SCHEDULED"
ST_STARTING = "STARTING"
ST_WAITING = "WAITING"
ST_LIVE = "LIVE"
ST_ENDING = "ENDING"
ST_ENDED = "ENDED"
ST_CANCELLED = "CANCELLED"
ST_FAILED = "FAILED"

MEETING_STATUSES: frozenset[str] = frozenset({
    ST_DRAFT, ST_SCHEDULED, ST_STARTING, ST_WAITING, ST_LIVE,
    ST_ENDING, ST_ENDED, ST_CANCELLED, ST_FAILED,
})
MEETING_FINAL: frozenset[str] = frozenset({ST_ENDED, ST_CANCELLED, ST_FAILED})

MEETING_TRANSITIONS: dict[str, frozenset[str]] = {
    ST_DRAFT: frozenset({ST_SCHEDULED, ST_STARTING, ST_CANCELLED}),
    ST_SCHEDULED: frozenset({ST_STARTING, ST_WAITING, ST_CANCELLED, ST_FAILED}),
    ST_STARTING: frozenset({ST_LIVE, ST_FAILED}),
    ST_WAITING: frozenset({ST_STARTING, ST_CANCELLED, ST_FAILED}),
    ST_LIVE: frozenset({ST_ENDING, ST_ENDED, ST_FAILED}),
    ST_ENDING: frozenset({ST_ENDED}),
    ST_ENDED: frozenset(),
    ST_CANCELLED: frozenset(),
    ST_FAILED: frozenset(),
}

P_INVITED = "INVITED"
P_RINGING = "RINGING"
P_WAITING_ROOM = "WAITING_ROOM"
P_ADMITTED = "ADMITTED"
P_JOINING = "JOINING"
P_JOINED = "JOINED"
P_RECONNECTING = "RECONNECTING"
P_LEFT = "LEFT"
P_REMOVED = "REMOVED"
P_DECLINED = "DECLINED"
P_EXPIRED = "EXPIRED"
P_BLOCKED = "BLOCKED"

PARTICIPANT_STATES: frozenset[str] = frozenset({
    P_INVITED, P_RINGING, P_WAITING_ROOM, P_ADMITTED, P_JOINING, P_JOINED,
    P_RECONNECTING, P_LEFT, P_REMOVED, P_DECLINED, P_EXPIRED, P_BLOCKED,
})

#: REMOVED and BLOCKED are terminal for the row: a removed participant does not
#: come back by clicking the link again, which is the whole point of removal.
#: LEFT / DECLINED / EXPIRED are re-enterable — same row, never a duplicate.
PARTICIPANT_TRANSITIONS: dict[str, frozenset[str]] = {
    P_INVITED: frozenset({P_RINGING, P_WAITING_ROOM, P_ADMITTED, P_DECLINED,
                          P_EXPIRED, P_BLOCKED, P_REMOVED}),
    P_RINGING: frozenset({P_WAITING_ROOM, P_ADMITTED, P_DECLINED, P_EXPIRED,
                          P_REMOVED}),
    P_WAITING_ROOM: frozenset({P_ADMITTED, P_REMOVED, P_LEFT, P_EXPIRED}),
    P_ADMITTED: frozenset({P_JOINING, P_JOINED, P_LEFT, P_REMOVED}),
    P_JOINING: frozenset({P_JOINED, P_RECONNECTING, P_LEFT, P_REMOVED}),
    P_JOINED: frozenset({P_RECONNECTING, P_LEFT, P_REMOVED}),
    P_RECONNECTING: frozenset({P_JOINED, P_LEFT, P_REMOVED}),
    P_LEFT: frozenset({P_WAITING_ROOM, P_ADMITTED, P_JOINING}),
    P_DECLINED: frozenset({P_WAITING_ROOM, P_ADMITTED}),
    P_EXPIRED: frozenset({P_WAITING_ROOM, P_ADMITTED}),
    P_REMOVED: frozenset(),
    P_BLOCKED: frozenset(),
}

#: States that count as "in the meeting" for grids, caps, and zombie sweeps.
PRESENT_STATES: frozenset[str] = frozenset({P_JOINING, P_JOINED, P_RECONNECTING})
#: States that may hold an RTC token (i.e. have a call-participant row).
ADMITTED_STATES: frozenset[str] = frozenset({P_ADMITTED} | PRESENT_STATES)

ROLE_HOST = "HOST"
ROLE_CO_HOST = "CO_HOST"
ROLE_PARTICIPANT = "PARTICIPANT"
ROLES: frozenset[str] = frozenset({ROLE_HOST, ROLE_CO_HOST, ROLE_PARTICIPANT})
MODERATOR_ROLES: frozenset[str] = frozenset({ROLE_HOST, ROLE_CO_HOST})

INVITE_PENDING = "PENDING"
INVITE_ACCEPTED = "ACCEPTED"
INVITE_DECLINED = "DECLINED"
INVITE_REVOKED = "REVOKED"
INVITE_EXPIRED = "EXPIRED"

KIND_TEXT = "text"
KIND_REACTION = "reaction"
KIND_SYSTEM = "system"
MESSAGE_KINDS: frozenset[str] = frozenset({KIND_TEXT, KIND_REACTION, KIND_SYSTEM})

REC_REQUESTED = "REQUESTED"
REC_ACTIVE = "ACTIVE"
REC_STOPPING = "STOPPING"
REC_COMPLETED = "COMPLETED"
REC_FAILED = "FAILED"
RECORDING_OPEN: frozenset[str] = frozenset({REC_REQUESTED, REC_ACTIVE, REC_STOPPING})

ARTIFACT_TYPES: frozenset[str] = frozenset({
    "SUMMARY", "DECISION", "ACTION", "OBLIGATION", "RISK", "NOTE",
})
PROV_TRANSCRIPT = "TRANSCRIPT_DERIVED"
PROV_USER = "USER_CONFIRMED"
PROV_SYSTEM = "SYSTEM_FACT"
PROVENANCES: frozenset[str] = frozenset({PROV_TRANSCRIPT, PROV_USER, PROV_SYSTEM})

#: Reminder row lifecycle. PENDING is the only state the due-sweep picks up;
#: everything else is a resting place. SENDING exists so a crashed worker
#: leaves evidence rather than silently re-sending on the next tick, and
#: SKIPPED is distinct from CANCELLED: SKIPPED means "the offset was already
#: in the past when the meeting was booked" (booking a meeting 20 minutes out
#: cannot honour a 24h reminder), CANCELLED means the meeting or the schedule
#: it belonged to went away.
R_PENDING = "PENDING"
R_SENDING = "SENDING"
R_SENT = "SENT"
R_FAILED = "FAILED"
R_BOUNCED = "BOUNCED"
R_SKIPPED = "SKIPPED"
R_CANCELLED = "CANCELLED"

REMINDER_STATUSES: frozenset[str] = frozenset({
    R_PENDING, R_SENDING, R_SENT, R_FAILED, R_BOUNCED, R_SKIPPED, R_CANCELLED,
})
#: Rows the sweep must never touch again.
REMINDER_FINAL: frozenset[str] = frozenset({
    R_SENT, R_BOUNCED, R_SKIPPED, R_CANCELLED,
})

#: What a row is *for*. Reminders are scheduled ahead of time; the other kinds
#: are one-shot rows written at the moment the event happens, and share this
#: table so a single delivery pipeline covers every meeting email.
RK_REMINDER = "REMINDER"
RK_CONFIRMATION = "CONFIRMATION"
RK_RESCHEDULE = "RESCHEDULE"
RK_CANCELLATION = "CANCELLATION"
REMINDER_KINDS: frozenset[str] = frozenset({
    RK_REMINDER, RK_CONFIRMATION, RK_RESCHEDULE, RK_CANCELLATION,
})

#: Default reminder ladder, in minutes before the start instant. Offsets that
#: have already elapsed at booking time are written as SKIPPED, not dropped, so
#: the row set is a complete record of what was intended.
DEFAULT_REMINDER_OFFSETS: tuple[int, ...] = (1440, 60, 15)
MAX_REMINDER_OFFSET_MINUTES = 525600  # one year
MAX_REMINDER_OFFSETS = 8

MAX_TITLE_CHARS = 200
MAX_AGENDA_CHARS = 4000
MAX_MESSAGE_CHARS = 2000
MAX_REACTION_CHARS = 16
MAX_ARTIFACT_CONTENT_CHARS = 8000
MAX_IDEMPOTENCY_KEY_CHARS = 128
MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 30

# --- Calendar window -------------------------------------------------------
# A calendar asks for a window; it must never ask for a history. The month grid
# is 42 cells and a year jump is twelve of those, so a year plus a month of
# slack covers every legitimate request. Without a cap, "show me March 2045"
# and "select every meeting I have ever been invited to" are the same query,
# and the second one gets slower every year the product is alive.
MAX_CALENDAR_SPAN_DAYS = 400
# Belt to the span's braces: a window can be legal and still contain more
# meetings than a grid can show. The cap is per request, not per day, so a
# pathological month cannot outweigh the rest of the year.
MAX_CALENDAR_ROWS = 500

# --- Schedule bounds -------------------------------------------------------
# A meeting may be scheduled arbitrarily far ahead; the product requirement is
# explicitly "any future date", so the only ceiling here is a technical one.
# 4000-01-01 keeps every instant inside the range `datetime` can represent
# after a timezone conversion (`datetime.max` is 9999-12-31, and converting a
# near-max instant eastward overflows), and inside the 4-digit ISO year that
# `fromisoformat` round-trips. It is a guard against nonsense and overflow, not
# a product limit: it is ~1,975 years out.
MAX_SCHEDULE_YEAR = 4000
# A meeting may not be scheduled into the past. A small backward tolerance
# absorbs clock skew between the phone that composed the request and the
# server, and the seconds the request spent in flight -- without it a user who
# picks "today, two minutes from now" on a slightly fast phone gets a
# confusing rejection.
SCHEDULE_PAST_TOLERANCE_SECONDS = 120
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 1440

# ---------------------------------------------------------------------------
# Schema — SQLite dialect; services.db rewrites for Postgres. No INSERT OR
# IGNORE anywhere in this package: dedupe lives in the writer.
# ---------------------------------------------------------------------------

MEETINGS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {MEETINGS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    public_id TEXT NOT NULL UNIQUE,
    meeting_code TEXT NOT NULL UNIQUE,
    code_rotated_at TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '{ST_DRAFT}',
    waiting_room_enabled INTEGER NOT NULL DEFAULT 1,
    locked INTEGER NOT NULL DEFAULT 0,
    scheduled_start_at TEXT NOT NULL DEFAULT '',
    scheduled_timezone TEXT NOT NULL DEFAULT '',
    schedule_version INTEGER NOT NULL DEFAULT 1,
    agenda TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    duration_minutes INTEGER NOT NULL DEFAULT 0,
    call_id INTEGER NOT NULL DEFAULT 0,
    call_public_id TEXT NOT NULL DEFAULT '',
    channel_name TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    ended_at TEXT NOT NULL DEFAULT '',
    end_reason TEXT NOT NULL DEFAULT '',
    host_disconnect_deadline TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

PARTICIPANTS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {PARTICIPANTS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT '{ROLE_PARTICIPANT}',
    state TEXT NOT NULL DEFAULT '{P_INVITED}',
    invited_by_user_id INTEGER NOT NULL DEFAULT 0,
    admitted_by_user_id INTEGER NOT NULL DEFAULT 0,
    rtc_uid INTEGER NOT NULL DEFAULT 0,
    raised_hand_at TEXT NOT NULL DEFAULT '',
    joined_at TEXT NOT NULL DEFAULT '',
    left_at TEXT NOT NULL DEFAULT '',
    removal_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(meeting_id, user_id)
)
"""

INVITES_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {INVITES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    inviter_user_id INTEGER NOT NULL,
    invitee_user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT '{INVITE_PENDING}',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    responded_at TEXT NOT NULL DEFAULT '',
    UNIQUE(meeting_id, invitee_user_id)
)
"""

MESSAGES_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    sender_user_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT '{KIND_TEXT}',
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

RECORDINGS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {RECORDINGS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    started_by_user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT '{REC_REQUESTED}',
    provider TEXT NOT NULL DEFAULT 'agora_cloud',
    provider_resource_id TEXT NOT NULL DEFAULT '',
    provider_sid TEXT NOT NULL DEFAULT '',
    storage_ref TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    stopped_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

MEETING_ARTIFACT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {MEETING_ARTIFACT_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    owner_user_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL DEFAULT 'NOTE',
    provenance TEXT NOT NULL DEFAULT '{PROV_USER}',
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    evidence_refs TEXT NOT NULL DEFAULT '',
    saved_record_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""

REMINDERS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {REMINDERS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    offset_minutes INTEGER NOT NULL,
    schedule_version INTEGER NOT NULL DEFAULT 1,
    send_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '{R_PENDING}',
    kind TEXT NOT NULL DEFAULT '{RK_REMINDER}',
    idempotency_key TEXT NOT NULL DEFAULT '',
    queued_at TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (meeting_id, user_id, offset_minutes, schedule_version)
)
"""

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_pm_meetings_owner "
    f"ON {MEETINGS_TABLE} (owner_user_id, status, id)",
    # The reminder sweep's only hot query: "what is due?". Status first so the
    # scan skips the large tail of already-sent rows.
    f"CREATE INDEX IF NOT EXISTS idx_pm_reminders_due "
    f"ON {REMINDERS_TABLE} (status, send_at, id)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_reminders_meeting "
    f"ON {REMINDERS_TABLE} (meeting_id, user_id, status)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_meetings_status "
    f"ON {MEETINGS_TABLE} (status, updated_at)",
    # Replay lookup on create. Owner-scoped so one user's key cannot match
    # another's row, and so the index is selective on a table where most rows
    # carry the empty-string default.
    f"CREATE INDEX IF NOT EXISTS idx_pm_meetings_idem "
    f"ON {MEETINGS_TABLE} (owner_user_id, idempotency_key)",
    # The Upcoming list's query: future meetings for one user, in start order.
    f"CREATE INDEX IF NOT EXISTS idx_pm_meetings_schedule "
    f"ON {MEETINGS_TABLE} (owner_user_id, scheduled_start_at)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_participants_user "
    f"ON {PARTICIPANTS_TABLE} (user_id, state, meeting_id)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_participants_meeting "
    f"ON {PARTICIPANTS_TABLE} (meeting_id, state)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_invites_invitee "
    f"ON {INVITES_TABLE} (invitee_user_id, status)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_messages_meeting "
    f"ON {MESSAGES_TABLE} (meeting_id, id)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_recordings_meeting "
    f"ON {RECORDINGS_TABLE} (meeting_id, status)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_artifacts_meeting "
    f"ON {MEETING_ARTIFACT_TABLE} (meeting_id, owner_user_id, id)",
)

_SCHEMA_READY = False


class PrivateMeetingRejected(ValueError):
    """A meeting request this module refuses. Carries HTTP mapping."""

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)


def reset_meetings_schema_cache() -> None:
    global _SCHEMA_READY
    _SCHEMA_READY = False


#: Columns added to an already-deployed table. ``CREATE TABLE IF NOT EXISTS``
#: is a no-op where the table exists, so every column introduced after the
#: first deploy has to arrive by ALTER as well as by the DDL above. Keep the
#: two in sync: a column here that is missing from the CREATE only reaches
#: upgraded databases, and one in the CREATE but missing here only reaches
#: fresh ones. Definitions carry NOT NULL DEFAULT so existing rows are legal
#: the moment the column lands.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (MEETINGS_TABLE, "scheduled_start_at", "TEXT NOT NULL DEFAULT ''"),
    (MEETINGS_TABLE, "scheduled_timezone", "TEXT NOT NULL DEFAULT ''"),
    (MEETINGS_TABLE, "schedule_version", "INTEGER NOT NULL DEFAULT 1"),
    (MEETINGS_TABLE, "agenda", "TEXT NOT NULL DEFAULT ''"),
    (MEETINGS_TABLE, "idempotency_key", "TEXT NOT NULL DEFAULT ''"),
    (MEETINGS_TABLE, "duration_minutes", "INTEGER NOT NULL DEFAULT 0"),
)


def _add_missing_columns(cur) -> None:
    """Bring an existing deployment's tables up to the current shape."""
    wanted: dict[str, list[tuple[str, str]]] = {}
    for table, column, definition in ADDED_COLUMNS:
        wanted.setdefault(table, []).append((column, definition))
    for table, columns in wanted.items():
        try:
            present = schema.table_columns(cur, table, refresh=True)
        except Exception:
            # Introspection failed: do not guess. A blind ALTER would either
            # duplicate a column or abort the caller's transaction, and this
            # runs inside whatever transaction the request already opened.
            LOGGER.exception("PM_COLUMN_INTROSPECT_FAILED table=%s", table)
            continue
        for column, definition in columns:
            if column in present:
                continue
            try:
                cur.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except Exception:
                # The web process and a worker can ensure at the same instant;
                # losing that race means the column now exists, which is the
                # outcome we wanted. Anything else surfaces at query time.
                LOGGER.exception(
                    "PM_COLUMN_ADD_FAILED table=%s column=%s", table, column)


def ensure_meetings_schema(cur, *, force: bool = False) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    cur.execute(MEETINGS_TABLE_DDL)
    cur.execute(PARTICIPANTS_TABLE_DDL)
    cur.execute(INVITES_TABLE_DDL)
    cur.execute(MESSAGES_TABLE_DDL)
    cur.execute(RECORDINGS_TABLE_DDL)
    cur.execute(MEETING_ARTIFACT_TABLE_DDL)
    cur.execute(REMINDERS_TABLE_DDL)
    _add_missing_columns(cur)
    for ddl in INDEX_DDL:
        cur.execute(ddl)
    # A meeting rides a communication_calls row, so this schema is not ready
    # until the call engine's is too.
    call_engine.ensure_schema(cur)
    _SCHEMA_READY = True


# ---------------------------------------------------------------------------
# Flags — fail closed. Absent env var means OFF (mission §54), which is the
# opposite of feature_matrix._flag_enabled's default; hence local helpers.
# ---------------------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def _flag_on(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return default
    return raw in _TRUTHY


def meetings_enabled() -> bool:
    return _flag_on("PRIVATE_MEETINGS_ENABLED", False)


def recording_enabled() -> bool:
    return meetings_enabled() and _flag_on("PRIVATE_MEETINGS_RECORDING_ENABLED", False)


def transcription_configured() -> bool:
    """No transcription provider exists anywhere in this codebase.

    Hardcoded False is the truthful answer; when a provider is wired this
    becomes a real diagnostic. Everything downstream (captions UI, UNDX
    TRANSCRIPT_DERIVED artifacts) keys off this one function.
    """
    return False


def _require_enabled() -> None:
    if not meetings_enabled():
        raise PrivateMeetingRejected(
            "Private meetings are not enabled.", status=403, code="flag_disabled")


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def max_participants() -> int:
    return _int_env("PRIVATE_MEETINGS_MAX_PARTICIPANTS", 12, 2, 50)


def host_reconnect_seconds() -> int:
    return _int_env("PRIVATE_MEETINGS_HOST_RECONNECT_SECONDS", 120, 15, 900)


def max_meeting_seconds() -> int:
    return _int_env("PRIVATE_MEETINGS_MAX_SECONDS", 21600, 300, 86400)


def empty_meeting_timeout_seconds() -> int:
    return _int_env("PRIVATE_MEETINGS_EMPTY_TIMEOUT", 300, 60, 3600)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_utc_iso(moment: datetime) -> str:
    """The one way an instant is allowed to reach the database.

    Every stored timestamp is UTC, normalised, with an explicit `+00:00`.
    Before this existed the column kept whatever offset the client happened to
    send, so the same instant was written three different ways depending on
    where the organiser was standing -- and `list_meetings` sorts that column
    as *text*. Mixed offsets therefore did not merely look untidy, they put
    the upcoming list in the wrong order: `2032-05-18T22:00:00-07:00` sorts
    before `2032-05-18T23:00:00+02:00` while actually starting eight hours
    later. Normalising on the way in makes the lexicographic sort and the
    chronological sort the same sort, which is what the rest of the module
    already assumes.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize_timezone(value: object) -> str:
    """Validate an IANA zone name, returning '' for absent and raising on junk.

    An empty zone is allowed and means "the organiser expressed no preference";
    callers render UTC in that case. An *invalid* zone is refused rather than
    silently coerced, because silently falling back to UTC would show every
    invitee a time that is wrong by the organiser's offset -- a failure that
    looks like a working feature.
    """
    name = str(value or "").strip()
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise PrivateMeetingRejected(
            "That time zone is not recognised.",
            status=400, code="invalid_timezone")
    return name


def resolve_schedule(scheduled_start_at: object, tz_name: object = "",
                     *, now: datetime | None = None) -> tuple[str, str]:
    """Turn a client's requested start into a canonical (utc_iso, zone) pair.

    Accepts either an absolute instant (`...Z` / `...+02:00`) or a *local wall
    clock* time plus a zone. The second form is what a calendar UI naturally
    produces -- the user picked "2:35 PM" on a day, in a zone -- and it is the
    only form that can express DST intent correctly, so it is handled here
    rather than asking the client to do timezone arithmetic it cannot do
    reliably.
    """
    zone_name = normalize_timezone(tz_name)
    text = str(scheduled_start_at or "").strip()
    if not text:
        raise PrivateMeetingRejected(
            "A scheduled meeting needs a valid start time.",
            status=400, code="invalid_schedule")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise PrivateMeetingRejected(
            "A scheduled meeting needs a valid start time.",
            status=400, code="invalid_schedule")
    if "T" not in text and " " not in text:
        # `fromisoformat("2032-06-01")` succeeds and yields midnight. A client
        # that sent only a date has not chosen a time, and silently booking
        # 00:00 puts the meeting somewhere the host never picked -- on the
        # wrong day, in most zones. The flow is date *then* time; a date alone
        # is an incomplete request, not a midnight one.
        raise PrivateMeetingRejected(
            "A scheduled meeting needs a time of day, not just a date.",
            status=400, code="invalid_schedule")

    if parsed.tzinfo is None:
        # A wall-clock time. Interpret it in the organiser's zone; with no zone
        # the historical behaviour (assume UTC) is preserved so existing
        # callers do not change meaning.
        if zone_name:
            parsed = _localize_wall_clock(parsed, zone_name)
        else:
            parsed = parsed.replace(tzinfo=timezone.utc)

    if parsed.year > MAX_SCHEDULE_YEAR:
        raise PrivateMeetingRejected(
            "That date is too far in the future.",
            status=400, code="schedule_out_of_range")

    reference = now or _now_dt()
    if parsed < reference - timedelta(seconds=SCHEDULE_PAST_TOLERANCE_SECONDS):
        raise PrivateMeetingRejected(
            "A meeting cannot be scheduled in the past.",
            status=400, code="schedule_in_past")

    return _to_utc_iso(parsed), zone_name


def _localize_wall_clock(naive: datetime, zone_name: str) -> datetime:
    """Attach a zone to a wall-clock time, resolving DST edge cases explicitly.

    Two days a year a local time is not a simple function of the clock:

    * **Spring forward** -- 2:30 AM may not exist. `ZoneInfo` does not raise;
      it invents an answer by applying the pre-transition offset, which lands
      the meeting an hour before the user meant. We detect the gap (the
      round-trip through UTC does not return the wall clock we started with)
      and move *forward* past it, so "2:30 AM" on a spring-forward day becomes
      3:30 AM rather than 1:30 AM. Forward is the safe direction: a meeting
      that drifts later is late, one that drifts earlier is missed.
    * **Fall back** -- 1:30 AM happens twice. `fold=0` selects the first
      (pre-transition) occurrence, which is the earlier instant and the
      conventional reading of an ambiguous local time. We pin it explicitly so
      the choice is a decision rather than a default that could change.
    """
    zone = ZoneInfo(zone_name)
    attached = naive.replace(tzinfo=zone, fold=0)
    # Round-trip: if the wall clock does not survive a trip through UTC, the
    # time we were handed does not exist on that day.
    round_tripped = attached.astimezone(timezone.utc).astimezone(zone)
    if round_tripped.replace(tzinfo=None) != naive:
        shifted = (attached + timedelta(hours=1))
        recovered = shifted.astimezone(timezone.utc).astimezone(zone)
        if recovered.replace(tzinfo=None) != shifted.replace(tzinfo=None):
            # Pathological zone (a gap wider than an hour). Fall back to the
            # instant ZoneInfo derives rather than refusing the booking.
            return attached
        return shifted
    return attached


def _clean_duration(value: object, *, required: bool = False) -> int:
    """Clamp a duration, refusing the degenerate ones.

    Zero used to be legal and meant "unset". It is still accepted when a
    duration is genuinely optional (an instant meeting has no planned length),
    but a *scheduled* meeting with a zero-minute duration cannot be rendered in
    an invitation or used for conflict detection, so `required=True` refuses it
    rather than storing a meeting that ends when it starts.
    """
    if value is None or value == "":
        minutes = 0
    elif isinstance(value, bool):
        # `int(True)` is 1, which would book a one-minute meeting from a
        # JSON `true`. Nonsense in, refusal out.
        raise PrivateMeetingRejected(
            "Duration must be a whole number of minutes.",
            status=400, code="invalid_duration")
    else:
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            raise PrivateMeetingRejected(
                "Duration must be a whole number of minutes.",
                status=400, code="invalid_duration")
        if isinstance(value, float) and minutes != value:
            # int(45.9) is 45. Truncating silently would end the meeting at a
            # time the caller never asked for; a fractional minute is a bug in
            # the caller, not an input to round.
            raise PrivateMeetingRejected(
                "Duration must be a whole number of minutes.",
                status=400, code="invalid_duration")
    if minutes < 0:
        raise PrivateMeetingRejected(
            "Duration cannot be negative.", status=400, code="invalid_duration")
    if minutes == 0:
        if required:
            raise PrivateMeetingRejected(
                "A scheduled meeting needs a duration.",
                status=400, code="invalid_duration")
        return 0
    if minutes < MIN_DURATION_MINUTES:
        raise PrivateMeetingRejected(
            "That duration is too short.", status=400, code="invalid_duration")
    return min(minutes, MAX_DURATION_MINUTES)


def _row(value: Any) -> dict:
    if value is None:
        return {}
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _clip(text: object, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _new_public_id() -> str:
    return f"mtg_{secrets.token_urlsafe(12)}"


def _mint_meeting_code() -> str:
    """Non-sequential, human-relayable: three groups of three digits."""
    groups = [f"{secrets.randbelow(1000):03d}" for _ in range(3)]
    return "-".join(groups)


def _normalize_code(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) != 9:
        return ""
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:9]}"


def _blocked(cur, user_a: int, user_b: int) -> bool:
    """True if EITHER blocking system blocks EITHER direction.

    comm_v2_blocks is authoritative for the communications surface; the legacy
    ``blocked_users`` table still backs the rest of the app. Mission rule: a
    block in either system is a block here — code never picks the permissive
    one. Failures fail toward "not blocked" only for the legacy probe (its
    absence in a fresh test DB must not lock everyone out), never for comm_v2.
    """
    a, b = int(user_a or 0), int(user_b or 0)
    if a <= 0 or b <= 0 or a == b:
        return False
    try:
        if comm_service._blocked_between(cur, a, [b]):
            return True
    except Exception as exc:  # pragma: no cover - schema-absent environments
        LOGGER.warning("PRIVATE_MEETING_BLOCK_CHECK_COMM_FAILED error=%s", exc)
    try:
        cur.execute(
            """SELECT 1 FROM blocked_users
            WHERE (blocker_user_id=? AND blocked_user_id=?)
               OR (blocker_user_id=? AND blocked_user_id=?)
            LIMIT 1""",
            (a, b, b, a),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _audit(cur, *, actor: int, owner: int, action: str, meeting_id: int,
           outcome: str = audit.OUTCOME_OK, count: int = 0) -> None:
    audit.record(
        cur, actor_user_id=actor, owner_user_id=owner, action=action,
        object_type="PRIVATE_MEETING", object_id=f"MEETING:{int(meeting_id)}",
        purpose="user_request", outcome=outcome, result_count=count)


# ---------------------------------------------------------------------------
# Row access + transitions
# ---------------------------------------------------------------------------

def _meeting_by_ref(cur, ref: object) -> dict:
    """Look up by public_id, meeting code, or integer id. Empty dict if none."""
    text = str(ref or "").strip()
    if not text:
        return {}
    if text.startswith("mtg_"):
        cur.execute(f"SELECT * FROM {MEETINGS_TABLE} WHERE public_id=? LIMIT 1", (text,))
        return _row(cur.fetchone())
    code = _normalize_code(text)
    if code:
        cur.execute(f"SELECT * FROM {MEETINGS_TABLE} WHERE meeting_code=? LIMIT 1", (code,))
        return _row(cur.fetchone())
    try:
        meeting_id = int(text)
    except ValueError:
        return {}
    cur.execute(f"SELECT * FROM {MEETINGS_TABLE} WHERE id=? LIMIT 1", (meeting_id,))
    return _row(cur.fetchone())


def _clean_idempotency_key(value: object) -> str:
    """Normalize a client-supplied replay key, or '' for "no key given".

    Deliberately lenient about *shape* — any opaque token the client can
    generate is fine — and strict about length, because the key is stored and
    indexed. It is scoped to the owner at lookup time, so one user cannot
    collide with, or probe for, another user's meeting by guessing a key.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_IDEMPOTENCY_KEY_CHARS:
        raise PrivateMeetingRejected(
            "Idempotency key is too long.",
            status=400, code="invalid_idempotency_key")
    return text


def _meeting_by_idempotency(cur, owner_user_id: int, key: str) -> dict | None:
    """The meeting this owner already created under ``key``, or None.

    None and `{}` would be the same thing to a caller that only truth-tests,
    so this returns None explicitly: "no prior attempt" is a different fact
    from "a row that happens to be empty", and the create path branches on it.
    """
    if not key:
        return None
    cur.execute(
        f"""SELECT * FROM {MEETINGS_TABLE}
        WHERE owner_user_id=? AND idempotency_key=? ORDER BY id LIMIT 1""",
        (int(owner_user_id), str(key)),
    )
    row = _row(cur.fetchone())
    return row or None


def _require_meeting(cur, ref: object) -> dict:
    meeting = _meeting_by_ref(cur, ref)
    if not meeting:
        raise PrivateMeetingRejected("Meeting not found.", status=404, code="not_found")
    return meeting


def _participant_row(cur, meeting_id: int, user_id: int) -> dict:
    cur.execute(
        f"SELECT * FROM {PARTICIPANTS_TABLE} WHERE meeting_id=? AND user_id=? LIMIT 1",
        (int(meeting_id), int(user_id)),
    )
    return _row(cur.fetchone())


def _require_moderator(cur, meeting: dict, user_id: int) -> dict:
    participant = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not participant or participant.get("role") not in MODERATOR_ROLES \
            or participant.get("state") in {P_REMOVED, P_BLOCKED}:
        raise PrivateMeetingRejected(
            "Only the host or a co-host may do that.", status=403, code="forbidden")
    return participant


def _transition_meeting(cur, meeting: dict, new_status: str, *,
                        extra_sql: str = "", extra_params: tuple = ()) -> dict:
    current = str(meeting.get("status") or "")
    if new_status == current:
        return meeting
    if new_status not in MEETING_TRANSITIONS.get(current, frozenset()):
        raise PrivateMeetingRejected(
            f"Meeting cannot go from {current} to {new_status}.",
            status=409, code="invalid_transition")
    now = _now_iso()
    cur.execute(
        f"UPDATE {MEETINGS_TABLE} SET status=?, updated_at=?{extra_sql} WHERE id=?",
        (new_status, now, *extra_params, int(meeting["id"])),
    )
    meeting = dict(meeting)
    meeting["status"] = new_status
    meeting["updated_at"] = now
    return meeting


def _transition_participant(cur, participant: dict, new_state: str, *,
                            extra_sql: str = "", extra_params: tuple = ()) -> dict:
    current = str(participant.get("state") or "")
    if new_state == current:
        return participant
    if new_state not in PARTICIPANT_TRANSITIONS.get(current, frozenset()):
        raise PrivateMeetingRejected(
            f"Participant cannot go from {current} to {new_state}.",
            status=409, code="invalid_transition")
    now = _now_iso()
    cur.execute(
        f"UPDATE {PARTICIPANTS_TABLE} SET state=?, updated_at=?{extra_sql} WHERE id=?",
        (new_state, now, *extra_params, int(participant["id"])),
    )
    participant = dict(participant)
    participant["state"] = new_state
    participant["updated_at"] = now
    return participant


def _insert_participant(cur, *, meeting_id: int, user_id: int, role: str,
                        state: str, invited_by: int = 0,
                        admitted_by: int = 0) -> dict:
    """One logical participant per (meeting, user) — insert or reuse the row."""
    existing = _participant_row(cur, meeting_id, user_id)
    if existing:
        return existing
    now = _now_iso()
    cur.execute(
        f"""INSERT INTO {PARTICIPANTS_TABLE}
        (meeting_id, user_id, role, state, invited_by_user_id,
         admitted_by_user_id, rtc_uid, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(meeting_id), int(user_id), role, state, int(invited_by),
         int(admitted_by), int(user_id), now, now),
    )
    return _participant_row(cur, meeting_id, user_id)


def _present_count(cur, meeting_id: int) -> int:
    states = sorted(ADMITTED_STATES)
    placeholders = ",".join(["?"] * len(states))
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {PARTICIPANTS_TABLE} "
        f"WHERE meeting_id=? AND state IN ({placeholders})",
        (int(meeting_id), *states),
    )
    row = _row(cur.fetchone())
    return int(row.get("n") or row.get("count") or 0)


# ---------------------------------------------------------------------------
# Engine bridge — the ONLY place meetings touch communication_calls. The
# engine stays the sole Agora authority; we own one scope-"room" row.
# ---------------------------------------------------------------------------

def _create_room_call(cur, *, owner_user_id: int) -> dict:
    public_id = f"call_{secrets.token_urlsafe(10)}"
    room_name = f"pulsesoc-{public_id}"
    now = _now_iso()
    cur.execute(
        """INSERT INTO communication_calls
        (public_id, conversation_id, room_name, provider, call_type, call_scope,
         status, created_by_user_id, started_at, metadata_json, created_at, updated_at)
        VALUES (?, 0, ?, 'agora', 'video', 'room', 'connecting', ?, ?, '', ?, ?)""",
        (public_id, room_name, int(owner_user_id), now, now, now),
    )
    cur.execute(
        "SELECT id, public_id, room_name FROM communication_calls WHERE public_id=? LIMIT 1",
        (public_id,),
    )
    return _row(cur.fetchone())


def _call_row(cur, call_id: int) -> dict:
    cur.execute("SELECT * FROM communication_calls WHERE id=? LIMIT 1", (int(call_id),))
    return _row(cur.fetchone())


def _ensure_call_participant(cur, call_id: int, user_id: int, *, host: bool) -> None:
    """Admission == this row exists. Idempotent; revives a left/removed row."""
    if int(call_id) <= 0:
        return
    cur.execute(
        "SELECT id, status FROM communication_call_participants "
        "WHERE call_id=? AND user_id=? LIMIT 1",
        (int(call_id), int(user_id)),
    )
    existing = _row(cur.fetchone())
    now = _now_iso()
    if existing:
        cur.execute(
            "UPDATE communication_call_participants "
            "SET status='joined', left_at=NULL, updated_at=? WHERE id=?",
            (now, int(existing["id"])),
        )
        return
    cur.execute(
        """INSERT INTO communication_call_participants
        (call_id, user_id, role, status, joined_at, created_at, updated_at)
        VALUES (?, ?, ?, 'joined', ?, ?, ?)""",
        (int(call_id), int(user_id), "caller" if host else "member", now, now, now),
    )


def _drop_call_participant(cur, call_id: int, user_id: int, status: str = "left") -> None:
    """Revocation == this row is terminal. The engine refuses the next token."""
    if int(call_id) <= 0:
        return
    now = _now_iso()
    cur.execute(
        "UPDATE communication_call_participants "
        "SET status=?, left_at=?, updated_at=? WHERE call_id=? AND user_id=?",
        (status, now, now, int(call_id), int(user_id)),
    )


def _end_room_call(cur, call_id: int, reason: str) -> None:
    if int(call_id) <= 0:
        return
    now = _now_iso()
    cur.execute(
        "UPDATE communication_calls SET status='ended', ended_at=?, "
        "end_reason=?, updated_at=? WHERE id=? AND status NOT IN "
        "('ended','failed','canceled','cancelled','expired')",
        (now, str(reason or "meeting_ended")[:64], now, int(call_id)),
    )
    cur.execute(
        "UPDATE communication_call_participants SET status='left', left_at=?, "
        "updated_at=? WHERE call_id=? AND status NOT IN ('left','removed')",
        (now, now, int(call_id)),
    )


# ---------------------------------------------------------------------------
# Lifecycle — create / start / join / admit / lock / roles / leave / end
# ---------------------------------------------------------------------------

def create_meeting(cur, *, owner_user_id: int, title: str = "",
                   scheduled_start_at: str = "", duration_minutes: int = 0,
                   waiting_room_enabled: bool = True,
                   instant: bool = False, timezone_name: str = "",
                   agenda: str = "", idempotency_key: str = "",
                   reminder_offsets: object = None) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateMeetingRejected("Owner required.", status=401, code="unauthorized")
    clean_title = _clip(title, MAX_TITLE_CHARS)
    clean_agenda = _clip(agenda, MAX_AGENDA_CHARS)
    # An instant meeting starts now, so it has no schedule to validate and no
    # duration to honour; a scheduled one must survive every check before a
    # row exists. resolve_schedule returns the canonical UTC instant plus the
    # zone it was expressed in — both are stored, because the instant alone
    # cannot tell a later reschedule what "same time next week" means.
    scheduled, zone = "", ""
    duration = 0
    if not instant:
        scheduled, zone = resolve_schedule(scheduled_start_at, timezone_name)
        duration = _clean_duration(duration_minutes, required=True)
    key = _clean_idempotency_key(idempotency_key)
    if key:
        existing = _meeting_by_idempotency(cur, owner, key)
        if existing is not None:
            # A retry, not a second meeting. Returning the original is the
            # whole contract: the client cannot tell whether its first request
            # was lost before or after the insert, so both must look the same.
            _telemetry.emit(_telemetry.EVENT_MEETING_LIFECYCLE,
                            transition="create_replayed", end_reason="not_ended",
                            scheduled=bool(existing.get("scheduled_start_at")),
                            waiting_room=bool(existing.get("waiting_room_enabled")),
                            participant_count=1)
            return _project_meeting(cur, existing, viewer_user_id=owner)
    now = _now_iso()
    public_id = _new_public_id()
    meeting_code = ""
    for _ in range(20):
        candidate = _mint_meeting_code()
        cur.execute(
            f"SELECT 1 FROM {MEETINGS_TABLE} WHERE meeting_code=? LIMIT 1",
            (candidate,))
        if cur.fetchone() is None:
            meeting_code = candidate
            break
    if not meeting_code:
        raise PrivateMeetingRejected(
            "Could not allocate a meeting code.", status=503, code="code_exhausted")
    status = ST_DRAFT if instant else ST_SCHEDULED
    cur.execute(
        f"""INSERT INTO {MEETINGS_TABLE}
        (owner_user_id, public_id, meeting_code, title, status,
         waiting_room_enabled, locked, scheduled_start_at, scheduled_timezone,
         schedule_version, agenda, idempotency_key, duration_minutes,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 1, ?, ?, ?, ?, ?)""",
        (owner, public_id, meeting_code, clean_title, status,
         1 if waiting_room_enabled else 0, scheduled, zone, clean_agenda,
         key, duration, now, now),
    )
    meeting = _require_meeting(cur, public_id)
    _insert_participant(
        cur, meeting_id=int(meeting["id"]), user_id=owner, role=ROLE_HOST,
        state=P_ADMITTED, invited_by=owner, admitted_by=owner)
    _audit(cur, actor=owner, owner=owner, action=audit.ACTION_MEETING_CREATE,
           meeting_id=int(meeting["id"]))
    _telemetry.emit(_telemetry.EVENT_MEETING_LIFECYCLE, transition="created",
                    end_reason="not_ended", scheduled=bool(scheduled),
                    waiting_room=bool(waiting_room_enabled),
                    participant_count=1)
    if instant:
        return start_meeting(cur, actor_user_id=owner, meeting_ref=public_id)
    _plan_reminders(cur, int(meeting["id"]), reminder_offsets)
    _announce(cur, meeting, "CONFIRMATION")
    return _project_meeting(cur, meeting, viewer_user_id=owner)


def _plan_reminders(cur, meeting_id: int, offsets: object = None) -> None:
    """Plan (or replan) reminders, never at the cost of the meeting itself.

    Imported here rather than at module scope because the reminder module
    reads this one. A reminder that could not be planned is a degraded
    meeting, not a failed booking — refusing the schedule because the mail
    plan hiccuped would be the tail wagging the dog.
    """
    try:
        from services.private_office import meeting_emails, meeting_reminders

        meeting_reminders.plan_reminders(
            cur, meeting_id=int(meeting_id), offsets=offsets)
        # Straight into the outbox, each row held until its own moment. This is
        # the step that makes the plan survive a deploy, a restart, or nine
        # years of nothing happening.
        meeting_emails.enqueue_reminders(cur, meeting_id=int(meeting_id))
    except Exception:  # noqa: BLE001
        LOGGER.exception("PM_REMINDER_PLAN_FAILED meeting=%s", meeting_id)


def _drop_reminders(cur, meeting_id: int, *, reason: str,
                    user_id: int = 0, before_version: int = 0) -> None:
    try:
        from services.private_office import meeting_reminders

        meeting_reminders.cancel_reminders(
            cur, meeting_id=int(meeting_id), user_id=int(user_id or 0),
            before_version=int(before_version or 0), reason=reason)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PM_REMINDER_CANCEL_FAILED meeting=%s", meeting_id)


def _announce(cur, meeting: dict, kind: str, *, user_ids=None) -> None:
    """Mail the people on a meeting, one message each. Never fatal."""
    try:
        from services.private_office import meeting_emails

        meeting_emails.announce(cur, kind=kind, meeting=meeting,
                                user_ids=user_ids)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PM_ANNOUNCE_FAILED kind=%s meeting=%s",
                         kind, meeting.get("id"))


def start_meeting(cur, *, actor_user_id: int, meeting_ref: object) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    owner = int(meeting.get("owner_user_id") or 0)
    if actor != owner:
        raise PrivateMeetingRejected(
            "Only the host can start this meeting.", status=403, code="forbidden")
    if meeting.get("status") in MEETING_FINAL:
        raise PrivateMeetingRejected(
            "This meeting is over.", status=410, code="meeting_over")
    if meeting.get("status") == ST_LIVE:
        return _project_meeting(cur, meeting, viewer_user_id=actor)
    rtc = call_engine.agora_config_status()
    if not rtc.get("configured"):
        # Fail closed, and truthfully: the meeting is not FAILED, the
        # transport is unavailable. Nothing is minted, nothing pretends.
        raise PrivateMeetingRejected(
            "Real-time service is not configured.", status=503, code="rtc_unavailable")
    meeting = _transition_meeting(cur, meeting, ST_STARTING)
    call = _create_room_call(cur, owner_user_id=owner)
    if not call.get("id"):
        raise PrivateMeetingRejected(
            "Could not create the meeting room.", status=503, code="call_create_failed")
    now = _now_iso()
    meeting = _transition_meeting(
        cur, meeting, ST_LIVE,
        extra_sql=", call_id=?, call_public_id=?, channel_name=?, started_at=?, "
                  "host_disconnect_deadline=''",
        extra_params=(int(call["id"]), str(call["public_id"]),
                      str(call["room_name"]), now))
    meeting["call_id"] = int(call["id"])
    meeting["call_public_id"] = str(call["public_id"])
    meeting["channel_name"] = str(call["room_name"])
    meeting["started_at"] = now
    host_row = _participant_row(cur, int(meeting["id"]), owner)
    if host_row and host_row.get("state") not in ADMITTED_STATES:
        host_row = _transition_participant(cur, host_row, P_ADMITTED)
    _ensure_call_participant(cur, int(call["id"]), owner, host=True)
    _audit(cur, actor=actor, owner=owner, action=audit.ACTION_MEETING_START,
           meeting_id=int(meeting["id"]))
    _telemetry.emit(_telemetry.EVENT_MEETING_LIFECYCLE, transition="started",
                    end_reason="not_ended",
                    scheduled=bool(meeting.get("scheduled_start_at")),
                    waiting_room=bool(int(meeting.get("waiting_room_enabled") or 0)),
                    participant_count=_present_count(cur, int(meeting["id"])))
    return _project_meeting(cur, meeting, viewer_user_id=actor)


def cancel_meeting(cur, *, actor_user_id: int, meeting_ref: object,
                   reason: str = "cancelled_by_host") -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    if actor != int(meeting.get("owner_user_id") or 0):
        raise PrivateMeetingRejected(
            "Only the host can cancel this meeting.", status=403, code="forbidden")
    meeting = _transition_meeting(
        cur, meeting, ST_CANCELLED,
        extra_sql=", ended_at=?, end_reason=?",
        extra_params=(_now_iso(), _clip(reason, 64)))
    cur.execute(
        f"UPDATE {INVITES_TABLE} SET status=?, responded_at=? "
        f"WHERE meeting_id=? AND status=?",
        (INVITE_REVOKED, _now_iso(), int(meeting["id"]), INVITE_PENDING))
    _drop_reminders(cur, int(meeting["id"]), reason="meeting_cancelled")
    _announce(cur, meeting, "CANCELLATION")
    _audit(cur, actor=actor, owner=actor, action=audit.ACTION_MEETING_CANCEL,
           meeting_id=int(meeting["id"]))
    # `reason` goes through vocabulary membership in telemetry — a caller-
    # supplied string that is not a known lifecycle word becomes "other",
    # never a log line.
    _telemetry.emit(_telemetry.EVENT_MEETING_LIFECYCLE, transition="cancelled",
                    end_reason=reason,
                    scheduled=bool(meeting.get("scheduled_start_at")),
                    waiting_room=bool(int(meeting.get("waiting_room_enabled") or 0)),
                    participant_count=0)
    return _project_meeting(cur, meeting, viewer_user_id=actor)


#: Fields a reschedule may touch. Anything outside this set is a different
#: operation with its own rules: the status is a state machine, the meeting
#: code has its own rotation verb, the waiting-room flag is a security control.
EDITABLE_FIELDS: frozenset[str] = frozenset({
    "title", "agenda", "scheduled_start_at", "timezone_name", "duration_minutes",
})


def reschedule_meeting(cur, *, actor_user_id: int, meeting_ref: object,
                       scheduled_start_at: object = None,
                       timezone_name: object = None,
                       duration_minutes: object = None,
                       title: object = None,
                       agenda: object = None) -> dict:
    """Move or re-title a scheduled meeting. Host only.

    `None` means "leave this alone" — distinct from `""`, which for the text
    fields means "clear it". That distinction is why every parameter defaults
    to None rather than to the empty string: a partial edit from the UI sends
    only what changed, and treating an absent field as a blank would wipe the
    agenda every time someone fixed a typo in the title.

    **Only a time change bumps `schedule_version`.** The version is the
    invalidation token for everything keyed to *when* the meeting happens —
    pending reminders, "the time you were told" in an already-delivered
    invitation. Fixing a typo in the title must not invalidate them; moving
    the meeting must. Bumping on every edit would mean a title fix silently
    re-queues every reminder, and re-mails every attendee.

    A meeting that has started, ended, or been cancelled cannot be moved: the
    thing being rescheduled is a plan, and once it is a fact there is nothing
    to move. That refusal is 409, not 403 — the caller had the right, the
    meeting was in the wrong state.
    """
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    if actor <= 0:
        raise PrivateMeetingRejected("Sign in required.", status=401,
                                     code="unauthorized")
    if actor != int(meeting.get("owner_user_id") or 0):
        # Deliberately the same 403 an unrelated stranger gets. A participant
        # learning "you may not reschedule" and a stranger learning "no such
        # meeting" are different leaks; this endpoint is reached by public_id,
        # which a participant already holds, so 403 leaks nothing new to them.
        raise PrivateMeetingRejected(
            "Only the host can reschedule this meeting.",
            status=403, code="forbidden")

    status = str(meeting.get("status") or "")
    if status not in (ST_DRAFT, ST_SCHEDULED):
        raise PrivateMeetingRejected(
            "This meeting can no longer be rescheduled.",
            status=409, code="not_reschedulable")

    sets: list[str] = []
    params: list[object] = []
    time_changed = False

    if scheduled_start_at is not None or timezone_name is not None:
        # The instant and the zone resolve together. Changing only the zone
        # still has to re-resolve, because "09:00 in the host's zone" means a
        # different instant once the zone changes -- and because a stored UTC
        # instant cannot be re-localized without the wall clock that produced
        # it. Re-sending the wall clock is the caller's job; the current zone
        # is the default when only the time moves.
        zone_in = (meeting.get("scheduled_timezone") or ""
                   if timezone_name is None else timezone_name)
        start_in = scheduled_start_at
        if start_in is None:
            start_in = str(meeting.get("scheduled_start_at") or "")
        new_start, new_zone = resolve_schedule(start_in, zone_in)
        old_start = str(meeting.get("scheduled_start_at") or "")
        old_zone = str(meeting.get("scheduled_timezone") or "")
        if new_start != old_start or new_zone != old_zone:
            time_changed = True
        sets.append("scheduled_start_at=?")
        params.append(new_start)
        sets.append("scheduled_timezone=?")
        params.append(new_zone)

    if duration_minutes is not None:
        new_duration = _clean_duration(duration_minutes, required=True)
        if new_duration != int(meeting.get("duration_minutes") or 0):
            # The end time is part of "when". An attendee who blocked out an
            # hour needs to know it became three.
            time_changed = True
        sets.append("duration_minutes=?")
        params.append(new_duration)

    if title is not None:
        sets.append("title=?")
        params.append(_clip(title, MAX_TITLE_CHARS))

    if agenda is not None:
        sets.append("agenda=?")
        params.append(_clip(agenda, MAX_AGENDA_CHARS))

    if not sets:
        raise PrivateMeetingRejected(
            "Nothing to change.", status=400, code="no_changes")

    if time_changed:
        sets.append("schedule_version=schedule_version+1")

    params.append(_now_iso())
    params.append(int(meeting["id"]))
    cur.execute(
        f"UPDATE {MEETINGS_TABLE} SET {', '.join(sets)}, updated_at=? WHERE id=?",
        tuple(params))

    meeting = _require_meeting(cur, int(meeting["id"]))
    if time_changed:
        # Retire the old version's plan and lay down a new one. Order matters
        # only for tidiness: the guard already refuses any survivor of the old
        # version, because the version on the meeting row has moved past it.
        _drop_reminders(cur, int(meeting["id"]), reason="schedule_changed",
                        before_version=int(meeting.get("schedule_version") or 1))
        _plan_reminders(cur, int(meeting["id"]))
        _announce(cur, meeting, "RESCHEDULE")
    _audit(cur, actor=actor, owner=actor,
           action=audit.ACTION_MEETING_RESCHEDULE, meeting_id=int(meeting["id"]))
    _telemetry.emit(_telemetry.EVENT_MEETING_LIFECYCLE,
                    transition="rescheduled" if time_changed else "edited",
                    end_reason="not_ended",
                    scheduled=bool(meeting.get("scheduled_start_at")),
                    waiting_room=bool(int(meeting.get("waiting_room_enabled") or 0)),
                    participant_count=_present_count(cur, int(meeting["id"])))
    return _project_meeting(cur, meeting, viewer_user_id=actor)


def join_meeting(cur, *, user_id: int, meeting_ref: object) -> dict:
    """Join by link (public_id) or by meeting code. Never leaks existence.

    Outcomes: WAITING_ROOM (hold), ADMITTED (proceed to token), or a refusal.
    A locked meeting refuses everyone new — link, code, and invite alike —
    while existing ADMITTED/JOINED participants may re-enter (reconnect is not
    "new").
    """
    _require_enabled()
    ensure_meetings_schema(cur)
    joiner = int(user_id or 0)
    if joiner <= 0:
        raise PrivateMeetingRejected("Sign in required.", status=401, code="unauthorized")
    not_found = PrivateMeetingRejected("Meeting not found.", status=404, code="not_found")
    meeting = _meeting_by_ref(cur, meeting_ref)
    if not meeting:
        raise not_found
    owner = int(meeting.get("owner_user_id") or 0)
    meeting_id = int(meeting["id"])
    participant = _participant_row(cur, meeting_id, joiner)
    if participant and participant.get("state") in {P_REMOVED, P_BLOCKED}:
        # Removal is final for this meeting; indistinguishable from absence.
        raise not_found
    if not participant and _blocked(cur, joiner, owner):
        # Blocked non-participants never learn the meeting exists.
        raise not_found
    if meeting.get("status") in MEETING_FINAL:
        raise PrivateMeetingRejected(
            "This meeting has ended.", status=410, code="meeting_over")
    if meeting.get("status") not in {ST_LIVE, ST_STARTING, ST_SCHEDULED, ST_WAITING}:
        raise PrivateMeetingRejected(
            "This meeting is not accepting participants.", status=409,
            code="not_joinable")
    is_returning = bool(participant) and participant.get("state") in ADMITTED_STATES
    if int(meeting.get("locked") or 0) and not is_returning:
        raise PrivateMeetingRejected(
            "This meeting is locked.", status=403, code="locked")
    if not is_returning and _present_count(cur, meeting_id) >= max_participants():
        raise PrivateMeetingRejected(
            "This meeting is full.", status=409, code="meeting_full")
    moderator = bool(participant) and participant.get("role") in MODERATOR_ROLES
    hold = bool(int(meeting.get("waiting_room_enabled") or 0)) and not moderator
    target_state = P_WAITING_ROOM if hold else P_ADMITTED
    if participant:
        if participant.get("state") not in ADMITTED_STATES:
            participant = _transition_participant(
                cur, participant, target_state,
                extra_sql=", left_at=''" if target_state == P_ADMITTED else "",
                extra_params=())
    else:
        participant = _insert_participant(
            cur, meeting_id=meeting_id, user_id=joiner, role=ROLE_PARTICIPANT,
            state=target_state)
    if participant.get("state") in ADMITTED_STATES and int(meeting.get("call_id") or 0):
        _ensure_call_participant(cur, int(meeting["call_id"]), joiner,
                                 host=(joiner == owner))
    if joiner == owner and str(meeting.get("host_disconnect_deadline") or ""):
        cur.execute(
            f"UPDATE {MEETINGS_TABLE} SET host_disconnect_deadline='', "
            f"updated_at=? WHERE id=?", (_now_iso(), meeting_id))
        meeting["host_disconnect_deadline"] = ""
    _audit(cur, actor=joiner, owner=owner, action=audit.ACTION_MEETING_JOIN,
           meeting_id=meeting_id,
           outcome=audit.OUTCOME_OK if participant.get("state") in ADMITTED_STATES
           else "held")
    _telemetry.emit(
        _telemetry.EVENT_MEETING_JOIN,
        outcome="admitted" if participant.get("state") in ADMITTED_STATES
        else "waiting_room",
        role=str(participant.get("role") or ""),
        returning=is_returning)
    return _project_meeting(cur, _meeting_by_ref(cur, meeting["public_id"]),
                            viewer_user_id=joiner)


def admit_participant(cur, *, actor_user_id: int, meeting_ref: object,
                      user_id: int) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    _require_moderator(cur, meeting, actor)
    target = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not target:
        raise PrivateMeetingRejected(
            "That person is not waiting.", status=404, code="not_participant")
    if target.get("state") in ADMITTED_STATES:
        return _project_participant(target)
    target = _transition_participant(
        cur, target, P_ADMITTED,
        extra_sql=", admitted_by_user_id=?, left_at=''",
        extra_params=(actor,))
    if int(meeting.get("call_id") or 0):
        _ensure_call_participant(cur, int(meeting["call_id"]), int(user_id),
                                 host=False)
    _audit(cur, actor=actor, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_ADMIT, meeting_id=int(meeting["id"]))
    return _project_participant(target)


def deny_participant(cur, *, actor_user_id: int, meeting_ref: object,
                     user_id: int) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    _require_moderator(cur, meeting, actor)
    target = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not target:
        raise PrivateMeetingRejected(
            "That person is not waiting.", status=404, code="not_participant")
    if target.get("role") == ROLE_HOST:
        raise PrivateMeetingRejected(
            "The host cannot be denied.", status=403, code="host_immutable")
    target = _transition_participant(
        cur, target, P_REMOVED,
        extra_sql=", removal_reason=?, left_at=?",
        extra_params=("denied", _now_iso()))
    _drop_call_participant(cur, int(meeting.get("call_id") or 0), int(user_id),
                           status="removed")
    _audit(cur, actor=actor, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_DENY, meeting_id=int(meeting["id"]))
    return _project_participant(target)


def set_locked(cur, *, actor_user_id: int, meeting_ref: object,
               locked: bool) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    _require_moderator(cur, meeting, actor)
    if meeting.get("status") in MEETING_FINAL:
        raise PrivateMeetingRejected(
            "This meeting is over.", status=410, code="meeting_over")
    now = _now_iso()
    cur.execute(
        f"UPDATE {MEETINGS_TABLE} SET locked=?, updated_at=? WHERE id=?",
        (1 if locked else 0, now, int(meeting["id"])))
    meeting["locked"] = 1 if locked else 0
    _audit(cur, actor=actor, owner=int(meeting["owner_user_id"]),
           action=(audit.ACTION_MEETING_LOCK if locked
                   else audit.ACTION_MEETING_UNLOCK),
           meeting_id=int(meeting["id"]))
    return _project_meeting(cur, meeting, viewer_user_id=actor)


def set_role(cur, *, actor_user_id: int, meeting_ref: object, user_id: int,
             role: str) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    if actor != int(meeting.get("owner_user_id") or 0):
        raise PrivateMeetingRejected(
            "Only the host can change roles.", status=403, code="forbidden")
    new_role = str(role or "").strip().upper()
    if new_role not in {ROLE_CO_HOST, ROLE_PARTICIPANT}:
        raise PrivateMeetingRejected(
            "Role must be CO_HOST or PARTICIPANT.", status=400, code="invalid_role")
    target = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not target:
        raise PrivateMeetingRejected(
            "Not a participant of this meeting.", status=404, code="not_participant")
    if target.get("role") == ROLE_HOST:
        raise PrivateMeetingRejected(
            "The host role cannot be changed.", status=403, code="host_immutable")
    now = _now_iso()
    cur.execute(
        f"UPDATE {PARTICIPANTS_TABLE} SET role=?, updated_at=? WHERE id=?",
        (new_role, now, int(target["id"])))
    target["role"] = new_role
    _audit(cur, actor=actor, owner=actor,
           action=audit.ACTION_MEETING_ROLE_CHANGE, meeting_id=int(meeting["id"]))
    return _project_participant(target)


def remove_participant(cur, *, actor_user_id: int, meeting_ref: object,
                       user_id: int, reason: str = "removed_by_moderator") -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    _require_moderator(cur, meeting, actor)
    target = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not target:
        raise PrivateMeetingRejected(
            "Not a participant of this meeting.", status=404, code="not_participant")
    if target.get("role") == ROLE_HOST:
        raise PrivateMeetingRejected(
            "The host cannot be removed.", status=403, code="host_immutable")
    if target.get("state") == P_REMOVED:
        return _project_participant(target)
    target = _transition_participant(
        cur, target, P_REMOVED,
        extra_sql=", removal_reason=?, left_at=?",
        extra_params=(_clip(reason, 64), _now_iso()))
    _drop_call_participant(cur, int(meeting.get("call_id") or 0), int(user_id),
                           status="removed")
    _drop_reminders(cur, int(meeting["id"]), reason="removed",
                    user_id=int(user_id))
    _audit(cur, actor=actor, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_REMOVE, meeting_id=int(meeting["id"]))
    return _project_participant(target)


def leave_meeting(cur, *, user_id: int, meeting_ref: object) -> dict:
    """Leave for yourself. The host leaving does NOT end the meeting — it arms
    the bounded host-disconnect window; :func:`sweep_meetings` ends the meeting
    only if no moderator returns before the deadline (mission §40)."""
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    leaver = int(user_id or 0)
    participant = _participant_row(cur, int(meeting["id"]), leaver)
    if not participant:
        raise PrivateMeetingRejected(
            "Not a participant of this meeting.", status=404, code="not_participant")
    if participant.get("state") not in {P_REMOVED, P_BLOCKED, P_LEFT}:
        participant = _transition_participant(
            cur, participant, P_LEFT,
            extra_sql=", left_at=?, raised_hand_at=''",
            extra_params=(_now_iso(),))
    _drop_call_participant(cur, int(meeting.get("call_id") or 0), leaver,
                           status="left")
    if leaver == int(meeting.get("owner_user_id") or 0) \
            and meeting.get("status") == ST_LIVE:
        deadline = (_now_dt() + timedelta(seconds=host_reconnect_seconds()))
        cur.execute(
            f"UPDATE {MEETINGS_TABLE} SET host_disconnect_deadline=?, "
            f"updated_at=? WHERE id=?",
            (deadline.isoformat(timespec="seconds"), _now_iso(),
             int(meeting["id"])))
    _audit(cur, actor=leaver, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_LEAVE, meeting_id=int(meeting["id"]))
    return _project_participant(participant)


def end_meeting(cur, *, actor_user_id: int, meeting_ref: object,
                reason: str = "ended_by_host") -> dict:
    """End for everyone. Moderator-only; idempotent on already-ended."""
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    if meeting.get("status") in MEETING_FINAL:
        return _project_meeting(cur, meeting, viewer_user_id=actor)
    _require_moderator(cur, meeting, actor)
    return _finalize_meeting(cur, meeting, actor=actor, reason=reason)


def _finalize_meeting(cur, meeting: dict, *, actor: int, reason: str) -> dict:
    now = _now_iso()
    if meeting.get("status") == ST_LIVE:
        meeting = _transition_meeting(cur, meeting, ST_ENDING)
    if meeting.get("status") == ST_ENDING:
        meeting = _transition_meeting(
            cur, meeting, ST_ENDED,
            extra_sql=", ended_at=?, end_reason=?, host_disconnect_deadline=''",
            extra_params=(now, _clip(reason, 64)))
    else:
        meeting = _transition_meeting(
            cur, meeting, ST_FAILED,
            extra_sql=", ended_at=?, end_reason=?, host_disconnect_deadline=''",
            extra_params=(now, _clip(reason, 64)))
    _end_room_call(cur, int(meeting.get("call_id") or 0), reason)
    states = sorted(ADMITTED_STATES - {P_ADMITTED})
    placeholders = ",".join(["?"] * len(states))
    cur.execute(
        f"UPDATE {PARTICIPANTS_TABLE} SET state=?, left_at=?, updated_at=? "
        f"WHERE meeting_id=? AND state IN ({placeholders})",
        (P_LEFT, now, now, int(meeting["id"]), *states))
    departed = max(0, int(getattr(cur, "rowcount", 0) or 0))
    # Waiting-room occupants simply expire — they were never in.
    cur.execute(
        f"UPDATE {PARTICIPANTS_TABLE} SET state=?, updated_at=? "
        f"WHERE meeting_id=? AND state=?",
        (P_EXPIRED, now, int(meeting["id"]), P_WAITING_ROOM))
    for rec in _open_recordings(cur, int(meeting["id"])):
        _close_recording(cur, rec, REC_COMPLETED if rec.get("status") == REC_ACTIVE
                         else REC_FAILED)
    _audit(cur, actor=actor, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_END, meeting_id=int(meeting["id"]))
    _telemetry.emit(
        _telemetry.EVENT_MEETING_LIFECYCLE,
        transition="ended" if meeting.get("status") == ST_ENDED else "failed",
        end_reason=reason,
        scheduled=bool(meeting.get("scheduled_start_at")),
        waiting_room=bool(int(meeting.get("waiting_room_enabled") or 0)),
        participant_count=departed)
    return _project_meeting(cur, meeting, viewer_user_id=actor)


# ---------------------------------------------------------------------------
# Presence marks — driven by the native client via the routes; idempotent.
# ---------------------------------------------------------------------------

def mark_joined(cur, *, user_id: int, meeting_ref: object) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    participant = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not participant or participant.get("state") not in ADMITTED_STATES:
        raise PrivateMeetingRejected(
            "Not admitted to this meeting.", status=403, code="not_admitted")
    if participant.get("state") != P_JOINED:
        first_join = not str(participant.get("joined_at") or "")
        _transition_participant(
            cur, participant, P_JOINED,
            extra_sql=", joined_at=?" if first_join else "",
            extra_params=(_now_iso(),) if first_join else ())
        participant = _participant_row(cur, int(meeting["id"]), int(user_id))
    return _project_participant(participant)


def mark_reconnecting(cur, *, user_id: int, meeting_ref: object) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    participant = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not participant or participant.get("state") not in PRESENT_STATES:
        raise PrivateMeetingRejected(
            "Not in this meeting.", status=403, code="not_admitted")
    participant = _transition_participant(cur, participant, P_RECONNECTING)
    return _project_participant(participant)


def rotate_code(cur, *, actor_user_id: int, meeting_ref: object) -> dict:
    """Revoke the shareable code — the old one stops resolving immediately."""
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    if actor != int(meeting.get("owner_user_id") or 0):
        raise PrivateMeetingRejected(
            "Only the host can rotate the code.", status=403, code="forbidden")
    for _ in range(20):
        candidate = _mint_meeting_code()
        cur.execute(
            f"SELECT 1 FROM {MEETINGS_TABLE} WHERE meeting_code=? LIMIT 1",
            (candidate,))
        if cur.fetchone() is None:
            now = _now_iso()
            cur.execute(
                f"UPDATE {MEETINGS_TABLE} SET meeting_code=?, code_rotated_at=?, "
                f"updated_at=? WHERE id=?",
                (candidate, now, now, int(meeting["id"])))
            meeting["meeting_code"] = candidate
            meeting["code_rotated_at"] = now
            _audit(cur, actor=actor, owner=actor,
                   action=audit.ACTION_MEETING_CODE_ROTATED,
                   meeting_id=int(meeting["id"]))
            return _project_meeting(cur, meeting, viewer_user_id=actor)
    raise PrivateMeetingRejected(
        "Could not allocate a meeting code.", status=503, code="code_exhausted")


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

def invite_users(cur, *, actor_user_id: int, meeting_ref: object,
                 user_ids: list[int], message: str = "") -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    _require_moderator(cur, meeting, actor)
    if meeting.get("status") in MEETING_FINAL:
        raise PrivateMeetingRejected(
            "This meeting is over.", status=410, code="meeting_over")
    meeting_id = int(meeting["id"])
    owner = int(meeting["owner_user_id"])
    clean_message = _clip(message, 280)
    invited: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()
    for raw in list(user_ids or [])[:50]:
        try:
            invitee = int(raw)
        except (TypeError, ValueError):
            continue
        if invitee <= 0 or invitee in seen:
            continue
        seen.add(invitee)
        if invitee == owner:
            skipped.append({"user_id": invitee, "reason": "is_host"})
            continue
        if _blocked(cur, actor, invitee) or _blocked(cur, owner, invitee):
            # Blocked either way, against either the inviter or the meeting
            # owner: silently skipped with a neutral reason — the inviter does
            # not learn which system or which direction.
            skipped.append({"user_id": invitee, "reason": "unavailable"})
            continue
        existing = _participant_row(cur, meeting_id, invitee)
        if existing and existing.get("state") in {P_REMOVED, P_BLOCKED}:
            skipped.append({"user_id": invitee, "reason": "unavailable"})
            continue
        if existing and existing.get("state") in ADMITTED_STATES:
            skipped.append({"user_id": invitee, "reason": "already_in"})
            continue
        now = _now_iso()
        cur.execute(
            f"SELECT id, status FROM {INVITES_TABLE} "
            f"WHERE meeting_id=? AND invitee_user_id=? LIMIT 1",
            (meeting_id, invitee))
        invite = _row(cur.fetchone())
        if invite:
            cur.execute(
                f"UPDATE {INVITES_TABLE} SET status=?, inviter_user_id=?, "
                f"message=?, responded_at='' WHERE id=?",
                (INVITE_PENDING, actor, clean_message, int(invite["id"])))
        else:
            cur.execute(
                f"""INSERT INTO {INVITES_TABLE}
                (meeting_id, inviter_user_id, invitee_user_id, status, message,
                 created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (meeting_id, actor, invitee, INVITE_PENDING, clean_message, now))
        if not existing:
            _insert_participant(
                cur, meeting_id=meeting_id, user_id=invitee,
                role=ROLE_PARTICIPANT, state=P_INVITED, invited_by=actor)
        invited.append(invitee)
    if invited:
        # Someone invited late still gets the reminders that have not gone yet;
        # planning is keyed on the recipient, so this adds rows for the new
        # people and leaves everyone else's plan exactly as it was.
        _plan_reminders(cur, meeting_id)
        # Only the new people. Re-confirming everyone else every time someone
        # else is added is how a meeting turns into a mailing list.
        _announce(cur, meeting, "CONFIRMATION", user_ids=invited)
    _audit(cur, actor=actor, owner=owner, action=audit.ACTION_MEETING_INVITE,
           meeting_id=meeting_id, count=len(invited))
    return {"invited": invited, "skipped": skipped}


def respond_invite(cur, *, user_id: int, meeting_ref: object,
                   accept: bool) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    invitee = int(user_id or 0)
    meeting_id = int(meeting["id"])
    cur.execute(
        f"SELECT * FROM {INVITES_TABLE} "
        f"WHERE meeting_id=? AND invitee_user_id=? LIMIT 1",
        (meeting_id, invitee))
    invite = _row(cur.fetchone())
    if not invite or invite.get("status") != INVITE_PENDING:
        raise PrivateMeetingRejected(
            "No pending invite.", status=404, code="no_invite")
    if accept and meeting.get("status") in MEETING_FINAL:
        raise PrivateMeetingRejected(
            "This meeting is over.", status=410, code="meeting_over")
    now = _now_iso()
    new_status = INVITE_ACCEPTED if accept else INVITE_DECLINED
    cur.execute(
        f"UPDATE {INVITES_TABLE} SET status=?, responded_at=? WHERE id=?",
        (new_status, now, int(invite["id"])))
    participant = _participant_row(cur, meeting_id, invitee)
    if participant and participant.get("state") == P_INVITED:
        if accept:
            hold = bool(int(meeting.get("waiting_room_enabled") or 0)) \
                and participant.get("role") not in MODERATOR_ROLES
            participant = _transition_participant(
                cur, participant, P_WAITING_ROOM if hold else P_ADMITTED)
            if participant.get("state") == P_ADMITTED \
                    and int(meeting.get("call_id") or 0):
                _ensure_call_participant(
                    cur, int(meeting["call_id"]), invitee, host=False)
        else:
            participant = _transition_participant(cur, participant, P_DECLINED)
            _drop_reminders(cur, meeting_id, reason="declined", user_id=invitee)
    return {"invite_status": new_status,
            "participant": _project_participant(participant) if participant else None}


# ---------------------------------------------------------------------------
# In-meeting chat, reactions, raise hand
# ---------------------------------------------------------------------------

def _require_present(cur, meeting: dict, user_id: int) -> dict:
    participant = _participant_row(cur, int(meeting["id"]), int(user_id))
    if not participant or participant.get("state") not in ADMITTED_STATES:
        raise PrivateMeetingRejected(
            "Not admitted to this meeting.", status=403, code="not_admitted")
    return participant


def post_message(cur, *, user_id: int, meeting_ref: object, body: str,
                 kind: str = KIND_TEXT) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    sender = int(user_id or 0)
    _require_present(cur, meeting, sender)
    if meeting.get("status") not in {ST_LIVE, ST_STARTING}:
        raise PrivateMeetingRejected(
            "Chat is only available while the meeting is live.",
            status=409, code="not_live")
    clean_kind = str(kind or KIND_TEXT).strip().lower()
    if clean_kind not in (MESSAGE_KINDS - {KIND_SYSTEM}):
        raise PrivateMeetingRejected(
            "Unknown message kind.", status=400, code="invalid_kind")
    limit = MAX_REACTION_CHARS if clean_kind == KIND_REACTION else MAX_MESSAGE_CHARS
    clean_body = str(body or "").strip()[:limit]
    if not clean_body:
        raise PrivateMeetingRejected(
            "Message body required.", status=400, code="empty_body")
    now = _now_iso()
    cur.execute(
        f"""INSERT INTO {MESSAGES_TABLE}
        (meeting_id, sender_user_id, kind, body, created_at)
        VALUES (?, ?, ?, ?, ?)""",
        (int(meeting["id"]), sender, clean_kind, clean_body, now))
    _audit(cur, actor=sender, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_MESSAGE, meeting_id=int(meeting["id"]))
    cur.execute(
        f"SELECT * FROM {MESSAGES_TABLE} WHERE meeting_id=? AND "
        f"sender_user_id=? ORDER BY id DESC LIMIT 1",
        (int(meeting["id"]), sender))
    return _project_message(_row(cur.fetchone()))


def list_messages(cur, *, user_id: int, meeting_ref: object,
                  since_id: int = 0, limit: int = 50) -> list[dict]:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    viewer = int(user_id or 0)
    participant = _participant_row(cur, int(meeting["id"]), viewer)
    if not participant or participant.get("state") in {P_REMOVED, P_BLOCKED,
                                                       P_WAITING_ROOM}:
        raise PrivateMeetingRejected(
            "Not admitted to this meeting.", status=403, code="not_admitted")
    capped = max(1, min(int(limit or 50), MAX_LIST_LIMIT))
    cur.execute(
        f"SELECT * FROM {MESSAGES_TABLE} WHERE meeting_id=? AND id>? "
        f"ORDER BY id ASC LIMIT ?",
        (int(meeting["id"]), max(0, int(since_id or 0)), capped))
    return [_project_message(_row(item)) for item in cur.fetchall()]


def set_raised_hand(cur, *, user_id: int, meeting_ref: object,
                    raised: bool) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    participant = _require_present(cur, meeting, int(user_id))
    now = _now_iso()
    value = now if raised else ""
    cur.execute(
        f"UPDATE {PARTICIPANTS_TABLE} SET raised_hand_at=?, updated_at=? "
        f"WHERE id=?", (value, now, int(participant["id"])))
    participant["raised_hand_at"] = value
    return _project_participant(participant)


# ---------------------------------------------------------------------------
# Recording — metadata only; media never touches this database. The provider
# call happens at the route layer; these writers hold the truthful states.
# ---------------------------------------------------------------------------

def _open_recordings(cur, meeting_id: int) -> list[dict]:
    states = sorted(RECORDING_OPEN)
    placeholders = ",".join(["?"] * len(states))
    cur.execute(
        f"SELECT * FROM {RECORDINGS_TABLE} WHERE meeting_id=? AND "
        f"status IN ({placeholders}) ORDER BY id ASC",
        (int(meeting_id), *states))
    return [_row(item) for item in cur.fetchall()]


def _close_recording(cur, recording: dict, status: str) -> None:
    now = _now_iso()
    cur.execute(
        f"UPDATE {RECORDINGS_TABLE} SET status=?, stopped_at=?, updated_at=? "
        f"WHERE id=?", (status, now, now, int(recording["id"])))


def start_recording(cur, *, actor_user_id: int, meeting_ref: object) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    if not recording_enabled():
        raise PrivateMeetingRejected(
            "Recording is not enabled.", status=403, code="recording_disabled")
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    _require_moderator(cur, meeting, actor)
    if meeting.get("status") != ST_LIVE:
        raise PrivateMeetingRejected(
            "Recording requires a live meeting.", status=409, code="not_live")
    if _open_recordings(cur, int(meeting["id"])):
        raise PrivateMeetingRejected(
            "A recording is already running.", status=409, code="already_recording")
    now = _now_iso()
    cur.execute(
        f"""INSERT INTO {RECORDINGS_TABLE}
        (meeting_id, started_by_user_id, status, started_at, created_at,
         updated_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (int(meeting["id"]), actor, REC_REQUESTED, now, now, now))
    _audit(cur, actor=actor, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_RECORDING_START,
           meeting_id=int(meeting["id"]))
    rows = _open_recordings(cur, int(meeting["id"]))
    return _project_recording(rows[-1]) if rows else {}


def mark_recording_active(cur, *, meeting_ref: object, recording_id: int,
                          provider_resource_id: str = "",
                          provider_sid: str = "") -> dict:
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    cur.execute(
        f"SELECT * FROM {RECORDINGS_TABLE} WHERE id=? AND meeting_id=? LIMIT 1",
        (int(recording_id), int(meeting["id"])))
    recording = _row(cur.fetchone())
    if not recording or recording.get("status") != REC_REQUESTED:
        raise PrivateMeetingRejected(
            "No requested recording.", status=409, code="invalid_recording_state")
    now = _now_iso()
    cur.execute(
        f"UPDATE {RECORDINGS_TABLE} SET status=?, provider_resource_id=?, "
        f"provider_sid=?, updated_at=? WHERE id=?",
        (REC_ACTIVE, _clip(provider_resource_id, 128), _clip(provider_sid, 128),
         now, int(recording["id"])))
    recording.update({"status": REC_ACTIVE})
    return _project_recording(recording)


def stop_recording(cur, *, actor_user_id: int, meeting_ref: object,
                   failed: bool = False, storage_ref: str = "") -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(actor_user_id or 0)
    _require_moderator(cur, meeting, actor)
    open_rows = _open_recordings(cur, int(meeting["id"]))
    if not open_rows:
        raise PrivateMeetingRejected(
            "No recording is running.", status=409, code="not_recording")
    recording = open_rows[-1]
    final = REC_FAILED if failed or recording.get("status") == REC_REQUESTED \
        else REC_COMPLETED
    now = _now_iso()
    cur.execute(
        f"UPDATE {RECORDINGS_TABLE} SET status=?, stopped_at=?, storage_ref=?, "
        f"updated_at=? WHERE id=?",
        (final, now, _clip(storage_ref, 256), now, int(recording["id"])))
    recording.update({"status": final, "stopped_at": now})
    _audit(cur, actor=actor, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_RECORDING_STOP,
           meeting_id=int(meeting["id"]))
    return _project_recording(recording)


# ---------------------------------------------------------------------------
# UNDX artifacts — provenance-tagged, fabrication structurally refused
# ---------------------------------------------------------------------------

def save_artifact(cur, *, user_id: int, meeting_ref: object,
                  artifact_type: str, provenance: str, title: str,
                  content: str, evidence_refs: str = "") -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    actor = int(user_id or 0)
    participant = _participant_row(cur, int(meeting["id"]), actor)
    if not participant or participant.get("state") in {P_REMOVED, P_BLOCKED,
                                                       P_WAITING_ROOM}:
        raise PrivateMeetingRejected(
            "Only meeting participants can save artifacts.",
            status=403, code="not_admitted")
    clean_type = str(artifact_type or "").strip().upper()
    if clean_type not in ARTIFACT_TYPES:
        raise PrivateMeetingRejected(
            "Unknown artifact type.", status=400, code="invalid_artifact_type")
    clean_prov = str(provenance or "").strip().upper()
    if clean_prov not in PROVENANCES:
        raise PrivateMeetingRejected(
            "Unknown provenance.", status=400, code="invalid_provenance")
    if clean_prov == PROV_TRANSCRIPT and not transcription_configured():
        # The fabrication refusal (mission §31/§34): no transcript exists, so
        # nothing can honestly be derived from one.
        raise PrivateMeetingRejected(
            "No transcript exists for this meeting — transcription is not "
            "configured.", status=409, code="transcript_unavailable")
    clean_title = _clip(title, MAX_TITLE_CHARS)
    clean_content = str(content or "").strip()[:MAX_ARTIFACT_CONTENT_CHARS]
    if not clean_title or not clean_content:
        raise PrivateMeetingRejected(
            "Artifact title and content required.", status=400, code="empty_body")
    now = _now_iso()
    cur.execute(
        f"""INSERT INTO {MEETING_ARTIFACT_TABLE}
        (meeting_id, owner_user_id, artifact_type, provenance, title, content,
         evidence_refs, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(meeting["id"]), actor, clean_type, clean_prov, clean_title,
         clean_content, _clip(evidence_refs, 512), now))
    _audit(cur, actor=actor, owner=actor,
           action=audit.ACTION_MEETING_ARTIFACT_SAVE,
           meeting_id=int(meeting["id"]))
    cur.execute(
        f"SELECT * FROM {MEETING_ARTIFACT_TABLE} WHERE meeting_id=? AND "
        f"owner_user_id=? ORDER BY id DESC LIMIT 1",
        (int(meeting["id"]), actor))
    return _project_artifact(_row(cur.fetchone()))


def list_artifacts(cur, *, user_id: int, meeting_ref: object,
                   limit: int = DEFAULT_LIST_LIMIT) -> list[dict]:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    viewer = int(user_id or 0)
    cur.execute(
        f"SELECT * FROM {MEETING_ARTIFACT_TABLE} WHERE meeting_id=? AND "
        f"owner_user_id=? ORDER BY id DESC LIMIT ?",
        (int(meeting["id"]), viewer,
         max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))))
    return [_project_artifact(_row(item)) for item in cur.fetchall()]


def build_intelligence(cur, *, user_id: int, meeting_ref: object) -> dict:
    """Deterministic meeting intelligence (mission §32-36).

    Every fact below is computed from stored rows and tagged SYSTEM_FACT.
    There is no model call anywhere on this path, so fabrication is
    structurally impossible: nothing here can describe what was *said* in the
    meeting — only what the server recorded *happening*. The draft is never
    auto-saved; saving goes through save_artifact() with USER_CONFIRMED
    provenance after a human reviewed and (optionally) edited it, and
    TRANSCRIPT_DERIVED remains refused while no transcript exists.
    """
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    viewer = int(user_id or 0)
    participant = _participant_row(cur, int(meeting["id"]), viewer)
    if not participant or participant.get("state") in {P_REMOVED, P_BLOCKED,
                                                       P_WAITING_ROOM}:
        raise PrivateMeetingRejected(
            "Only meeting participants can view meeting intelligence.",
            status=403, code="not_admitted")
    meeting_id = int(meeting["id"])
    facts: list[dict] = []

    def _fact(kind: str, text: str) -> None:
        facts.append({"kind": kind, "text": text, "provenance": PROV_SYSTEM})

    title = str(meeting.get("title") or "")
    _fact("status", f'Meeting "{title}" is {meeting.get("status") or ""}.')
    started = str(meeting.get("started_at") or "")
    ended = str(meeting.get("ended_at") or "")
    if started:
        _fact("timing", f"Started at {started}.")
    if ended:
        reason = str(meeting.get("end_reason") or "")
        suffix = f" (reason: {reason})." if reason else "."
        _fact("timing", f"Ended at {ended}{suffix}")
    start_dt = _parse_iso(started)
    end_dt = _parse_iso(ended)
    if start_dt and end_dt and end_dt >= start_dt:
        minutes = int((end_dt - start_dt).total_seconds() // 60)
        _fact("timing", f"Duration: {minutes} minute(s).")

    cur.execute(
        f"SELECT * FROM {PARTICIPANTS_TABLE} WHERE meeting_id=? "
        f"ORDER BY id ASC LIMIT 100", (meeting_id,))
    roster = [_row(item) for item in cur.fetchall()]
    attended = [row for row in roster if str(row.get("joined_at") or "")]
    _fact("attendance",
          f"{len(attended)} of {len(roster)} invited participant(s) joined.")
    for row in attended:
        left = str(row.get("left_at") or "")
        suffix = f", left at {left}." if left else "."
        _fact("attendance",
              f"User {int(row.get('user_id') or 0)} ({row.get('role')}) "
              f"joined at {row.get('joined_at')}{suffix}")

    counts: dict[str, int] = {}
    senders = 0
    for kind in (KIND_TEXT, KIND_REACTION):
        cur.execute(
            f"SELECT COUNT(*) AS n FROM {MESSAGES_TABLE} "
            f"WHERE meeting_id=? AND kind=?", (meeting_id, kind))
        counts[kind] = int(_row(cur.fetchone()).get("n") or 0)
    cur.execute(
        f"SELECT COUNT(DISTINCT sender_user_id) AS n FROM {MESSAGES_TABLE} "
        f"WHERE meeting_id=? AND kind=?", (meeting_id, KIND_TEXT))
    senders = int(_row(cur.fetchone()).get("n") or 0)
    _fact("chat",
          f"{counts[KIND_TEXT]} chat message(s) from {senders} sender(s); "
          f"{counts[KIND_REACTION]} reaction(s).")

    cur.execute(
        f"SELECT * FROM {RECORDINGS_TABLE} WHERE meeting_id=? "
        f"ORDER BY id ASC LIMIT 20", (meeting_id,))
    recordings = [_row(item) for item in cur.fetchall()]
    if recordings:
        for rec in recordings:
            stopped = str(rec.get("stopped_at") or "")
            suffix = f" until {stopped}." if stopped else "."
            _fact("recording",
                  f"Recording #{int(rec.get('id') or 0)} "
                  f"({rec.get('status') or ''}) started by user "
                  f"{int(rec.get('started_by_user_id') or 0)} at "
                  f"{rec.get('started_at')}{suffix}")
    else:
        _fact("recording", "No recording was made.")

    transcript_available = transcription_configured()
    limitations = {
        "transcript_available": transcript_available,
        "note": ("" if transcript_available else
                 "No transcript exists for this meeting — transcription is "
                 "not configured. Nothing below describes what was said out "
                 "loud; every fact is a server-recorded event."),
    }
    draft_content = "\n".join(f"- {item['text']}" for item in facts)
    draft = {
        "artifact_type": "SUMMARY",
        "title": _clip(f"Summary: {title}", MAX_TITLE_CHARS),
        "content": draft_content[:MAX_ARTIFACT_CONTENT_CHARS],
        "requires_confirmation": True,
        "save_provenance": PROV_USER,
    }
    return {
        "meeting_ref": str(meeting.get("public_id") or ""),
        "generated_at": _now_iso(),
        "facts": facts,
        "limitations": limitations,
        "draft": draft,
    }


# ---------------------------------------------------------------------------
# Projections + reads
# ---------------------------------------------------------------------------

def capability_states() -> dict[str, Any]:
    """Truthful availability, one place. UI renders exactly this — no screen
    invents a capability the server did not assert."""
    return {
        "screen_share": {
            "available": False,
            "reason": "not_implemented",
            # Escalated per mission §3: requires a third Agora engine owner
            # (ReplayKit broadcast-upload extension). STOP AND REPORT filed in
            # PRIVATE_MEETINGS_FOUNDATION_MAP.md §5.
        },
        "captions": {
            "available": transcription_configured(),
            "reason": "" if transcription_configured() else "provider_required",
        },
        "recording": {
            "available": recording_enabled(),
            "reason": "" if recording_enabled() else "flag_disabled",
        },
    }


def _project_participant(row: dict) -> dict:
    return {
        "user_id": int(row.get("user_id") or 0),
        "role": str(row.get("role") or ROLE_PARTICIPANT),
        "state": str(row.get("state") or ""),
        "rtc_uid": int(row.get("rtc_uid") or 0),
        "raised_hand": bool(str(row.get("raised_hand_at") or "")),
        "raised_hand_at": str(row.get("raised_hand_at") or ""),
        "joined_at": str(row.get("joined_at") or ""),
        "left_at": str(row.get("left_at") or ""),
    }


def _project_message(row: dict) -> dict:
    return {
        "id": int(row.get("id") or 0),
        "meeting_id": int(row.get("meeting_id") or 0),
        "sender_user_id": int(row.get("sender_user_id") or 0),
        "kind": str(row.get("kind") or KIND_TEXT),
        "body": str(row.get("body") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def _project_recording(row: dict) -> dict:
    return {
        "id": int(row.get("id") or 0),
        "status": str(row.get("status") or ""),
        "started_by_user_id": int(row.get("started_by_user_id") or 0),
        "started_at": str(row.get("started_at") or ""),
        "stopped_at": str(row.get("stopped_at") or ""),
    }


def _project_artifact(row: dict) -> dict:
    return {
        "id": int(row.get("id") or 0),
        "meeting_id": int(row.get("meeting_id") or 0),
        "artifact_type": str(row.get("artifact_type") or ""),
        "provenance": str(row.get("provenance") or ""),
        "title": str(row.get("title") or ""),
        "content": str(row.get("content") or ""),
        "evidence_refs": str(row.get("evidence_refs") or ""),
        "saved_record_id": int(row.get("saved_record_id") or 0),
        "created_at": str(row.get("created_at") or ""),
    }


def _project_meeting(cur, meeting: dict, *, viewer_user_id: int) -> dict:
    """The meeting as one viewer may see it. The code is host-only; the
    call_public_id is only handed to ADMITTED+ viewers — a waiting-room
    occupant cannot even name the call it is not in."""
    viewer = int(viewer_user_id or 0)
    meeting_id = int(meeting.get("id") or 0)
    me = _participant_row(cur, meeting_id, viewer) if meeting_id else {}
    is_host = viewer == int(meeting.get("owner_user_id") or 0)
    admitted = bool(me) and me.get("state") in ADMITTED_STATES
    cur.execute(
        f"SELECT * FROM {PARTICIPANTS_TABLE} WHERE meeting_id=? "
        f"ORDER BY id ASC LIMIT 100", (meeting_id,))
    rows = [_row(item) for item in cur.fetchall()]
    visible_states = PARTICIPANT_STATES - {P_BLOCKED}
    moderator = bool(me) and me.get("role") in MODERATOR_ROLES
    if not moderator:
        visible_states = visible_states - {P_WAITING_ROOM, P_INVITED, P_RINGING,
                                           P_DECLINED, P_EXPIRED, P_REMOVED}
    participants = [_project_participant(row) for row in rows
                    if row.get("state") in visible_states]
    open_recs = _open_recordings(cur, meeting_id) if meeting_id else []
    payload = {
        "public_id": str(meeting.get("public_id") or ""),
        "title": str(meeting.get("title") or ""),
        "status": str(meeting.get("status") or ""),
        "waiting_room_enabled": bool(int(meeting.get("waiting_room_enabled") or 0)),
        "locked": bool(int(meeting.get("locked") or 0)),
        # The instant is canonical UTC; the zone is what the host chose. The
        # client needs both: rendering the UTC instant in the *device's* zone
        # would silently relabel a meeting the host booked as 09:00 Tokyo.
        "scheduled_start_at": str(meeting.get("scheduled_start_at") or ""),
        "scheduled_timezone": str(meeting.get("scheduled_timezone") or ""),
        "schedule_version": int(meeting.get("schedule_version") or 1),
        "agenda": str(meeting.get("agenda") or ""),
        "duration_minutes": int(meeting.get("duration_minutes") or 0),
        "started_at": str(meeting.get("started_at") or ""),
        "ended_at": str(meeting.get("ended_at") or ""),
        "end_reason": str(meeting.get("end_reason") or ""),
        "owner_user_id": int(meeting.get("owner_user_id") or 0),
        "me": _project_participant(me) if me else None,
        "participants": participants,
        "recording_active": any(r.get("status") == REC_ACTIVE for r in open_recs),
        "capabilities": capability_states(),
        "created_at": str(meeting.get("created_at") or ""),
    }
    if is_host:
        payload["meeting_code"] = str(meeting.get("meeting_code") or "")
        payload["code_rotated_at"] = str(meeting.get("code_rotated_at") or "")
    if admitted:
        payload["call_public_id"] = str(meeting.get("call_public_id") or "")
        payload["channel_name"] = str(meeting.get("channel_name") or "")
    return payload


def get_meeting(cur, *, user_id: int, meeting_ref: object) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    meeting = _require_meeting(cur, meeting_ref)
    viewer = int(user_id or 0)
    participant = _participant_row(cur, int(meeting["id"]), viewer)
    if not participant and viewer != int(meeting.get("owner_user_id") or 0):
        raise PrivateMeetingRejected(
            "Meeting not found.", status=404, code="not_found")
    if participant and participant.get("state") in {P_REMOVED, P_BLOCKED}:
        raise PrivateMeetingRejected(
            "Meeting not found.", status=404, code="not_found")
    _audit(cur, actor=viewer, owner=int(meeting["owner_user_id"]),
           action=audit.ACTION_MEETING_READ, meeting_id=int(meeting["id"]))
    return _project_meeting(cur, meeting, viewer_user_id=viewer)


def list_meetings(cur, *, user_id: int,
                  limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """Meetings home: live now, upcoming scheduled, recent history — only
    meetings this user participates in and was not removed from."""
    _require_enabled()
    ensure_meetings_schema(cur)
    viewer = int(user_id or 0)
    capped = max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
    excluded = (P_REMOVED, P_BLOCKED)
    cur.execute(
        f"""SELECT m.* FROM {MEETINGS_TABLE} m
        JOIN {PARTICIPANTS_TABLE} p ON p.meeting_id = m.id
        WHERE p.user_id=? AND p.state NOT IN (?, ?)
        ORDER BY m.id DESC LIMIT 200""",
        (viewer, *excluded))
    rows = [_row(item) for item in cur.fetchall()]
    live = [m for m in rows if m.get("status") in {ST_LIVE, ST_STARTING}]
    upcoming = sorted(
        (m for m in rows if m.get("status") == ST_SCHEDULED),
        key=lambda m: str(m.get("scheduled_start_at") or ""))
    recent = [m for m in rows if m.get("status") in MEETING_FINAL]
    return {
        "live": [_project_meeting(cur, m, viewer_user_id=viewer)
                 for m in live[:capped]],
        "upcoming": [_project_meeting(cur, m, viewer_user_id=viewer)
                     for m in upcoming[:capped]],
        "recent": [_project_meeting(cur, m, viewer_user_id=viewer)
                   for m in recent[:capped]],
    }


def _calendar_bounds(start: object, end: object) -> tuple[datetime, datetime]:
    """Parse and sanity-check a requested window.

    Both ends are required. A half-open request ("everything after March") is
    refused rather than defaulted, because every sensible default here is a
    guess about how much history the caller wanted, and the wrong guess is the
    unbounded one.
    """
    first = _parse_iso(start)
    last = _parse_iso(end)
    if not first or not last:
        raise PrivateMeetingRejected(
            "A calendar window needs a start and an end.",
            status=400, code="invalid_range")
    if last <= first:
        raise PrivateMeetingRejected(
            "The calendar window ends before it starts.",
            status=400, code="invalid_range")
    if (last - first) > timedelta(days=MAX_CALENDAR_SPAN_DAYS):
        raise PrivateMeetingRejected(
            "That calendar window is too wide.",
            status=400, code="range_too_wide")
    return first, last


def calendar_range(cur, *, user_id: int, start: object, end: object,
                   timezone_name: object = "") -> dict:
    """Meetings inside one bounded window, grouped by local day.

    This is what the month grid reads, and it is deliberately a different
    query from :func:`list_meetings`. That one answers "what is happening
    around now" and is anchored to the present; a calendar is anchored to
    whatever month the user scrolled to, which may be in 2045 and may contain
    the only three meetings that matter. Filtering the recent-first list in
    the client would show an empty March 2045 forever while the rows sat in
    the table, and widening that list until far-future rows appeared would
    mean loading a lifetime to render six weeks.

    Days are keyed in ``timezone_name`` rather than UTC because "which day is
    this meeting on" is a local question: 23:30 UTC on the 4th is the 5th in
    Tokyo, and a grid that disagrees with the invitation is worse than no
    grid. The zone the *host* chose still travels on each entry, so a viewer
    in another country sees the cell in their own calendar and the time in the
    organiser's words.
    """
    _require_enabled()
    ensure_meetings_schema(cur)
    viewer = int(user_id or 0)
    first, last = _calendar_bounds(start, end)
    zone_name = normalize_timezone(timezone_name)
    zone = ZoneInfo(zone_name) if zone_name else timezone.utc
    cur.execute(
        f"""SELECT m.* FROM {MEETINGS_TABLE} m
        JOIN {PARTICIPANTS_TABLE} p ON p.meeting_id = m.id
        WHERE p.user_id=? AND p.state NOT IN (?, ?)
          AND m.scheduled_start_at >= ? AND m.scheduled_start_at < ?
        ORDER BY m.scheduled_start_at ASC LIMIT ?""",
        (viewer, P_REMOVED, P_BLOCKED,
         _to_utc_iso(first), _to_utc_iso(last), MAX_CALENDAR_ROWS + 1))
    rows = [_row(item) for item in cur.fetchall()]
    truncated = len(rows) > MAX_CALENDAR_ROWS
    rows = rows[:MAX_CALENDAR_ROWS]
    entries: list[dict] = []
    days: dict[str, int] = {}
    for meeting in rows:
        moment = _parse_iso(meeting.get("scheduled_start_at"))
        if not moment:
            continue
        day = moment.astimezone(zone).date().isoformat()
        days[day] = days.get(day, 0) + 1
        # A summary, not a projection. The grid draws a dot and a title; the
        # participant list, the code and the call identity are all answers to
        # questions a calendar cell never asks, and running the full
        # projection for every meeting in a year would fan one request out
        # into hundreds of participant queries.
        entries.append({
            "public_id": str(meeting.get("public_id") or ""),
            "title": str(meeting.get("title") or ""),
            "status": str(meeting.get("status") or ""),
            "scheduled_start_at": str(meeting.get("scheduled_start_at") or ""),
            "scheduled_timezone": str(meeting.get("scheduled_timezone") or ""),
            "duration_minutes": int(meeting.get("duration_minutes") or 0),
            "local_day": day,
            "is_host": viewer == int(meeting.get("owner_user_id") or 0),
        })
    return {
        "start": _to_utc_iso(first),
        "end": _to_utc_iso(last),
        "timezone": zone_name,
        "days": days,
        "meetings": entries,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Sweep — no zombie meetings (mission §41). Callable from any worker tick or
# lazily from the routes; every rule is idempotent.
# ---------------------------------------------------------------------------

def sweep_meetings(cur, *, now: datetime | None = None) -> int:
    ensure_meetings_schema(cur)
    moment = now or _now_dt()
    swept = 0
    cur.execute(
        f"SELECT * FROM {MEETINGS_TABLE} WHERE status IN (?, ?, ?, ?) "
        f"ORDER BY id ASC LIMIT 200",
        (ST_LIVE, ST_STARTING, ST_WAITING, ST_SCHEDULED))
    for item in cur.fetchall():
        meeting = _row(item)
        status = str(meeting.get("status") or "")
        owner = int(meeting.get("owner_user_id") or 0)
        try:
            if status == ST_SCHEDULED:
                scheduled = _parse_iso(meeting.get("scheduled_start_at"))
                if scheduled and moment - scheduled > timedelta(hours=24):
                    _transition_meeting(
                        cur, meeting, ST_CANCELLED,
                        extra_sql=", ended_at=?, end_reason=?",
                        extra_params=(_now_iso(), "never_started"))
                    _telemetry.emit(
                        _telemetry.EVENT_MEETING_LIFECYCLE,
                        transition="cancelled", end_reason="never_started",
                        scheduled=True,
                        waiting_room=bool(int(meeting.get("waiting_room_enabled") or 0)),
                        participant_count=0)
                    swept += 1
                continue
            if status == ST_STARTING:
                updated = _parse_iso(meeting.get("updated_at"))
                if updated and moment - updated > timedelta(minutes=5):
                    _finalize_meeting(cur, meeting, actor=owner,
                                      reason="start_timed_out")
                    swept += 1
                continue
            # LIVE / WAITING below.
            started = _parse_iso(meeting.get("started_at"))
            if started and (moment - started).total_seconds() > max_meeting_seconds():
                _finalize_meeting(cur, meeting, actor=owner, reason="max_duration")
                swept += 1
                continue
            deadline = _parse_iso(meeting.get("host_disconnect_deadline"))
            if deadline and moment > deadline:
                # Bounded host-disconnect window (§40): a co-host present and
                # JOINED keeps the meeting alive; otherwise it ends honestly.
                cur.execute(
                    f"SELECT 1 FROM {PARTICIPANTS_TABLE} WHERE meeting_id=? "
                    f"AND role=? AND state=? LIMIT 1",
                    (int(meeting["id"]), ROLE_CO_HOST, P_JOINED))
                if cur.fetchone() is None:
                    _finalize_meeting(cur, meeting, actor=owner,
                                      reason="host_disconnected")
                    swept += 1
                    continue
                cur.execute(
                    f"UPDATE {MEETINGS_TABLE} SET host_disconnect_deadline='', "
                    f"updated_at=? WHERE id=?", (_now_iso(), int(meeting["id"])))
            call = _call_row(cur, int(meeting.get("call_id") or 0))
            if call and str(call.get("status") or "") in call_engine.FINAL_STATUSES:
                # The engine's stale sweeper expired our transport out from
                # under us — the meeting must not outlive its call.
                _finalize_meeting(cur, meeting, actor=owner, reason="call_expired")
                swept += 1
                continue
            states = sorted(PRESENT_STATES)
            placeholders = ",".join(["?"] * len(states))
            cur.execute(
                f"SELECT COUNT(*) AS n FROM {PARTICIPANTS_TABLE} "
                f"WHERE meeting_id=? AND state IN ({placeholders})",
                (int(meeting["id"]), *states))
            present = int((_row(cur.fetchone())).get("n") or 0)
            updated = _parse_iso(meeting.get("updated_at"))
            if present == 0 and updated and \
                    (moment - updated).total_seconds() > empty_meeting_timeout_seconds():
                _finalize_meeting(cur, meeting, actor=owner, reason="empty_timeout")
                swept += 1
        except PrivateMeetingRejected as exc:
            LOGGER.warning("PRIVATE_MEETING_SWEEP_SKIP meeting=%s error=%s",
                           meeting.get("public_id"), exc)
    if swept:
        # Only when something moved: the sweep rides list/get requests, and a
        # per-request "swept 0" line is noise that buries the real signal.
        _telemetry.emit(_telemetry.EVENT_MEETING_SWEEP, swept_count=swept)
    return swept
