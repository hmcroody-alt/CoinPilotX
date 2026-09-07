"""Cross-process rate counters on PostgreSQL (Stage 6).

Why this exists
---------------

``services/security_guard.py`` keeps its state in ``BUCKETS = defaultdict(list)``
and ``services/pulse_security_core.py`` in ``_RATE_BUCKETS`` — both module-level
dictionaries, i.e. **process memory**. The Procfile runs::

    web: gunicorn bot:app --workers ${WEB_CONCURRENCY:-4} --threads ${WEB_THREADS:-8}

Four independent OS processes, each with its own copy. So a rule that reads
"5 attempts per 15 minutes" actually permits *up to 20*, and which limit an
attacker hits depends on which worker the load balancer happened to pick. The
limits are not wrong by a little; they are wrong by the worker count, and
nothing in the codebase says so.

``pulse_security_core`` looks like it solves this — it consults
``services/cache_engine.py`` — but ``cache_get()`` falls back to a module-level
``_MEMORY`` dict when ``redis_client()`` is ``None``, and **there is no Redis in
this project**: the live Railway environment has 228 variables and no
``REDIS_URL``, and ``railway list`` shows only ``Postgres``. So that path is
process memory wearing a cache API — the most dangerous shape a fake control can
take, because the seam for distribution exists and looks used.

Two limiters are honest exceptions and they are the important ones: member login
(``bot.login_security_preflight``) and admin login
(``admin_gateway.login_rate_limited``) are both DB-backed and therefore genuinely
cross-process. The latter is the in-repo precedent this module follows rather
than replaces (Hard Rule #6): same store, same degradation philosophy, extended
to the other security-critical endpoints instead of a second parallel mechanism.

What this is not
----------------

This does **not** replace ``security_guard`` or ``pulse_security_core``. Those
guard high-volume product endpoints where a database round trip per request
would cost more than the limit is worth, and they keep working exactly as
before. Their per-worker multiplication remains real and remains documented.
This module is for the narrow set of low-volume, security-critical actions where
a shared count is worth a connection: the paths ``bot.basic_abuse_guard`` already
names — login, signup, password and username recovery, admin login, checkout,
the AI assistant. That guard supplies the limits and keeps its own in-process
bucket; this module supplies the count all four workers share.

Three design decisions worth stating outright
---------------------------------------------

**1. The counter always commits on its own connection.** ``check()`` will not
increment on a connection handed to it by a caller. A counter row is by
definition hot — every worker rate-limiting the same subject contends for the
same row — so an uncommitted increment inside a long request holds a row lock
that blocks every other worker's upsert until that request finishes. A limiter
that serialises the workers it is meant to protect would be worse than the
per-worker counters it replaces. One short transaction, committed immediately.

**2. The process-local counter is consulted first, and it is not a lie.** Every
check increments an in-process window too. Because a worker's own count can
never exceed the global count, a local count already over the limit *proves* the
global one is, and the decision can be made without touching the database. This
matters most exactly when it is needed most: under a flood, each worker stops
querying as soon as it has locally seen enough. The database is consulted in the
case that actually requires cross-process knowledge — when this worker alone has
not seen enough. The local counter is a short-circuit, never the whole answer.

**3. On database failure it degrades to per-process counting, and says so.**
Hard Rule #5 says fail closed at authority boundaries, and a naive reading would
deny every request when the database is unreachable — converting a database
incident into a total outage, self-inflicted. The opposite, allowing everything,
is the failure that Hard Rule #4 calls a fake control. ``admin_gateway`` already
chose the middle and documents it: fall back to the per-process window "instead
of failing fully open". This does the same and marks the decision
``distributed=False`` so no caller or health surface can mistake a degraded
answer for a distributed one.

Default **OFF**, and ``shadow`` before ``enforce``
--------------------------------------------------

Switching on a *correct* distributed limiter is not a neutral act: production has
only ever experienced limits multiplied by the worker count, so correct
enforcement is an immediate ~4x tightening against real users on a shipped client
that cannot be updated (Hard Rule #3). ``shadow`` counts and reports what
enforcement would have done without changing a single response, so the tightening
can be measured before it is imposed.

    SENTINEL_DISTRIBUTED_LIMITS_MODE = off | shadow | enforce      (default off)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

from services.sentinel import bootstrap, killswitches, store

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"
_MODES = (MODE_OFF, MODE_SHADOW, MODE_ENFORCE)

#: Rows older than this many windows are prunable.
PRUNE_RETENTION_WINDOWS = 4

#: A process prunes at most this often, regardless of traffic.
PRUNE_INTERVAL_SECONDS = 300.0

#: Cap on the in-process short-circuit map, so the fallback cannot become the
#: unbounded-growth vector that ``security_guard.BUCKETS`` already is.
MAX_LOCAL_KEYS = 20000


# NOTE: this module deliberately owns **no** table of limits.
#
# The first draft had a `RULES` registry naming password reset, registration and
# so on with limits of its own. That was wrong, and finding out why is the most
# useful thing this stage produced: `bot.basic_abuse_guard` already carries a
# deployed table of exactly those routes and their limits, and a second registry
# beside it would be a duplicate rate-limit policy — the precise thing Hard Rule
# #6 forbids, and the failure mode where two limits disagree and nobody knows
# which one production is applying. Limits come from the caller. This module
# owns the *counting*, not the policy.


@dataclass(frozen=True)
class Decision:
    """The outcome of one check.

    ``allowed`` is the only field a caller must obey. The rest exist so that the
    difference between "allowed because it is under the limit", "allowed because
    we are only observing" and "allowed because the database was unreachable and
    this worker alone has not seen enough" is never collapsed into one boolean.
    """

    allowed: bool
    limited: bool          # the limit was exceeded, whatever we then did about it
    enforced: bool         # whether `allowed` reflects enforcement
    distributed: bool      # False => decided from process memory only
    mode: str
    scope: str
    subject: str
    count: float           # weighted estimate at decision time
    limit: int
    window_seconds: int
    retry_after: int
    reason: str
    degraded_error: str = ""

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed, "limited": self.limited,
            "enforced": self.enforced, "distributed": self.distributed,
            "mode": self.mode, "scope": self.scope, "count": self.count,
            "limit": self.limit, "window_seconds": self.window_seconds,
            "retry_after": self.retry_after, "reason": self.reason,
        }


# --- mode --------------------------------------------------------------------


def mode() -> str:
    """Current mode. Default ``off``; an unrecognised value is also ``off``.

    Unknown-means-off rather than unknown-means-enforce: a typo in a Railway
    variable must not silently start rejecting production traffic on a client
    that cannot be updated. Note this is the opposite default from Hard Rule #5's
    authorization boundaries, and deliberately so — an unparseable *authorization*
    answer means deny, but an unparseable *limiter configuration* means the
    operator has not yet made the decision, and the pre-existing per-worker
    limiters are still in place underneath.
    """
    if killswitches.emergency_killed():
        # The emergency switch stops Sentinel touching production. Falling back
        # here is safe in a way it would not be for an authorization gate: the
        # platform's own limiters remain, so this reverts to today's behaviour
        # rather than to no protection.
        return MODE_OFF
    raw = str(os.getenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "")).strip().lower()
    return raw if raw in _MODES else MODE_OFF


def enabled() -> bool:
    return mode() != MODE_OFF


# --- process-local short circuit ---------------------------------------------

_LOCK = threading.Lock()
_LOCAL: dict[tuple, int] = {}
_LAST_PRUNE = 0.0

_STATS: dict = {
    "checks": 0,
    "limited": 0,           # decisions where the limit was exceeded
    "shadow_limited": 0,    # would have been blocked, but mode was shadow
    "enforced_blocks": 0,   # actually returned allowed=False
    "db_reads": 0,          # checks that reached PostgreSQL
    "local_short_circuits": 0,
    "degraded": 0,          # checks answered from memory after a DB failure
    "prunes": 0,
    "pruned_rows": 0,
    "last_error": "",
    "last_error_type": "",
}


def _window_start(now: float, window_seconds: int) -> int:
    return int(now // window_seconds) * window_seconds


def _bump_local(key: tuple, cost: int) -> int:
    with _LOCK:
        if len(_LOCAL) >= MAX_LOCAL_KEYS:
            # Evict the oldest window rather than grow without bound. Windows
            # sort by their start time, so the smallest key element 2 is the
            # stalest thing present.
            try:
                oldest = min(_LOCAL, key=lambda k: k[2])
                _LOCAL.pop(oldest, None)
            except ValueError:  # pragma: no cover - empty dict cannot be full
                pass
        _LOCAL[key] = _LOCAL.get(key, 0) + cost
        return _LOCAL[key]


def _local_count(key: tuple) -> int:
    with _LOCK:
        return _LOCAL.get(key, 0)


# --- the shared counter ------------------------------------------------------

_UPSERT_SQL = (
    "INSERT INTO sentinel_rate_counters "
    "(scope, subject, window_start, hits, updated_at) VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT (scope, subject, window_start) "
    "DO UPDATE SET hits = sentinel_rate_counters.hits + ?, updated_at = ? "
    "RETURNING hits"
)

_PREV_SQL = (
    "SELECT hits FROM sentinel_rate_counters "
    "WHERE scope = ? AND subject = ? AND window_start = ?"
)

_PRUNE_SQL = "DELETE FROM sentinel_rate_counters WHERE window_start < ?"


def _bump_shared(scope: str, subject: str, window_start: int, prev_start: int,
                 cost: int, stamp: str) -> tuple[int, int]:
    """Increment the current window and read the previous one. Own connection.

    Returns ``(current_hits, previous_hits)``. Raises on database failure — the
    caller decides what a failure means, because only it knows whether it is
    allowed to degrade.

    The upsert is a single statement on purpose. A ``SELECT`` followed by an
    ``UPDATE`` would be a read-modify-write race between four workers, which is
    the exact bug this module exists to fix; ``ON CONFLICT DO UPDATE`` makes the
    increment atomic and ``RETURNING`` reports the value our own increment
    produced.
    """
    conn = store.platform_db.connect()
    try:
        cur = conn.cursor()
        cur.execute(_UPSERT_SQL,
                    (scope, subject, window_start, cost, stamp, cost, stamp))
        row = cur.fetchone()
        current = int(row[0]) if row else cost

        cur.execute(_PREV_SQL, (scope, subject, prev_start))
        prow = cur.fetchone()
        previous = int(prow[0]) if prow else 0

        # Committed immediately and deliberately: see design decision 1 in the
        # module docstring. Holding this row past the end of the check would
        # block every other worker counting the same subject.
        conn.commit()
        return current, previous
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _maybe_prune(window_seconds: int, now: float) -> None:
    """Delete expired windows, at most once per ``PRUNE_INTERVAL_SECONDS``.

    Never raises: failing to prune is a disk problem for later, not a reason to
    fail the security decision that just succeeded.
    """
    global _LAST_PRUNE
    with _LOCK:
        if now - _LAST_PRUNE < PRUNE_INTERVAL_SECONDS:
            return
        _LAST_PRUNE = now

    cutoff = int(now) - window_seconds * PRUNE_RETENTION_WINDOWS
    conn = None
    try:
        conn = store.platform_db.connect()
        cur = conn.cursor()
        cur.execute(_PRUNE_SQL, (cutoff,))
        removed = max(0, int(getattr(cur, "rowcount", 0) or 0))
        conn.commit()
        with _LOCK:
            _STATS["prunes"] += 1
            _STATS["pruned_rows"] += removed
            # The in-process map needs the same treatment; it has the same
            # unbounded-growth shape as the table.
            for key in [k for k in _LOCAL if k[2] < cutoff]:
                _LOCAL.pop(key, None)
    except Exception as exc:
        _record_error(exc)
        logger.warning("SENTINEL_RATE_PRUNE_FAILED type=%s error=%s",
                       type(exc).__name__, str(exc)[:200])
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _record_error(exc: BaseException) -> None:
    try:
        with _LOCK:
            _STATS["last_error"] = str(exc)[:300]
            _STATS["last_error_type"] = type(exc).__name__
    except Exception:
        pass


# --- the decision ------------------------------------------------------------


def check(scope: str, subject: str, *, limit: int, window_seconds: int,
          cost: int = 1, now: float | None = None) -> Decision:
    """Count one action and decide whether it may proceed. Never raises.

    ``limit`` and ``window_seconds`` are required and come from the caller — see
    the note above ``Decision`` on why this module holds no limits of its own.

    ``scope`` names what is being counted (in practice, the request path).
    ``subject`` is whatever the limit is per — a hashed IP, an account id, an
    email domain. Pass an already-hashed value; this module does not hash, so
    that Sentinel's subjects join against the identifiers the platform already
    stores (``admin_audit_logs.ip_hash`` and friends) rather than forming a
    private identifier space that correlates with nothing.
    """
    now = time.time() if now is None else now
    window_seconds = max(1, int(window_seconds))
    limit = max(0, int(limit))
    cost = max(1, int(cost))

    active = mode()
    if active == MODE_OFF:
        # No counting, no connection, no state. The default configuration of
        # this module must cost production exactly nothing.
        return Decision(
            allowed=True, limited=False, enforced=False, distributed=False,
            mode=MODE_OFF, scope=scope, subject=subject, count=0.0,
            limit=limit, window_seconds=window_seconds, retry_after=0,
            reason="mode:off")

    window_start = _window_start(now, window_seconds)
    prev_start = window_start - window_seconds
    elapsed = now - window_start
    # Weighted sliding window. A plain fixed window lets an attacker send the
    # full limit in the last instant of one window and again in the first
    # instant of the next — 2x the limit across a millisecond boundary. Carrying
    # a decaying share of the previous window forward removes that seam without
    # storing per-request timestamps.
    weight = max(0.0, 1.0 - (elapsed / float(window_seconds)))
    retry_after = max(1, int(window_start + window_seconds - now))

    key = (scope, subject, window_start)
    prev_key = (scope, subject, prev_start)

    try:
        local_current = _bump_local(key, cost)
        local_estimate = local_current + _local_count(prev_key) * weight

        distributed = False
        degraded_error = ""

        if local_estimate > limit:
            # A single worker has already seen more than the global limit
            # permits, and a worker's own count can only be a lower bound on the
            # global one. No database round trip can change this answer, and
            # under a flood this is the branch nearly every request takes.
            with _LOCK:
                _STATS["local_short_circuits"] += 1
            estimate = local_estimate
            reason = "local_lower_bound_exceeds_limit"
        else:
            try:
                current, previous = _bump_shared(
                    scope, subject, window_start, prev_start, cost,
                    _stamp(now))
                estimate = current + previous * weight
                distributed = True
                reason = "distributed"
                with _LOCK:
                    _STATS["db_reads"] += 1
            except Exception as exc:
                # Degrade to this worker's own count. It under-counts by roughly
                # the worker count, which is precisely today's behaviour — so
                # the failure mode of this control is the status quo, not an
                # outage and not an open door.
                #
                # Note the shape: this branch is only reached when
                # `local_estimate <= limit`, so a degraded call can never itself
                # be the one that blocks. The limiting still happens — on the
                # next call, once the local counter has passed the limit and the
                # short circuit above takes over. So `estimate` here is not the
                # decision, it is the *reported count*, and reporting it honestly
                # is what lets an operator see that a degraded window was counted
                # per-worker rather than globally.
                _record_error(exc)
                logger.warning(
                    "SENTINEL_RATE_DEGRADED scope=%s type=%s error=%s",
                    scope, type(exc).__name__, str(exc)[:200])
                with _LOCK:
                    _STATS["degraded"] += 1
                estimate = local_estimate
                degraded_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                reason = "degraded_to_process_memory"

        limited = estimate > limit
        enforced = active == MODE_ENFORCE
        allowed = not (limited and enforced)

        with _LOCK:
            _STATS["checks"] += 1
            if limited:
                _STATS["limited"] += 1
                if enforced:
                    _STATS["enforced_blocks"] += 1
                else:
                    _STATS["shadow_limited"] += 1

        if limited and not enforced:
            # Shadow mode returns no 429, so the Stage 3 request bridge sees
            # nothing — the whole point of shadow is to measure a tightening
            # before imposing it, and an unmeasured shadow is just an off
            # switch with extra steps.
            logger.warning(
                "SENTINEL_RATE_SHADOW_WOULD_LIMIT scope=%s count=%.2f limit=%s "
                "window=%s distributed=%s",
                scope, estimate, limit, window_seconds, distributed)

        _maybe_prune(window_seconds, now)

        return Decision(
            allowed=allowed, limited=limited, enforced=enforced,
            distributed=distributed, mode=active, scope=scope, subject=subject,
            count=round(float(estimate), 3), limit=limit,
            window_seconds=window_seconds, retry_after=retry_after,
            reason=reason, degraded_error=degraded_error)

    except Exception as exc:  # pragma: no cover - check must never escape
        # A limiter that can 500 the endpoint it protects has made the system
        # less available, not more secure.
        _record_error(exc)
        return Decision(
            allowed=True, limited=False, enforced=False, distributed=False,
            mode=active, scope=scope, subject=subject, count=0.0, limit=limit,
            window_seconds=window_seconds, retry_after=0,
            reason="internal_error", degraded_error=type(exc).__name__)


def _stamp(now: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))


def stats() -> dict:
    """Counters for health surfaces.

    ``degraded`` and ``distributed_ok`` are the honest pair: a non-zero
    ``degraded`` count means some decisions in this process were made from
    process memory, so the limit that was actually applied during that period
    was the per-worker one. A health surface that reported only "limiter: on"
    would be describing a control it had not verified (Hard Rule #4).
    """
    with _LOCK:
        snapshot = dict(_STATS)
        snapshot["local_keys"] = len(_LOCAL)
    snapshot["mode"] = mode()
    snapshot["enabled"] = snapshot["mode"] != MODE_OFF
    snapshot["schema_ready"] = bootstrap.schema_ready()
    snapshot["distributed_ok"] = snapshot["degraded"] == 0
    return snapshot


def reset_for_tests() -> None:
    """Drop process-local state and counters. Test-only."""
    global _LAST_PRUNE
    with _LOCK:
        _LOCAL.clear()
        _LAST_PRUNE = 0.0
        _STATS.update({
            "checks": 0, "limited": 0, "shadow_limited": 0,
            "enforced_blocks": 0, "db_reads": 0, "local_short_circuits": 0,
            "degraded": 0, "prunes": 0, "pruned_rows": 0,
            "last_error": "", "last_error_type": "",
        })
