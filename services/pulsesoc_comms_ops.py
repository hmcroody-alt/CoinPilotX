"""Operational snapshot of PulseSoc communications for the admin command centre.

Reads metadata only. The denylist in PRIVATE_COLUMNS is enforced by
tests/test_comms_ops_privacy.py: no message body, no call content, no device or
provider token may appear in a snapshot.

A section that cannot be read reports state "error" rather than returning zeroes.
An empty result and a failed query are different answers and must never render
the same way.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Windows the UI offers. Every query is bounded by one of them.
#
# 7d is here because of what production actually looks like: measured 2026-10-03,
# the last 24h held 1 call and 0 messages, so a 15m/1h/24h-only dashboard reads
# 0/0/0 and an owner reasonably concludes it is broken -- which is the complaint
# this page exists to answer. Over 7d the same data shows 5 calls, 67 messages
# and 312 delivery attempts. The widest window is the one that proves the
# pipeline works at this traffic level.
WINDOWS: dict[str, int] = {"15m": 900, "1h": 3600, "24h": 86400, "7d": 604800}
DEFAULT_WINDOW = "24h"

# Hard caps so a busy period cannot turn a dashboard load into a table dump.
MAX_ACTIVE_CALLS = 50
MAX_ACTIVE_CONVERSATIONS = 50
MAX_FAILURE_ROWS = 12

# Columns that hold private communication, personal data or credentials. Named
# here so a test can assert no snapshot value was ever drawn from one of them.
PRIVATE_COLUMNS: frozenset[str] = frozenset({
    "body",
    "title",
    "description",
    "payload_json",
    "metadata_json",
    "event_payload_json",
    "provider_response",
    "provider_response_json",
    "url",
    "cdn_url",
    "playback_url",
    "thumbnail_url",
    "media_url",
    "push_token",
    "token_hash",
    "subscription_json",
    "p256dh",
    "auth",
    "stream_key",
    "device_info_json",
    "permissions_json",
    "reason",
    "direct_key",
})

# Call status vocabulary, mirrored from services.pulsesoc_communications_engine.
# Duplicated deliberately: this module must keep working if the engine fails to
# import, and test_comms_ops_contract.py asserts the two sets stay identical.
ACTIVE_CALL_STATUSES: tuple[str, ...] = (
    "created", "ringing", "accepted", "connecting", "connected", "active", "reconnecting",
)
LIVE_CALL_STATUSES: tuple[str, ...] = ("connected", "active", "reconnecting")

# end_reason values observed in production, mapped to operator-facing categories.
# Only reasons the system actually writes appear here; anything else lands in
# "other" rather than being invented into a category.
FAILURE_CATEGORIES: dict[str, str] = {
    "ring_timeout": "Unanswered (ring timeout)",
    "stale_connected_timeout": "Abandoned while connected",
    "stale_connecting_timeout": "Stalled while connecting",
    "stale_ringing_timeout": "Stalled while ringing",
    "stale_created_timeout": "Stalled before ringing",
    "client_connect_failed": "Client could not connect",
    "agora_token_failed": "Provider token issuance failed",
    "livekit_token_failed": "Provider token issuance failed (retired provider)",
    "declined": "Declined by recipient",
}
# The exact end_reason values the engine writes when token issuance fails.
TOKEN_FAILURE_REASONS: tuple[str, ...] = ("agora_token_failed", "livekit_token_failed")
# Reasons that mean the call worked and someone hung up. Never counted a failure.
BENIGN_END_REASONS: frozenset[str] = frozenset({
    "native_hangup", "ended_by_user", "ended_by_host", "callkit_hangup",
    "ended_by_participant", "answered_elsewhere", "admin_force_end",
    "admin_command_center_force_end", "admin_api_force_end",
})

# Statuses that mean a call never completed. Prod has no 'failed' rows at all;
# 'missed' and 'expired' carry the real failure signal, so both are included.
UNSUCCESSFUL_CALL_STATUSES: tuple[str, ...] = ("failed", "missed", "expired", "rejected", "disconnected")

# notification_delivery_jobs.status values that mean the send itself broke.
DELIVERY_FAILURE_STATUSES: tuple[str, ...] = (
    "failed", "config_missing", "invalid_device", "dead_letter", "error",
)
# Statuses that mean there was nobody to deliver to -- the recipient has no
# registered device or contact. Deliberately NOT a failure: production shows 29
# of 154 pushes in 7d as skipped_no_device, and rating APNs "degraded" because a
# member never enabled notifications would be a false alarm about the provider.
# Counted and shown separately, because "a fifth of pushes have nowhere to go"
# is worth an operator's attention under its own name.
DELIVERY_UNROUTABLE_STATUSES: tuple[str, ...] = ("skipped_no_device", "skipped_no_contact")
DELIVERY_SUCCESS_STATUSES: tuple[str, ...] = ("sent", "ready", "delivered")

#: §22. A long unbroken run of identifier characters inside a failure reason.
#: Nothing an operator needs to read looks like this -- "410 BadDeviceToken",
#: "Brevo email is not configured." and "No active push device or subscription."
#: are all short words and spaces -- whereas a push token, a JWT and an APNs key
#: all do.
_OPAQUE_RUN = re.compile(r"[A-Za-z0-9_\-+/=.]{40,}")
#: Named credential prefixes, redacted whole rather than by length: an Expo
#: token's body sits inside brackets and would otherwise survive the rule above
#: in fragments.
_NAMED_CREDENTIAL = re.compile(
    r"(ExponentPushToken|ExpoPushToken)\s*\[[^\]]*\]|eyJ[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+){1,2}",
    re.IGNORECASE,
)
REASON_MAX_CHARS = 160


def safe_failure_reason(raw: Any) -> str:
    """A delivery failure reason with any credential-shaped run removed.

    This function exists because the comment that used to sit at the call site
    was wrong. It said ``failure_reason`` was operator-authored text and so safe
    to surface verbatim; the writer is
    ``pulsesoc_notification_system.py`` ::

        message = str(result.get("message") or result.get("error") or ...)[:1000]

    which is whatever the channel adapter returned, bounded only by length. Push
    providers routinely echo the offending token in exactly that message, and
    §22 names push tokens as never-expose. Production happens to hold only five
    short hand-written reasons today, so this is a latent leak rather than a live
    one -- but "latent" here means "one adapter change away", and the page must
    not depend on a provider's good manners for a privacy property.

    The redaction is deliberately shape-based and not an allowlist of known
    providers, because the failure mode is a provider we have not met yet. What
    survives is the part an operator acts on: "410 BadDeviceToken" and
    "Brevo email is not configured." pass through untouched, because neither
    contains a forty-character run of identifier characters.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    text = _NAMED_CREDENTIAL.sub("[redacted]", text)
    text = _OPAQUE_RUN.sub("[redacted]", text)
    return text[:REASON_MAX_CHARS]


def _bot():
    import bot

    return bot


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    """Match the ISO-with-offset format every communications writer stores."""
    return moment.isoformat(timespec="seconds")


def _window_floor(seconds: int, now: datetime | None = None) -> str:
    return _stamp((now or _now()) - timedelta(seconds=seconds))


def _rows(cur) -> list[dict[str, Any]]:
    # Mapping access only: iterating a row yields values on SQLite but column
    # names on Postgres, so dict(row) is the one portable form.
    return [dict(row) for row in cur.fetchall() or []]


def _one(cur) -> dict[str, Any]:
    row = cur.fetchone()
    return dict(row) if row else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _short_id(value: Any, keep: int = 8) -> str:
    """Operators need a handle to search on, not a full opaque identifier."""
    text = str(value or "").strip()
    if len(text) <= keep:
        return text
    return f"{text[:keep]}…"


def _last_stamp(cur, table: str, column: str) -> str:
    """When did this stream last move?

    A window that reads zero is ambiguous on its own -- "nothing is happening"
    and "nothing works" look identical. Production traffic here is low enough
    that the short windows are legitimately empty most of the time, so every
    stream carries the timestamp of its most recent row. MAX over an unindexed
    column is affordable at these table sizes (545 / 1375 / 10422 rows) and is
    what turns an empty panel into an answer.

    The table and column names are module constants, never caller input.
    """
    cur.execute(f"SELECT MAX({column}) AS newest FROM {table}")  # noqa: S608
    return str(_one(cur).get("newest") or "")


def _window_key(label: str) -> str:
    return "w" + label.replace("-", "_")


def _window_rollup(
    cur,
    table: str,
    time_column: str,
    metrics: dict[str, tuple[str, tuple[Any, ...]]],
    now: datetime,
) -> dict[str, dict[str, int]]:
    """Compute every window and metric in a single round trip.

    The obvious shape -- loop the windows, loop the metrics, one query each --
    costs 16 queries for the calls panel alone. Measured against production over
    the public proxy that read as 2.7s wall clock for a 17ms workload: the cost
    was round trips, not the database. Conditional aggregation collapses all of
    it into one statement, bounded by the widest window so the scan stays small.

    ``table`` and ``time_column`` are module constants, never caller input.
    ``metrics`` maps a result name to an extra SQL predicate and its parameters.
    """
    floors = {label: _window_floor(seconds, now) for label, seconds in WINDOWS.items()}
    widest = _window_floor(max(WINDOWS.values()), now)
    selects: list[str] = []
    params: list[Any] = []
    for label in WINDOWS:
        for metric, (predicate, predicate_params) in metrics.items():
            condition = f"{time_column} >= ?"
            if predicate:
                condition += f" AND ({predicate})"
            selects.append(f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END) AS {_window_key(label)}_{metric}")
            params.append(floors[label])
            params.extend(predicate_params)
    sql = (
        f"SELECT {', '.join(selects)} FROM {table} WHERE {time_column} >= ?"  # noqa: S608
    )
    params.append(widest)
    cur.execute(sql, tuple(params))
    row = _one(cur)
    return {
        label: {metric: _int(row.get(f"{_window_key(label)}_{metric}")) for metric in metrics}
        for label in WINDOWS
    }


def _section(name: str, fn: Callable[[], dict[str, Any]], errors: dict[str, str]) -> dict[str, Any]:
    """Run one snapshot section, recording failure instead of faking success."""
    started = time.perf_counter()
    try:
        payload = fn()
        payload["state"] = "ready"
        payload["query_ms"] = int((time.perf_counter() - started) * 1000)
        return payload
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        errors[name] = type(exc).__name__
        logger.warning(
            "PULSESOC_COMMS_OPS_SECTION_FAILED section=%s error=%s duration_ms=%s",
            name, type(exc).__name__, elapsed,
        )
        # "error" must not be mistaken for "nothing happening". The UI keys off
        # this and renders an explicit failure rather than a zero.
        return {"state": "error", "error": type(exc).__name__, "query_ms": elapsed}


def _calls_section(cur, now: datetime) -> dict[str, Any]:
    active_marks = ",".join(["?"] * len(ACTIVE_CALL_STATUSES))
    cur.execute(
        f"""
        SELECT public_id, call_type, call_scope, provider, status, room_name,
               started_at, answered_at, created_at, end_reason
        FROM communication_calls
        WHERE status IN ({active_marks})
        ORDER BY id DESC
        LIMIT {MAX_ACTIVE_CALLS}
        """,
        ACTIVE_CALL_STATUSES,
    )
    active_rows = _rows(cur)

    call_ids: dict[str, int] = {}
    active: list[dict[str, Any]] = []
    for row in active_rows:
        started = row.get("answered_at") or row.get("started_at") or row.get("created_at")
        active.append({
            "session": _short_id(row.get("public_id")),
            "type": str(row.get("call_type") or "") or "unknown",
            "scope": str(row.get("call_scope") or "") or "unknown",
            "provider": str(row.get("provider") or "") or "unknown",
            "status": str(row.get("status") or "") or "unknown",
            "live": str(row.get("status") or "") in LIVE_CALL_STATUSES,
            "started_at": started or "",
            "duration_seconds": _duration_since(started, now),
            "participants": 0,
            "error": str(row.get("end_reason") or ""),
        })
        call_ids[_short_id(row.get("public_id"))] = 0

    # One grouped query for participant counts rather than a lookup per call.
    if active_rows:
        public_ids = [str(row.get("public_id") or "") for row in active_rows]
        marks = ",".join(["?"] * len(public_ids))
        cur.execute(
            f"""
            SELECT c.public_id AS public_id, COUNT(p.id) AS joined
            FROM communication_calls c
            LEFT JOIN communication_call_participants p
                   ON p.call_id = c.id AND p.status IN ('joined', 'ringing')
            WHERE c.public_id IN ({marks})
            GROUP BY c.public_id
            """,
            public_ids,
        )
        counts = {_short_id(row.get("public_id")): _int(row.get("joined")) for row in _rows(cur)}
        for entry in active:
            entry["participants"] = counts.get(entry["session"], 0)

    # Counted separately from the list above, which is capped. A count taken as
    # len() of a LIMIT-ed list reports the cap once the cap is reached, so an
    # overloaded system would look identical to a busy-but-fine one.
    cur.execute(
        f"""
        SELECT status, COUNT(*) AS n
        FROM communication_calls
        WHERE status IN ({active_marks})
        GROUP BY status
        """,
        ACTIVE_CALL_STATUSES,
    )
    by_status = {str(row.get("status") or ""): _int(row.get("n")) for row in _rows(cur)}
    active_count = sum(by_status.values())
    live_count = sum(n for status, n in by_status.items() if status in LIVE_CALL_STATUSES)

    bad_marks = ",".join(["?"] * len(UNSUCCESSFUL_CALL_STATUSES))
    rollup = _window_rollup(
        cur,
        "communication_calls",
        "created_at",
        {
            "started": ("", ()),
            "answered": ("COALESCE(answered_at,'') <> ''", ()),
            "unsuccessful": (f"status IN ({bad_marks})", UNSUCCESSFUL_CALL_STATUSES),
        },
        now,
    )
    windows: dict[str, dict[str, int]] = {}
    for label, counts in rollup.items():
        windows[label] = {
            "started": counts["started"],
            "answered": counts["answered"],
            "unsuccessful": counts["unsuccessful"],
            "completed": max(counts["started"] - counts["unsuccessful"], 0),
        }

    # Failure categories over the longest window only; the short windows are
    # usually empty on this traffic volume and an empty breakdown reads as broken.
    day_floor = _window_floor(WINDOWS["24h"], now)
    cur.execute(
        """
        SELECT COALESCE(NULLIF(end_reason, ''), 'unspecified') AS reason, COUNT(*) AS n
        FROM communication_calls
        WHERE created_at >= ? AND COALESCE(answered_at, '') = ''
        GROUP BY COALESCE(NULLIF(end_reason, ''), 'unspecified')
        ORDER BY COUNT(*) DESC
        LIMIT ?
        """,
        (day_floor, MAX_FAILURE_ROWS),
    )
    failures = []
    for row in _rows(cur):
        reason = str(row.get("reason") or "unspecified")
        if reason in BENIGN_END_REASONS:
            continue
        failures.append({
            "reason": reason,
            "label": FAILURE_CATEGORIES.get(reason, "Other / unclassified"),
            "classified": reason in FAILURE_CATEGORIES,
            "count": _int(row.get("n")),
        })

    return {
        "active": active,
        "active_count": active_count,
        "live_count": live_count,
        "active_truncated": active_count > len(active),
        "windows": windows,
        "failures": failures,
        "funnel": _call_funnel(cur, now),
        "quality": _call_quality(cur, now),
        "last_call_at": _last_stamp(cur, "communication_calls", "created_at"),
    }


def _call_quality(cur, now: datetime) -> dict[str, Any]:
    """Network measurements for recent calls, over the rows that hold one.

    communication_call_quality_reports exists and production has rows in it, so
    this is a real signal rather than an aspirational one. But most of those rows
    are placeholders: in production 452 of 542 carry latency, jitter and score
    all exactly zero. A zero-millisecond round trip is not a measurement, so
    averaging the column would report a two-millisecond platform built almost
    entirely out of rows where nothing was measured.

    So the measured rows are separated from the placeholders and the statistics
    are taken over the measured ones only, with the placeholder count reported
    beside them. The alternative -- one average over everything -- is the same
    mistake as calling a provider healthy because its config exists: a number
    that is arithmetically correct and operationally false.

    Nothing here is per-person. Latency and jitter describe a network path, and
    the reports are joined only to a call, never to a participant.
    """
    floor = _window_floor(WINDOWS["7d"], now)
    cur.execute(
        """
        SELECT COUNT(*) AS reports,
               SUM(CASE WHEN COALESCE(latency_ms,0) > 0
                          OR COALESCE(jitter_ms,0) > 0
                          OR COALESCE(quality_score,0) > 0
                        THEN 1 ELSE 0 END) AS measured
        FROM communication_call_quality_reports
        WHERE created_at >= ?
        """,
        (floor,),
    )
    row = _one(cur)
    reports = _int(row.get("reports"))
    measured = _int(row.get("measured"))
    if not measured:
        return {
            "measurable": False,
            "reports": reports,
            "measured": 0,
            "note": (
                f"{reports} quality report(s) in 7d, none carrying a measurement: latency, jitter "
                "and score are all zero, which is a row written rather than a network observed. "
                "No quality figure is shown, because an average over those rows would be a "
                "statement about the instrumentation and not about the calls."
            ),
        }
    # Each figure is taken over the rows that actually carry it, not over the
    # measured set as a whole. MAX tolerates a stray zero; MIN does not, so a
    # plain MIN(quality_score) would report the worst possible score for a
    # perfectly good call whose row happened to record latency and no score.
    cur.execute(
        """
        SELECT MAX(latency_ms) AS worst_latency,
               MAX(jitter_ms) AS worst_jitter,
               MAX(packet_loss) AS worst_loss,
               MIN(CASE WHEN COALESCE(quality_score,0) > 0 THEN quality_score END) AS worst_score,
               COUNT(DISTINCT call_id) AS calls
        FROM communication_call_quality_reports
        WHERE created_at >= ?
          AND (COALESCE(latency_ms,0) > 0 OR COALESCE(jitter_ms,0) > 0
               OR COALESCE(quality_score,0) > 0)
        """,
        (floor,),
    )
    stats = _one(cur)
    worst_score = stats.get("worst_score")
    return {
        "measurable": True,
        "reports": reports,
        "measured": measured,
        "unmeasured": max(reports - measured, 0),
        "calls": _int(stats.get("calls")),
        "worst_latency_ms": _int(stats.get("worst_latency")),
        "worst_jitter_ms": _int(stats.get("worst_jitter")),
        "worst_packet_loss": float(stats.get("worst_loss") or 0.0),
        # None rather than 0 when no row scored the call: an absent score and
        # the worst possible score must not render as the same thing.
        "worst_quality_score": None if worst_score is None else float(worst_score),
        # Said out loud because the obvious reading of this panel is that it
        # describes calls happening now, and it cannot: production has no
        # quality report against any call that is still in progress.
        "note": (
            "Measured after a call ends, so this describes recent calls and never the one in "
            "progress. Figures are the worst value seen, not an average, because an average "
            "over a handful of calls hides the one that went wrong."
        ),
    }


def _duration_since(started: Any, now: datetime) -> int:
    text = str(started or "").strip()
    if not text:
        return 0
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(int((now - moment).total_seconds()), 0)


def _call_funnel(cur, now: datetime) -> dict[str, int]:
    """initiated -> rang -> answered -> connected, over 24h. Real columns only."""
    floor = _window_floor(WINDOWS["24h"], now)
    cur.execute(
        """
        SELECT COUNT(*) AS initiated,
               SUM(CASE WHEN COALESCE(started_at, '') <> '' THEN 1 ELSE 0 END) AS rang,
               SUM(CASE WHEN COALESCE(answered_at, '') <> '' THEN 1 ELSE 0 END) AS answered,
               SUM(CASE WHEN COALESCE(duration_seconds, 0) > 0 THEN 1 ELSE 0 END) AS connected
        FROM communication_calls
        WHERE created_at >= ?
        """,
        (floor,),
    )
    row = _one(cur)
    return {
        "initiated": _int(row.get("initiated")),
        "rang": _int(row.get("rang")),
        "answered": _int(row.get("answered")),
        "connected": _int(row.get("connected")),
    }


def _chat_section(cur, now: datetime) -> dict[str, Any]:
    # Two tables, so two round trips rather than one -- still eight fewer than
    # a query per window per metric.
    conversations = _window_rollup(
        cur, "comm_v2_conversations", "last_activity_at", {"active_conversations": ("", ())}, now,
    )
    messages = _window_rollup(
        cur, "comm_v2_messages", "created_at", {"messages": ("", ())}, now,
    )
    windows = {
        label: {
            "active_conversations": conversations[label]["active_conversations"],
            "messages": messages[label]["messages"],
        }
        for label in WINDOWS
    }

    day_floor = _window_floor(WINDOWS["24h"], now)
    cur.execute(
        """
        SELECT COALESCE(NULLIF(conversation_type, ''), 'unknown') AS kind,
               COUNT(*) AS n
        FROM comm_v2_conversations
        WHERE last_activity_at >= ?
        GROUP BY COALESCE(NULLIF(conversation_type, ''), 'unknown')
        ORDER BY COUNT(*) DESC
        """,
        (day_floor,),
    )
    by_type = [{"kind": str(row.get("kind")), "count": _int(row.get("n"))} for row in _rows(cur)]

    # Conversation rows carry no title, no body and no participant identities.
    cur.execute(
        f"""
        SELECT public_id, conversation_type, member_count, last_activity_at, status
        FROM comm_v2_conversations
        WHERE last_activity_at >= ?
        ORDER BY last_activity_at DESC
        LIMIT {MAX_ACTIVE_CONVERSATIONS}
        """,
        (day_floor,),
    )
    active = [{
        "conversation": _short_id(row.get("public_id")),
        "kind": str(row.get("conversation_type") or "") or "unknown",
        "participants": _int(row.get("member_count")),
        "last_activity_at": str(row.get("last_activity_at") or ""),
        "status": str(row.get("status") or "") or "unknown",
    } for row in _rows(cur)]

    cur.execute(
        """
        SELECT COALESCE(NULLIF(delivery_status, ''), 'unspecified') AS delivery_status,
               COUNT(*) AS n
        FROM comm_v2_messages
        WHERE created_at >= ?
        GROUP BY COALESCE(NULLIF(delivery_status, ''), 'unspecified')
        """,
        (day_floor,),
    )
    statuses = {str(row.get("delivery_status")): _int(row.get("n")) for row in _rows(cur)}

    return {
        "windows": windows,
        "by_type": by_type,
        "active": active,
        "message_delivery": _message_delivery_signal(statuses),
        "last_message_at": _last_stamp(cur, "comm_v2_messages", "created_at"),
    }


def _message_delivery_signal(statuses: dict[str, int]) -> dict[str, Any]:
    """Report per-message delivery as unmeasured unless the column varies.

    comm_v2_messages.delivery_status is written 'sent' on insert and nothing
    moves it afterwards, so a "0 failures" tile would be an artefact of a
    constant column rather than an observation. Say "not instrumented" instead.
    """
    distinct = {key for key, count in statuses.items() if count}
    failing = sum(count for key, count in statuses.items() if key in ("failed", "error", "rejected"))
    if failing:
        return {"measurable": True, "failed": failing, "statuses": statuses}
    if len(distinct) <= 1:
        return {
            "measurable": False,
            "failed": 0,
            "statuses": statuses,
            "note": "Per-message delivery state is not instrumented: every row carries the same status. "
                    "Use push and notification delivery below to judge whether messages reached people.",
        }
    return {"measurable": True, "failed": 0, "statuses": statuses}


def _delivery_section(cur, now: datetime) -> dict[str, Any]:
    ok_marks = ",".join(["?"] * len(DELIVERY_SUCCESS_STATUSES))
    bad_marks = ",".join(["?"] * len(DELIVERY_FAILURE_STATUSES))
    skip_marks = ",".join(["?"] * len(DELIVERY_UNROUTABLE_STATUSES))
    windows = _window_rollup(
        cur,
        "notification_delivery_jobs",
        "created_at",
        {
            "total": ("", ()),
            "delivered": (f"status IN ({ok_marks})", DELIVERY_SUCCESS_STATUSES),
            "failed": (f"status IN ({bad_marks})", DELIVERY_FAILURE_STATUSES),
            "unroutable": (f"status IN ({skip_marks})", DELIVERY_UNROUTABLE_STATUSES),
        },
        now,
    )

    day_floor = _window_floor(WINDOWS["24h"], now)
    cur.execute(
        """
        SELECT COALESCE(NULLIF(channel, ''), 'unknown') AS channel,
               COALESCE(NULLIF(status, ''), 'unknown') AS status,
               COUNT(*) AS n
        FROM notification_delivery_jobs
        WHERE created_at >= ?
        GROUP BY COALESCE(NULLIF(channel, ''), 'unknown'), COALESCE(NULLIF(status, ''), 'unknown')
        ORDER BY COUNT(*) DESC
        LIMIT 40
        """,
        (day_floor,),
    )
    by_channel = [{
        "channel": str(row.get("channel")),
        "status": str(row.get("status")),
        "count": _int(row.get("n")),
        "failed": str(row.get("status")) in DELIVERY_FAILURE_STATUSES,
        "unroutable": str(row.get("status")) in DELIVERY_UNROUTABLE_STATUSES,
    } for row in _rows(cur)]

    # failure_reason is whatever the channel adapter returned, not operator-
    # authored text, so it goes through safe_failure_reason() before it leaves
    # this module. See that function for why the comment here used to be wrong.
    unhappy = (*DELIVERY_FAILURE_STATUSES, *DELIVERY_UNROUTABLE_STATUSES)
    unhappy_marks = ",".join(["?"] * len(unhappy))
    cur.execute(
        f"""
        SELECT COALESCE(NULLIF(failure_reason, ''), NULLIF(failed_reason, ''), 'unspecified') AS reason,
               COALESCE(NULLIF(channel, ''), 'unknown') AS channel,
               COALESCE(NULLIF(status, ''), 'unknown') AS status,
               COUNT(*) AS n
        FROM notification_delivery_jobs
        WHERE created_at >= ? AND status IN ({unhappy_marks})
        GROUP BY 1, 2, 3
        ORDER BY COUNT(*) DESC
        LIMIT ?
        """,
        # Over-read, then fold and cut below. The database groups by the raw
        # reason, so an adapter that echoes a distinct token per failure would
        # otherwise fill the whole panel with rows that all render "[redacted]"
        # and push the one informative reason off the bottom -- the panel would
        # degrade worst in exactly the case the redaction exists for.
        (day_floor, *unhappy, MAX_FAILURE_ROWS * 6),
    )
    folded: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in _rows(cur):
        reason = safe_failure_reason(row.get("reason"))
        status = str(row.get("status"))
        key = (reason, str(row.get("channel")), status)
        entry = folded.setdefault(key, {
            "reason": reason,
            "channel": str(row.get("channel")),
            "status": status,
            "count": 0,
            "kind": "unroutable" if status in DELIVERY_UNROUTABLE_STATUSES else "failure",
        })
        entry["count"] += _int(row.get("n"))
    reasons = sorted(folded.values(), key=lambda e: -e["count"])[:MAX_FAILURE_ROWS]

    return {
        "windows": windows,
        "by_channel": by_channel,
        "reasons": reasons,
        "last_attempt_at": _last_stamp(cur, "notification_delivery_jobs", "created_at"),
    }


def _providers_section(cur, now: datetime) -> dict[str, Any]:
    """Provider states from observed outcomes only.

    Configuration presence is reported as configuration, never as health. A
    provider with no traffic in the window is "unknown", not "healthy".
    """
    providers: list[dict[str, Any]] = []
    # Health is rated over 7d for the same reason the UI offers that window:
    # production sees single-digit calls per week, so a 24h verdict is almost
    # always "unknown" and answers nothing.
    day_floor = _window_floor(WINDOWS["7d"], now)

    # Agora: judged by what calls actually did, not by AGORA_APP_ID being set.
    # Token failures are matched against the exact reasons the engine writes
    # rather than a LIKE pattern: the compat layer leaves a literal '%s' alone,
    # so any LIKE string is one character away from becoming a placeholder.
    token_marks = ",".join(["?"] * len(TOKEN_FAILURE_REASONS))
    cur.execute(
        f"""
        SELECT COUNT(*) AS attempts,
               SUM(CASE WHEN COALESCE(answered_at, '') <> '' THEN 1 ELSE 0 END) AS answered,
               SUM(CASE WHEN end_reason IN ({token_marks}) THEN 1 ELSE 0 END) AS token_failures,
               SUM(CASE WHEN end_reason = 'client_connect_failed' THEN 1 ELSE 0 END) AS connect_failures
        FROM communication_calls
        WHERE created_at >= ? AND COALESCE(provider, '') = 'agora'
        """,
        (*TOKEN_FAILURE_REASONS, day_floor),
    )
    row = _one(cur)
    attempts = _int(row.get("attempts"))
    token_failures = _int(row.get("token_failures"))
    connect_failures = _int(row.get("connect_failures"))
    if not attempts:
        providers.append(_provider(
            "agora", "Agora (calls)", "unknown",
            "No call attempts in the last 7 days, so there is nothing to judge.",
            "observed call outcomes",
        ))
    elif token_failures:
        providers.append(_provider(
            "agora", "Agora (calls)", "failed",
            f"{token_failures} of {attempts} call attempts failed at token issuance in 7d.",
            "observed call outcomes",
        ))
    elif connect_failures:
        providers.append(_provider(
            "agora", "Agora (calls)", "degraded",
            f"{connect_failures} of {attempts} call attempts could not connect in 7d.",
            "observed call outcomes",
        ))
    else:
        providers.append(_provider(
            "agora", "Agora (calls)", "healthy",
            f"{attempts} call attempts in 7d with no token or connect failures.",
            "observed call outcomes",
        ))

    # Push, email and SMS: judged by delivery job outcomes per channel. Rated
    # over 7d, not 24h, because at this traffic volume a 24h window is often
    # empty and an empty window can only honestly be reported "unknown" -- which
    # tells the owner nothing about whether push works.
    week_floor = _window_floor(WINDOWS["7d"], now)
    ok_marks = ",".join(["?"] * len(DELIVERY_SUCCESS_STATUSES))
    bad_marks = ",".join(["?"] * len(DELIVERY_FAILURE_STATUSES))
    skip_marks = ",".join(["?"] * len(DELIVERY_UNROUTABLE_STATUSES))
    cur.execute(
        f"""
        SELECT COALESCE(NULLIF(channel, ''), 'unknown') AS channel,
               COUNT(*) AS total,
               SUM(CASE WHEN status IN ({ok_marks}) THEN 1 ELSE 0 END) AS delivered,
               SUM(CASE WHEN status IN ({bad_marks}) THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN status IN ({skip_marks}) THEN 1 ELSE 0 END) AS unroutable
        FROM notification_delivery_jobs
        WHERE created_at >= ?
        GROUP BY COALESCE(NULLIF(channel, ''), 'unknown')
        """,
        (*DELIVERY_SUCCESS_STATUSES, *DELIVERY_FAILURE_STATUSES,
         *DELIVERY_UNROUTABLE_STATUSES, week_floor),
    )
    channel_labels = {
        "push": "Push delivery",
        "email": "Email delivery",
        "sms": "SMS delivery",
        "in_app": "In-app delivery",
    }
    seen_channels = set()
    for channel_row in _rows(cur):
        channel = str(channel_row.get("channel"))
        seen_channels.add(channel)
        total = _int(channel_row.get("total"))
        failed = _int(channel_row.get("failed"))
        unroutable = _int(channel_row.get("unroutable"))
        label = channel_labels.get(channel, f"{channel.title()} delivery")
        # A recipient with no registered device is not a broken provider, so
        # unroutable attempts are excluded from the health verdict and reported
        # alongside it instead.
        attempted = max(total - unroutable, 0)
        if not total:
            state, detail = "unknown", "No delivery attempts in the last 7 days."
        elif not attempted:
            state = "unknown"
            detail = f"All {total} attempts in 7d had no registered recipient, so the provider was never exercised."
        elif failed >= attempted:
            state, detail = "failed", f"All {attempted} real attempts failed in 7d."
        elif failed:
            state, detail = "degraded", f"{failed} of {attempted} real attempts failed in 7d."
        else:
            state, detail = "healthy", f"{attempted} attempts in 7d, none failed."
        if unroutable:
            detail += f" {unroutable} further attempt(s) had no registered device or contact."
        providers.append(_provider(f"delivery_{channel}", label, state, detail, "delivery job outcomes"))

    for channel, label in channel_labels.items():
        if channel not in seen_channels:
            providers.append(_provider(
                f"delivery_{channel}", label, "unknown",
                "No delivery attempts recorded in the last 7 days.",
                "delivery job outcomes",
            ))

    # Mux has no persisted call outcome of any kind, so it cannot be rated.
    providers.append(_provider(
        "mux", "Mux (live distribution)", "unknown",
        "Mux API results are not persisted, so health cannot be observed from stored data.",
        "not instrumented",
    ))

    return {"providers": providers, "transport_breakdown_available": False}


def _provider(key: str, label: str, state: str, detail: str, basis: str) -> dict[str, Any]:
    return {"key": key, "label": label, "state": state, "detail": detail, "basis": basis}


def _incidents(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Material conditions worth an operator's attention, derived from sections."""
    incidents: list[dict[str, Any]] = []

    for name in ("calls", "chat", "delivery", "providers"):
        section = snapshot.get(name) or {}
        if section.get("state") == "error":
            incidents.append({
                "severity": "critical",
                "title": f"{name.title()} metrics unavailable",
                "detail": f"The {name} section failed to load ({section.get('error')}). "
                          "Counts shown elsewhere may be incomplete.",
            })

    providers = (snapshot.get("providers") or {}).get("providers") or []
    for provider in providers:
        if provider.get("state") == "failed":
            incidents.append({
                "severity": "critical",
                "title": f"{provider.get('label')} failing",
                "detail": str(provider.get("detail") or ""),
            })
        elif provider.get("state") == "degraded":
            incidents.append({
                "severity": "warning",
                "title": f"{provider.get('label')} degraded",
                "detail": str(provider.get("detail") or ""),
            })

    # Rates are judged over 24h rather than 1h. At measured production volume a
    # 1h window holds no delivery attempts at all, so a 1h rate rule can never
    # fire and would be a detector that exists only on paper.
    delivery = snapshot.get("delivery") or {}
    day = ((delivery.get("windows") or {}).get("24h") or {})
    attempted = _int(day.get("total")) - _int(day.get("unroutable"))
    if _int(day.get("failed")) and attempted > 0:
        share = _int(day.get("failed")) * 100 // attempted
        if share >= 25:
            incidents.append({
                "severity": "warning",
                "title": "Notification delivery failure rate elevated",
                "detail": f"{day.get('failed')} of {attempted} real delivery attempts failed in the last 24h ({share}%).",
            })

    # Unroutable is its own condition: nothing is broken, but a large share of
    # notifications has no device to go to, which is why members report silence.
    week = ((delivery.get("windows") or {}).get("7d") or {})
    if _int(week.get("unroutable")) and _int(week.get("total")):
        share = _int(week.get("unroutable")) * 100 // max(_int(week.get("total")), 1)
        if share >= 15:
            incidents.append({
                "severity": "warning",
                "title": "Notifications with no registered recipient",
                "detail": f"{week.get('unroutable')} of {week.get('total')} delivery attempts in 7 days "
                          f"had no registered device or contact ({share}%). The provider is fine; "
                          "these members cannot be reached.",
            })

    calls = snapshot.get("calls") or {}
    call_day = ((calls.get("windows") or {}).get("24h") or {})
    if _int(call_day.get("unsuccessful")) >= 3:
        incidents.append({
            "severity": "warning",
            "title": "Calls not completing",
            "detail": f"{call_day.get('unsuccessful')} of {call_day.get('started')} calls in the last 24h did not complete.",
        })

    return incidents


def _overall_state(snapshot: dict[str, Any], incidents: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "critical" for item in incidents):
        return "critical"
    if any(item.get("severity") == "warning" for item in incidents):
        return "degraded"
    sections = [(snapshot.get(name) or {}).get("state") for name in ("calls", "chat", "delivery", "providers")]
    if any(state != "ready" for state in sections):
        return "degraded"
    return "healthy"


def comms_ops_snapshot() -> dict[str, Any]:
    """Bounded, metadata-only operational snapshot of PulseSoc communications."""
    started = time.perf_counter()
    now = _now()
    errors: dict[str, str] = {}
    bot = _bot()
    conn = None
    try:
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        snapshot: dict[str, Any] = {
            "calls": _section("calls", lambda: _calls_section(cur, now), errors),
            "chat": _section("chat", lambda: _chat_section(cur, now), errors),
            "delivery": _section("delivery", lambda: _delivery_section(cur, now), errors),
            "providers": _section("providers", lambda: _providers_section(cur, now), errors),
        }
    except Exception as exc:
        logger.warning("PULSESOC_COMMS_OPS_SNAPSHOT_FAILED error=%s", type(exc).__name__)
        return {
            "ok": False,
            "state": "critical",
            "generated_at": _stamp(now),
            "error": type(exc).__name__,
            "incidents": [{
                "severity": "critical",
                "title": "Communications metrics unavailable",
                "detail": "The communications snapshot could not reach the database.",
            }],
            "section_errors": {"snapshot": type(exc).__name__},
            "query_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    incidents = _incidents(snapshot)
    snapshot.update({
        "ok": not errors,
        "state": _overall_state(snapshot, incidents),
        "generated_at": _stamp(now),
        "windows": list(WINDOWS.keys()),
        "incidents": incidents,
        "section_errors": errors,
        "query_ms": int((time.perf_counter() - started) * 1000),
    })
    logger.info(
        "PULSESOC_COMMS_OPS_SNAPSHOT state=%s duration_ms=%s section_errors=%s",
        snapshot["state"], snapshot["query_ms"], ",".join(sorted(errors)) or "none",
    )
    return snapshot


SNAPSHOT_CACHE_TTL_SECONDS = 10

_cache_lock = threading.Lock()
_cache_entry: tuple[float, dict[str, Any]] | None = None


def cached_comms_ops_snapshot(ttl: int = SNAPSHOT_CACHE_TTL_SECONDS) -> dict[str, Any]:
    """The snapshot every HTTP caller should use.

    The page refreshes itself so an operator can watch a call land, which means
    the snapshot is re-requested far more often than the underlying numbers can
    change. Without a cache, one open tab is a steady stream of ~16 queries
    every few seconds and several open tabs multiply it. A short TTL keeps the
    page feeling live while bounding the database cost to one snapshot per ttl
    per worker.

    Errors are cached too, deliberately: a database that is refusing
    connections must not be hammered harder because the page is polling. The
    cost is that recovery takes up to ttl seconds to show, which is why ttl is
    seconds rather than minutes. ``comms_ops_snapshot()`` stays uncached so
    tests observe what the database holds right now.
    """
    global _cache_entry
    now = time.monotonic()
    with _cache_lock:
        entry = _cache_entry
        if entry is not None and now - entry[0] < ttl:
            payload = dict(entry[1])
            payload["cache_age_seconds"] = int(now - entry[0])
            return payload
    snapshot = comms_ops_snapshot()
    with _cache_lock:
        _cache_entry = (time.monotonic(), snapshot)
    payload = dict(snapshot)
    payload["cache_age_seconds"] = 0
    return payload


def reset_snapshot_cache() -> None:
    global _cache_entry
    with _cache_lock:
        _cache_entry = None


#: §16. What a lookup term is allowed to look like. Deliberately narrow: the
#: identifiers this surface hands an operator are opaque public ids, so anything
#: that is not shaped like one is refused rather than searched.
_LOOKUP_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$")

#: The prefix length shown on the page by ``_short_id``. A lookup has to accept
#: the handle the operator can actually see, which is the first eight characters
#: and an ellipsis, so the query matches on that prefix as well as the whole id.
LOOKUP_PREFIX = 8

MAX_LOOKUP_ROWS = 10


def lookup_term_refusal(term: str) -> str:
    """Why this term will not be searched, or "" if it will be.

    §16 asks for search over operational identifiers and explicitly not for user
    enumeration, and the difference between the two is entirely in what the box
    accepts. Three refusals, each closing a specific door:

    * An address -- anything containing "@" -- is refused because answering
      "found"/"not found" for an email turns the box into an oracle for whether
      a person has an account, which is enumeration with extra steps.
    * A bare number is refused for the same reason and a worse one: sequential
      primary keys are enumerable by construction, so a numeric lookup is a
      loop away from a full dump.
    * Anything not shaped like a public id is refused rather than passed to a
      query, so the term never reaches SQL as a pattern.

    Returning the reason rather than a bool exists so the page can say which
    rule applied. An operator told only "no results" would reasonably retype
    an email twice before concluding the box is broken.
    """
    text = str(term or "").strip()
    if not text:
        return "Enter a call or conversation id."
    if "@" in text:
        return (
            "Addresses are not searched here. This box resolves call and conversation ids; "
            "answering for an address would make it a way to discover who has an account."
        )
    if text.isdigit():
        return (
            "Numeric ids are not searched here. Row numbers run in sequence, so accepting one "
            "would make this box enumerable. Use the session id shown in the tables."
        )
    if not _LOOKUP_TERM.match(text):
        return (
            "Not shaped like an identifier. Call and conversation ids are 4-64 characters of "
            "letters, digits, hyphen or underscore."
        )
    return ""


def comms_ops_lookup(term: str) -> dict[str, Any]:
    """§16. Resolve one operational identifier to operational facts.

    Scoped to the two identifier spaces this surface puts in front of an
    operator: ``communication_calls.public_id`` and
    ``comm_v2_conversations.public_id``. Both are opaque, neither is a person.

    What comes back is the same classification the snapshot uses -- state,
    timing, counts, provider, end reason. Not ``room_name``, which is a joinable
    handle and therefore a credential; not a message body; not a participant
    roster. A lookup is the most tempting place on an operations page to answer
    "who" instead of "what", so it answers the same questions the tables do and
    no additional ones.

    Matching is exact on the full id or exact on its first eight characters, via
    SUBSTR rather than LIKE. That is not a style choice: the database compat
    layer escapes ``%`` in literals, so a LIKE pattern written here would be
    mangled on PostgreSQL and silently behave differently from SQLite.
    """
    text = str(term or "").strip()
    refusal = lookup_term_refusal(text)
    if refusal:
        return {"state": "refused", "term": text, "reason": refusal,
                "calls": [], "conversations": []}

    prefix = text[:LOOKUP_PREFIX]
    now = _now()
    bot = _bot()
    conn = bot.db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT public_id, call_type, call_scope, provider, status, created_at,
                   started_at, answered_at, ended_at, end_reason, duration_seconds
            FROM communication_calls
            WHERE public_id = ? OR SUBSTR(public_id, 1, {LOOKUP_PREFIX}) = ?
            ORDER BY id DESC
            LIMIT {MAX_LOOKUP_ROWS}
            """,  # noqa: S608 -- both interpolations are module int constants
            (text, prefix),
        )
        calls = []
        for row in _rows(cur):
            status = str(row.get("status") or "") or "unknown"
            calls.append({
                "session": _short_id(row.get("public_id")),
                "type": str(row.get("call_type") or "") or "unknown",
                "scope": str(row.get("call_scope") or "") or "unknown",
                "provider": str(row.get("provider") or "") or "unknown",
                "status": status,
                "live": status in LIVE_CALL_STATUSES,
                "active": status in ACTIVE_CALL_STATUSES,
                "created_at": str(row.get("created_at") or ""),
                "started_at": str(row.get("started_at") or ""),
                "answered": bool(str(row.get("answered_at") or "").strip()),
                "ended_at": str(row.get("ended_at") or ""),
                "end_reason": str(row.get("end_reason") or ""),
                "duration_seconds": _int(row.get("duration_seconds")),
                "participants": _participant_count(cur, row.get("public_id")),
            })

        cur.execute(
            f"""
            SELECT public_id, conversation_type, member_count, status,
                   created_at, last_message_at, last_activity_at
            FROM comm_v2_conversations
            WHERE public_id = ? OR SUBSTR(public_id, 1, {LOOKUP_PREFIX}) = ?
            ORDER BY id DESC
            LIMIT {MAX_LOOKUP_ROWS}
            """,  # noqa: S608 -- both interpolations are module int constants
            (text, prefix),
        )
        conversations = [{
            "conversation": _short_id(row.get("public_id")),
            "kind": str(row.get("conversation_type") or "") or "unknown",
            "members": _int(row.get("member_count")),
            "status": str(row.get("status") or "") or "unknown",
            "created_at": str(row.get("created_at") or ""),
            "last_message_at": str(row.get("last_message_at") or ""),
            "last_activity_at": str(row.get("last_activity_at") or ""),
        } for row in _rows(cur)]
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - close is best effort
            pass

    found = len(calls) + len(conversations)
    return {
        # "found"/"not_found" rather than an empty list, so the page can tell an
        # operator that the id does not exist instead of rendering a blank table
        # that reads the same as a query that failed.
        "state": "found" if found else "not_found",
        "term": text,
        "matched_on": "id or 8-character prefix",
        "calls": calls,
        "conversations": conversations,
        "generated_at": _stamp(now),
    }


def _participant_count(cur, public_id: Any) -> int:
    """How many took part, never who.

    A count is operations; a roster is a contact graph. The join goes through
    the call's row id rather than trusting the caller's text.
    """
    try:
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM communication_call_participants p
            JOIN communication_calls c ON c.id = p.call_id
            WHERE c.public_id = ?
            """,
            (str(public_id or ""),),
        )
        return _int(_one(cur).get("n"))
    except Exception:
        # The participants table is lazily created like the rest of the comms
        # schema. An absent one must not take the lookup down with it.
        return 0


def comms_user_diagnostics(user_id: int) -> dict[str, Any]:
    """§17. Why communication is or is not working for one account.

    The operational question this answers is a support question: a member says
    calls do not ring, or that they get no notifications, and the admin looking
    at their account needs to know whether the system agrees. So what comes back
    is counts and outcomes -- did calls happen, did any connect, is delivery
    failing and on which channel, and what the pipeline said about it.

    What does not come back is who they talked to. A per-user panel is where an
    operations page turns into a social graph, and the slide is one join away:
    ``communication_call_participants`` holds every other participant, and a
    "spoke with" column would read as helpful context while being a record of
    who knows whom. Peers are counted, never named. Message bodies and
    conversation identifiers are absent for the same reason -- a conversation id
    here would hand the lookup above a way to walk from a person to their
    threads, which is the one path this surface must not have.

    Every section is independently guarded, because the comms schema is created
    lazily and a deployment that has never placed a call has no participants
    table. A missing table yields "unavailable" for that line and leaves the
    rest, rather than taking the account page down.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return {"state": "error", "error": "no_user", "sections": {}}

    now = _now()
    day = _window_floor(WINDOWS["24h"], now)
    week = _window_floor(WINDOWS["7d"], now)
    sections: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def part(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        """One line of the panel, allowed to fail on its own."""
        try:
            sections[name] = dict(fn(), state="ready")
        except Exception as exc:
            logger.warning("COMMS_USER_DIAG_SECTION_FAILED user=%s section=%s error=%s",
                           uid, name, type(exc).__name__)
            errors[name] = type(exc).__name__
            # Never zeroes. "No calls" and "we could not look" are different
            # answers to a support question, and the wrong one sends an admin
            # to tell a member their phone is fine.
            sections[name] = {"state": "error", "error": type(exc).__name__}

    bot = _bot()
    conn = bot.db()
    try:
        cur = conn.cursor()

        def calls_started() -> dict[str, Any]:
            cur.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS week,
                       SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS day,
                       SUM(CASE WHEN COALESCE(duration_seconds,0) > 0 THEN 1 ELSE 0 END) AS connected,
                       SUM(CASE WHEN answered_at IS NOT NULL AND answered_at <> ''
                                THEN 1 ELSE 0 END) AS answered,
                       MAX(created_at) AS last_at
                FROM communication_calls
                WHERE created_by_user_id = ?
                """,
                (week, day, uid),
            )
            row = _one(cur)
            return {
                "total": _int(row.get("total")),
                "7d": _int(row.get("week")),
                "24h": _int(row.get("day")),
                "answered": _int(row.get("answered")),
                "connected": _int(row.get("connected")),
                "last_at": str(row.get("last_at") or ""),
            }

        def calls_placed_outcomes() -> dict[str, Any]:
            # The diagnostic shape: a member whose calls all end 'failed' or
            # 'missed' has a different problem from one whose calls connect.
            cur.execute(
                f"""
                SELECT status, COUNT(*) AS n
                FROM communication_calls
                WHERE created_by_user_id = ? AND created_at >= ?
                GROUP BY status
                ORDER BY n DESC
                LIMIT {MAX_FAILURE_ROWS}
                """,  # noqa: S608 -- the interpolation is a module int constant
                (uid, week),
            )
            return {"by_status": [{"status": str(r.get("status") or "") or "unknown",
                                   "count": _int(r.get("n"))} for r in _rows(cur)]}

        def calls_joined() -> dict[str, Any]:
            cur.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN c.created_at >= ? THEN 1 ELSE 0 END) AS week,
                       MAX(c.created_at) AS last_at
                FROM communication_call_participants p
                JOIN communication_calls c ON c.id = p.call_id
                WHERE p.user_id = ?
                """,
                (week, uid),
            )
            row = _one(cur)
            return {
                "total": _int(row.get("total")),
                "7d": _int(row.get("week")),
                "last_at": str(row.get("last_at") or ""),
            }

        def messages_sent() -> dict[str, Any]:
            # Counts and timing. Not bodies, not conversation ids, not peers.
            cur.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS week,
                       SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS day,
                       COUNT(DISTINCT conversation_id) AS threads,
                       MAX(created_at) AS last_at
                FROM comm_v2_messages
                WHERE sender_user_id = ?
                """,
                (week, day, uid),
            )
            row = _one(cur)
            return {
                "total": _int(row.get("total")),
                "7d": _int(row.get("week")),
                "24h": _int(row.get("day")),
                # A number, so an admin can see "they are messaging" without
                # being handed a list of which threads to go and read.
                "threads": _int(row.get("threads")),
                "last_at": str(row.get("last_at") or ""),
            }

        def delivery() -> dict[str, Any]:
            # The most useful line on the panel: "why am I not getting
            # notifications?" is the complaint this exists to answer.
            cur.execute(
                """
                SELECT channel, status, COUNT(*) AS n, MAX(created_at) AS last_at
                FROM notification_delivery_jobs
                WHERE recipient_user_id = ? AND created_at >= ?
                GROUP BY channel, status
                ORDER BY n DESC
                LIMIT 24
                """,
                (uid, week),
            )
            rows = _rows(cur)
            by_channel: dict[str, dict[str, Any]] = {}
            for row in rows:
                channel = str(row.get("channel") or "") or "unknown"
                status = str(row.get("status") or "") or "unknown"
                count = _int(row.get("n"))
                entry = by_channel.setdefault(channel, {
                    "channel": channel, "attempted": 0, "delivered": 0,
                    "failed": 0, "unroutable": 0, "last_at": "",
                })
                entry["attempted"] += count
                if status in DELIVERY_SUCCESS_STATUSES:
                    entry["delivered"] += count
                elif status in DELIVERY_FAILURE_STATUSES:
                    entry["failed"] += count
                elif status in DELIVERY_UNROUTABLE_STATUSES:
                    entry["unroutable"] += count
                last_at = str(row.get("last_at") or "")
                if last_at > entry["last_at"]:
                    entry["last_at"] = last_at
            channels = sorted(by_channel.values(), key=lambda e: -e["attempted"])
            return {"channels": channels,
                    "failing": [c["channel"] for c in channels if c["failed"]]}

        def delivery_reasons() -> dict[str, Any]:
            # The pipeline's own words, which is what makes this actionable:
            # "410 BadDeviceToken" tells an admin to have them reinstall.
            cur.execute(
                f"""
                SELECT channel, failure_reason, COUNT(*) AS n
                FROM notification_delivery_jobs
                WHERE recipient_user_id = ? AND created_at >= ?
                  AND COALESCE(failure_reason, '') <> ''
                GROUP BY channel, failure_reason
                ORDER BY n DESC
                LIMIT {MAX_FAILURE_ROWS}
                """,  # noqa: S608 -- the interpolation is a module int constant
                (uid, week),
            )
            return {"reasons": [{
                "channel": str(r.get("channel") or "") or "unknown",
                # Redacted for the same reason as the snapshot's copy: this is
                # adapter output, and on this panel it sits beside a named
                # person's account.
                "reason": safe_failure_reason(r.get("failure_reason")),
                "count": _int(r.get("n")),
            } for r in _rows(cur)]}

        part("calls_placed", calls_started)
        part("calls_placed_outcomes", calls_placed_outcomes)
        part("calls_joined", calls_joined)
        part("messages_sent", messages_sent)
        part("delivery", delivery)
        part("delivery_reasons", delivery_reasons)
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - close is best effort
            pass

    return {
        "state": "degraded" if errors else "ready",
        "user_id": uid,
        "window": "7 days",
        "section_errors": errors,
        "sections": sections,
        "generated_at": _stamp(now),
    }


def dashboard_headline(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """The few numbers the main admin dashboard shows, from one snapshot.

    Every count is 24h, and every stream also carries when it last moved. A
    failed section yields None, never 0, so the dashboard can say "unavailable"
    where it would otherwise print a zero it did not measure.

    Takes an optional snapshot so a caller that already has one -- the admin
    dashboard, which renders the headline and links to the full page -- does not
    pay for a second set of queries.
    """
    if snapshot is None:
        snapshot = comms_ops_snapshot()
    calls = snapshot.get("calls") or {}
    chat = snapshot.get("chat") or {}
    delivery = snapshot.get("delivery") or {}
    calls_ready = calls.get("state") == "ready"
    chat_ready = chat.get("state") == "ready"
    delivery_ready = delivery.get("state") == "ready"
    day_calls = ((calls.get("windows") or {}).get("24h") or {})
    day_chat = ((chat.get("windows") or {}).get("24h") or {})
    day_delivery = ((delivery.get("windows") or {}).get("24h") or {})
    return {
        "state": snapshot.get("state") or "unknown",
        "generated_at": snapshot.get("generated_at") or "",
        "active_calls": calls.get("active_count") if calls_ready else None,
        "live_calls": calls.get("live_count") if calls_ready else None,
        "calls_24h": day_calls.get("started") if calls_ready else None,
        "calls_unsuccessful_24h": day_calls.get("unsuccessful") if calls_ready else None,
        "last_call_at": calls.get("last_call_at") if calls_ready else None,
        "active_conversations_24h": day_chat.get("active_conversations") if chat_ready else None,
        "messages_24h": day_chat.get("messages") if chat_ready else None,
        "last_message_at": chat.get("last_message_at") if chat_ready else None,
        "delivery_failures_24h": day_delivery.get("failed") if delivery_ready else None,
        "delivery_unroutable_24h": day_delivery.get("unroutable") if delivery_ready else None,
        "last_delivery_at": delivery.get("last_attempt_at") if delivery_ready else None,
        "incidents": snapshot.get("incidents") or [],
        "section_errors": snapshot.get("section_errors") or {},
    }
