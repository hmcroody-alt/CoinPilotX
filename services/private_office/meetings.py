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

from pulse_communications_v2 import service as comm_service
from services import pulsesoc_communications_engine as call_engine
from services.private_office import audit

LOGGER = logging.getLogger("private_office.meetings")

MEETINGS_TABLE = "private_meetings"
PARTICIPANTS_TABLE = "private_meeting_participants"
INVITES_TABLE = "private_meeting_invites"
MESSAGES_TABLE = "private_meeting_messages"
RECORDINGS_TABLE = "private_meeting_recordings"
MEETING_ARTIFACT_TABLE = "private_meeting_artifacts"

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

MAX_TITLE_CHARS = 200
MAX_MESSAGE_CHARS = 2000
MAX_REACTION_CHARS = 16
MAX_ARTIFACT_CONTENT_CHARS = 8000
MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 30

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

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_pm_meetings_owner "
    f"ON {MEETINGS_TABLE} (owner_user_id, status, id)",
    f"CREATE INDEX IF NOT EXISTS idx_pm_meetings_status "
    f"ON {MEETINGS_TABLE} (status, updated_at)",
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
                   instant: bool = False) -> dict:
    _require_enabled()
    ensure_meetings_schema(cur)
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateMeetingRejected("Owner required.", status=401, code="unauthorized")
    clean_title = _clip(title, MAX_TITLE_CHARS)
    scheduled = ""
    if not instant:
        parsed = _parse_iso(scheduled_start_at)
        if not parsed:
            raise PrivateMeetingRejected(
                "A scheduled meeting needs a valid start time.",
                status=400, code="invalid_schedule")
        scheduled = parsed.isoformat(timespec="seconds")
    duration = max(0, min(int(duration_minutes or 0), 1440))
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
         waiting_room_enabled, locked, scheduled_start_at, duration_minutes,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
        (owner, public_id, meeting_code, clean_title, status,
         1 if waiting_room_enabled else 0, scheduled, duration, now, now),
    )
    meeting = _require_meeting(cur, public_id)
    _insert_participant(
        cur, meeting_id=int(meeting["id"]), user_id=owner, role=ROLE_HOST,
        state=P_ADMITTED, invited_by=owner, admitted_by=owner)
    _audit(cur, actor=owner, owner=owner, action=audit.ACTION_MEETING_CREATE,
           meeting_id=int(meeting["id"]))
    if instant:
        return start_meeting(cur, actor_user_id=owner, meeting_ref=public_id)
    return _project_meeting(cur, meeting, viewer_user_id=owner)


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
    _audit(cur, actor=actor, owner=actor, action=audit.ACTION_MEETING_CANCEL,
           meeting_id=int(meeting["id"]))
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
        "scheduled_start_at": str(meeting.get("scheduled_start_at") or ""),
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
    return swept
