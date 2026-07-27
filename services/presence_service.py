"""Unified, server-authoritative presence for PulseSoc.

This module is the single source of truth for whether a user is online, when
they were last seen, and what they are currently doing. Every PulseSoc
subsystem (Messenger, Feed, Profiles, Search, Live, Calls, Groups, Rooms,
Communities, Notifications, UNDX) must read presence from here. No subsystem
may keep its own presence logic or synthesise an online state.

Design notes
------------
Presence is derived, never stored as a standing truth. A device writes a
heartbeat; the heartbeat carries an expiry. A session is live if and only if
it has not been revoked and its expiry is still in the future. Therefore:

    online(user) == any live session for that user

Because the answer is computed from timestamps at read time, presence cannot
go stale. If every background worker dies, if the process is killed, if the
scheduler never runs, presence still degrades correctly to offline on its own.
This is deliberate: the defect this module replaces was a presence row that
was only ever written with 'online' and had nothing to ever set it back.

Multi-device is handled by tracking sessions rather than users. Closing a
laptop revokes one session; the phone's session keeps the user online. A user
is only offline once every one of their sessions has expired or been revoked.

Activity states (typing, recording voice, in a call, hosting live) also carry
their own short expiry, so a client that disappears mid-typing cannot leave a
stuck indicator behind.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


# How often a connected client should heartbeat.
HEARTBEAT_INTERVAL_SECONDS = _env_int("PULSE_PRESENCE_HEARTBEAT_SECONDS", 45, 10, 300)

# How long after the last heartbeat a session stays live. Two missed
# heartbeats tolerates one dropped request or a brief tunnel without
# flapping the user offline.
GRACE_PERIOD_SECONDS = _env_int("PULSE_PRESENCE_GRACE_SECONDS", 90, 20, 600)

# Idle time before a live session is reported as 'away' rather than 'online'.
AWAY_AFTER_SECONDS = _env_int("PULSE_PRESENCE_AWAY_SECONDS", 300, 60, 3600)

# Activity indicators (typing, recording, uploading) expire fast so they can
# never get stuck on screen.
ACTIVITY_TTL_SECONDS = _env_int("PULSE_PRESENCE_ACTIVITY_TTL_SECONDS", 12, 5, 120)

# Long-lived activities are bound to the session lifetime instead.
SESSION_BOUND_ACTIVITIES = {
    "in_audio_call",
    "in_video_call",
    "live_hosting",
    "live_guest",
    "live_watching",
}

TRANSIENT_ACTIVITIES = {
    "typing",
    "recording_voice",
    "uploading_media",
    "sending_files",
}

VALID_ACTIVITIES = {"idle"} | SESSION_BOUND_ACTIVITIES | TRANSIENT_ACTIVITIES

VALID_STATUSES = {"online", "away", "offline"}

# Presence rows older than this with no live session are pruned opportunistically.
SESSION_RETENTION_DAYS = 30


class PresenceError(ValueError):
    """Raised when a caller supplies an invalid presence argument."""


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_now() -> str:
    return iso(utc_now())


def parse_dt(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _user_id(value) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        raise PresenceError("invalid_user_id")
    if parsed <= 0:
        raise PresenceError("invalid_user_id")
    return parsed


def normalize_activity(value) -> str:
    activity = str(value or "idle").strip().lower()
    if activity not in VALID_ACTIVITIES:
        raise PresenceError("invalid_activity")
    return activity


def normalize_device_label(value) -> str:
    label = str(value or "").strip().lower()[:40]
    return label or "unknown"


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

_SCHEMA_READY = False


def ensure_schema(cur, conn=None) -> bool:
    """Create the presence tables. Safe to call on every request."""
    global _SCHEMA_READY
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                device_id TEXT,
                device_label TEXT,
                platform TEXT,
                status TEXT NOT NULL DEFAULT 'online',
                activity TEXT NOT NULL DEFAULT 'idle',
                activity_context TEXT,
                activity_expires_at TEXT,
                invisible INTEGER NOT NULL DEFAULT 0,
                connected_at TEXT,
                last_heartbeat_at TEXT,
                expires_at TEXT,
                revoked_at TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_presence_sessions_user ON presence_sessions(user_id, expires_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_presence_sessions_expiry ON presence_sessions(expires_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_presence_sessions_device ON presence_sessions(user_id, device_id)"
        )
        # Durable last-seen survives session pruning.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_last_seen (
                user_id INTEGER PRIMARY KEY,
                last_seen_at TEXT,
                last_device_label TEXT,
                updated_at TEXT
            )
            """
        )
        # Per-user privacy preferences owned by the presence service.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_privacy_settings (
                user_id INTEGER PRIMARY KEY,
                hide_last_seen INTEGER NOT NULL DEFAULT 0,
                invisible_mode INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
            """
        )
        _SCHEMA_READY = True
        return True
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.info("PRESENCE_SCHEMA_SKIPPED error=%s", exc.__class__.__name__)
        return False


# --------------------------------------------------------------------------
# Write path: heartbeat / connect / disconnect
# --------------------------------------------------------------------------

def connect(cur, user_id, device_id="", device_label="", platform="", invisible=False, conn=None) -> dict:
    """Register a new authenticated session and return its heartbeat contract.

    Callers must have already verified the session is authenticated; this
    module does not perform authentication itself.
    """
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    now = utc_now()
    session_id = secrets.token_urlsafe(24)
    device_id = _text(device_id, 120) or session_id
    expires_at = now + timedelta(seconds=GRACE_PERIOD_SECONDS)

    # One live session per (user, device): reconnecting a device supersedes
    # its previous session instead of accumulating ghosts.
    cur.execute(
        "UPDATE presence_sessions SET revoked_at=? WHERE user_id=? AND device_id=? AND revoked_at IS NULL",
        (iso(now), uid, device_id),
    )
    cur.execute(
        """
        INSERT INTO presence_sessions
            (session_id, user_id, device_id, device_label, platform, status, activity,
             activity_context, activity_expires_at, invisible, connected_at,
             last_heartbeat_at, expires_at, revoked_at)
        VALUES (?, ?, ?, ?, ?, 'online', 'idle', '', NULL, ?, ?, ?, ?, NULL)
        """,
        (
            session_id,
            uid,
            device_id,
            normalize_device_label(device_label),
            _text(platform, 40),
            1 if invisible else 0,
            iso(now),
            iso(now),
            iso(expires_at),
        ),
    )
    _touch_last_seen(cur, uid, normalize_device_label(device_label), now)
    return {
        "ok": True,
        "session_id": session_id,
        "device_id": device_id,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "grace_period_seconds": GRACE_PERIOD_SECONDS,
        "expires_at": iso(expires_at),
    }


def heartbeat(cur, user_id, session_id, activity=None, activity_context="", conn=None) -> dict:
    """Extend a session's life. Returns ok=False if the session is unknown.

    A client receiving ok=False must call connect() again; this is the
    reconnect path after a server restart or an expired session.
    """
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    sid = _text(session_id, 120)
    if not sid:
        return {"ok": False, "reason": "missing_session"}

    now = utc_now()
    cur.execute(
        "SELECT session_id, device_label FROM presence_sessions WHERE session_id=? AND user_id=? AND revoked_at IS NULL LIMIT 1",
        (sid, uid),
    )
    row = cur.fetchone()
    if not row:
        return {"ok": False, "reason": "unknown_session", "reconnect": True}

    expires_at = now + timedelta(seconds=GRACE_PERIOD_SECONDS)
    if activity is None:
        cur.execute(
            "UPDATE presence_sessions SET last_heartbeat_at=?, expires_at=? WHERE session_id=?",
            (iso(now), iso(expires_at), sid),
        )
    else:
        normalized = normalize_activity(activity)
        activity_expiry = _activity_expiry(normalized, now, expires_at)
        cur.execute(
            """
            UPDATE presence_sessions
               SET last_heartbeat_at=?, expires_at=?, activity=?, activity_context=?, activity_expires_at=?
             WHERE session_id=?
            """,
            (
                iso(now),
                iso(expires_at),
                normalized,
                _text(activity_context, 120),
                iso(activity_expiry) if activity_expiry else None,
                sid,
            ),
        )

    label = ""
    try:
        label = normalize_device_label(dict(row).get("device_label"))
    except Exception:
        label = normalize_device_label(row[1] if len(row) > 1 else "")
    _touch_last_seen(cur, uid, label, now)
    return {
        "ok": True,
        "session_id": sid,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "grace_period_seconds": GRACE_PERIOD_SECONDS,
        "expires_at": iso(expires_at),
    }


def _activity_expiry(activity: str, now: datetime, session_expiry: datetime) -> datetime | None:
    if activity == "idle":
        return None
    if activity in TRANSIENT_ACTIVITIES:
        return now + timedelta(seconds=ACTIVITY_TTL_SECONDS)
    # Session-bound activities live as long as the session does.
    return session_expiry


def set_activity(cur, user_id, session_id, activity, activity_context="", conn=None) -> dict:
    """Set an activity indicator. Also refreshes the heartbeat."""
    return heartbeat(
        cur,
        user_id,
        session_id,
        activity=normalize_activity(activity),
        activity_context=activity_context,
        conn=conn,
    )


def clear_activity(cur, user_id, session_id, conn=None) -> dict:
    return heartbeat(cur, user_id, session_id, activity="idle", activity_context="", conn=conn)


def touch_device(cur, user_id, device_id, device_label="", platform="", conn=None) -> dict:
    """Heartbeat a device's live session, opening one if it has none.

    Server-side callers that observe traffic from an authenticated user know
    the device but not the presence session id -- the web page-view path is the
    obvious case. Without this entry point such a caller has two bad options:
    call connect() per request, which revokes and re-creates a session row on
    every page load, or keep its own table, which is precisely what the legacy
    `pulse_online_sessions` writer did.

    Note what this does *not* do: it never marks anyone online. It extends a
    session in exactly the same way a client heartbeat does, and that session
    still expires on the same clock as every other. A user who stops making
    requests stops being online without anything having to notice.
    """
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    did = _text(device_id, 120)
    if uid <= 0 or not did:
        return {"ok": False, "reason": "missing_device"}
    cur.execute(
        "SELECT session_id FROM presence_sessions WHERE user_id=? AND device_id=? AND revoked_at IS NULL ORDER BY connected_at DESC LIMIT 1",
        (uid, did),
    )
    row = cur.fetchone()
    if row:
        sid = str(_row_to_dict(row).get("session_id") or "")
        result = heartbeat(cur, uid, sid, conn=conn)
        if result.get("ok"):
            result["device_id"] = did
            return result
        # The session was pruned or revoked between the select and now; fall
        # through and open a fresh one rather than silently dropping presence.
    return connect(cur, uid, device_id=did, device_label=device_label, platform=platform, conn=conn)


def set_activity_for_user(cur, user_id, activity, activity_context="", conn=None) -> dict:
    """Apply an activity to every live session a user has.

    Some server-side callers know *who* is doing something but not *from which
    device* -- the Messenger typing endpoint, for instance, authenticates a user
    and receives no session id. Rather than let those callers keep a parallel
    typing table, they record here.

    Applying it to all live sessions is the correct reading of the event: a
    person typing is typing, regardless of which of their devices the keystrokes
    came from, and the indicator their peers see is per-person anyway. Expiry is
    the same as for any other activity, so this path cannot produce a stuck
    indicator that the session-scoped path could not.
    """
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    normalized = normalize_activity(activity)
    now = utc_now()
    cur.execute(
        "SELECT session_id FROM presence_sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>?",
        (uid, iso(now)),
    )
    session_ids = [str(_row_to_dict(row).get("session_id") or "") for row in cur.fetchall() or []]
    session_ids = [sid for sid in session_ids if sid]
    if not session_ids:
        # No live session means no presence to decorate. Recording the activity
        # anyway would be exactly the fabrication this system exists to remove:
        # a typing indicator for someone who is not connected.
        return {"ok": False, "reason": "no_live_session", "sessions": 0}
    for sid in session_ids:
        heartbeat(cur, uid, sid, activity=normalized, activity_context=activity_context, conn=conn)
    return {"ok": True, "activity": normalized, "sessions": len(session_ids)}


def activity_by_context(cur, viewer_user_id, contexts, activities=("typing",), conn=None) -> dict[str, dict[int, str]]:
    """Who is performing a context-scoped activity, batched over many contexts.

    A conversation list needs the answer for every row at once. Without a batch
    entry point the caller would either issue one query per row or -- as the
    Messenger list previously did -- keep its own table and re-derive expiry
    itself. Re-deriving expiry is the part that matters: it is a second opinion
    on staleness, and two opinions is how an indicator gets stuck.

    Invisible sessions are excluded. An invisible user is not observable, and a
    typing bubble is a presence claim like any other.
    """
    ensure_schema(cur, conn)
    viewer_id = int(viewer_user_id or 0)
    keys = [str(c) for c in (contexts or []) if str(c or "")]
    wanted = {normalize_activity(a) for a in (activities or ())} - {"idle"}
    result: dict[str, dict[int, str]] = {key: {} for key in keys}
    if not keys or not wanted:
        return result

    now_iso = iso(utc_now())
    key_slots = ",".join(["?"] * len(keys))
    act_slots = ",".join(["?"] * len(wanted))
    try:
        cur.execute(
            f"""
            SELECT DISTINCT s.user_id, s.activity, s.activity_context
              FROM presence_sessions s
             WHERE s.revoked_at IS NULL
               AND s.expires_at > ?
               AND COALESCE(s.invisible,0)=0
               AND s.activity IN ({act_slots})
               AND s.activity_context IN ({key_slots})
               AND (s.activity_expires_at IS NULL OR s.activity_expires_at > ?)
               AND s.user_id != ?
            """,
            (now_iso, *sorted(wanted), *keys, now_iso, viewer_id),
        )
        rows = cur.fetchall() or []
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("PRESENCE_ACTIVITY_CONTEXT_FAILED error=%s", exc.__class__.__name__)
        return result

    candidates = {int(_row_to_dict(r).get("user_id") or 0) for r in rows}
    hidden = _blocked_pairs(cur, viewer_id, sorted(candidates)) if candidates else set()
    privacy = _privacy_settings(cur, sorted(candidates)) if candidates else {}
    for raw in rows:
        item = _row_to_dict(raw)
        uid = int(item.get("user_id") or 0)
        if uid <= 0 or uid in hidden:
            continue
        if bool((privacy.get(uid) or {}).get("invisible_mode")):
            continue
        key = str(item.get("activity_context") or "")
        if key in result:
            result[key][uid] = str(item.get("activity") or "idle")
    return result


def disconnect(cur, user_id, session_id, conn=None) -> dict:
    """Explicitly end one session (tab close, sign out, app termination)."""
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    sid = _text(session_id, 120)
    if not sid:
        return {"ok": False, "reason": "missing_session"}
    now = utc_now()
    cur.execute(
        "UPDATE presence_sessions SET revoked_at=?, activity='idle', activity_expires_at=NULL WHERE session_id=? AND user_id=? AND revoked_at IS NULL",
        (iso(now), sid, uid),
    )
    _touch_last_seen(cur, uid, "", now)
    return {"ok": True, "session_id": sid, "disconnected_at": iso(now)}


def disconnect_all(cur, user_id, conn=None) -> dict:
    """End every session for a user (full sign-out across devices)."""
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    now = utc_now()
    cur.execute(
        "UPDATE presence_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
        (iso(now), uid),
    )
    _touch_last_seen(cur, uid, "", now)
    return {"ok": True, "user_id": uid, "disconnected_at": iso(now)}


def _touch_last_seen(cur, user_id: int, device_label: str, now: datetime) -> None:
    stamp = iso(now)
    try:
        cur.execute(
            """
            INSERT INTO presence_last_seen (user_id, last_seen_at, last_device_label, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                last_device_label=COALESCE(NULLIF(excluded.last_device_label,''), presence_last_seen.last_device_label),
                updated_at=excluded.updated_at
            """,
            (user_id, stamp, device_label or "", stamp),
        )
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.info("PRESENCE_LAST_SEEN_SKIPPED user_id=%s error=%s", user_id, exc.__class__.__name__)


# --------------------------------------------------------------------------
# Read path
# --------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _live_sessions(cur, user_ids: list[int], now: datetime) -> dict[int, list[dict]]:
    """Return non-revoked, non-expired sessions grouped by user."""
    ids = sorted({int(uid) for uid in user_ids if int(uid or 0) > 0})
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(
        f"""
        SELECT session_id, user_id, device_id, device_label, platform, status, activity,
               activity_context, activity_expires_at, invisible, connected_at,
               last_heartbeat_at, expires_at
          FROM presence_sessions
         WHERE user_id IN ({placeholders})
           AND revoked_at IS NULL
           AND expires_at > ?
        """,
        (*ids, iso(now)),
    )
    grouped: dict[int, list[dict]] = {uid: [] for uid in ids}
    for raw in cur.fetchall() or []:
        item = _row_to_dict(raw)
        uid = int(item.get("user_id") or 0)
        if uid in grouped:
            grouped[uid].append(item)
    return grouped


def _effective_activity(sessions: list[dict], now: datetime) -> tuple[str, str]:
    """Pick the most meaningful non-expired activity across a user's devices."""
    priority = [
        "in_video_call",
        "in_audio_call",
        "live_hosting",
        "live_guest",
        "live_watching",
        "recording_voice",
        "uploading_media",
        "sending_files",
        "typing",
    ]
    best = "idle"
    best_context = ""
    best_rank = len(priority)
    for session in sessions:
        activity = str(session.get("activity") or "idle").lower()
        if activity == "idle" or activity not in priority:
            continue
        expiry = parse_dt(session.get("activity_expires_at"))
        if expiry is not None and expiry <= now:
            continue  # stuck indicator defused
        rank = priority.index(activity)
        if rank < best_rank:
            best_rank = rank
            best = activity
            best_context = str(session.get("activity_context") or "")
    return best, best_context


def _status_from_sessions(sessions: list[dict], now: datetime) -> str:
    if not sessions:
        return "offline"
    freshest = None
    for session in sessions:
        beat = parse_dt(session.get("last_heartbeat_at"))
        if beat and (freshest is None or beat > freshest):
            freshest = beat
    if freshest is None:
        return "online"
    if (now - freshest).total_seconds() >= AWAY_AFTER_SECONDS:
        return "away"
    return "online"


def _privacy_settings(cur, user_ids: list[int]) -> dict[int, dict]:
    ids = sorted({int(uid) for uid in user_ids if int(uid or 0) > 0})
    if not ids:
        return {}
    settings = {uid: {"hide_last_seen": False, "invisible_mode": False, "presence_privacy": "everyone"} for uid in ids}
    placeholders = ",".join(["?"] * len(ids))
    try:
        cur.execute(
            f"SELECT user_id, hide_last_seen, invisible_mode FROM presence_privacy_settings WHERE user_id IN ({placeholders})",
            tuple(ids),
        )
        for raw in cur.fetchall() or []:
            item = _row_to_dict(raw)
            uid = int(item.get("user_id") or 0)
            if uid in settings:
                settings[uid]["hide_last_seen"] = bool(int(item.get("hide_last_seen") or 0))
                settings[uid]["invisible_mode"] = bool(int(item.get("invisible_mode") or 0))
    except Exception:
        pass
    # Reuse the messenger's existing presence_privacy preference so the two
    # systems can never disagree about who may see a user.
    try:
        cur.execute(
            f"SELECT user_id, presence_privacy FROM comm_v2_user_settings WHERE user_id IN ({placeholders})",
            tuple(ids),
        )
        for raw in cur.fetchall() or []:
            item = _row_to_dict(raw)
            uid = int(item.get("user_id") or 0)
            if uid in settings:
                settings[uid]["presence_privacy"] = str(item.get("presence_privacy") or "everyone").lower()
    except Exception:
        pass
    return settings


_WARNED: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    """Log a condition that is worth knowing about but is not per-request news.

    Presence is read on nearly every page render, so an unconditional warning
    in this path would emit thousands of identical lines a minute and bury the
    signal it exists to raise.
    """
    if key in _WARNED:
        return
    _WARNED.add(key)
    logging.warning(message)


def _blocked_pairs(cur, viewer_id: int, target_ids: list[int]) -> set[int]:
    """Return the subset of target_ids where a block exists in either direction."""
    ids = sorted({int(uid) for uid in target_ids if int(uid or 0) > 0 and int(uid) != int(viewer_id)})
    if not ids or viewer_id <= 0:
        return set()
    blocked: set[int] = set()
    found_table = False
    placeholders = ",".join(["?"] * len(ids))
    for table, active_clause in (("comm_v2_blocks", " AND status='active'"), ("blocked_users", "")):
        try:
            cur.execute(
                f"""
                SELECT blocker_user_id, blocked_user_id FROM {table}
                 WHERE ((blocker_user_id=? AND blocked_user_id IN ({placeholders}))
                     OR (blocked_user_id=? AND blocker_user_id IN ({placeholders}))){active_clause}
                """,
                (viewer_id, *ids, viewer_id, *ids),
            )
            for raw in cur.fetchall() or []:
                item = _row_to_dict(raw)
                blocker = int(item.get("blocker_user_id") or 0)
                blocked_user = int(item.get("blocked_user_id") or 0)
                other = blocked_user if blocker == viewer_id else blocker
                if other in ids:
                    blocked.add(other)
            found_table = True
        except Exception as exc:
            # A missing table is expected: deployments carry one block store or
            # the other. Any *other* failure means block enforcement has
            # silently stopped applying -- a privacy regression that must not
            # pass unnoticed simply because the surrounding read still returns.
            if "no such table" not in str(exc).lower():
                logging.warning(
                    "PRESENCE_BLOCK_LOOKUP_FAILED table=%s error=%s detail=%s",
                    table, exc.__class__.__name__, exc,
                )
            continue
    if not found_table:
        _warn_once("block_no_source", "PRESENCE_BLOCK_LOOKUP_NO_SOURCE blocks_not_enforced=1 -- neither comm_v2_blocks nor blocked_users is readable")
    return blocked


def _shares_conversation(cur, viewer_id: int, target_id: int) -> bool:
    try:
        cur.execute(
            """
            SELECT 1
              FROM comm_v2_participants a
              JOIN comm_v2_participants b ON b.conversation_id=a.conversation_id
             WHERE a.user_id=? AND b.user_id=?
               AND a.membership_state='active' AND b.membership_state='active'
               AND COALESCE(a.left_at,'')='' AND COALESCE(b.left_at,'')=''
             LIMIT 1
            """,
            (viewer_id, target_id),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _hidden_presence(user_id: int) -> dict:
    """The response for a user the viewer is not allowed to observe.

    This is byte-for-byte identical to the payload of a genuinely offline
    user with no recorded last-seen. That is a security property, not a
    convenience: if the response carried any marker such as visible=False,
    a viewer could diff it against a known-offline user and infer that they
    had been blocked or that the target was in invisible mode. Callers that
    need to know the difference must use the server-side helpers, never the
    viewer-facing payload.
    """
    return {
        "user_id": int(user_id),
        "status": "offline",
        "online": False,
        "activity": "idle",
        "activity_context": "",
        "last_seen_at": "",
        "last_seen_text": "",
        "devices": 0,
        "available": True,
        "self": False,
    }


def presence_for(cur, viewer_user_id, target_user_ids, conn=None, locale="en") -> dict[int, dict]:
    """Return presence for each target as the viewer is permitted to see it.

    This is the only read entry point subsystems should use.
    """
    ensure_schema(cur, conn)
    viewer_id = int(viewer_user_id or 0)
    targets = sorted({int(uid) for uid in (target_user_ids or []) if int(uid or 0) > 0})
    if not targets:
        return {}

    now = utc_now()
    sessions_by_user = _live_sessions(cur, targets, now)
    privacy = _privacy_settings(cur, targets)
    blocked = _blocked_pairs(cur, viewer_id, targets)
    last_seen = _last_seen_map(cur, targets)

    result: dict[int, dict] = {}
    for uid in targets:
        is_self = uid == viewer_id
        prefs = privacy.get(uid, {})

        if not is_self:
            if uid in blocked:
                result[uid] = _hidden_presence(uid)
                continue
            visibility = str(prefs.get("presence_privacy") or "everyone").lower()
            if visibility == "nobody":
                result[uid] = _hidden_presence(uid)
                continue
            if visibility == "contacts" and not _shares_conversation(cur, viewer_id, uid):
                result[uid] = _hidden_presence(uid)
                continue

        sessions = sessions_by_user.get(uid, [])

        # Invisible mode: either the account-level preference, or every live
        # session having opted in for this connection.
        invisible = bool(prefs.get("invisible_mode"))
        if not invisible and sessions:
            invisible = all(int(s.get("invisible") or 0) == 1 for s in sessions)

        if invisible and not is_self:
            result[uid] = _hidden_presence(uid)
            continue

        status = _status_from_sessions(sessions, now)
        activity, activity_context = _effective_activity(sessions, now)
        stamp = last_seen.get(uid, "")

        hide_last_seen = bool(prefs.get("hide_last_seen")) and not is_self
        exposed_stamp = "" if (hide_last_seen or status != "offline") else stamp

        result[uid] = {
            "user_id": uid,
            "status": status,
            "online": status in {"online", "away"},
            "activity": activity,
            "activity_context": activity_context,
            "last_seen_at": exposed_stamp,
            "last_seen_text": format_last_seen(exposed_stamp, now=now, locale=locale) if exposed_stamp else "",
            "devices": len(sessions),
            "available": True,
            "self": is_self,
        }
        if is_self:
            result[uid]["invisible"] = invisible
            result[uid]["hide_last_seen"] = bool(prefs.get("hide_last_seen"))
    return result


def _last_seen_map(cur, user_ids: list[int]) -> dict[int, str]:
    ids = sorted({int(uid) for uid in user_ids if int(uid or 0) > 0})
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    out: dict[int, str] = {}
    try:
        cur.execute(
            f"SELECT user_id, last_seen_at FROM presence_last_seen WHERE user_id IN ({placeholders})",
            tuple(ids),
        )
        for raw in cur.fetchall() or []:
            item = _row_to_dict(raw)
            out[int(item.get("user_id") or 0)] = str(item.get("last_seen_at") or "")
    except Exception:
        pass
    return out


def presence_of(cur, viewer_user_id, target_user_id, conn=None, locale="en") -> dict:
    """Convenience wrapper for a single user."""
    uid = int(target_user_id or 0)
    return presence_for(cur, viewer_user_id, [uid], conn=conn, locale=locale).get(uid, _hidden_presence(uid))


def is_online(cur, user_id, conn=None) -> bool:
    """Raw, privacy-independent liveness check for server-side logic.

    Use this for routing decisions (e.g. whether to send a push notification),
    never for deciding what to display to another user.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return False
    ensure_schema(cur, conn)
    return bool(_live_sessions(cur, [uid], utc_now()).get(uid))


def active_sessions(cur, user_id, conn=None) -> list[dict]:
    """List a user's own live devices, for a 'signed in devices' surface."""
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    now = utc_now()
    sessions = _live_sessions(cur, [uid], now).get(uid, [])
    return [
        {
            "session_id": s.get("session_id"),
            "device_id": s.get("device_id"),
            "device_label": s.get("device_label") or "unknown",
            "platform": s.get("platform") or "",
            "activity": s.get("activity") or "idle",
            "connected_at": s.get("connected_at") or "",
            "last_heartbeat_at": s.get("last_heartbeat_at") or "",
        }
        for s in sessions
    ]


# --------------------------------------------------------------------------
# Privacy preferences
# --------------------------------------------------------------------------

def set_privacy(cur, user_id, hide_last_seen=None, invisible_mode=None, conn=None) -> dict:
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    now = iso_now()
    cur.execute(
        "INSERT OR IGNORE INTO presence_privacy_settings (user_id, hide_last_seen, invisible_mode, updated_at) VALUES (?, 0, 0, ?)",
        (uid, now),
    )
    if hide_last_seen is not None:
        cur.execute(
            "UPDATE presence_privacy_settings SET hide_last_seen=?, updated_at=? WHERE user_id=?",
            (1 if hide_last_seen else 0, now, uid),
        )
    if invisible_mode is not None:
        cur.execute(
            "UPDATE presence_privacy_settings SET invisible_mode=?, updated_at=? WHERE user_id=?",
            (1 if invisible_mode else 0, now, uid),
        )
    cur.execute(
        "SELECT hide_last_seen, invisible_mode FROM presence_privacy_settings WHERE user_id=? LIMIT 1",
        (uid,),
    )
    row = _row_to_dict(cur.fetchone())
    return {
        "ok": True,
        "user_id": uid,
        "hide_last_seen": bool(int(row.get("hide_last_seen") or 0)),
        "invisible_mode": bool(int(row.get("invisible_mode") or 0)),
    }


def get_privacy(cur, user_id, conn=None) -> dict:
    uid = _user_id(user_id)
    ensure_schema(cur, conn)
    cur.execute(
        "SELECT hide_last_seen, invisible_mode FROM presence_privacy_settings WHERE user_id=? LIMIT 1",
        (uid,),
    )
    row = _row_to_dict(cur.fetchone())
    return {
        "user_id": uid,
        "hide_last_seen": bool(int(row.get("hide_last_seen") or 0)),
        "invisible_mode": bool(int(row.get("invisible_mode") or 0)),
    }


# --------------------------------------------------------------------------
# Last-seen formatting
# --------------------------------------------------------------------------

def format_last_seen(value, now: datetime | None = None, locale: str = "en") -> str:
    """Render a last-seen timestamp for display.

    The server produces a sensible default; clients that can do better with
    the user's real locale (Intl.RelativeTimeFormat on web, toLocaleString on
    native) should format from last_seen_at themselves.
    """
    parsed = parse_dt(value)
    if not parsed:
        return ""
    now = now or utc_now()
    delta = now - parsed
    seconds = int(delta.total_seconds())

    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "Last seen just now"
    minutes = seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"Last seen {minutes} {unit} ago"
    hours = minutes // 60
    if hours < 24 and parsed.date() == now.date():
        unit = "hour" if hours == 1 else "hours"
        return f"Last seen {hours} {unit} ago"

    yesterday = (now - timedelta(days=1)).date()
    if parsed.date() == yesterday:
        return f"Last seen yesterday at {_format_time(parsed, locale)}"
    if (now - parsed).days < 7:
        return f"Last seen {parsed.strftime('%A')} at {_format_time(parsed, locale)}"
    if parsed.year == now.year:
        return f"Last seen {parsed.strftime('%B %-d')} at {_format_time(parsed, locale)}"
    return f"Last seen {parsed.strftime('%B %-d, %Y')} at {_format_time(parsed, locale)}"


def _format_time(value: datetime, locale: str = "en") -> str:
    # 24-hour clock for locales that conventionally use it.
    twenty_four_hour = str(locale or "en").lower().split("-")[0] not in {"en"}
    if twenty_four_hour:
        return value.strftime("%H:%M")
    return value.strftime("%-I:%M %p")


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------

def sweep(cur, conn=None, retention_days: int = SESSION_RETENTION_DAYS) -> dict:
    """Delete long-dead session rows.

    Purely housekeeping: correctness never depends on this running, because
    expiry is evaluated at read time.
    """
    ensure_schema(cur, conn)
    cutoff = iso(utc_now() - timedelta(days=max(1, int(retention_days or 1))))
    removed = 0
    try:
        cur.execute(
            "DELETE FROM presence_sessions WHERE COALESCE(revoked_at, expires_at) < ?",
            (cutoff,),
        )
        removed = max(0, int(getattr(cur, "rowcount", 0) or 0))
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.info("PRESENCE_SWEEP_SKIPPED error=%s", exc.__class__.__name__)
    return {"ok": True, "removed": removed}


def health_snapshot(cur, conn=None) -> dict:
    """Real presence metrics. No synthetic values."""
    ensure_schema(cur, conn)
    now = iso_now()
    snapshot = {
        "live_sessions": 0,
        "online_users": 0,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "grace_period_seconds": GRACE_PERIOD_SECONDS,
        "away_after_seconds": AWAY_AFTER_SECONDS,
        "activity_ttl_seconds": ACTIVITY_TTL_SECONDS,
        "devices": [],
    }
    try:
        cur.execute(
            "SELECT COUNT(*) AS c FROM presence_sessions WHERE revoked_at IS NULL AND expires_at > ?",
            (now,),
        )
        snapshot["live_sessions"] = int(_row_to_dict(cur.fetchone()).get("c") or 0)
        cur.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM presence_sessions WHERE revoked_at IS NULL AND expires_at > ?",
            (now,),
        )
        snapshot["online_users"] = int(_row_to_dict(cur.fetchone()).get("c") or 0)
        cur.execute(
            """
            SELECT device_label, COUNT(*) AS c
              FROM presence_sessions
             WHERE revoked_at IS NULL AND expires_at > ?
             GROUP BY device_label ORDER BY c DESC
            """,
            (now,),
        )
        snapshot["devices"] = [
            (str(_row_to_dict(r).get("device_label") or "unknown"), int(_row_to_dict(r).get("c") or 0))
            for r in (cur.fetchall() or [])
        ]
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.info("PRESENCE_HEALTH_SKIPPED error=%s", exc.__class__.__name__)
    return snapshot
