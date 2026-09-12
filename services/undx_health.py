"""UNDX provider health — what each provider has actually been doing, shared by every worker.

Two separate defects live here, and they compound.

1. The breaker counted to three nine times
------------------------------------------
`undx_router` kept `_health_state` in a module-level dict. The Procfile runs
four gunicorn workers plus five background workers, so "three consecutive
failures opens the circuit" actually meant *up to twenty-seven* calls into a
dead provider before every process had independently arrived at the same
conclusion — and on Meta, whose `META_MUSE_TIMEOUT_MS` is 60000, a share of
those cost a full minute each. Worse, the half-open probe was per process too:
the breaker's central promise is that exactly one trial request tests recovery
while everyone else keeps resting, and nine processes each granting their own
trial is not that promise, it is the herd the breaker exists to prevent,
divided by nine and then multiplied by nine again.

The fix is the same shape the repo already uses for shared counters
(`services/sentinel/rate_limit.py`): one row per provider in Postgres,
mutated by a single atomic statement. There is no Redis in this deployment —
228 Railway variables, no `REDIS_URL` — so Postgres is the shared store, and
this module deliberately does not introduce a second one.

The probe is claimed by a conditional `UPDATE ... WHERE probe_owner = ''`
whose `rowcount` is the answer. Concurrent writers serialise on the row and
the loser re-reads the winner's committed value, so exactly one worker gets
`rowcount == 1`. That is the whole of the single-probe guarantee, and
`OneProbeAcrossProcessesTest` spends real subprocesses proving it rather than
threads, because threads inside one interpreter would pass even if the state
were process-local — which is precisely the bug.

2. "Online" was a claim about configuration, not about health
--------------------------------------------------------------
`provider_health()` returned "Online" whenever a key was present and no switch
was off. Claude and Gemini both read Online for the entire period they were
returning 404 to every single request. The word was not slightly wrong; it was
answering a different question than the one an operator reads it to answer,
and failover meant every request still returned 200, so nothing else
contradicted it.

A provider that has never answered is `UNKNOWN`. Not Online, not Healthy —
unknown, which is the true state of a provider nobody has called. Everything
else is derived from observed outcomes:

    HEALTHY        last call succeeded, no failures since
    DEGRADED       one isolated failure since the last success
    RATE_LIMITED   last failure was HTTP 429
    AUTH_FAILED    last failure was HTTP 401 or 403
    BILLING_FAILED last failure was HTTP 402 — DeepSeek's real state today
    MODEL_RETIRED  last failure was HTTP 404 or named the model as unknown
    UNAVAILABLE    repeated transport faults, timeouts or 5xx
    CIRCUIT_OPEN   the breaker has taken it out and has not seen it answer
    UNKNOWN        never observed, or the store could not be read

The named failure classes exist because they are the ones where the fix is a
person doing something specific — rotate a key, pay an invoice, change a model
ID — and collapsing them into "down" sends whoever is paged looking in the
wrong place. Four of these states describe conditions this deployment is
*actually* in right now.

Why this defaults on, when `sentinel.rate_limit` defaults off
--------------------------------------------------------------
That module ships default-OFF for a good reason: production had only ever
experienced limits multiplied by the worker count, so making them correct is a
real tightening that can lock out real users, permanently, with no self-heal.

The asymmetry here is that a breaker that opens too eagerly costs one provider
for `BREAKER_COOLDOWN_SECONDS`, fails over to the next in the chain, and closes
itself on the next success. The blast radius is bounded and it recovers without
a human. So this defaults on, with the effect stated plainly: the threshold
stops being 3-per-process and becomes 3 across the deployment.
`UNDX_BREAKER_THRESHOLD` widens it for anyone who finds that too tight, and
`UNDX_PROVIDER_HEALTH_SHARED=false` reverts to the old per-process behaviour
without disabling the breaker itself.

On database failure this falls back to the per-process mirror and reports
`distributed: false`. Denying every provider because a bookkeeping table is
unreachable would convert a database incident into a total AI outage,
self-inflicted; trusting a stale local count is the smaller, and visible, harm.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from typing import Any

from services import db as platform_db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- states

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
RATE_LIMITED = "RATE_LIMITED"
UNAVAILABLE = "UNAVAILABLE"
AUTH_FAILED = "AUTH_FAILED"
BILLING_FAILED = "BILLING_FAILED"
MODEL_RETIRED = "MODEL_RETIRED"
CIRCUIT_OPEN = "CIRCUIT_OPEN"
UNKNOWN = "UNKNOWN"

#: The complete set. Anything a caller renders that is not in here is a bug in
#: this module, and `test_no_state_outside_the_declared_set` says so.
HEALTH_STATES: tuple[str, ...] = (
    HEALTHY, DEGRADED, RATE_LIMITED, UNAVAILABLE, AUTH_FAILED,
    BILLING_FAILED, MODEL_RETIRED, CIRCUIT_OPEN, UNKNOWN,
)

#: States that mean "a person has to do something", as opposed to "wait".
#: A retry will not fix any of these, so a chain that keeps trying is burning
#: latency to reach a conclusion it already has.
ACTIONABLE_STATES: frozenset[str] = frozenset(
    {AUTH_FAILED, BILLING_FAILED, MODEL_RETIRED})


# ---------------------------------------------------------------- failure kind

#: Status tokens the router records. Kept as strings rather than an enum
#: because they also travel in the `attempts` envelope, which is JSON.
STATUS_TIMEOUT = "timeout"
STATUS_CONNECTION = "connection_failed"
STATUS_RESPONSE = "response_failed"
STATUS_REQUEST = "request_failed"


def classify_failure(exc: BaseException) -> str:
    """Turn an adapter exception into a status token that keeps the HTTP code.

    Every adapter calls `raise_for_status()`, so a provider answering 401 and a
    provider whose DNS does not resolve both arrived here as
    `requests.RequestException` and were both recorded as `request_failed`.
    That is the erasure that let a billing failure and a network blip look
    identical on the health surface. The code is right there on
    `exc.response`; this keeps it.
    """
    try:  # `requests` is optional at import time for the same reason the router treats it so
        import requests  # noqa: PLC0415
    except Exception:  # pragma: no cover - requests is a hard dep in practice
        requests = None  # type: ignore[assignment]

    if requests is not None:
        if isinstance(exc, requests.Timeout):
            return STATUS_TIMEOUT
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        if isinstance(code, int) and code:
            return f"http_{code}"
        if isinstance(exc, requests.ConnectionError):
            return STATUS_CONNECTION
        if isinstance(exc, requests.RequestException):
            return STATUS_REQUEST
    return STATUS_RESPONSE


#: Substrings that mean the *model* is gone rather than the provider. A 404 on
#: a chat endpoint is ambiguous — wrong path or wrong model — so the message is
#: consulted before concluding retirement, which is the louder claim.
_RETIRED_HINTS: tuple[str, ...] = (
    "model_not_found", "model not found", "does not exist",
    "is not supported", "has been deprecated", "no longer available",
    "unknown model", "invalid model",
)


def state_for(row: dict[str, Any] | None) -> str:
    """Map one observation record to one of `HEALTH_STATES`.

    Order matters. `CIRCUIT_OPEN` outranks the reason the circuit opened
    because it is the state that changes what the router *does*.

    But it must not be the *only* thing reported, which is why
    `failure_state()` exists beside it. A circuit that opened on timeouts
    closes itself; a circuit that opened on HTTP 402 will reopen every
    cooldown forever until somebody pays an invoice, and reporting both as
    "CIRCUIT_OPEN, wait for it" hides the second case behind the first.
    DeepSeek is in exactly that state in this deployment today.
    """
    if not row:
        return UNKNOWN
    if row.get("opened_at"):
        return CIRCUIT_OPEN
    return failure_state(row)


def failure_state(row: dict[str, Any] | None) -> str:
    """The same classification with the circuit ignored: *why* it is unhealthy.

    Reported alongside `state_for()` so that an operator can tell "this will
    recover on its own" from "this needs a person", which the circuit state
    alone cannot express.
    """
    if not row:
        return UNKNOWN
    failures = int(row.get("consecutive_failures") or 0)
    if not failures:
        # No failure outstanding. But "no failure" is only health if something
        # has actually succeeded — a provider with an empty record has not.
        return HEALTHY if row.get("last_success_at") else UNKNOWN

    status = str(row.get("last_status") or "")
    error = str(row.get("last_error") or "").lower()
    if status == "http_429":
        return RATE_LIMITED
    if status in ("http_401", "http_403"):
        return AUTH_FAILED
    if status == "http_402":
        return BILLING_FAILED
    if status == "http_404":
        return MODEL_RETIRED if any(h in error for h in _RETIRED_HINTS) else UNAVAILABLE
    if status.startswith("http_4") and any(h in error for h in _RETIRED_HINTS):
        # Several providers report a retired model as 400 with the name in the
        # body rather than 404. The message is the evidence either way.
        return MODEL_RETIRED
    # Transport faults and 5xx. One is noise; two in a row is a pattern, and
    # calling a single blip UNAVAILABLE would make the word useless.
    return UNAVAILABLE if failures >= 2 else DEGRADED


# -------------------------------------------------------------------- settings

HEALTH_ENV_VARS: tuple[str, ...] = (
    "UNDX_PROVIDER_HEALTH_SHARED",
    "UNDX_BREAKER_THRESHOLD",
    "UNDX_BREAKER_COOLDOWN_S",
)

#: Consecutive failures before a provider is rested, and for how long.
#: Deliberately not aggressive: three strikes tolerates the transient upstream
#: 503s that Gemini demonstrably produces, while still catching a provider that
#: is genuinely down. Read via `threshold()` — the constant is the default, not
#: the setting, because an operator who finds the now-correct (deployment-wide
#: rather than per-worker) count too tight needs a dial that is not "off".
DEFAULT_BREAKER_THRESHOLD = 3
DEFAULT_BREAKER_COOLDOWN_SECONDS = 120

HEALTH_TABLE = "undx_provider_health"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def shared_enabled() -> bool:
    return _flag("UNDX_PROVIDER_HEALTH_SHARED", True)


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(float(_env(name) or default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def threshold() -> int:
    return _positive_int("UNDX_BREAKER_THRESHOLD", DEFAULT_BREAKER_THRESHOLD)


def cooldown_seconds() -> int:
    return _positive_int("UNDX_BREAKER_COOLDOWN_S", DEFAULT_BREAKER_COOLDOWN_SECONDS)


# ---------------------------------------------------------------------- schema

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    # One row per provider, holding *current* state rather than history. The
    # counters are lifetime totals; `consecutive_failures` is the one the
    # breaker reads, and it is the one a success clears.
    f"""CREATE TABLE IF NOT EXISTS {HEALTH_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        opened_at DOUBLE PRECISION NOT NULL DEFAULT 0,
        probe_owner TEXT NOT NULL DEFAULT '',
        probe_started_at DOUBLE PRECISION NOT NULL DEFAULT 0,
        last_status TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT '',
        last_success_at DOUBLE PRECISION NOT NULL DEFAULT 0,
        last_failure_at DOUBLE PRECISION NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # Required by `ON CONFLICT (provider)`, and it is what makes the whole
    # module atomic instead of a read-modify-write race between nine processes.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{HEALTH_TABLE}_provider "
    f"ON {HEALTH_TABLE}(provider)",
)

_COLUMNS = ("provider", "consecutive_failures", "opened_at", "probe_owner",
            "probe_started_at", "last_status", "last_error", "last_success_at",
            "last_failure_at", "successes", "failures")

_SELECT_ONE = f"SELECT {', '.join(_COLUMNS)} FROM {HEALTH_TABLE} WHERE provider = ?"
_SELECT_ALL = f"SELECT {', '.join(_COLUMNS)} FROM {HEALTH_TABLE}"

_SUCCESS_SQL = f"""INSERT INTO {HEALTH_TABLE}
    (provider, consecutive_failures, opened_at, probe_owner, probe_started_at,
     last_status, last_error, last_success_at, successes, failures, updated_at)
    VALUES (?, 0, 0, '', 0, 'success', '', ?, 1, 0, ?)
    ON CONFLICT (provider) DO UPDATE SET
        consecutive_failures = 0,
        opened_at = 0,
        probe_owner = '',
        probe_started_at = 0,
        last_status = 'success',
        last_error = '',
        last_success_at = ?,
        successes = {HEALTH_TABLE}.successes + 1,
        updated_at = ?
    RETURNING successes"""

# The whole breaker decision in one statement. Read-then-write would be the
# exact race this module exists to remove, so "did this failure open the
# circuit" is computed by the database from the row it is already locking.
#
# The two CASE arms are distinct events:
#   * a failed half-open probe restarts the cooldown from now, because the
#     stored `opened_at` is already expired and would admit the next caller
#     instantly — recovery would be tested continuously instead of once.
#   * crossing the threshold opens it, but only if it is not already open.
_FAILURE_SQL = f"""INSERT INTO {HEALTH_TABLE}
    (provider, consecutive_failures, opened_at, probe_owner, probe_started_at,
     last_status, last_error, last_failure_at, successes, failures, updated_at)
    VALUES (?, 1, ?, '', 0, ?, ?, ?, 0, 1, ?)
    ON CONFLICT (provider) DO UPDATE SET
        consecutive_failures = {HEALTH_TABLE}.consecutive_failures + 1,
        failures = {HEALTH_TABLE}.failures + 1,
        last_status = ?,
        last_error = ?,
        last_failure_at = ?,
        opened_at = CASE
            WHEN {HEALTH_TABLE}.probe_owner <> '' THEN ?
            WHEN {HEALTH_TABLE}.opened_at = 0
                 AND {HEALTH_TABLE}.consecutive_failures + 1 >= ? THEN ?
            ELSE {HEALTH_TABLE}.opened_at
        END,
        probe_owner = '',
        probe_started_at = 0,
        updated_at = ?
    RETURNING consecutive_failures, opened_at"""

# §17: exactly one half-open probe across all workers.
#
# This is a compare-and-swap, not a read followed by a write. Two workers
# issuing it concurrently serialise on the row; the loser re-evaluates the
# WHERE clause against the winner's committed row, sees a fresh `probe_owner`,
# and matches nothing. `rowcount` is therefore 1 for exactly one caller, and
# that caller owns the trial request.
#
# The `probe_started_at` clause is the lease: a worker killed mid-probe —
# deploy, OOM, restart — would otherwise hold the claim forever and rest the
# provider forever, making the breaker a permanent outage of its own making.
_CLAIM_PROBE_SQL = f"""UPDATE {HEALTH_TABLE}
    SET probe_owner = ?, probe_started_at = ?, updated_at = ?
    WHERE provider = ?
      AND opened_at > 0
      AND ? - opened_at >= ?
      AND (probe_owner = '' OR ? - probe_started_at >= ?)"""


_LOCK = threading.Lock()
_schema_ready = False

#: Per-process mirror. Not the source of truth; it is what the breaker is
#: enforced against when the database is unreachable, so a store outage
#: degrades this control's *reach* rather than turning it into either an open
#: door or an outage. Same trade, and the same `degraded` counter, as
#: `sentinel.rate_limit` and `undx_cost`.
_local: dict[str, dict[str, Any]] = {}

_STATS: dict[str, Any] = {
    "writes": 0,
    "write_failures": 0,
    "reads": 0,
    "read_failures": 0,
    "degraded": 0,
    "probes_claimed": 0,
    "probes_denied": 0,
    "last_error": "",
}

#: Identifies this worker in `probe_owner`. Host and pid make it readable in a
#: dashboard; the uuid makes it unique across a pid that was reused after a
#: restart, which is the case where a stale claim would otherwise look like
#: this process's own.
_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def worker_id() -> str:
    return _WORKER_ID


def _connect():
    return platform_db.connect()


def ensure_schema(conn=None) -> int:
    """Create the health table. Idempotent; safe at every boot.

    When no connection is supplied this opens and **commits** its own, for the
    reason `undx_cost.ensure_schema` spells out: DDL riding a caller's
    uncommitted transaction holds a catalog lock that the next connection
    blocks behind, and that has already hung a route in this repo.
    """
    own = conn is None
    if own:
        conn = _connect()
    try:
        cur = conn.cursor()
        for statement in _SCHEMA_STATEMENTS:
            cur.execute(statement)
        if own:
            conn.commit()
        return len(_SCHEMA_STATEMENTS)
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # pragma: no cover - close failure is not our story
                pass


def _ensure_schema_once() -> None:
    global _schema_ready
    if _schema_ready:
        return
    ensure_schema()
    with _LOCK:
        _schema_ready = True


def _note_error(exc: BaseException, kind: str) -> None:
    with _LOCK:
        _STATS[kind] += 1
        _STATS["degraded"] += 1
        _STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:200]


def _blank(provider: str) -> dict[str, Any]:
    return {"provider": provider, "consecutive_failures": 0, "opened_at": 0.0,
            "probe_owner": "", "probe_started_at": 0.0, "last_status": "",
            "last_error": "", "last_success_at": 0.0, "last_failure_at": 0.0,
            "successes": 0, "failures": 0}


def _local_bucket(provider: str) -> dict[str, Any]:
    return _local.setdefault(provider, _blank(provider))


def _row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(zip(_COLUMNS, row))
    for key in ("consecutive_failures", "successes", "failures"):
        out[key] = int(out.get(key) or 0)
    for key in ("opened_at", "probe_started_at", "last_success_at", "last_failure_at"):
        out[key] = float(out.get(key) or 0.0)
    for key in ("probe_owner", "last_status", "last_error"):
        out[key] = str(out.get(key) or "")
    return out


# ------------------------------------------------------------------- recording

def record_success(provider: str) -> dict[str, Any]:
    """Mark a completed call. Never raises.

    Clears the consecutive count, closes the circuit and releases any probe —
    which is the point: the worker that held the trial request is the one whose
    success closes the breaker for everybody, in one statement, without anyone
    else having to notice.
    """
    provider = (provider or "").strip().lower()
    if not provider:
        return {}
    now = time.time()
    with _LOCK:
        bucket = _local_bucket(provider)
        was_open = bool(bucket["opened_at"])
        bucket.update(consecutive_failures=0, opened_at=0.0, probe_owner="",
                      probe_started_at=0.0, last_status="success", last_error="",
                      last_success_at=now)
        bucket["successes"] += 1
        local = dict(bucket)
    if was_open:
        log.warning("UNDX provider recovered provider=%s", provider)

    if not shared_enabled():
        return local
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
    conn = None
    try:
        _ensure_schema_once()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(_SUCCESS_SQL, (provider, now, stamp, now, stamp))
        cur.fetchone()
        conn.commit()
        with _LOCK:
            _STATS["writes"] += 1
        return local
    except Exception as exc:  # noqa: BLE001 - see docstring
        _note_error(exc, "write_failures")
        log.warning("UNDX health write failed provider=%s error=%s",
                    provider, type(exc).__name__)
        return local
    finally:
        _close(conn)


def record_failure(provider: str, status: str, error: str = "") -> dict[str, Any]:
    """Mark a failed call and, atomically, decide whether it opened the circuit.

    Never raises. The request has already failed; a bookkeeping fault must not
    become a second, different failure on top of it.
    """
    provider = (provider or "").strip().lower()
    if not provider:
        return {}
    now = time.time()
    status = (status or STATUS_RESPONSE).strip()
    error = (error or "")[:200]
    limit = threshold()

    with _LOCK:
        bucket = _local_bucket(provider)
        bucket["consecutive_failures"] += 1
        bucket["failures"] += 1
        bucket["last_status"] = status
        bucket["last_error"] = error
        bucket["last_failure_at"] = now
        was_probe = bool(bucket["probe_owner"])
        bucket["probe_owner"] = ""
        bucket["probe_started_at"] = 0.0
        if was_probe:
            bucket["opened_at"] = now
        elif not bucket["opened_at"] and bucket["consecutive_failures"] >= limit:
            bucket["opened_at"] = now
        local = dict(bucket)

    if not shared_enabled():
        if local["opened_at"] == now:
            _log_opened(provider, local["consecutive_failures"], status)
        return local

    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
    # `now` is passed for both CASE arms so that an `opened_at` equal to it in
    # the returned row means *this* call opened the circuit. That is how the
    # one-and-only-once log line is decided without a second read.
    #
    # The INSERT arm is the first failure this provider has ever recorded, so
    # it opens the circuit only where the threshold is 1. Hardcoding 0 there
    # would make `UNDX_BREAKER_THRESHOLD=1` silently never open on a cold row.
    first_open = now if limit <= 1 else 0.0
    params = (provider, first_open, status, error, now, stamp,
              status, error, now, now, limit, now, stamp)
    conn = None
    try:
        _ensure_schema_once()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(_FAILURE_SQL, params)
        row = cur.fetchone()
        conn.commit()
        with _LOCK:
            _STATS["writes"] += 1
        if row:
            shared = dict(local)
            shared["consecutive_failures"] = int(row[0])
            shared["opened_at"] = float(row[1] or 0.0)
            if shared["opened_at"] == now:
                _log_opened(provider, shared["consecutive_failures"], status)
            return shared
        return local
    except Exception as exc:  # noqa: BLE001 - see docstring
        _note_error(exc, "write_failures")
        log.warning("UNDX health write failed provider=%s error=%s",
                    provider, type(exc).__name__)
        if local["opened_at"] == now:
            _log_opened(provider, local["consecutive_failures"], status)
        return local
    finally:
        _close(conn)


def _log_opened(provider: str, count: int, status: str) -> None:
    # Louder than the per-request warning, and the only line that says a
    # provider is *out*. Claude and Gemini were each dead in production for an
    # unknown period behind nothing but repeated per-request warnings, because
    # failover meant every request still returned 200.
    log.error("UNDX provider circuit opened provider=%s consecutive_failures=%s "
              "last_status=%s cooldown_s=%s", provider, count, status, cooldown_seconds())


# -------------------------------------------------------------------- decisions

def read(provider: str) -> dict[str, Any] | None:
    """The shared row if it can be read, else this process's mirror.

    Returns `None` only for a provider nothing has ever observed, which is what
    makes `UNKNOWN` reachable rather than theoretical.
    """
    provider = (provider or "").strip().lower()
    if shared_enabled():
        conn = None
        try:
            _ensure_schema_once()
            conn = _connect()
            cur = conn.cursor()
            cur.execute(_SELECT_ONE, (provider,))
            row = cur.fetchone()
            with _LOCK:
                _STATS["reads"] += 1
            if row:
                return _row_to_dict(row)
            return None
        except Exception as exc:  # noqa: BLE001
            _note_error(exc, "read_failures")
        finally:
            _close(conn)
    with _LOCK:
        bucket = _local.get(provider)
        return dict(bucket) if bucket else None


def is_open(provider: str) -> bool:
    """Read-only: is this provider currently rested?

    Separate from `should_skip` because that one *claims* the probe. A status
    endpoint calling it would spend the single trial request the breaker
    allows, every other caller would go on resting behind a probe nobody is
    going to resolve, and recovery would be delayed by the act of looking at
    the dashboard.

    "Open" includes the window where the cooldown has expired but no trial has
    yet answered. Reporting that as closed would show an operator a provider
    back in service before anything had confirmed it.
    """
    row = read(provider)
    return bool(row and row.get("opened_at"))


def should_skip(provider: str, probe_timeout: float = 75.0) -> bool:
    """True if this request must not try the provider. Mutates: claims the probe.

    When the cooldown expires the breaker does not simply close. It hands the
    *first* caller — across the whole deployment, not the first in each of nine
    processes — a single trial request, and keeps resting everyone else until
    that trial resolves. Closing outright would let every request that happens
    to arrive in that instant hit a provider nobody has yet confirmed is back,
    and on Meta, where `META_MUSE_TIMEOUT_MS` is 60000, each of those pays a
    full minute before failing over. The herd is the specific harm the breaker
    exists to prevent, so it must not be reintroduced at the moment of recovery.
    """
    provider = (provider or "").strip().lower()
    if shared_enabled():
        skip = _should_skip_shared(provider, probe_timeout)
        if skip is not None:
            return skip
        with _LOCK:
            _STATS["degraded"] += 1
    return _should_skip_local(provider, probe_timeout)


def _should_skip_shared(provider: str, probe_timeout: float) -> bool | None:
    """None means the store could not answer; the caller falls back."""
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
    conn = None
    try:
        _ensure_schema_once()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(_SELECT_ONE, (provider,))
        row = cur.fetchone()
        if not row or not float(row[_COLUMNS.index("opened_at")] or 0.0):
            return False
        cur.execute(_CLAIM_PROBE_SQL, (
            _WORKER_ID, now, stamp, provider,
            now, float(cooldown_seconds()),
            now, float(probe_timeout),
        ))
        claimed = cur.rowcount == 1
        conn.commit()
        with _LOCK:
            _STATS["reads"] += 1
            _STATS["probes_claimed" if claimed else "probes_denied"] += 1
        if claimed:
            # Mirror the claim so that `record_failure` on this worker knows it
            # was a probe and restarts the cooldown rather than letting the
            # expired timestamp admit the next caller immediately.
            with _LOCK:
                bucket = _local_bucket(provider)
                bucket["probe_owner"] = _WORKER_ID
                bucket["probe_started_at"] = now
                if not bucket["opened_at"]:
                    bucket["opened_at"] = now
            log.info("UNDX breaker half-open probe claimed provider=%s owner=%s",
                     provider, _WORKER_ID)
        return not claimed
    except Exception as exc:  # noqa: BLE001
        _note_error(exc, "read_failures")
        return None
    finally:
        _close(conn)


def _should_skip_local(provider: str, probe_timeout: float) -> bool:
    now = time.time()
    with _LOCK:
        bucket = _local.get(provider)
        if not bucket or not bucket["opened_at"]:
            return False
        if now - bucket["opened_at"] < cooldown_seconds():
            return True
        if bucket["probe_owner"] and now - bucket["probe_started_at"] < probe_timeout:
            return True
        bucket["probe_owner"] = _WORKER_ID
        bucket["probe_started_at"] = now
        return False


# ------------------------------------------------------------------- reporting

def snapshot() -> dict[str, dict[str, Any]]:
    """Every provider this deployment has observed, with its §19 state.

    `distributed` is on every entry and is not decoration: a caller that cannot
    tell a deployment-wide answer from one worker's guess will read a degraded
    answer as an authoritative one, which is how the original per-process
    breaker looked correct for as long as it did.
    """
    rows: dict[str, dict[str, Any]] = {}
    distributed = False
    if shared_enabled():
        conn = None
        try:
            _ensure_schema_once()
            conn = _connect()
            cur = conn.cursor()
            cur.execute(_SELECT_ALL)
            for row in cur.fetchall() or []:
                record = _row_to_dict(row)
                rows[record["provider"]] = record
            distributed = True
            with _LOCK:
                _STATS["reads"] += 1
        except Exception as exc:  # noqa: BLE001
            _note_error(exc, "read_failures")
        finally:
            _close(conn)
    if not distributed:
        with _LOCK:
            rows = {name: dict(bucket) for name, bucket in _local.items()}

    now = time.time()
    cooldown = cooldown_seconds()
    out: dict[str, dict[str, Any]] = {}
    for provider, record in rows.items():
        open_for = now - record["opened_at"] if record["opened_at"] else 0.0
        underlying = failure_state(record)
        out[provider] = {
            "state": state_for(record),
            # What is wrong, as distinct from what the router is doing about
            # it. Equal to `state` whenever the circuit is closed.
            "underlying_state": underlying,
            "distributed": distributed,
            "circuit": "open" if record["opened_at"] else "closed",
            "probing": bool(record["probe_owner"]),
            "probe_owner": record["probe_owner"],
            "consecutive_failures": record["consecutive_failures"],
            "successes": record["successes"],
            "failures": record["failures"],
            "last_status": record["last_status"],
            "last_error": record["last_error"],
            "last_success_at": record["last_success_at"],
            "last_failure_at": record["last_failure_at"],
            "cooldown_remaining_s": max(0, int(cooldown - open_for)) if open_for else 0,
            # Keyed off the underlying reason, not the circuit. A breaker that
            # opened on 402 is not going to heal by waiting, and an alert that
            # said otherwise would route the page to the wrong person.
            "actionable": underlying in ACTIONABLE_STATES,
        }
    return out


def stats() -> dict[str, Any]:
    with _LOCK:
        out = dict(_STATS)
    out["shared_enabled"] = shared_enabled()
    out["threshold"] = threshold()
    out["cooldown_s"] = cooldown_seconds()
    out["worker_id"] = _WORKER_ID
    return out


def reset_for_tests() -> None:
    """Test-only. Clears the process mirror **and the shared rows**.

    Clearing only the mirror would have been the natural thing to write and
    would have been wrong in a way worth stating: once the state is shared,
    a per-process reset no longer resets anything, so each test would inherit
    the breaker position the previous test left behind and the failures would
    look like breaker bugs rather than what they are. That the shared store
    needs a shared teardown is the same fact as the module's whole premise,
    arriving from the other side.
    """
    global _schema_ready
    with _LOCK:
        _local.clear()
        for key in ("writes", "write_failures", "reads", "read_failures",
                    "degraded", "probes_claimed", "probes_denied"):
            _STATS[key] = 0
        _STATS["last_error"] = ""
        _schema_ready = False
    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {HEALTH_TABLE}")
        conn.commit()
    except Exception:  # noqa: BLE001 - the table may simply not exist yet
        pass
    finally:
        _close(conn)


def rewind_for_tests(provider: str, seconds: float,
                     columns: tuple[str, ...] = ("opened_at", "probe_started_at")) -> None:
    """Test-only. Move a provider's timestamps back, in the mirror and the store.

    Tests that exercise cooldown expiry and probe abandonment previously
    reached into `undx_router._health_state` and subtracted from `opened_at`
    directly. That no longer works, and the reason is the point of the module:
    the timestamp the breaker reads is not in this process any more.

    Shifting the clock backwards rather than mocking `time.time` keeps the test
    exercising the same comparison the production path does, including inside
    the SQL, which is where the cooldown and lease arithmetic actually happens.
    """
    provider = (provider or "").strip().lower()
    with _LOCK:
        bucket = _local.get(provider)
        if bucket:
            for column in columns:
                if bucket.get(column):
                    bucket[column] -= seconds
    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        assignments = ", ".join(
            f"{c} = CASE WHEN {c} > 0 THEN {c} - ? ELSE 0 END" for c in columns)
        cur.execute(f"UPDATE {HEALTH_TABLE} SET {assignments} WHERE provider = ?",
                    tuple([seconds] * len(columns)) + (provider,))
        conn.commit()
    except Exception:  # noqa: BLE001 - the row may not exist yet
        pass
    finally:
        _close(conn)


def _close(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # pragma: no cover
        pass


__all__ = [
    "HEALTHY", "DEGRADED", "RATE_LIMITED", "UNAVAILABLE", "AUTH_FAILED",
    "BILLING_FAILED", "MODEL_RETIRED", "CIRCUIT_OPEN", "UNKNOWN",
    "HEALTH_STATES", "ACTIONABLE_STATES", "HEALTH_ENV_VARS", "HEALTH_TABLE",
    "DEFAULT_BREAKER_THRESHOLD", "DEFAULT_BREAKER_COOLDOWN_SECONDS",
    "classify_failure", "state_for", "failure_state", "shared_enabled", "threshold",
    "cooldown_seconds", "ensure_schema", "record_success", "record_failure",
    "read", "is_open", "should_skip", "snapshot", "stats", "reset_for_tests",
    "worker_id",
]
