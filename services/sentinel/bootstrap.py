"""Boot-time activation of the Sentinel schema (Stage 2).

Sentinel's 22 tables have never existed in production. ``store.init_db()`` had
no caller anywhere in the repository, so the package's storage layer was
complete and unreachable: every ``events.ingest()`` would have failed against a
live database. This module is the missing call, plus the honesty that has to
come with it.

Three properties matter more than the DDL itself.

**Failure here must not take the platform down.** Sentinel observes; it is not
on the critical path. ``_init_db_impl()`` has no ``except`` around it and is
reached from ordinary route handlers, so an exception raised from a schema
bootstrap would turn "the security package had a bad day" into "the product
returns 500". Every entry point below therefore returns rather than raises.

**But a caught exception must not become silence.** Hard Rule #4 forbids
swallowing errors and forbids reporting UNKNOWN as healthy. So the exception is
caught, scrubbed, counted, timestamped, and exposed through :func:`schema_state`
— a failing bootstrap is *loud and legible*, it simply is not fatal. The
distinction this module is built around: not raising is fine, not knowing is not.

**READY is asserted from observation, not from control flow.** A DDL call that
returns without throwing is weak evidence that the tables exist; on PostgreSQL,
``CREATE TABLE IF NOT EXISTS`` also has a genuine race between concurrent
creators, and the Procfile runs four gunicorn workers that all boot at once. So
:func:`ensure_schema` finishes by *probing* the tables and only reports READY if
the probe passes. That inverts the usual failure mode — a worker that loses the
creation race and raises ``DuplicateTable`` still, correctly, ends up READY,
because the thing we actually care about is true.

State is per-process by construction. Four workers means four independent
answers, which is the truth: this records what *this* process knows.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time

from services.sentinel import store

logger = logging.getLogger(__name__)

#: Bootstrap has not run in this process. Explicitly **not** healthy.
NEVER_ATTEMPTED = "NEVER_ATTEMPTED"
#: Tables were verified present by probe. The only state that permits ingest.
READY = "READY"
#: An attempt ran and the probe did not pass. Explicitly **not** healthy.
FAILED = "FAILED"

#: Probed to decide READY. Not the full set — this is the ingest critical path:
#: the event table, the incident ledger it feeds, and the evidence chain. A
#: partial schema that lacks any of these cannot honestly serve ingest.
REQUIRED_TABLES = ("sentinel_events", "sentinel_incidents", "sentinel_evidence")

#: Minimum gap between attempts, so a caller may retry freely without
#: hammering a database that is already unwell.
RETRY_COOLDOWN_SECONDS = 60.0

_LOCK = threading.Lock()
_STATE: dict = {
    "state": NEVER_ATTEMPTED,
    "attempts": 0,
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": "",
    "last_error_type": "",
    "statements_executed": 0,
    "missing_tables": list(REQUIRED_TABLES),
}

# Credentials reach exception text easily: a psycopg2 connection failure quotes
# the DSN. This state is rendered into health output, so scrub before storing —
# not before logging, before *storing*, so there is no window where the raw
# string sits in memory we later serialise.
_URL_CREDENTIALS = re.compile(r"://[^/\s:@]+(:[^/\s@]*)?@")
_SCRUBBED_MESSAGE_LIMIT = 300


def _safe_error(exc: BaseException) -> str:
    """Exception text with URL credentials removed and length bounded."""
    text = _URL_CREDENTIALS.sub("://***:***@", str(exc))
    return text[:_SCRUBBED_MESSAGE_LIMIT]


def _missing_tables(conn=None) -> list[str]:
    """Names from :data:`REQUIRED_TABLES` that are not queryable.

    Probes with ``SELECT ... LIMIT 0`` rather than reading a catalog, because
    the catalog can disagree with what this connection may actually read
    (search_path, permissions, a table that exists but is not visible). The
    question worth answering is "can ingest use this?", and the only honest way
    to ask it is to try.

    Each probe gets its own connection: on PostgreSQL a failed statement aborts
    the surrounding transaction, so sharing one would make the first miss
    poison every probe after it and over-report the damage.
    """
    missing: list[str] = []
    for table in REQUIRED_TABLES:
        probe = conn
        owned = probe is None
        try:
            if owned:
                probe = store.platform_db.connect()
            cur = probe.cursor()
            cur.execute(f"SELECT 1 FROM {table} LIMIT 0")  # nosec B608: literal from REQUIRED_TABLES
        except Exception:
            missing.append(table)
        finally:
            if owned and probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass
    return missing


def ensure_schema(conn=None, *, force: bool = False) -> str:
    """Create and then verify Sentinel's tables. Never raises.

    Returns the resulting state: :data:`READY` or :data:`FAILED`.

    Idempotent and safe to call repeatedly — once READY it is a no-op, and
    after a failure it will not retry within :data:`RETRY_COOLDOWN_SECONDS`.
    Pass ``force=True`` to override both (tests, and an operator-triggered
    repair).
    """
    with _LOCK:
        if not force:
            if _STATE["state"] == READY:
                return READY
            last = _STATE["last_attempt_at"]
            if last is not None and (time.time() - last) < RETRY_COOLDOWN_SECONDS:
                return _STATE["state"]

        _STATE["attempts"] += 1
        _STATE["last_attempt_at"] = time.time()
        attempt = _STATE["attempts"]

        executed = 0
        ddl_error = ""
        ddl_error_type = ""
        try:
            executed = store.ensure_schema(conn)
            if conn is not None:
                # store.ensure_schema only commits the connection it opened
                # itself. Handed one, it leaves the DDL uncommitted — which on
                # PostgreSQL is worse than it sounds: the transaction still
                # holds catalog locks, so the very next connection that tries
                # to create the same tables blocks on it, and the DDL is rolled
                # back at teardown anyway. Commit here so a caller-supplied
                # connection behaves the same as an owned one.
                conn.commit()
        except Exception as exc:
            # Deliberately not re-raised, and deliberately not the end of the
            # story: a lost CREATE race still leaves the tables present, so the
            # probe below — not this exception — decides the outcome.
            ddl_error = _safe_error(exc)
            ddl_error_type = type(exc).__name__
            logger.warning(
                "SENTINEL_SCHEMA_DDL_FAILED attempt=%s type=%s error=%s",
                attempt, ddl_error_type, ddl_error,
            )

        try:
            missing = _missing_tables(conn)
        except Exception as exc:  # pragma: no cover - probe itself is defensive
            missing = list(REQUIRED_TABLES)
            ddl_error = ddl_error or _safe_error(exc)
            ddl_error_type = ddl_error_type or type(exc).__name__

        _STATE["statements_executed"] = executed
        _STATE["missing_tables"] = missing

        if missing:
            _STATE["state"] = FAILED
            _STATE["last_error"] = ddl_error or f"tables not queryable: {', '.join(missing)}"
            _STATE["last_error_type"] = ddl_error_type or "SchemaIncomplete"
            logger.error(
                "SENTINEL_SCHEMA_UNAVAILABLE attempt=%s missing=%s error=%s",
                attempt, ",".join(missing), _STATE["last_error"],
            )
            return FAILED

        _STATE["state"] = READY
        _STATE["last_success_at"] = time.time()
        _STATE["last_error"] = ""
        _STATE["last_error_type"] = ""
        if ddl_error:
            # Present but the DDL complained: the normal signature of losing a
            # concurrent CREATE race against a sibling gunicorn worker. Worth a
            # line, not worth a failure.
            logger.info(
                "SENTINEL_SCHEMA_READY_AFTER_DDL_ERROR attempt=%s type=%s error=%s",
                attempt, ddl_error_type, ddl_error,
            )
        else:
            logger.info(
                "SENTINEL_SCHEMA_READY attempt=%s statements=%s", attempt, executed,
            )
        return READY


def schema_ready() -> bool:
    """True only when this process has *verified* the tables exist.

    NEVER_ATTEMPTED is false here, which is the point: absence of evidence is
    not health (Hard Rule #4). Callers gate ingest on this.
    """
    return _STATE["state"] == READY


def schema_state() -> dict:
    """Snapshot for health and evidence surfaces.

    ``healthy`` is READY and nothing else — NEVER_ATTEMPTED and FAILED both
    report false, so a Sentinel that was never switched on cannot be mistaken
    for a Sentinel that is watching.
    """
    snapshot = dict(_STATE)
    snapshot["healthy"] = snapshot["state"] == READY
    snapshot["required_tables"] = list(REQUIRED_TABLES)
    snapshot["missing_tables"] = list(snapshot.get("missing_tables") or [])
    return snapshot


def reset_for_tests() -> None:
    """Restore pristine module state. Test-only."""
    with _LOCK:
        _STATE.update({
            "state": NEVER_ATTEMPTED,
            "attempts": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": "",
            "last_error_type": "",
            "statements_executed": 0,
            "missing_tables": list(REQUIRED_TABLES),
        })


def bootstrap_enabled() -> bool:
    """Whether boot should create the schema.

    Defaults **on**: creating empty, unread tables is inert, and the schema has
    to exist before anything can be observed. What is separately gated, and
    defaults off, is whether the request path *writes* to them — see
    ``SENTINEL_REQUEST_BRIDGE_ENABLED``. Creating storage and filling it are
    different decisions and get different switches.
    """
    raw = os.getenv("SENTINEL_SCHEMA_BOOTSTRAP_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}
