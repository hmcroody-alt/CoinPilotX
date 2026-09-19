"""Post-purchase Marketplace fulfillment state, and who is allowed to move it.

Distinct from :mod:`services.marketplace_fulfillment`, which is the *pre*-purchase
taxonomy — what a listing is and what a buyer must type to order it. This module
begins where that one ends: the money has been taken, and the question is whether
the order has actually reached the buyer.

It exists because nothing else answered that question. ``seller_transactions``
and ``marketplace_orders`` carry payment state only, and
``marketplace_commercial_settlements.delivered_at`` was read in two places but
written in none, because :func:`marketplace_settlement_service.mark_delivered`
had no caller. Delivery was a fact the system already depended on and never
recorded.

**The seller cannot release the money.** That is the whole shape of this module
and the reason the authority map, not the transition graph, is the source of
truth: :data:`ALLOWED_TRANSITIONS` is *derived* from
:data:`TRANSITION_AUTHORITY`, so a transition cannot be added without deciding
who may drive it. A seller may say *shipped*; only the buyer, the carrier, an
admin, or the timeout sweeper may say *delivered*. Local pickup has no carrier
and no sweeper at all — there is no third party to corroborate a handover, so a
timeout there would be the seller's own word wearing a different hat.

State is kept in its own table keyed on ``seller_transaction_id`` rather than as
columns on ``seller_transactions``, which also carries Premium, courses, ads,
lessons and live classes; a ``tracking_reference`` column there would attach to
every Premium subscription ever sold.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from services import db

PAID = "paid"
PROCESSING = "processing"
SHIPPED = "shipped"
READY_FOR_PICKUP = "ready_for_pickup"
DELIVERED = "delivered"
PICKED_UP = "picked_up"
COMPLETED = "completed"
CANCELLED = "cancelled"

STATES = frozenset({PAID, PROCESSING, SHIPPED, READY_FOR_PICKUP,
                    DELIVERED, PICKED_UP, COMPLETED, CANCELLED})

#: The states that mean the goods reached the buyer, and so the states that
#: start the settlement protection window.
DELIVERING_STATES = frozenset({DELIVERED, PICKED_UP})

SELLER = "seller"
BUYER = "buyer"
CARRIER = "carrier"
ADMIN = "admin"
SYSTEM = "system"

ACTOR_ROLES = frozenset({SELLER, BUYER, CARRIER, ADMIN, SYSTEM})

#: Every legal move, and the roles permitted to make it. Read the ``delivered``
#: and ``picked_up`` rows first — they are the point of the file.
TRANSITION_AUTHORITY: dict[tuple[str, str], frozenset[str]] = {
    (PAID, PROCESSING): frozenset({SELLER, ADMIN}),
    (PAID, SHIPPED): frozenset({SELLER, ADMIN}),
    (PAID, READY_FOR_PICKUP): frozenset({SELLER, ADMIN}),
    (PROCESSING, SHIPPED): frozenset({SELLER, ADMIN}),
    (PROCESSING, READY_FOR_PICKUP): frozenset({SELLER, ADMIN}),

    # No SELLER. A seller pressing "delivered" would be releasing their own
    # money on their own say-so, which is the one thing this module is for.
    (SHIPPED, DELIVERED): frozenset({BUYER, CARRIER, ADMIN, SYSTEM}),
    # No SELLER and no SYSTEM: a pickup has no carrier to corroborate it, so an
    # unattended timeout out of this state would be the seller's word by
    # another name. A pickup that the buyer never confirms stays here until an
    # admin or the buyer resolves it.
    (READY_FOR_PICKUP, PICKED_UP): frozenset({BUYER, ADMIN}),

    (DELIVERED, COMPLETED): frozenset({BUYER, ADMIN, SYSTEM}),
    (PICKED_UP, COMPLETED): frozenset({BUYER, ADMIN, SYSTEM}),

    (PAID, CANCELLED): frozenset({SELLER, BUYER, ADMIN}),
    (PROCESSING, CANCELLED): frozenset({SELLER, BUYER, ADMIN}),
    (READY_FOR_PICKUP, CANCELLED): frozenset({SELLER, BUYER, ADMIN}),
    # Once it is in transit neither party can unilaterally undo it.
    (SHIPPED, CANCELLED): frozenset({ADMIN}),
}

#: Derived, never hand-written: a move with no authority row does not exist.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    state: frozenset(to for (frm, to) in TRANSITION_AUTHORITY if frm == state)
    for state in STATES
}

#: The pre-purchase kinds that produce a parcel. Only these may take the
#: ``shipped`` lane, because only they have a carrier — and the shipped lane is
#: the one with an unattended timeout on the end of it. A digital download sent
#: down it would collect a made-up tracking number and then auto-confirm its own
#: delivery on a clock. Everything else hands over in person or online and waits
#: for the buyer to say so.
SHIPPING_KINDS = frozenset({"shipping"})

AUTO_DELIVER_HOURS_ENV_VAR = "MARKETPLACE_AUTO_DELIVER_AFTER_SHIPPED_HOURS"
AUTO_COMPLETE_HOURS_ENV_VAR = "MARKETPLACE_AUTO_COMPLETE_AFTER_DELIVERY_HOURS"


class FulfillmentError(ValueError):
    """Carries a stable ``code`` so a route can answer without rephrasing."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


NOT_FOUND = "FULFILLMENT_NOT_FOUND"
ILLEGAL_TRANSITION = "FULFILLMENT_ILLEGAL_TRANSITION"
ACTOR_NOT_AUTHORIZED = "FULFILLMENT_ACTOR_NOT_AUTHORIZED"
TRACKING_REQUIRED = "FULFILLMENT_TRACKING_REQUIRED"
LANE_MISMATCH = "FULFILLMENT_LANE_MISMATCH"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat()


def _hours(name: str) -> int:
    """Read per call. A module-level read freezes the answer at first import.

    Zero, unset, or anything unparseable means *never advance on its own*: the
    timeout is the only transition in this module with no human behind it, so
    the value that a misconfigured deployment falls back to has to be the one
    that does nothing.
    """
    try:
        hours = int(str(os.getenv(name, "") or "").strip() or 0)
    except ValueError:
        return 0
    return hours if hours > 0 else 0


SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS marketplace_order_fulfillment (
        seller_transaction_id INTEGER PRIMARY KEY,
        order_id TEXT,
        seller_id TEXT NOT NULL,
        buyer_user_id INTEGER,
        fulfillment_kind TEXT NOT NULL,
        state TEXT NOT NULL,
        carrier TEXT,
        tracking_reference TEXT,
        tracking_url TEXT,
        shipped_at TEXT,
        ready_at TEXT,
        delivered_at TEXT,
        completed_at TEXT,
        cancelled_at TEXT,
        auto_advance_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS marketplace_order_fulfillment_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_transaction_id INTEGER NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        from_state TEXT,
        to_state TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_mkt_fulfillment_seller "
    "ON marketplace_order_fulfillment(seller_id, state)",
    # The sweeper's only query. Without this it is a full scan of every order
    # ever placed, run on a schedule, forever.
    "CREATE INDEX IF NOT EXISTS idx_mkt_fulfillment_auto "
    "ON marketplace_order_fulfillment(auto_advance_at)",
)

_SCHEMA_READY = False


def create_schema(cur) -> None:
    """The statements themselves, so a caller with a cursor can apply them.

    Exists so tests build the real tables rather than a hand-copied imitation
    that drifts from this file the first time a column is added.
    """
    for statement in SCHEMA_STATEMENTS:
        cur.execute(statement)


def ensure_schema() -> None:
    """Owns its connection on purpose.

    Passing a caller's connection in would leave the DDL uncommitted inside the
    caller's transaction; on Postgres the next connection then blocks on the
    uncommitted catalog lock and the route hangs. This one commits, and only
    caches the flag afterwards so a rolled-back create is not remembered as
    applied.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    conn = db.connect()
    try:
        create_schema(conn)
        conn.commit()
        _SCHEMA_READY = True
    finally:
        conn.close()


def _row(value) -> dict | None:
    return dict(value) if value is not None else None


def open_fulfillment(cur, *, seller_transaction_id: Any, seller_id: Any,
                     fulfillment_kind: str, order_id: str = "",
                     buyer_user_id: Any = None) -> dict | None:
    """Record that a paid order now owes the buyer something. Idempotent.

    Called from inside the checkout transaction, so it takes a cursor and never
    commits: an order that was recorded as paid but not as owed would be
    invisible to every seller dashboard and every sweeper.
    """
    now = _stamp(_now())
    cur.execute(
        "INSERT OR IGNORE INTO marketplace_order_fulfillment "
        "(seller_transaction_id, order_id, seller_id, buyer_user_id, fulfillment_kind, "
        " state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (int(seller_transaction_id), str(order_id or ""), str(seller_id or ""),
         int(buyer_user_id) if buyer_user_id is not None else None,
         str(fulfillment_kind or ""), PAID, now, now))
    return get_fulfillment(cur, seller_transaction_id)


def open_from_transaction(cur, tx: Mapping[str, Any]) -> dict | None:
    """Open the record for a ``seller_transactions`` row, or do nothing.

    Marketplace goods only: that table also carries Premium, courses, ads,
    lessons and live classes, and none of those owe anybody a parcel.

    The kind is read back out of the transaction's frozen metadata rather than
    from the listing, because a listing offering both lanes resolves to
    ``shipping_or_pickup`` and only the buyer's answer at checkout narrowed it —
    re-deriving would recover the ambiguity instead of the choice.
    """
    tx = dict(tx or {})
    if str(tx.get("item_type") or "") != "marketplace_product":
        return None
    transaction_id = tx.get("id") or tx.get("seller_transaction_id")
    if transaction_id is None:
        return None
    from services import marketplace_fulfillment
    # `order_kind` reads a parsed mapping and silently answers "" for a string,
    # so the raw column has to be decoded here rather than passed through.
    raw = tx.get("metadata_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    kind = marketplace_fulfillment.order_kind(raw if isinstance(raw, dict) else {})
    return open_fulfillment(
        cur, seller_transaction_id=transaction_id,
        seller_id=tx.get("seller_user_id"), buyer_user_id=tx.get("buyer_user_id"),
        fulfillment_kind=kind, order_id=str(tx.get("order_id") or ""))


def get_fulfillment(cur, seller_transaction_id: Any) -> dict | None:
    cur.execute("SELECT * FROM marketplace_order_fulfillment WHERE seller_transaction_id=?",
                (int(seller_transaction_id),))
    return _row(cur.fetchone())


def fulfillment_map(cur, transaction_ids: Iterable[Any]) -> dict[int, dict]:
    ids = sorted({int(value) for value in transaction_ids if value is not None})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    cur.execute(f"SELECT * FROM marketplace_order_fulfillment "
                f"WHERE seller_transaction_id IN ({marks})", tuple(ids))
    return {int(row["seller_transaction_id"]): dict(row) for row in cur.fetchall()}


def role_of(record: Mapping[str, Any], user_id: Any) -> str:
    """Which party a signed-in user is on this order, from the record itself.

    Routes resolve the role here rather than asserting one, so a seller-facing
    endpoint cannot hand :func:`transition` the string ``"buyer"`` and walk
    straight past the authority map.
    """
    if user_id is None:
        return ""
    user = str(user_id).strip()
    if not user:
        return ""
    if record.get("buyer_user_id") is not None and str(record["buyer_user_id"]) == user:
        return BUYER
    if str(record.get("seller_id") or "") == user:
        return SELLER
    return ""


def _auto_advance_at(to_state: str, now: datetime) -> str | None:
    if to_state == SHIPPED:
        hours = _hours(AUTO_DELIVER_HOURS_ENV_VAR)
    elif to_state in DELIVERING_STATES:
        hours = _hours(AUTO_COMPLETE_HOURS_ENV_VAR)
    else:
        return None
    if not hours:
        return None
    # `picked_up` reaches here for the *completion* timeout only. Nothing
    # schedules an automatic move *into* a pickup — see TRANSITION_AUTHORITY.
    return _stamp(now + timedelta(hours=hours))


def transition(cur, seller_transaction_id: Any, to_state: str, *, actor_role: str,
               actor: str, idempotency_key: str, reason: str = "",
               carrier: str = "", tracking_reference: str = "",
               tracking_url: str = "") -> dict:
    """Move one order, inside the caller's transaction.

    Returns ``{"fulfillment": row, "duplicate": bool, "settles_delivery": bool}``.

    ``settles_delivery`` is the caller's instruction to invoke
    :func:`settle_delivery` *after* committing. That call opens its own
    connection, so making it from in here would have it read a delivery that
    has not landed yet and, on Postgres, block on this transaction's own locks.
    """
    if actor_role not in ACTOR_ROLES:
        raise FulfillmentError(ACTOR_NOT_AUTHORIZED, "unknown actor role")
    if to_state not in STATES:
        raise FulfillmentError(ILLEGAL_TRANSITION, f"unknown state: {to_state}")
    if not actor or not idempotency_key:
        raise FulfillmentError(ILLEGAL_TRANSITION, "actor and idempotency key are required")

    cur.execute("SELECT * FROM marketplace_order_fulfillment_events WHERE idempotency_key=?",
                (idempotency_key,))
    if cur.fetchone() is not None:
        return {"fulfillment": get_fulfillment(cur, seller_transaction_id),
                "duplicate": True, "settles_delivery": False}

    record = get_fulfillment(cur, seller_transaction_id)
    if record is None:
        raise FulfillmentError(NOT_FOUND, "this order has no fulfillment record")

    from_state = str(record["state"])
    allowed = TRANSITION_AUTHORITY.get((from_state, to_state))
    if allowed is None:
        raise FulfillmentError(
            ILLEGAL_TRANSITION, f"an order that is {from_state} cannot become {to_state}")
    if actor_role not in allowed:
        raise FulfillmentError(
            ACTOR_NOT_AUTHORIZED,
            f"a {actor_role} may not mark this order {to_state}")

    if to_state == SHIPPED:
        if str(record.get("fulfillment_kind") or "") not in SHIPPING_KINDS:
            raise FulfillmentError(
                LANE_MISMATCH, "this order is not shipped, so it cannot be marked shipped")
        if not str(tracking_reference or "").strip():
            # The shipped state's entire value is that somebody other than the
            # seller can later confirm it. Without a tracking reference there is
            # nothing for a carrier to confirm against, and the order would have
            # to wait on the buyer alone.
            raise FulfillmentError(TRACKING_REQUIRED, "a tracking reference is required to ship")

    now = _now()
    stamp = _stamp(now)
    cur.execute(
        "INSERT INTO marketplace_order_fulfillment_events "
        "(seller_transaction_id, idempotency_key, from_state, to_state, actor_role, actor, reason, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (int(seller_transaction_id), idempotency_key, from_state, to_state,
         actor_role, str(actor), str(reason or ""), stamp))

    columns = {SHIPPED: "shipped_at", READY_FOR_PICKUP: "ready_at",
               DELIVERED: "delivered_at", PICKED_UP: "delivered_at",
               COMPLETED: "completed_at", CANCELLED: "cancelled_at"}
    assignments = ["state=?", "auto_advance_at=?", "updated_at=?"]
    values: list[Any] = [to_state, _auto_advance_at(to_state, now), stamp]
    stamped = columns.get(to_state)
    if stamped:
        # COALESCE so a later correction cannot restart a window that has
        # already begun running against the buyer.
        assignments.append(f"{stamped}=COALESCE({stamped}, ?)")
        values.append(stamp)
    if to_state == SHIPPED:
        assignments += ["carrier=?", "tracking_reference=?", "tracking_url=?"]
        values += [str(carrier or ""), str(tracking_reference or "").strip(), str(tracking_url or "")]
    values.append(int(seller_transaction_id))
    cur.execute(f"UPDATE marketplace_order_fulfillment SET {', '.join(assignments)} "
                f"WHERE seller_transaction_id=?", tuple(values))

    return {"fulfillment": get_fulfillment(cur, seller_transaction_id),
            "duplicate": False,
            "settles_delivery": to_state in DELIVERING_STATES}


def settle_delivery(seller_transaction_id: Any, *, actor: str, idempotency_key: str) -> bool:
    """Tell the settlement layer the goods arrived. Call after the commit.

    This is the production caller ``mark_delivered`` never had: until now the
    protection window opened only in tests, so no settlement could ever reach
    ``eligible`` and no payout could ever be owed.

    Never raises into a route. A settlement that is missing or already past
    ``protection_hold`` is not a fulfillment problem — the buyer's order really
    did arrive, and the record that says so is already committed.
    """
    try:
        from services import marketplace_settlement_service
        marketplace_settlement_service.mark_delivered(
            seller_transaction_id, actor=actor,
            idempotency_key=f"fulfillment:{idempotency_key}")
        return True
    except Exception:  # noqa: BLE001 - settlement state is not the buyer's problem
        return False


def due_for_auto_advance(cur, *, now: datetime | None = None, limit: int = 200) -> list[dict]:
    """Orders whose timeout has expired, oldest first.

    Only ``shipped`` and ``delivered`` can appear here: they are the only states
    :func:`_auto_advance_at` ever stamps. A deployment that has not configured
    the timeouts stamps nothing, so this returns nothing and the sweeper is a
    no-op rather than a silent policy of its own.
    """
    cutoff = _stamp(now or _now())
    cur.execute(
        "SELECT * FROM marketplace_order_fulfillment "
        "WHERE auto_advance_at IS NOT NULL AND auto_advance_at <> '' AND auto_advance_at <= ? "
        "AND state IN (?,?) ORDER BY auto_advance_at LIMIT ?",
        (cutoff, SHIPPED, DELIVERED, int(limit)))
    return [dict(row) for row in cur.fetchall()]


def next_auto_state(state: str) -> str:
    return {SHIPPED: DELIVERED, DELIVERED: COMPLETED}.get(str(state), "")


def sweep_auto_advance(*, now: datetime | None = None, limit: int = 200) -> dict:
    """Advance every order whose timeout has elapsed. One cycle, own connection.

    The switch for this is the timeout configuration itself, not a separate
    enable flag. An unconfigured deployment stamps no ``auto_advance_at`` at
    all, so :func:`due_for_auto_advance` returns nothing and this is a no-op —
    a second flag would only create a state where the timeouts are set and
    silently ignored.

    Settlement calls are collected and made after the commit, for the reason
    :func:`transition` documents: ``settle_delivery`` opens its own connection
    and would otherwise read a delivery that has not landed yet.

    A row the state machine refuses is counted and left where it is rather than
    retried, because the refusal is about authority or lane and will not be
    different next cycle. It stays visible in ``refused``.
    """
    ensure_schema()
    conn = db.connect()
    settling: list[tuple[int, str]] = []
    metrics = {"considered": 0, "advanced": 0, "refused": 0, "settled": 0}
    try:
        cur = conn.cursor()
        rows = due_for_auto_advance(cur, now=now, limit=limit)
        metrics["considered"] = len(rows)
        for row in rows:
            target = next_auto_state(str(row.get("state") or ""))
            if not target:
                metrics["refused"] += 1
                continue
            transaction_id = int(row["seller_transaction_id"])
            # Derived from the move, so a cycle that runs twice over the same
            # row — a retry, an overlapping replica — is answered, not reapplied.
            key = f"auto:{transaction_id}:{row['state']}:{target}"
            try:
                result = transition(cur, transaction_id, target, actor_role=SYSTEM,
                                    actor="fulfillment_sweeper", idempotency_key=key,
                                    reason="configured timeout elapsed")
            except FulfillmentError:
                metrics["refused"] += 1
                continue
            metrics["advanced"] += 1
            if result["settles_delivery"]:
                settling.append((transaction_id, key))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for transaction_id, key in settling:
        if settle_delivery(transaction_id, actor="fulfillment_sweeper", idempotency_key=key):
            metrics["settled"] += 1
    return metrics
