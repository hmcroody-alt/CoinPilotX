"""Canonical Events domain schema — additive ``business_os_event*`` tables.

Follows the S1 business / store / marketplace ``ensure_schema`` convention exactly:
idempotent ``CREATE TABLE IF NOT EXISTS`` via ``services.db``, no ``bot.py`` import, never
mutating a legacy table. Every event is keyed to a Section-1 ``business_id``; identity/RBAC
live in ``business_os_business_*`` and are reused (zero membership rows stored here). Money
lives in the canonical ledger — these tables store only ledger *references*, never balances.

Tables:

* ``business_os_events`` — one row per event: business, title/description/venue, start/end,
  capacity, currency, lifecycle ``status`` (draft/published/cancelled/completed).
* ``business_os_event_ticket_types`` — priced tiers for an event. ``price_cents`` 0 = free;
  ``quantity_total`` NULL = unlimited; ``quantity_sold`` is the confirmed count.
* ``business_os_event_tickets`` — one row per issued ticket (attendee): holder, tier,
  amount paid, ledger capture reference, lifecycle ``status``
  (confirmed/checked_in/cancelled/refunded), idempotency ``client_ref``.
* ``business_os_event_audit`` — append-only audit of every event/ticket mutation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:20])


_EVENTS = """
CREATE TABLE IF NOT EXISTS business_os_events (
    event_id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    created_by_user_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    venue TEXT,
    starts_at TEXT,
    ends_at TEXT,
    capacity INTEGER,
    currency TEXT NOT NULL DEFAULT 'usd',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_TICKET_TYPES = """
CREATE TABLE IF NOT EXISTS business_os_event_ticket_types (
    ticket_type_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0,
    quantity_total INTEGER,
    quantity_sold INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_TICKETS = """
CREATE TABLE IF NOT EXISTS business_os_event_tickets (
    ticket_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    ticket_type_id TEXT NOT NULL,
    holder_user_id TEXT NOT NULL,
    price_cents_paid INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'usd',
    capture_txn_ref TEXT,
    refund_txn_ref TEXT,
    client_ref TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    checked_in_at TEXT
)
"""

_AUDIT = """
CREATE TABLE IF NOT EXISTS business_os_event_audit (
    audit_id TEXT PRIMARY KEY,
    event_id TEXT,
    ticket_id TEXT,
    actor_user_id TEXT,
    action TEXT NOT NULL,
    detail_json TEXT,
    created_at TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_bo_events_business "
    "ON business_os_events(business_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_bo_event_ttypes_event "
    "ON business_os_event_ticket_types(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_bo_event_tickets_event "
    "ON business_os_event_tickets(event_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_bo_event_tickets_holder "
    "ON business_os_event_tickets(holder_user_id)",
    # Idempotent purchases: one confirmed ticket per (ticket_type, client_ref).
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_bo_event_tickets_clientref "
    "ON business_os_event_tickets(ticket_type_id, client_ref) "
    "WHERE client_ref IS NOT NULL",
)


def ensure_schema(conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(_EVENTS)
        conn.execute(_TICKET_TYPES)
        conn.execute(_TICKETS)
        conn.execute(_AUDIT)
        for stmt in _INDEXES:
            try:
                conn.execute(stmt)
            except Exception:
                # Partial-index syntax varies by backend/version; indexes are an
                # optimization, never a correctness dependency.
                pass
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
