"""Transactional outbox for portfolio mutations.

Why an outbox rather than a call
--------------------------------
The Capital Graph shows a projection of the member's Portfolio. The Portfolio
is the ledger; the graph is a view of it, and the view has to *follow* the
ledger without ever being able to hold it up. Calling the projector inline
from ``add_portfolio_item`` would couple the member's "Holding added." to the
health of the private-office substrate — a locked graph table would fail a
portfolio write that had nothing wrong with it.

The in-memory alternative (``services.event_bus_engine``) is a deque that dies
with the process, which for a projection means: deploy at the wrong moment and
a sale silently never leaves the graph. The durable precedent in this codebase
is the email outbox — a row written in the same transaction as the thing it
announces, processed after commit, swept later if that fails. This module is
that pattern for portfolio changes.

Why events carry almost nothing
-------------------------------
An event row names the user, the kind of change, and (when known) the item and
symbol. It deliberately does not carry quantities or prices, because the
consumer does not apply deltas — it re-projects the user's whole portfolio
from current state (see ``private_office.portfolio_projection``). A full-state
projection is convergent: processing an event twice, out of order, or after
missing three others all land on the same answer, which is what makes the
idempotency and ordering requirements *structural* instead of promised.
The event's job is only to say "this user's projection is behind", promptly
and durably.

Delivery
--------
Two legs, both required:

* **Post-commit kick** — the mutation path calls the projector after its own
  commit, best-effort. This is what makes the graph feel live.
* **Lazy sweep** — every projection read drains this user's pending rows
  first. This is what makes the graph *correct* when the kick failed, the
  process died, or the flag was off for a while.

A failed enqueue is logged and swallowed: the projection converges on the next
read regardless, and failing the member's portfolio write over its shadow
would invert the authority relationship this design exists to keep.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

LOGGER = logging.getLogger("portfolio_events")

OUTBOX_TABLE = "portfolio_outbox"

EVENT_HOLDING_ADDED = "HOLDING_ADDED"
EVENT_HOLDING_UPDATED = "HOLDING_UPDATED"
EVENT_HOLDING_REMOVED = "HOLDING_REMOVED"
EVENT_BACKFILL = "BACKFILL"

EVENT_TYPES: tuple[str, ...] = (
    EVENT_HOLDING_ADDED,
    EVENT_HOLDING_UPDATED,
    EVENT_HOLDING_REMOVED,
    EVENT_BACKFILL,
)

STATUS_PENDING = "pending"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"

#: One drain may claim this many rows. The consumer re-projects once per user
#: regardless of how many events are pending, so the bound is about how much
#: bookkeeping one read is asked to settle, not about how much work it does.
MAX_DRAIN = 200

OUTBOX_DDL = f"""
CREATE TABLE IF NOT EXISTS {OUTBOX_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    item_id INTEGER NOT NULL DEFAULT 0,
    symbol TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    processed_at TEXT
)
"""

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_portfolio_outbox_pending "
    f"ON {OUTBOX_TABLE} (user_id, status, id)",
)

_SCHEMA_READY = False


def projection_enabled() -> bool:
    """The rollout switch for the whole Portfolio → Capital Graph flow.

    Off means: no events enqueued, no projection runs. The graph then shows
    its last projected state with honest staleness labels rather than a
    half-updating one — degrading to "older but true" instead of "fresh but
    partial".
    """
    return os.getenv("PORTFOLIO_CAPITAL_PROJECTION_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_outbox_schema_cache() -> None:
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_outbox_schema(cur, *, force: bool = False) -> bool:
    """Create the outbox table and index. Returns usability, never raises.

    Failure is not cached (only success sets the flag), so a database that
    heals is noticed on the next call rather than after a restart — the same
    rule ``private_office.schema`` follows, for the same Stage 176B reason.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return True
    try:
        cur.execute(OUTBOX_DDL)
        for statement in INDEX_DDL:
            cur.execute(statement)
    except Exception as exc:  # noqa: BLE001 — a missing outbox degrades, never breaks
        LOGGER.warning("PORTFOLIO_OUTBOX_ENSURE_FAILED error=%s", exc)
        return False
    _SCHEMA_READY = True
    return True


def enqueue(cur, *, user_id: int, event_type: str, item_id: int = 0,
            symbol: str = "") -> bool:
    """Write one pending event on the caller's cursor, inside their transaction.

    Returns whether the row was written. Never raises: the portfolio mutation
    this rides along with must not fail because its shadow could not be cast.
    Because it shares the mutation's transaction, a rolled-back mutation takes
    its event with it — the outbox cannot announce a change that never landed.
    """
    owner = int(user_id or 0)
    kind = str(event_type or "").strip().upper()
    if owner <= 0 or kind not in EVENT_TYPES:
        return False
    if not projection_enabled():
        return False
    if not ensure_outbox_schema(cur):
        return False
    try:
        cur.execute(
            f"INSERT INTO {OUTBOX_TABLE} "
            f"(user_id, event_type, item_id, symbol, status, created_at) "
            f"VALUES (?, ?, ?, ?, ?, ?)",
            (owner, kind, int(item_id or 0),
             str(symbol or "").upper().strip()[:16], STATUS_PENDING, _now_iso()),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("PORTFOLIO_OUTBOX_ENQUEUE_FAILED user=%s error=%s", owner, exc)
        return False


def pending_events(cur, *, user_id: int, limit: int = MAX_DRAIN) -> list[dict]:
    """This user's unprocessed events, oldest first. Owner-scoped by shape."""
    owner = int(user_id or 0)
    if owner <= 0 or not ensure_outbox_schema(cur):
        return []
    bounded = max(1, min(int(limit or MAX_DRAIN), MAX_DRAIN))
    cur.execute(
        f"SELECT * FROM {OUTBOX_TABLE} "
        f"WHERE user_id = ? AND status = ? ORDER BY id LIMIT ?",
        (owner, STATUS_PENDING, bounded),
    )
    return [dict(row) for row in cur.fetchall()]


def _mark(cur, *, user_id: int, event_ids, status: str, error: str = "") -> int:
    owner = int(user_id or 0)
    ids = [int(value) for value in (event_ids or ()) if int(value or 0) > 0]
    if owner <= 0 or not ids or not ensure_outbox_schema(cur):
        return 0
    placeholders = ",".join("?" * len(ids))
    cur.execute(
        f"UPDATE {OUTBOX_TABLE} SET status = ?, attempts = attempts + 1, "
        f"last_error = ?, processed_at = ? "
        f"WHERE user_id = ? AND id IN ({placeholders})",
        (status, str(error or "")[:400], _now_iso(), owner, *ids),
    )
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def mark_processed(cur, *, user_id: int, event_ids) -> int:
    return _mark(cur, user_id=user_id, event_ids=event_ids, status=STATUS_PROCESSED)


def mark_failed(cur, *, user_id: int, event_ids, error: str) -> int:
    """A failed projection leaves the events FAILED but visible, not silently
    pending forever: `sync_status` reports them, and the next successful
    projection run (which reads current portfolio state, not these rows)
    supersedes whatever they announced anyway."""
    return _mark(cur, user_id=user_id, event_ids=event_ids,
                 status=STATUS_FAILED, error=error)


def sync_status(cur, *, user_id: int) -> dict:
    """How far behind this user's projection may be, as data.

    ``pending`` > 0 means a change exists that the graph has not absorbed yet.
    ``failed`` > 0 means a past projection run errored — the graph still
    converges on the next read, but somebody's log has the reason.
    """
    owner = int(user_id or 0)
    empty = {"pending": 0, "failed": 0, "last_event_at": None,
             "last_processed_at": None, "enabled": projection_enabled()}
    if owner <= 0 or not ensure_outbox_schema(cur):
        return empty
    cur.execute(
        f"SELECT status, COUNT(*) AS n, MAX(created_at) AS latest, "
        f"MAX(processed_at) AS done FROM {OUTBOX_TABLE} "
        f"WHERE user_id = ? GROUP BY status",
        (owner,),
    )
    result = dict(empty)
    for row in cur.fetchall():
        data = dict(row)
        status = str(data.get("status") or "")
        if status == STATUS_PENDING:
            result["pending"] = int(data.get("n") or 0)
            result["last_event_at"] = data.get("latest")
        elif status == STATUS_FAILED:
            result["failed"] = int(data.get("n") or 0)
        elif status == STATUS_PROCESSED:
            result["last_processed_at"] = data.get("done")
    return result
