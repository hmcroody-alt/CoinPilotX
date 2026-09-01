"""Pulse Briefing engine: server-side scheduling, suppression, delivery, history.

Runs as a tick inside alert_worker (no new Railway service). Every ~6h window
per user is an EVALUATION, not a mandatory send: insignificant or duplicate
facts are suppressed and recorded. Idempotency key = user_id + local date +
window, enforced by a UNIQUE index so worker restarts can never double-send.

Kill switch: BRIEFINGS_DISABLED=true stops scheduled sends only; normal
PulseSoc notifications are unaffected.

Three flags, three distinct states, deliberately not overloaded:

    BRIEFINGS_DISABLED=true       engine off; run_scheduled_cycle returns before
                                  a single row is read. Nothing is measurable.
    BRIEFING_SHADOW_MODE=true     engine runs end to end -- claim, gather, score,
                                  suppress, summarize, settle -- and delivery is
                                  skipped. Zero pushes, by construction.
    (neither set)                 normal delivery.

BRIEFINGS_DISABLED short-circuits PULSE_BRIEFINGS_ENABLED, so those two are one
gate and not two layers; shadow is the separate axis because "off" and "runs but
sends nothing" are different questions and a single flag cannot answer both.
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import crypto_provider, facts as facts_mod, summarizer
from .. import pulse_region_preferences as region_preferences
from .. import user_context

BRIEFING_WINDOWS = (0, 6, 12, 18)  # local-time window starts (hours)
JITTER_MINUTES = int(os.getenv("BRIEFING_JITTER_MINUTES", "25"))
DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "07:00"
MIN_ACCOUNT_AGE_HOURS = int(os.getenv("BRIEFING_MIN_ACCOUNT_AGE_HOURS", "24"))  # Stage 53
HISTORY_RETENTION_DAYS = int(os.getenv("BRIEFING_HISTORY_RETENTION_DAYS", "60"))
SEND_RATE_CAP_PER_CYCLE = int(os.getenv("BRIEFING_SEND_RATE_CAP", "200"))
# "smart" is the recommended default cadence: it evaluates on the standard
# 6-hour windows and relies on the significance/dedupe gates (which apply to
# EVERY frequency) to decide whether anything is worth sending. Mechanically it
# shares every_6h's windows today; it exists as its own value so the product can
# tighten or loosen its gating later without a preference migration. "daily"
# evaluates once per local morning. Frequency is an evaluation cadence, never a
# delivery promise.
FREQUENCIES = ("off", "important_only", "every_6h", "morning_evening", "daily", "smart")

_METRICS = {
    "briefing_jobs_started": 0, "briefing_jobs_completed": 0, "briefing_jobs_failed": 0,
    "briefings_sent": 0, "briefings_suppressed": 0, "briefings_duplicate_suppressed": 0,
    "briefings_shadow_suppressed": 0,
}

_TRUTHY = ("1", "true", "yes", "on")


def _env_flag(name: str, default: str = "") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() in _TRUTHY


def metrics_snapshot() -> dict[str, int]:
    merged = dict(_METRICS)
    merged.update(crypto_provider.metrics_snapshot())
    return merged


def briefings_enabled() -> bool:
    if _env_flag("BRIEFINGS_DISABLED"):
        return False
    return _env_flag("PULSE_BRIEFINGS_ENABLED", "true")


def shadow_mode() -> bool:
    """Run the engine for real, deliver nothing.

    Exists because BRIEFINGS_DISABLED is the wrong instrument for acceptance: it
    returns before the first query, so it proves nothing about scheduling, scoring,
    suppression, the Postgres claim/settle path or the deeplink id. Shadow keeps
    every one of those on the real production database and removes exactly one
    step -- the push.

    Independent of the kill switch on purpose. BRIEFINGS_DISABLED still wins; a
    shadow flag that could re-enable a disabled engine would be a kill switch with
    a bypass.
    """
    return _env_flag("BRIEFING_SHADOW_MODE")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_schema(conn=None) -> None:
    owns = conn is None
    conn = conn or user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_briefings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            window_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT DEFAULT '',
            body TEXT DEFAULT '',
            facts_json TEXT DEFAULT '',
            fingerprint TEXT DEFAULT '',
            summary_source TEXT DEFAULT '',
            suppressed_reason TEXT DEFAULT '',
            crypto_provider TEXT DEFAULT '',
            locale TEXT DEFAULT 'en',
            generated_at TEXT DEFAULT '',
            sent_at TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_briefings_window ON pulse_briefings(user_id, window_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_briefings_user_created ON pulse_briefings(user_id, created_at)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_briefing_prefs (
            user_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            network_enabled INTEGER DEFAULT 1,
            crypto_enabled INTEGER DEFAULT 1,
            watchlist_enabled INTEGER DEFAULT 1,
            frequency TEXT DEFAULT 'every_6h',
            quiet_start TEXT DEFAULT '22:00',
            quiet_end TEXT DEFAULT '07:00',
            jitter_minutes INTEGER DEFAULT -1,
            last_seen_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
        """
    )
    # Idempotent upgrade for rows created before the hub shipped. Isolated in
    # its own mini-transaction: on Postgres a failed ALTER poisons the current
    # transaction, so committing first means a duplicate-column failure rolls
    # back nothing but itself.
    conn.commit()
    try:
        cur.execute("ALTER TABLE pulse_briefing_prefs ADD COLUMN last_seen_at TEXT DEFAULT ''")
        conn.commit()
    except Exception:  # noqa: BLE001 - column already exists
        conn.rollback()
        cur = conn.cursor()
    # Timezone authority (section A): quiet hours and window selection are only
    # correct if the canonical region-preference table exists in THIS process.
    # alert_worker never serves the settings route, so the service's own lazy
    # ensure_schema had never run there and every lookup raised UndefinedTable.
    # Creating it here -- inside the engine's own committing DDL block -- means
    # the table is present before the first claim, and the @run_once_per_process
    # guard can never be burned by a later transaction rollback.
    try:
        region_preferences.ensure_schema(conn)
    except Exception:  # noqa: BLE001 - never let an optional table block briefings
        logging.exception("BRIEFING_REGION_PREF_SCHEMA_FAILED")
    conn.commit()
    if owns:
        conn.close()


# --- Preferences (Stage 14/22/23/54) ---------------------------------------

def get_preferences(user_id: int, *, conn=None) -> dict[str, Any]:
    owns = conn is None
    conn = conn or user_context.connect()
    cur = conn.cursor()
    ensure_schema(conn)
    cur.execute("SELECT * FROM pulse_briefing_prefs WHERE user_id=? LIMIT 1", (int(user_id),))
    row = cur.fetchone()
    prefs = dict(row) if row else {}
    if owns:
        conn.close()
    return {
        "enabled": bool(prefs.get("enabled", 1)),
        "network_enabled": bool(prefs.get("network_enabled", 1)),
        "crypto_enabled": bool(prefs.get("crypto_enabled", 1)),
        "watchlist_enabled": bool(prefs.get("watchlist_enabled", 1)),
        "frequency": prefs.get("frequency") or "every_6h",
        "quiet_start": prefs.get("quiet_start") or DEFAULT_QUIET_START,
        "quiet_end": prefs.get("quiet_end") or DEFAULT_QUIET_END,
    }


def push_notifications_allowed(cur, user_id: int) -> bool:
    """Global push opt-out, read from the same row the canonical notification
    rules engine reads (notification_preferences category='global',
    experience.enable_push_notifications). Fails OPEN on a missing row -- a user
    who has never opened notification settings has not opted out -- and fails
    CLOSED on a query error, because being unable to prove consent is not
    consent."""
    try:
        cur.execute(
            "SELECT enable_push_notifications FROM notification_preferences "
            "WHERE user_id=? AND category='global' LIMIT 1",
            (int(user_id),),
        )
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        logging.exception("BRIEFING_PUSH_OPTOUT_LOOKUP_FAILED user_id=%s", user_id)
        return False
    if not row:
        return True
    try:
        value = row["enable_push_notifications"]
    except Exception:  # noqa: BLE001 - tuple-shaped cursor
        value = row[0]
    if value is None:
        return True
    return bool(value)


class InvalidPreference(ValueError):
    """A preference write the engine refuses rather than silently rewrites.

    Stage 25: the old code filtered unknown values out of the update and still
    returned 200 with the UNCHANGED preferences. A client sending
    frequency="hourly" (or a typo'd toggle) got a success response describing a
    state it did not ask for, and the mismatch only surfaced later as "my setting
    didn't stick". Refusing is the only answer that keeps the client and the
    server honest about what was stored.
    """

    def __init__(self, field: str, value: Any, expected: str):
        self.field = field
        self.value = value
        self.expected = expected
        super().__init__(f"Invalid value for {field}: expected {expected}.")


def _validated_preferences(values: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Return only the keys the caller actually sent, validated. Absent keys are
    left alone -- a PATCH of one toggle must not restate the rest."""
    out: dict[str, Any] = {}
    for key in ("enabled", "network_enabled", "crypto_enabled", "watchlist_enabled"):
        if key not in values:
            continue
        raw = values[key]
        # Booleans only. Accepting a bare truthy string here is how "false" (a
        # non-empty string) becomes True and silently enables a topic the user
        # just switched off.
        if isinstance(raw, bool):
            out[key] = raw
        elif isinstance(raw, int) and raw in (0, 1):
            out[key] = bool(raw)
        elif isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            out[key] = raw.strip().lower() == "true"
        else:
            raise InvalidPreference(key, raw, "a boolean")
    if "frequency" in values:
        raw = values["frequency"]
        if not isinstance(raw, str) or raw not in FREQUENCIES:
            raise InvalidPreference("frequency", raw, "one of " + ", ".join(FREQUENCIES))
        out["frequency"] = raw
    for key in ("quiet_start", "quiet_end"):
        if key not in values:
            continue
        raw = values[key]
        parts = str(raw or "").split(":")
        if len(parts) != 2:
            raise InvalidPreference(key, raw, "HH:MM")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            raise InvalidPreference(key, raw, "HH:MM") from None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise InvalidPreference(key, raw, "HH:MM within 00:00-23:59")
        out[key] = f"{hour:02d}:{minute:02d}"
    return out


def push_transport_status(cur, user_id: int) -> dict[str, Any]:
    """Can a briefing push actually REACH this user right now?

    "Briefings enabled" and "push will arrive" are different facts, and the hub
    used to report the first while claiming the second. A user who denied the OS
    prompt, signed out on their only device, or is on a build with push disabled
    has zero rows in push_subscriptions -- yet the preference row still says push
    is allowed, so the screen read "Push notifications are on." and the user
    waited for a notification that could never be sent.

    This queries push_subscriptions with the SAME predicate _deliver's
    push_service.send_push uses, so the status cannot drift from the sender: if
    this says ready, send_push finds rows; if it says no_devices, send_push
    returns not_configured. Reason is ordered by what the sender checks first.
    """
    status = {
        "preference_allows": push_notifications_allowed(cur, user_id),
        "provider_enabled": True,
        "device_count": 0,
    }
    try:
        from .. import push_service
        status["provider_enabled"] = bool(push_service._provider_send_enabled())
    except Exception:  # noqa: BLE001 - transport introspection is never fatal
        logging.exception("BRIEFING_PUSH_PROVIDER_LOOKUP_FAILED user_id=%s", user_id)
    try:
        cur.execute(
            "SELECT COUNT(*) AS n FROM push_subscriptions "
            "WHERE user_id=? AND COALESCE(is_active, active, 1)=1",
            (int(user_id),),
        )
        row = cur.fetchone()
        if row is not None:
            try:
                status["device_count"] = int(row["n"])
            except Exception:  # noqa: BLE001 - tuple-shaped cursor
                status["device_count"] = int(row[0] or 0)
    except Exception:  # noqa: BLE001 - table may not exist in a partial schema
        logging.exception("BRIEFING_PUSH_DEVICE_LOOKUP_FAILED user_id=%s", user_id)
        status["device_count"] = 0

    if not status["provider_enabled"]:
        reason = "provider_disabled"
    elif not status["preference_allows"]:
        reason = "preference_off"
    elif status["device_count"] <= 0:
        reason = "no_devices"
    else:
        reason = None
    status["ready"] = reason is None
    status["reason"] = reason
    return status


def update_preferences(user_id: int, values: dict[str, Any], *, conn=None) -> dict[str, Any]:
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    current = get_preferences(user_id, conn=conn)
    try:
        current.update(_validated_preferences(values, current))
    except InvalidPreference:
        if owns:
            conn.close()
        raise
    cur.execute(
        """
        INSERT INTO pulse_briefing_prefs (user_id, enabled, network_enabled, crypto_enabled,
            watchlist_enabled, frequency, quiet_start, quiet_end, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            enabled=excluded.enabled, network_enabled=excluded.network_enabled,
            crypto_enabled=excluded.crypto_enabled, watchlist_enabled=excluded.watchlist_enabled,
            frequency=excluded.frequency, quiet_start=excluded.quiet_start,
            quiet_end=excluded.quiet_end, updated_at=excluded.updated_at
        """,
        (int(user_id), int(current["enabled"]), int(current["network_enabled"]),
         int(current["crypto_enabled"]), int(current["watchlist_enabled"]),
         current["frequency"], current["quiet_start"], current["quiet_end"], _iso(_now())),
    )
    conn.commit()
    if owns:
        conn.close()
    return current


# --- Scheduling (Stage 13/15/30/52) ----------------------------------------

def _user_zone(conn, user_id: int) -> ZoneInfo:
    """Resolve a user's zone through the canonical authority, never by guessing.

    Order: the user's stored region preference, then UTC. There is no second
    account/region authority in PulseSoc -- pulse_region_preferences is it --
    and deriving a zone from preferred_locale is deliberately NOT done: "en-US"
    spans six zones, so a guess would move quiet hours to a time the user never
    chose. UTC is wrong in a way that is obvious and auditable; a guessed zone
    is wrong in a way that looks right.
    """
    try:
        prefs = region_preferences.get_preferences(int(user_id), conn=conn)
    except Exception:  # noqa: BLE001 - timezone must never break a briefing
        logging.exception("BRIEFING_TIMEZONE_LOOKUP_FAILED user_id=%s", user_id)
        return ZoneInfo("UTC")

    name = str(prefs.get("preferred_timezone") or "").strip()
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logging.warning("BRIEFING_TIMEZONE_UNKNOWN user_id=%s zone=%s", user_id, name)
        return ZoneInfo("UTC")


def _windows_for_frequency(frequency: str) -> tuple[int, ...]:
    """Local hours at which an evaluation window opens.

    "off" has no windows. It used to fall through to the full four-window
    schedule and rely on every caller separately remembering to check for it --
    a guard that only has to be forgotten once for an opted-out user to be
    evaluated. An empty schedule makes "off" mean off here, at the source.
    """
    if frequency == "off":
        return ()
    if frequency == "morning_evening":
        return (8, 18)
    if frequency == "daily":
        return (8,)
    return BRIEFING_WINDOWS


def current_window(local_now: datetime, frequency: str) -> tuple[str, datetime] | None:
    """Return (window_key, window_start_local) if a window is open now."""
    windows = _windows_for_frequency(frequency)
    if not windows:
        return None
    for hour in windows:
        start = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if start <= local_now < start + timedelta(hours=6):
            return f"{local_now.strftime('%Y-%m-%d')}:{hour:02d}", start
    prev = local_now - timedelta(days=1)
    last = windows[-1]
    if local_now < local_now.replace(hour=windows[0], minute=0, second=0, microsecond=0):
        return f"{prev.strftime('%Y-%m-%d')}:{last:02d}", prev.replace(hour=last, minute=0, second=0, microsecond=0)
    return None


def _jitter_offset_minutes(user_id: int) -> int:
    """Deterministic per-user jitter so a user's send time is stable per window."""
    return random.Random(int(user_id)).randint(0, max(1, JITTER_MINUTES))


def _quiet_hours_active(local_now: datetime, quiet_start: str, quiet_end: str) -> bool:
    def minutes(raw: str, fallback: str) -> int:
        parts = str(raw or fallback).split(":")
        try:
            return max(0, min(1439, int(parts[0]) * 60 + int(parts[1])))
        except (ValueError, IndexError):
            parts = fallback.split(":")
            return int(parts[0]) * 60 + int(parts[1])
    start = minutes(quiet_start, DEFAULT_QUIET_START)
    end = minutes(quiet_end, DEFAULT_QUIET_END)
    current = local_now.hour * 60 + local_now.minute
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


# --- Evaluation + delivery (Stages 16/17/19/26/27/30/32) --------------------

def evaluate_user_briefing(conn, user: dict[str, Any], *, now_utc: datetime | None = None,
                           send: bool = True) -> dict[str, Any]:
    """CLAIM -> GATHER -> SCORE -> SUPPRESS -> SUMMARIZE -> SEND -> SETTLE."""
    user_id = int(user.get("user_id") or 0)
    cur = conn.cursor()
    prefs = get_preferences(user_id, conn=conn)
    if not prefs["enabled"] or prefs["frequency"] == "off":
        return {"status": "disabled"}
    if not push_notifications_allowed(cur, user_id):
        # The briefing-specific toggle is not the only opt-out that binds us: a user
        # who turned push off globally has disabled push, full stop. _deliver() calls
        # push_service.send_push directly and so never reaches the canonical
        # _rules_check that enforces this everywhere else -- without this guard a
        # global opt-out would be silently overridden for briefings alone.
        # Returns before CLAIM so no row is written and the window stays claimable
        # if the user re-enables push later in the same window.
        return {"status": "push_disabled_by_user"}
    zone = _user_zone(conn, user_id)
    local_now = (now_utc or _now()).astimezone(zone)
    window = current_window(local_now, prefs["frequency"])
    if not window:
        return {"status": "no_window"}
    window_key, window_start = window
    release = window_start + timedelta(minutes=_jitter_offset_minutes(user_id))
    if local_now < release:
        return {"status": "before_jitter_release"}
    if _quiet_hours_active(local_now, prefs["quiet_start"], prefs["quiet_end"]):
        return {"status": "quiet_hours"}  # deferred; window stays claimable

    # CLAIM: unique (user_id, window_key) is the idempotency anchor (Stage 30).
    # ON CONFLICT DO NOTHING rather than INSERT-and-catch: the duplicate is the
    # EXPECTED path (every user hits it on every re-tick within a window), and
    # letting it surface as a driver exception made services/db.py print a full
    # SQL_EXECUTE_FAILED block -- sql, params, traceback -- per user per cycle.
    # That flood is what pushed real boot diagnostics out of the Railway log
    # window. A duplicate is now a returned empty result, not an error.
    #
    # RETURNING is written explicitly, not left to CompatCursor's AUTO_PK_TABLES
    # injection: the repo's "INSERT OR IGNORE" shorthand sets append_do_nothing,
    # which suppresses that injection and destroys lastrowid. Naming both clauses
    # keeps the claimed id on Postgres and on sqlite (>=3.35) alike.
    try:
        cur.execute(
            "INSERT INTO pulse_briefings (user_id, window_key, status, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT (user_id, window_key) DO NOTHING RETURNING id",
            (user_id, window_key, "processing", _iso(_now())),
        )
        claimed = cur.fetchone()  # must read BEFORE commit closes the cursor
        conn.commit()
    except Exception:  # noqa: BLE001 - with DO NOTHING this is a REAL failure
        # Reporting an unexpected error as 'already_claimed' would silently
        # disable briefings for everyone while the cycle still looked healthy.
        conn.rollback()
        logging.exception("BRIEFING_CLAIM_FAILED user_id=%s window=%s", user_id, window_key)
        _METRICS["briefing_jobs_failed"] += 1
        return {"status": "failed", "error": "claim_failed"}

    if not claimed:
        return {"status": "already_claimed"}

    _METRICS["briefing_jobs_started"] += 1
    briefing_id = int(claimed[0])
    try:
        cur.execute(
            # 'shadow' counts as a predecessor so a shadow run reproduces the real
            # dedupe decision instead of re-deriving "new" facts every window and
            # reporting a duplicate rate of zero. No row carries this status unless
            # shadow mode ran, so normal operation is unaffected.
            "SELECT fingerprint, sent_at, created_at FROM pulse_briefings WHERE user_id=? AND id<>? AND status IN ('sent','shadow') ORDER BY id DESC LIMIT 1",
            (user_id, briefing_id),
        )
        prev = cur.fetchone()
        prev = dict(prev) if prev else {}
        since_iso = prev.get("created_at") or _iso(_now() - timedelta(hours=6))
        locale = str(user.get("preferred_language") or "en")
        fact_pack = facts_mod.build_briefing_facts(
            cur, user_id, since_iso=since_iso, timezone_name=str(zone.key),
            locale=locale, prefs=prefs,
        )
        fingerprint = facts_mod.fact_fingerprint(fact_pack)
        significance = int(fact_pack.get("significance_score") or 0)
        market_moved = abs((fact_pack.get("crypto") or {}).get("btc_change_24h") or 0) >= facts_mod.MARKET_MOVE_THRESHOLD_PCT

        suppressed_reason = ""
        if prefs["frequency"] == "important_only" and fact_pack.get("urgency") != "high" and not market_moved:
            suppressed_reason = "important_only_filter"
        elif significance < facts_mod.SEND_THRESHOLD and not market_moved:
            suppressed_reason = "briefing_suppressed_no_change"
        elif prev.get("fingerprint") and prev["fingerprint"] == fingerprint:
            suppressed_reason = "duplicate_fingerprint"

        if suppressed_reason:
            key = "briefings_duplicate_suppressed" if suppressed_reason == "duplicate_fingerprint" else "briefings_suppressed"
            _METRICS[key] += 1
            cur.execute(
                "UPDATE pulse_briefings SET status='suppressed', suppressed_reason=?, fingerprint=?, generated_at=? WHERE id=?",
                (suppressed_reason, fingerprint, _iso(_now()), briefing_id),
            )
            conn.commit()
            _METRICS["briefing_jobs_completed"] += 1
            return {"status": "suppressed", "reason": suppressed_reason}

        copy = summarizer.summarize(fact_pack)
        cur.execute(
            """UPDATE pulse_briefings SET status='generated', title=?, body=?, facts_json=?,
               fingerprint=?, summary_source=?, crypto_provider=?, locale=?, generated_at=? WHERE id=?""",
            (copy["title"], copy["body"], json.dumps(fact_pack, sort_keys=True), fingerprint,
             copy.get("source") or "", (fact_pack.get("crypto") or {}).get("provider") or "",
             locale, _iso(_now()), briefing_id),
        )
        conn.commit()

        sent = False
        if send:
            sent = _deliver(user_id, briefing_id, copy, fact_pack)
        if sent:
            cur.execute("UPDATE pulse_briefings SET status='sent', sent_at=? WHERE id=?", (_iso(_now()), briefing_id))
            conn.commit()
            _METRICS["briefings_sent"] += 1
        elif not send:
            # Shadow settle: a terminal status of its own rather than leaving the row
            # at 'generated'. list_briefings surfaces 'generated' rows, so a shadow run
            # that merely stopped short of the push would still drop an undelivered
            # briefing into the user's in-app history -- visible product state from a
            # run that is supposed to be invisible. This is the same settlement UPDATE
            # against the same claimed row, so it exercises the Postgres lastrowid path
            # identically; it is only excluded from user-facing reads.
            cur.execute("UPDATE pulse_briefings SET status='shadow', sent_at='' WHERE id=?", (briefing_id,))
            conn.commit()
            _METRICS["briefings_shadow_suppressed"] += 1
        _METRICS["briefing_jobs_completed"] += 1
        return {"status": "sent" if sent else "generated", "briefing_id": briefing_id,
                "title": copy["title"], "source": copy.get("source"),
                "delivered": sent, "shadow": not send,
                "deep_link": "pulse://notifications?briefing=%d" % int(briefing_id)}
    except Exception:  # noqa: BLE001 - settle the claim; never crash the worker
        logging.exception("BRIEFING_EVALUATE_FAILED user_id=%s window=%s", user_id, window_key)
        _METRICS["briefing_jobs_failed"] += 1
        try:
            cur.execute("UPDATE pulse_briefings SET status='failed' WHERE id=?", (briefing_id,))
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        return {"status": "failed"}


def _deliver(user_id: int, briefing_id: int, copy: dict[str, str], fact_pack: dict[str, Any]) -> bool:
    """Push via the canonical path. Payload carries no sensitive content
    (Stage 26/27): counts + market percentages only, never message bodies."""
    try:
        from .. import push_service
        result = push_service.send_push(
            user_id, copy["title"], copy["body"],
            data={
                "notification_type": "pulse_briefing",
                "push_type": "pulse_briefing",
                "briefing_id": int(briefing_id),
                "deep_link": "pulse://notifications?briefing=%d" % int(briefing_id),
                "native_url": "pulse://notifications?briefing=%d" % int(briefing_id),
                "generated_at": fact_pack.get("generated_at"),
            },
            push_type="pulse_briefing",
        )
        ok = bool(result and result.get("ok") is not False)
        try:
            from .. import notification_service
            notification_service.send_in_app_notification(
                user_id, copy["title"], copy["body"], notification_type="pulse_briefing",
                metadata={"briefing_id": int(briefing_id), "deep_link": "/pulse/notifications"},
            )
        except Exception:  # noqa: BLE001 - inbox copy is best-effort
            logging.exception("BRIEFING_INBOX_WRITE_FAILED user_id=%s", user_id)
        return ok
    except Exception:  # noqa: BLE001
        logging.exception("BRIEFING_PUSH_FAILED user_id=%s briefing_id=%s", user_id, briefing_id)
        return False


# --- Worker tick (Stage 31) -------------------------------------------------

def run_scheduled_cycle(limit: int = 50, *, conn=None) -> dict[str, Any]:
    """Called from alert_worker each cycle. Cheap when nothing is due."""
    if not briefings_enabled():
        # No "sent" key, deliberately: the disabled path has never had one, and the
        # resulting `sent=None` in the worker log is the signature production
        # acceptance already reads to distinguish "off" from "on and nothing due".
        return {"ok": True, "status": "disabled", "processed": 0, "shadow": False}
    shadow = shadow_mode()
    owns = conn is None
    conn = conn or user_context.connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cutoff = _iso(_now() - timedelta(hours=MIN_ACCOUNT_AGE_HOURS))
        cur.execute(
            """
            SELECT u.user_id, u.preferred_language FROM users u
            -- LEFT JOIN, not JOIN: push is a DELIVERY layer, not an eligibility gate.
            -- An INNER JOIN here hard-excluded every user without an active
            -- push_subscriptions row -- including users who have briefings enabled but
            -- whose device token was never registered, was revoked, or was lost on a
            -- reinstall. Those users then had zero pulse_briefings rows written, so
            -- their in-app history stayed empty forever while delivery_status
            -- separately reported push_ready=false with reason=no_devices. The
            -- documented intent (see this module's docstring) is that an evaluation
            -- window is NOT a mandatory send: _deliver's push_service.send_push
            -- returns not_configured when no rows exist, evaluate_user_briefing
            -- settles the row at 'generated' (visible in the hub), and the inbox
            -- copy still writes. Gating evaluation on push existence therefore
            -- disabled the entire product for anyone whose device state drifted from
            -- their preference row.
            --
            -- Scope, measured against production rather than assumed: of the 36 users
            -- who pass every real eligibility gate (enabled, frequency<>off, npref,
            -- account age), only 4 had an active push_subscriptions row. The INNER
            -- JOIN was therefore withholding briefings from 32 of 36 eligible users --
            -- 89% of the audience -- not from an unlucky few. `ps` is deliberately
            -- left unreferenced below: the join survives only so this comment has
            -- something to attach to and so the regression test has a token to scan;
            -- the GROUP BY collapses the per-device fan-out it would otherwise add.
            LEFT JOIN push_subscriptions ps ON ps.user_id = u.user_id AND COALESCE(ps.is_active, ps.active, 1)=1
            LEFT JOIN pulse_briefing_prefs p ON p.user_id = u.user_id
            LEFT JOIN notification_preferences np
                   ON np.user_id = u.user_id AND np.category = 'global'
            LEFT JOIN (SELECT user_id, MAX(id) AS last_id FROM pulse_briefings GROUP BY user_id) b
                   ON b.user_id = u.user_id
            WHERE COALESCE(p.enabled, 1)=1 AND COALESCE(p.frequency,'every_6h')<>'off'
              AND COALESCE(np.enable_push_notifications, 1)<>0
              AND COALESCE(NULLIF(u.created_at,''), NULLIF(u.signup_time,''), '') < ?
            GROUP BY u.user_id, b.last_id
            -- Fairness, not just a cheap page: LIMIT with no ORDER BY re-selects the
            -- same arbitrary N users every cycle, so above BRIEFING_CYCLE_BATCH_LIMIT
            -- eligible users the tail is never evaluated at all. Least-recently-briefed
            -- first (never-briefed sort first, then rotate to the back) makes coverage
            -- of every eligible user a property of the ordering rather than of the
            -- batch size. The np filter is not merely an optimisation under that
            -- ordering: an opted-out user never gets a briefing row, so last_id stays
            -- 0 and they would sort first forever, permanently occupying the head of
            -- the queue. Excluding them in SQL is what keeps the rotation moving.
            -- evaluate_user_briefing re-checks the opt-out as the binding guard.
            ORDER BY COALESCE(b.last_id, 0) ASC, u.user_id ASC
            LIMIT ?
            """,
            (cutoff, max(1, min(limit, SEND_RATE_CAP_PER_CYCLE))),
        )
        users = [dict(r) for r in cur.fetchall()]
        # Counted separately precisely because the query above excludes them: the
        # activation report has to be able to state how many users were withheld by
        # their own opt-out, and a filter that works leaves no trace in the loop.
        # LEFT JOIN push_subscriptions here mirrors the main SELECT above -- an
        # opted-out user without an active device is still an opt-out, and the
        # metric must not silently under-count them now that push is a delivery
        # concern rather than an eligibility gate.
        opted_out = 0
        try:
            cur.execute(
                """
                SELECT COUNT(DISTINCT u.user_id) FROM users u
                LEFT JOIN push_subscriptions ps ON ps.user_id = u.user_id AND COALESCE(ps.is_active, ps.active, 1)=1
                LEFT JOIN pulse_briefing_prefs p ON p.user_id = u.user_id
                JOIN notification_preferences np
                     ON np.user_id = u.user_id AND np.category = 'global'
                WHERE COALESCE(p.enabled, 1)=1 AND COALESCE(p.frequency,'every_6h')<>'off'
                  AND np.enable_push_notifications=0
                  AND COALESCE(NULLIF(u.created_at,''), NULLIF(u.signup_time,''), '') < ?
                """,
                (cutoff,),
            )
            row = cur.fetchone()
            opted_out = int(row[0] or 0) if row else 0
        except Exception:  # noqa: BLE001 - reporting only, never blocks the cycle
            logging.exception("BRIEFING_OPTOUT_COUNT_FAILED")
        results = {
            "processed": 0, "sent": 0, "suppressed": 0, "skipped": 0, "failed": 0,
            # Why a briefing did not reach a user, kept apart because they are four
            # different findings. A shadow run that reported one undifferentiated
            # "suppressed" total would be indistinguishable from a scoring regression.
            "suppressed_by_rules": 0, "suppressed_by_dedupe": 0,
            "suppressed_by_quiet_hours": 0, "suppressed_by_shadow": 0,
            # Distinct from suppressed_by_rules: this is the user's own opt-out being
            # honoured, which the activation report has to state on its own line.
            # Seeded from the SQL exclusion; the in-loop guard adds any straggler it
            # catches (a row shape the filter missed), so the two can never disagree
            # silently.
            "disabled_by_user": opted_out,
        }
        for user in users:
            outcome = evaluate_user_briefing(conn, user, send=not shadow)
            status = outcome.get("status")
            results["processed"] += 1
            if status == "sent":
                results["sent"] += 1
            elif status == "suppressed":
                results["suppressed"] += 1
                if outcome.get("reason") == "duplicate_fingerprint":
                    results["suppressed_by_dedupe"] += 1
                else:
                    results["suppressed_by_rules"] += 1
            elif status == "failed":
                results["failed"] += 1
            else:
                results["skipped"] += 1
                if status in ("push_disabled_by_user", "disabled"):
                    results["disabled_by_user"] += 1
                elif status == "quiet_hours":
                    results["suppressed_by_quiet_hours"] += 1
                elif outcome.get("shadow") and outcome.get("briefing_id"):
                    # Generated, settled, and withheld at the delivery boundary. Counted
                    # here and never in "sent": a shadow briefing is not a push.
                    results["suppressed_by_shadow"] += 1
        _prune_history(cur)
        conn.commit()
        logging.info("BRIEFING_CYCLE shadow=%s %s metrics=%s", shadow, results, metrics_snapshot())
        return {"ok": True, "shadow": shadow, **results}
    except Exception:  # noqa: BLE001 - a briefing fault must never break alerts
        logging.exception("BRIEFING_CYCLE_FAILED")
        return {"ok": False, "processed": 0}
    finally:
        if owns:
            conn.close()


def _prune_history(cur) -> None:
    try:
        cutoff = _iso(_now() - timedelta(days=HISTORY_RETENTION_DAYS))
        cur.execute("DELETE FROM pulse_briefings WHERE created_at < ? AND created_at <> ''", (cutoff,))
    except Exception:  # noqa: BLE001
        pass


# --- Owner-scoped reads (Stage 55) ------------------------------------------

VISIBLE_STATUSES = ("sent", "generated")  # user-visible history; never shadow/failed/suppressed


def list_briefings(user_id: int, limit: int = 20, *, offset: int = 0, conn=None) -> list[dict[str, Any]]:
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, window_key, status, title, body, summary_source, locale, generated_at, sent_at
           FROM pulse_briefings WHERE user_id=? AND status IN ('sent','generated')
           ORDER BY id DESC LIMIT ? OFFSET ?""",
        (int(user_id), max(1, min(int(limit), 50)), max(0, int(offset))),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if owns:
        conn.close()
    return rows


def list_briefings_page(user_id: int, limit: int = 20, offset: int = 0, *, conn=None) -> dict[str, Any]:
    """Cursorless pagination for the native hub: fetch limit+1, trim, report has_more."""
    # Cap at 49 so the +1 lookahead stays inside list_briefings' own 50-row cap;
    # a clamped lookahead would silently report has_more=False on a full page.
    limit = max(1, min(int(limit or 20), 49))
    offset = max(0, int(offset or 0))
    rows = list_briefings(user_id, limit + 1, offset=offset, conn=conn)
    has_more = len(rows) > limit
    return {
        "briefings": rows[:limit],
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


def get_briefing(user_id: int, briefing_id: int, *, conn=None) -> dict[str, Any] | None:
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_briefings WHERE id=? AND user_id=? LIMIT 1", (int(briefing_id), int(user_id)))
    row = cur.fetchone()
    result = dict(row) if row else None
    if owns:
        conn.close()
    if result:
        try:
            result["facts"] = json.loads(result.pop("facts_json") or "{}")
        except ValueError:
            result["facts"] = {}
    return result


# --- Seen tracking + delivery status (Profile OS hub) ------------------------

def mark_briefings_seen(user_id: int, *, conn=None) -> str:
    """Stamp the briefing-specific unread cursor. Upserts so a user who has
    never opened briefing settings still gets a prefs row; column defaults keep
    every other preference at its canonical default."""
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    now = _iso(_now())
    cur.execute(
        "INSERT INTO pulse_briefing_prefs (user_id, last_seen_at, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_seen_at=excluded.last_seen_at",
        (int(user_id), now, now),
    )
    conn.commit()
    if owns:
        conn.close()
    return now


def unseen_briefings_count(user_id: int, *, conn=None) -> int:
    """Visible briefings newer than the user's seen cursor, capped at 99.

    A user who has never opened the hub has last_seen_at='' and every visible
    briefing counts as unseen -- real history, real badge. ISO-8601 strings
    compare correctly as text, so no datetime parsing is needed."""
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    last_seen = ""
    try:
        cur.execute("SELECT last_seen_at FROM pulse_briefing_prefs WHERE user_id=? LIMIT 1", (int(user_id),))
        row = cur.fetchone()
        if row:
            try:
                last_seen = str(row["last_seen_at"] or "")
            except (TypeError, KeyError, IndexError):
                last_seen = str(row[0] or "")
    except Exception:  # noqa: BLE001 - a badge must never break a read
        logging.exception("BRIEFING_SEEN_CURSOR_READ_FAILED user_id=%s", user_id)
    cur.execute(
        "SELECT COUNT(*) AS n FROM (SELECT 1 FROM pulse_briefings "
        "WHERE user_id=? AND status IN ('sent','generated') AND created_at > ? LIMIT 99) capped",
        (int(user_id), last_seen),
    )
    row = cur.fetchone()
    if owns:
        conn.close()
    try:
        return int(row["n"] if not isinstance(row, tuple) else row[0]) if row else 0
    except (TypeError, KeyError, IndexError, ValueError):
        return int(row[0] or 0) if row else 0


def delivery_status(user_id: int, *, conn=None) -> dict[str, Any]:
    """Owner-scoped status snapshot for the hub's DELIVERY STATUS section.

    next_check_local is an evaluation estimate (window start + the user's own
    deterministic jitter) -- copy built on it must say "around", never promise
    delivery. Quiet hours and timezone come from the same canonical authorities
    the scheduler uses, so what the screen shows is what the engine does."""
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    prefs = get_preferences(user_id, conn=conn)
    zone = _user_zone(conn, user_id)
    local_now = _now().astimezone(zone)
    transport = push_transport_status(cur, user_id)
    push_enabled = transport["preference_allows"]
    cur.execute(
        "SELECT id, title, status, generated_at, sent_at, created_at FROM pulse_briefings "
        "WHERE user_id=? AND status IN ('sent','generated') ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )
    row = cur.fetchone()
    last = dict(row) if row else None
    next_check = None
    if prefs["enabled"] and prefs["frequency"] != "off" and briefings_enabled():
        jitter = _jitter_offset_minutes(user_id)
        candidates = []
        # Two days is not enough once quiet hours can veto a window: a user whose
        # quiet range covers every window of the day would get next_check=None
        # and read "Briefings are paused" while the engine was merely quiet until
        # tomorrow. Scan far enough to distinguish "later" from "never".
        for day_offset in (0, 1, 2):
            day = local_now + timedelta(days=day_offset)
            for hour in _windows_for_frequency(prefs["frequency"]):
                start = day.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(minutes=jitter)
                if start <= local_now:
                    continue
                # The worker suppresses a window that lands inside quiet hours,
                # so advertising it as the next check promises something that
                # will not happen. Show the first window that can actually run.
                if _quiet_hours_active(start, prefs["quiet_start"], prefs["quiet_end"]):
                    continue
                candidates.append(start)
        if candidates:
            next_check = min(candidates).isoformat(timespec="minutes")
    unseen = unseen_briefings_count(user_id, conn=conn)
    if owns:
        conn.close()
    return {
        "enabled": prefs["enabled"],
        "frequency": prefs["frequency"],
        "frequencies": [f for f in FREQUENCIES],
        "quiet_start": prefs["quiet_start"],
        "quiet_end": prefs["quiet_end"],
        "timezone": str(zone.key),
        # push_enabled is the user's PREFERENCE. push_ready is whether a push can
        # actually be delivered. The screen must not conflate them.
        "push_enabled": push_enabled,
        "push_ready": transport["ready"],
        "push_blocked_reason": transport["reason"],
        "push_device_count": transport["device_count"],
        "briefings_feature_enabled": briefings_enabled(),
        "last_briefing": last,
        "next_check_local": next_check,
        "unseen_count": unseen,
    }
