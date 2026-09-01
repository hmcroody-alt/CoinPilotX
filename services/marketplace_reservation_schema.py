"""Stage 176B — the canonical owner of the reservation lifecycle schema.

Why this module exists
----------------------
The expiry sweep shipped with a hidden dependency that only production could
reveal. The lifecycle columns it reads — ``expires_at`` above all — were created
by ``marketplace_cart_routes._ensure_schema``, which is reached from cart route
handlers and nowhere else. On a single-process deployment that is invisible:
somebody adds an item to a cart, the columns appear, the sweep works. On the
real deployment the sweeper runs in ``pulse_worker``, a separate process on a
separate Railway service, and it never serves an HTTP request. So the dependency
read::

    a buyer opens a cart  →  columns exist  →  the sweeper works

and until a buyer did, every sweep cycle died on::

    psycopg2.errors.UndefinedColumn: column r.expires_at does not exist

An inventory leak whose repair is contingent on the traffic it is supposed to be
independent of is not a repair. A durable worker has to be bootable against the
production database on its own.

The rule this module enforces
-----------------------------
There is exactly one copy of the reservation DDL, and everything that needs the
schema — cart routes, the sweeper worker, any future admin or manual recovery
path — calls the same function. Two copies of an ``ALTER TABLE ... ADD COLUMN``
list is how the two callers drift, and drift here is invisible until the day one
of them queries a column the other never added.

What "ensure" guarantees
------------------------
``ensure_reservation_schema`` is idempotent, non-destructive, safe to call
concurrently from the web process and the worker at the same instant, and safe
on both engines (SQLite in tests, PostgreSQL in production). It only ever
creates: a table if absent, nullable columns if absent, an index if absent. It
never drops, never rewrites and never backfills.

It also never raises. The worker loop calling it must survive a database that is
locked, a role without ``ALTER`` permission, or a replica that has not caught up
— so the failure is returned as data, the caller degrades, and the next
maintenance interval tries again. A schema helper that can take down the loop
that calls it would have replaced one leak with another.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

RESERVATION_TABLE = "marketplace_inventory_reservations"

#: The table as it originally shipped. Kept here rather than in the cart module
#: so that a process which has never served a cart request can still create it.
#: ``INTEGER PRIMARY KEY AUTOINCREMENT`` is rewritten to ``SERIAL PRIMARY KEY``
#: by ``services.db._translate_create_table`` on PostgreSQL, which is why the
#: SQLite spelling is safe to send on both engines.
RESERVATION_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {RESERVATION_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_transaction_id INTEGER UNIQUE,
    buyer_user_id INTEGER,
    listing_id INTEGER,
    quantity INTEGER DEFAULT 1,
    status TEXT DEFAULT 'held',
    created_at TEXT,
    updated_at TEXT
)
"""

# Lifecycle columns added after the table shipped. There is no migration
# framework in this repo (schema is created imperatively and must be
# idempotent), so they are added defensively rather than by editing the DDL
# above — editing it would only affect fresh databases and would silently skip
# production, which already has the table.
RESERVATION_LIFECYCLE_COLUMNS = (
    # When the hold was taken. Distinct from created_at, which a future
    # re-reservation on the same transaction would not update.
    ("reserved_at", "TEXT"),
    # The durable deadline. This is what makes expiry authoritative rather
    # than dependent on a client callback that may never arrive.
    ("expires_at", "TEXT"),
    # Terminal timestamps, kept separate so an audit can tell a capture from a
    # release without reading the status string.
    ("released_at", "TEXT"),
    ("captured_at", "TEXT"),
    # Why it was released — see marketplace_reservation_policy.RELEASE_REASONS.
    ("release_reason", "TEXT"),
    # Set when a release was deferred because Stripe said the intent was still
    # live. Lets the sweeper back off without losing the row.
    ("reconciled_at", "TEXT"),
    # How many consecutive sweeps have deferred this reservation. The
    # reconciler's deferral bound is a *count*, and without somewhere durable to
    # keep it every sweep would start again at zero — which would make
    # `requires_action` defer forever and never reach the
    # `buyer_never_completed` release. The bound would exist in the decision
    # table and be unreachable in production.
    ("reconcile_deferrals", "INTEGER"),
)

RESERVATION_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_mkt_reservations_status_expires "
    f"ON {RESERVATION_TABLE} (status, expires_at)"
)

#: Columns without which the sweep cannot select a candidate at all. Absent any
#: one of these there is no honest answer to "which holds have expired", so the
#: sweep must report that it could not look rather than report that it looked
#: and found nothing. Those two outcomes are identical in every counter and
#: opposite in meaning.
REQUIRED_SWEEP_COLUMNS = ("status", "expires_at", "seller_transaction_id",
                          "listing_id", "quantity")

#: Columns the sweep uses when present and does without when absent. These are
#: backoff bookkeeping: without them every deferred row is re-read on every
#: cycle, which is wasteful but not wrong.
OPTIONAL_SWEEP_COLUMNS = ("reconciled_at", "reconcile_deferrals")

STATUS_READY = "ready"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"

REASON_SCHEMA_MISSING = "schema_missing"
REASON_SCHEMA_ENSURE_FAILED = "schema_ensure_failed"


class ReservationSchemaMissing(RuntimeError):
    """The lifecycle columns the sweep needs are not on the table.

    Carried as a dedicated type rather than a bare ``Exception`` so the sweep
    can tell "this database cannot be swept yet" apart from "this query broke",
    and report the first as a migration problem instead of burying it in a
    generic failure counter.
    """

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__(
            f"{RESERVATION_TABLE} is missing lifecycle column(s): "
            + ", ".join(self.missing)
        )


# --------------------------------------------------------------------------
# Caches
# --------------------------------------------------------------------------
# Two separate caches with two different jobs. ``_SCHEMA_READY`` short-circuits
# the whole ensure once it has succeeded in this process, so a worker running
# every five minutes does not re-issue DDL forever. ``_COLUMN_CACHE`` answers
# "what shape is this table" for the query builder without a round trip.
#
# Both are deliberately *not* set when ensure fails. A process that could not
# migrate must try again on its next interval rather than caching a broken
# answer for its whole lifetime — that is the difference between an outage that
# heals when the lock clears and one that needs a restart.

_SCHEMA_READY = False
_COLUMN_CACHE: set[str] | None = None


def reset_schema_cache() -> None:
    """Forget both caches. For tests, and for any caller that changed the table."""
    global _SCHEMA_READY, _COLUMN_CACHE
    _SCHEMA_READY = False
    _COLUMN_CACHE = None


def reservation_columns(cur, *, refresh: bool = False) -> set[str]:
    """Column names actually present on the reservations table.

    Raises rather than swallowing: the two callers want different behaviour on
    an introspection failure — ``ensure_reservation_schema`` reports it as a
    structured error, the query builder treats it as "unknown shape, do not
    guess" — and a helper that returns an empty set for both would make an
    unreachable database indistinguishable from a table with no columns.
    """
    global _COLUMN_CACHE
    if _COLUMN_CACHE is not None and not refresh:
        return _COLUMN_CACHE
    from services import db as db_module

    _COLUMN_CACHE = db_module.get_table_columns(cur, RESERVATION_TABLE)
    return _COLUMN_CACHE


def missing_sweep_columns(columns) -> tuple[str, ...]:
    present = set(columns or ())
    return tuple(name for name in REQUIRED_SWEEP_COLUMNS if name not in present)


def _result(status: str, *, columns=(), missing=(), added=(),
            table_created: bool = False, error: str | None = None) -> dict:
    return {
        "status": status,
        "columns": sorted(columns),
        "missing": list(missing),
        "added": list(added),
        "table_created": bool(table_created),
        "error": error,
    }


def ensure_reservation_schema(cur, *, force: bool = False) -> dict:
    """Create the reservation table, its lifecycle columns and its index.

    Returns a structured result; never raises. ``status`` is one of:

    ``ready``    every required column is present — the sweep may run.
    ``missing``  the ensure completed but required columns are still absent,
                 e.g. the role cannot ``ALTER``. The sweep must not run.
    ``error``    the ensure itself failed — locked, unreachable, permissions.
                 The sweep must not run and the next interval retries.

    ``force`` bypasses the process cache. Used by tests and by any caller that
    has reason to believe the table changed underneath it.

    On the cost of calling this every cycle: after the first success this is a
    single dictionary read. Before it, it is one introspection query plus at
    most seven ``ADD COLUMN`` statements on a table that is small by
    construction. Neither is a migration in the expensive sense, which is what
    makes it safe to leave in the sweep's path rather than only at boot.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return _result(STATUS_READY, columns=_COLUMN_CACHE or ())

    table_created = False
    try:
        cur.execute(RESERVATION_TABLE_DDL)
        table_created = True
    except Exception as exc:
        # Not fatal on its own: the overwhelmingly common case in production is
        # that the table already exists and this is a no-op, and a `CREATE TABLE
        # IF NOT EXISTS` that raises anyway (a concurrent creator, a permission
        # that allows ALTER but not CREATE) should still let the column check
        # below decide whether the schema is usable.
        LOGGER.warning("RESERVATION_SCHEMA_TABLE_DDL_FAILED error=%s", exc)

    try:
        existing = reservation_columns(cur, refresh=True)
    except Exception as exc:
        LOGGER.exception("RESERVATION_SCHEMA_ENSURE_FAILED stage=introspect")
        return _result(STATUS_ERROR, error=str(exc)[:500])

    added = []
    for column, definition in RESERVATION_LIFECYCLE_COLUMNS:
        if column in existing:
            continue
        try:
            cur.execute(
                f"ALTER TABLE {RESERVATION_TABLE} ADD COLUMN {column} {definition}")
            added.append(column)
        except Exception:
            # A concurrent worker may have added it between the introspection
            # and here — the web process and this one can ensure at the same
            # instant, and losing that race is the correct outcome, not an
            # error. Anything else (no ALTER grant, a lock) is logged and shows
            # up below as a column that is still missing.
            LOGGER.exception("RESERVATION_COLUMN_ADD_FAILED column=%s", column)

    # The sweeper's only query shape is (status, expires_at). Without this it
    # degrades to a full scan of every reservation ever taken.
    try:
        cur.execute(RESERVATION_INDEX_DDL)
    except Exception as exc:
        # Non-fatal by design: a missing index makes the sweep slow, a missing
        # column makes it impossible. Only the second one blocks.
        LOGGER.warning("RESERVATION_INDEX_CREATE_FAILED error=%s", exc)

    if added:
        try:
            existing = reservation_columns(cur, refresh=True)
        except Exception as exc:
            LOGGER.exception("RESERVATION_SCHEMA_ENSURE_FAILED stage=verify")
            return _result(STATUS_ERROR, added=added, error=str(exc)[:500])

    missing = missing_sweep_columns(existing)
    if missing:
        LOGGER.error(
            "RESERVATION_SCHEMA_MISSING table=%s missing=%s present=%s added=%s",
            RESERVATION_TABLE, ",".join(missing), len(existing), ",".join(added))
        return _result(STATUS_MISSING, columns=existing, missing=missing,
                       added=added, table_created=table_created)

    _SCHEMA_READY = True
    LOGGER.info(
        "RESERVATION_SCHEMA_READY table=%s columns=%s added=%s optional_present=%s",
        RESERVATION_TABLE, len(existing), ",".join(added) or "-",
        ",".join(name for name in OPTIONAL_SWEEP_COLUMNS if name in existing) or "-")
    return _result(STATUS_READY, columns=existing, added=added,
                   table_created=table_created)
