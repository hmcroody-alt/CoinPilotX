"""Watch PostgreSQL for the lock convoy that took the site down on 2026-09-19.

Two signals, both of which were available and unwatched for the whole of that
outage:

* ``pg_stat_database.deadlocks`` — a *counter*, so what matters is its rate. It
  stood at 2041 during the incident and had moved 69 times in fifteen minutes.
  A raw threshold on the total would have fired forever after; a rate fires
  while it is happening and then stops.
* ``pg_stat_activity`` rows waiting on ``wait_event_type='Lock'`` — the shape of
  a convoy. During the incident every one of the 32 request slots was a waiter
  with no single holder to blame.

Three design constraints come directly from how that outage actually behaved:

**The probe may not use the shared pool.** A convoy exhausts exactly the thing a
pooled monitor would need to borrow, so this opens its own short-lived
connection. A monitor that blocks when the database is blocked reports nothing
at the only moment it matters.

**The probe may not wait.** ``statement_timeout`` is set low and
``connect_timeout`` with it. A sample that cannot be taken quickly is itself the
finding, and is reported as one rather than hanging the worker's cycle.

**The alert may not need the database.** Delivery is a log line plus
``email_service.send_email``, which goes straight to Brevo over HTTP and touches
no table. Anything that wrote to Postgres to announce that Postgres is stuck
would be the first thing to stall.

The two views read here are in-memory and take no table locks, so sampling
cannot itself join the convoy it is watching.
"""

from __future__ import annotations

import logging
import os
import time

from services import db

#: Module-level, so the rate is measured between consecutive cycles of the one
#: worker process that calls this. A restart loses the baseline, which only
#: costs the first cycle: `evaluate` reports no rate without a previous sample
#: rather than inventing one from a cumulative counter.
_PREVIOUS = {"deadlocks": None, "at": None}

#: Last time each alert kind was escalated, so a convoy that lasts twenty
#: minutes sends one email rather than twenty-seven.
_LAST_ALERT_AT = {}

ALERT_TOKEN = "PG_LOCK_HEALTH_ALERT"
OK_TOKEN = "PG_LOCK_HEALTH_OK"


def _env_int(name, default):
    """Read a tunable at call time, not import time, and never raise.

    Call-time keeps the module testable without reimporting and means a bad
    value degrades to the default instead of stopping the worker at boot.
    """
    try:
        return int(str(os.getenv(name, default)).strip())
    except Exception:
        return int(default)


def _enabled():
    return str(os.getenv("PG_LOCK_ALERT_ENABLED", "1")).strip().lower() not in ("0", "false", "no", "off")


def thresholds():
    return {
        "deadlocks_per_min": _env_int("PG_DEADLOCKS_PER_MIN_THRESHOLD", "3"),
        "lock_waiters": _env_int("PG_LOCK_WAITERS_THRESHOLD", "5"),
        "lock_wait_seconds": _env_int("PG_LOCK_WAIT_SECONDS_THRESHOLD", "30"),
        "cooldown_seconds": _env_int("PG_LOCK_ALERT_COOLDOWN_SECONDS", "900"),
        "statement_timeout_ms": _env_int("PG_LOCK_SAMPLE_TIMEOUT_MS", "3000"),
    }


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------

def read_signals(cur):
    """Read both signals through an already-open cursor.

    Split out from connection handling so the queries can be exercised against a
    real PostgreSQL cursor in isolation, and so `run_once` stays readable.
    """
    cur.execute(
        "SELECT deadlocks, xact_commit, xact_rollback, numbackends "
        "FROM pg_stat_database WHERE datname = current_database()"
    )
    row = cur.fetchone() or (0, 0, 0, 0)
    deadlocks, commits, rollbacks, backends = row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0

    # `state <> 'idle'` keeps out sessions parked between transactions; they are
    # not waiting on anything. max wait is what separates a convoy from the
    # ordinary momentary contention every busy database shows.
    #
    # The wait clock comes from `pg_locks.waitstart`, which is when this backend
    # started waiting for *this* lock. `pg_stat_activity` has no such column —
    # its `query_start` is when the statement began, which for a query that did
    # real work before blocking overstates the wait. Tried precisely first and
    # degraded rather than assumed, because `waitstart` only exists from
    # PostgreSQL 14 and a monitor that hard-fails on an older server would
    # alert forever about its own query instead of about the database.
    try:
        cur.execute(
            "SELECT count(*), "
            "       COALESCE(MAX(EXTRACT(EPOCH FROM (now() - COALESCE(l.waitstart, a.query_start, now())))), 0) "
            "FROM pg_stat_activity a "
            "LEFT JOIN LATERAL ("
            "    SELECT MIN(waitstart) AS waitstart FROM pg_locks WHERE pid = a.pid AND NOT granted"
            ") l ON true "
            "WHERE a.wait_event_type = 'Lock' AND a.state <> 'idle'"
        )
        waiter_row = cur.fetchone() or (0, 0)
    except Exception:
        logging.debug("pg_lock_health: waitstart unavailable, using query_start", exc_info=True)
        cur.execute(
            "SELECT count(*), "
            "       COALESCE(MAX(EXTRACT(EPOCH FROM (now() - COALESCE(query_start, now())))), 0) "
            "FROM pg_stat_activity "
            "WHERE wait_event_type = 'Lock' AND state <> 'idle'"
        )
        waiter_row = cur.fetchone() or (0, 0)
    waiters, longest_wait = int(waiter_row[0] or 0), float(waiter_row[1] or 0.0)

    relations = []
    if waiters:
        # Only when something is actually waiting: this is the question that took
        # the longest to answer by hand during the incident, so the alert should
        # carry the answer rather than the reader going to find it.
        try:
            cur.execute(
                "SELECT COALESCE(c.relname, 'unknown') AS rel, count(*) AS n "
                "FROM pg_locks l "
                "LEFT JOIN pg_class c ON c.oid = l.relation "
                "WHERE NOT l.granted "
                "GROUP BY 1 ORDER BY n DESC LIMIT 5"
            )
            relations = [(r[0], int(r[1])) for r in (cur.fetchall() or [])]
        except Exception:
            # Best-effort colour on the alert. Losing it must not lose the alert.
            logging.debug("pg_lock_health: contended-relation lookup failed", exc_info=True)

    return {
        "deadlocks": int(deadlocks),
        "commits": int(commits),
        "rollbacks": int(rollbacks),
        "backends": int(backends),
        "lock_waiters": waiters,
        "longest_lock_wait_seconds": round(longest_wait, 1),
        "contended_relations": relations,
    }


def _sample():
    """Open a dedicated connection, read both signals, close it. Never raises."""
    if not db.IS_POSTGRES:
        return {"ok": False, "supported": False, "reason": "not_postgres"}

    url = db._raw_database_url()
    if not url:
        return {"ok": False, "supported": False, "reason": "no_database_url"}

    limits = thresholds()
    conn = None
    try:
        import psycopg2
    except Exception:
        return {"ok": False, "supported": False, "reason": "psycopg2_unavailable"}

    started = time.time()
    try:
        conn = psycopg2.connect(url, connect_timeout=max(1, limits["statement_timeout_ms"] // 1000))
        # Read-only and autocommit: this must never hold a transaction open, or
        # the monitor would itself pin an xmin horizon on a struggling database.
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        cur.execute("SET statement_timeout = %s", (limits["statement_timeout_ms"],))
        signals = read_signals(cur)
        signals["ok"] = True
        signals["supported"] = True
        signals["sample_ms"] = int((time.time() - started) * 1000)
        return signals
    except Exception as exc:
        # A sample that times out is a finding, not a silent no-op: during a
        # convoy this is a plausible outcome and the operator should hear it.
        return {
            "ok": False,
            "supported": True,
            "reason": "sample_failed",
            "error": str(exc)[:200],
            "sample_ms": int((time.time() - started) * 1000),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Evaluation (pure — this is the part worth testing)
# --------------------------------------------------------------------------

def evaluate(signals, previous, now, limits=None):
    """Turn one sample plus the previous one into a verdict. No I/O.

    Kept free of database and clock access so the interesting behaviour — rate
    maths, counter resets, the first-sample case — can be tested directly
    instead of through a PostgreSQL fixture that the SQLite test suite could
    not provide anyway.
    """
    limits = limits or thresholds()
    verdict = {
        "alert": False,
        "reasons": [],
        "deadlocks_per_min": None,
        "lock_waiters": signals.get("lock_waiters", 0),
        "longest_lock_wait_seconds": signals.get("longest_lock_wait_seconds", 0),
        "contended_relations": signals.get("contended_relations", []),
    }

    if not signals.get("ok"):
        if signals.get("supported"):
            verdict["alert"] = True
            verdict["reasons"].append(
                "could not sample database health (%s)" % (signals.get("error") or signals.get("reason"))
            )
        return verdict

    prev_count, prev_at = previous.get("deadlocks"), previous.get("at")
    if prev_count is not None and prev_at is not None and now > prev_at:
        delta = signals["deadlocks"] - prev_count
        if delta < 0:
            # Statistics were reset between samples. A negative rate is
            # meaningless, so treat it as a fresh baseline rather than reporting
            # a spurious calm.
            delta = 0
        elapsed_min = (now - prev_at) / 60.0
        if elapsed_min > 0:
            rate = delta / elapsed_min
            verdict["deadlocks_per_min"] = round(rate, 2)
            if rate >= limits["deadlocks_per_min"]:
                verdict["alert"] = True
                verdict["reasons"].append(
                    "deadlocks %.2f/min (threshold %s/min, +%d since last check)"
                    % (rate, limits["deadlocks_per_min"], delta)
                )

    waiters = signals.get("lock_waiters", 0)
    longest = signals.get("longest_lock_wait_seconds", 0)
    if waiters >= limits["lock_waiters"]:
        verdict["alert"] = True
        verdict["reasons"].append(
            "%d sessions waiting on locks (threshold %s), longest %.1fs"
            % (waiters, limits["lock_waiters"], longest)
        )
    elif waiters and longest >= limits["lock_wait_seconds"]:
        # One session stuck for a long time is a different failure from many
        # stuck briefly, and the count threshold alone would never see it.
        verdict["alert"] = True
        verdict["reasons"].append(
            "a session has waited %.1fs on a lock (threshold %ss)"
            % (longest, limits["lock_wait_seconds"])
        )

    return verdict


def should_escalate(kind, now, limits=None, last_alert_at=None):
    """True at most once per cooldown per alert kind."""
    limits = limits or thresholds()
    store = _LAST_ALERT_AT if last_alert_at is None else last_alert_at
    previous = store.get(kind)
    if previous is not None and (now - previous) < limits["cooldown_seconds"]:
        return False
    store[kind] = now
    return True


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------

def _escalate(verdict, signals):
    # Falls back to the address that already receives owner-level admin mail, so
    # a deployment that sets nothing new still reaches a human. With neither set
    # the monitor runs and logs as usual; only delivery is off.
    recipient = str(os.getenv("PG_LOCK_ALERT_EMAIL", "") or os.getenv("OWNER_ADMIN_EMAIL", "")).strip()
    if not recipient:
        return {"sent": False, "reason": "no_recipient"}
    summary = "; ".join(verdict["reasons"])
    relations = ", ".join("%s(%d)" % (name, count) for name, count in verdict["contended_relations"][:5])
    body = (
        "PulseSoc database lock health alert\n\n"
        "%s\n\n"
        "deadlocks/min: %s\n"
        "sessions waiting on locks: %s\n"
        "longest lock wait: %ss\n"
        "most contended relations: %s\n"
        "backends: %s\n"
    ) % (
        summary,
        verdict["deadlocks_per_min"],
        verdict["lock_waiters"],
        verdict["longest_lock_wait_seconds"],
        relations or "n/a",
        signals.get("backends"),
    )
    try:
        from services import email_service

        email_service.send_email(
            recipient,
            "PulseSoc: database lock contention",
            "<pre>%s</pre>" % body,
            body,
            email_type="ops_alert",
        )
        return {"sent": True}
    except Exception as exc:
        # The log line above already carried the whole alert, so a failed email
        # downgrades the alert's reach without losing it.
        logging.exception("pg_lock_health: alert email failed")
        return {"sent": False, "reason": str(exc)[:200]}


def run_once(now=None):
    """Sample, evaluate, alert. Never raises — a monitor must not break its host."""
    if not _enabled():
        return {"ok": True, "skipped": "disabled"}

    now = time.time() if now is None else now
    try:
        signals = _sample()
    except Exception as exc:  # pragma: no cover - _sample already guards
        logging.exception("pg_lock_health: sampling raised")
        return {"ok": False, "reason": str(exc)[:200]}

    if not signals.get("supported"):
        return {"ok": True, "skipped": signals.get("reason")}

    verdict = evaluate(signals, _PREVIOUS, now)

    if signals.get("ok"):
        _PREVIOUS["deadlocks"] = signals["deadlocks"]
        _PREVIOUS["at"] = now

    if not verdict["alert"]:
        logging.info(
            "%s deadlocks_per_min=%s waiters=%s longest_wait_s=%s backends=%s sample_ms=%s",
            OK_TOKEN,
            verdict["deadlocks_per_min"],
            verdict["lock_waiters"],
            verdict["longest_lock_wait_seconds"],
            signals.get("backends"),
            signals.get("sample_ms"),
        )
        return {"ok": True, "alert": False, "verdict": verdict}

    # Logged every cycle the condition holds, and deliberately not
    # cooldown-suppressed: the log is the forensic record, and during the
    # incident its absence is what made the timeline hard to rebuild.
    logging.error(
        "%s %s | deadlocks_per_min=%s waiters=%s longest_wait_s=%s contended=%s backends=%s",
        ALERT_TOKEN,
        "; ".join(verdict["reasons"]),
        verdict["deadlocks_per_min"],
        verdict["lock_waiters"],
        verdict["longest_lock_wait_seconds"],
        verdict["contended_relations"],
        signals.get("backends"),
    )

    delivery = {"sent": False, "reason": "cooldown"}
    if should_escalate("lock_health", now):
        delivery = _escalate(verdict, signals)
    return {"ok": True, "alert": True, "verdict": verdict, "delivery": delivery}
