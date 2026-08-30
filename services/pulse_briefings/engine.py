"""Pulse Briefing engine: server-side scheduling, suppression, delivery, history.

Runs as a tick inside alert_worker (no new Railway service). Every ~6h window
per user is an EVALUATION, not a mandatory send: insignificant or duplicate
facts are suppressed and recorded. Idempotency key = user_id + local date +
window, enforced by a UNIQUE index so worker restarts can never double-send.

Kill switch: BRIEFINGS_DISABLED=true stops scheduled sends only; normal
PulseSoc notifications are unaffected.
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import crypto_provider, facts as facts_mod, summarizer
from .. import user_context

BRIEFING_WINDOWS = (0, 6, 12, 18)  # local-time window starts (hours)
JITTER_MINUTES = int(os.getenv("BRIEFING_JITTER_MINUTES", "25"))
DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "07:00"
MIN_ACCOUNT_AGE_HOURS = int(os.getenv("BRIEFING_MIN_ACCOUNT_AGE_HOURS", "24"))  # Stage 53
HISTORY_RETENTION_DAYS = int(os.getenv("BRIEFING_HISTORY_RETENTION_DAYS", "60"))
SEND_RATE_CAP_PER_CYCLE = int(os.getenv("BRIEFING_SEND_RATE_CAP", "200"))
FREQUENCIES = ("off", "important_only", "every_6h", "morning_evening")

_METRICS = {
    "briefing_jobs_started": 0, "briefing_jobs_completed": 0, "briefing_jobs_failed": 0,
    "briefings_sent": 0, "briefings_suppressed": 0, "briefings_duplicate_suppressed": 0,
}


def metrics_snapshot() -> dict[str, int]:
    merged = dict(_METRICS)
    merged.update(crypto_provider.metrics_snapshot())
    return merged


def briefings_enabled() -> bool:
    if str(os.getenv("BRIEFINGS_DISABLED", "")).strip().lower() in ("1", "true", "yes", "on"):
        return False
    return str(os.getenv("PULSE_BRIEFINGS_ENABLED", "true")).strip().lower() in ("1", "true", "yes", "on")


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
            updated_at TEXT DEFAULT ''
        )
        """
    )
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


def update_preferences(user_id: int, values: dict[str, Any], *, conn=None) -> dict[str, Any]:
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    current = get_preferences(user_id, conn=conn)
    for key in ("enabled", "network_enabled", "crypto_enabled", "watchlist_enabled"):
        if key in values:
            current[key] = bool(values[key])
    if "frequency" in values and str(values["frequency"]) in FREQUENCIES:
        current["frequency"] = str(values["frequency"])
    for key in ("quiet_start", "quiet_end"):
        raw = str(values.get(key) or "")
        if raw and len(raw.split(":")) == 2:
            try:
                h, m = int(raw.split(":")[0]), int(raw.split(":")[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    current[key] = f"{h:02d}:{m:02d}"
            except ValueError:
                pass
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

def _user_zone(cur, user_id: int) -> ZoneInfo:
    try:
        cur.execute("SELECT preferred_timezone FROM pulse_region_preferences WHERE user_id=? LIMIT 1", (int(user_id),))
        row = cur.fetchone()
        name = (dict(row) if row else {}).get("preferred_timezone") or ""
        if name:
            return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001
        pass
    return ZoneInfo("UTC")


def _windows_for_frequency(frequency: str) -> tuple[int, ...]:
    if frequency == "morning_evening":
        return (6, 18)
    return BRIEFING_WINDOWS


def current_window(local_now: datetime, frequency: str) -> tuple[str, datetime] | None:
    """Return (window_key, window_start_local) if a window is open now."""
    for hour in _windows_for_frequency(frequency):
        start = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if start <= local_now < start + timedelta(hours=6):
            return f"{local_now.strftime('%Y-%m-%d')}:{hour:02d}", start
    prev = local_now - timedelta(days=1)
    last = _windows_for_frequency(frequency)[-1]
    if local_now < local_now.replace(hour=_windows_for_frequency(frequency)[0], minute=0, second=0, microsecond=0):
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
    zone = _user_zone(cur, user_id)
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
    try:
        cur.execute(
            "INSERT INTO pulse_briefings (user_id, window_key, status, created_at) VALUES (?,?,?,?)",
            (user_id, window_key, "processing", _iso(_now())),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - unique violation: window already handled
        conn.rollback()
        return {"status": "already_claimed"}

    _METRICS["briefing_jobs_started"] += 1
    briefing_id = cur.lastrowid
    try:
        cur.execute(
            "SELECT fingerprint, sent_at, created_at FROM pulse_briefings WHERE user_id=? AND id<>? AND status='sent' ORDER BY id DESC LIMIT 1",
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
        _METRICS["briefing_jobs_completed"] += 1
        return {"status": "sent" if sent else "generated", "briefing_id": briefing_id,
                "title": copy["title"], "source": copy.get("source")}
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
        return {"ok": True, "status": "disabled", "processed": 0}
    owns = conn is None
    conn = conn or user_context.connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cutoff = _iso(_now() - timedelta(hours=MIN_ACCOUNT_AGE_HOURS))
        cur.execute(
            """
            SELECT u.user_id, u.preferred_language FROM users u
            JOIN push_subscriptions ps ON ps.user_id = u.user_id AND COALESCE(ps.is_active, ps.active, 1)=1
            LEFT JOIN pulse_briefing_prefs p ON p.user_id = u.user_id
            WHERE COALESCE(p.enabled, 1)=1 AND COALESCE(p.frequency,'every_6h')<>'off'
              AND COALESCE(NULLIF(u.created_at,''), NULLIF(u.signup_time,''), '') < ?
            GROUP BY u.user_id LIMIT ?
            """,
            (cutoff, max(1, min(limit, SEND_RATE_CAP_PER_CYCLE))),
        )
        users = [dict(r) for r in cur.fetchall()]
        results = {"processed": 0, "sent": 0, "suppressed": 0, "skipped": 0, "failed": 0}
        for user in users:
            outcome = evaluate_user_briefing(conn, user)
            status = outcome.get("status")
            results["processed"] += 1
            if status == "sent":
                results["sent"] += 1
            elif status == "suppressed":
                results["suppressed"] += 1
            elif status == "failed":
                results["failed"] += 1
            else:
                results["skipped"] += 1
        _prune_history(cur)
        conn.commit()
        logging.info("BRIEFING_CYCLE %s metrics=%s", results, metrics_snapshot())
        return {"ok": True, **results}
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

def list_briefings(user_id: int, limit: int = 20, *, conn=None) -> list[dict[str, Any]]:
    owns = conn is None
    conn = conn or user_context.connect()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, window_key, status, title, body, summary_source, locale, generated_at, sent_at
           FROM pulse_briefings WHERE user_id=? AND status IN ('sent','generated')
           ORDER BY id DESC LIMIT ?""",
        (int(user_id), max(1, min(int(limit), 50))),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if owns:
        conn.close()
    return rows


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
