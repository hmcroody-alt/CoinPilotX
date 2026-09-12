"""Sandbox supplier intents/outbox. Canonical orders and money remain untouched.

An intent is append-only. Its outbox is the mutable delivery cursor. Persisting
SENDING before the POST means a crash/expired lease can only enter UNKNOWN and
read back; it can never dispatch a second create. No absence proof is invented.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from services import db
from services.business_os.payments import webhook_inbox


class FulfillmentError(ValueError):
    def __init__(self, code, http_status=409):
        self.code, self.http_status = code, http_status
        super().__init__(code)


FUNDING_STATES = frozenset({"FUNDING_NOT_READY", "FUNDING_APPROVAL_REQUIRED",
                          "FUNDING_REAPPROVAL_REQUIRED", "FUNDED", "FUNDING_FAILED"})

#: How long a freight quote may be acted on. CJ prices a route at a moment, and
#: this deployment will not place an order against a price it cannot still see.
#:
#: Enforced twice, which is not duplication: `create_intent` refuses to *freeze*
#: a quote older than this, and `dispatch` refuses to *spend* one. The window is
#: shared between the two -- the merchant's review time is subtracted from the
#: worker's budget -- which is exactly why a quote can expire between them and
#: why `supplier_quote_expired` is a state a paid order really reaches.
QUOTE_MAX_AGE_SECONDS = 300

#: Why this deployment did not send a supplier order, keyed by the internal code
#: that refused it.
#:
#: :func:`dispatch` used to collapse every non-retryable preflight failure into
#: the single word ``preflight_blocked`` -- roughly a dozen distinct causes, at
#: least half of which a merchant can actually act on, all stored as one. The
#: reason the flattening existed is real and still holds: no provider message,
#: body or URL may ever be persisted, which is what :mod:`.errors` is for.
#:
#: But the codes being flattened were never the provider's. They are this
#: module's own :class:`FulfillmentError` constants, chosen locally on the lines
#: that raise them -- the same class of fact as a line number. A *closed dict* is
#: what keeps it that way: a ``SupplierError`` code that originated at CJ cannot
#: be a key here, so it falls through to ``preflight_blocked`` and nothing
#: provider-derived is ever written. Dropping the flattening entirely and
#: persisting ``exc.code`` would have been the leak the flattening prevented.
#:
#: Several internal codes deliberately share one reason. The grouping is by what
#: the merchant can do about it, not by which line raised it: a merchant told
#: "the product no longer matches what your supplier lists" re-imports the
#: product whether the mismatch was found in our binding or in CJ's variant
#: list, and splitting that into two messages would be describing our control
#: flow to someone who cannot see it.
PREFLIGHT_REASONS = {
    # The number the merchant approved is no longer the number CJ would charge.
    # Two codes, not one: an expired quote needs a fresh quote, a changed cost
    # needs a fresh approval, and those are different next actions.
    "supplier_quote_expired": "supplier_quote_expired",
    "supplier_cost_reapproval_required": "supplier_cost_changed",
    # The connection this order was queued against is not the connection that
    # would now receive it.
    "connection_binding_changed": "supplier_connection_changed",
    "connection_not_ready": "supplier_connection_changed",
    "api_shop_binding_required": "supplier_shop_unbound",
    "ambiguous_shop_name": "supplier_shop_unbound",
    # What CJ lists is no longer what was imported.
    "product_binding_mismatch": "supplier_item_changed",
    "provider_variant_mismatch": "supplier_item_changed",
    "supplier_cost_unverified": "supplier_cost_unknown",
    "inventory_quantity_not_verified": "supplier_stock_unconfirmed",
    # The sale stopped being a sale after the order was queued.
    "order_not_eligible": "order_no_longer_eligible",
    # Neither of these is about this order at all.
    "production_fulfillment_locked": "supplier_ordering_disabled",
    "explicit_sandbox_required": "supplier_ordering_disabled",
    "intent_integrity_failed": "supplier_order_needs_support",
}

#: Every value this package will ever write to
#: ``business_os_supplier_outbox.last_error``.
#:
#: It is a closed set because the column is *rendered to the merchant*, which is
#: not what its name suggests and was not what the screen showing it believed.
#: ``DropshippingOrdersScreen`` printed this value verbatim under a comment
#: calling it "the supplier's own refusal text ... a merchant chasing a blocked
#: order needs the words their supplier used". It is neither: it cannot be the
#: supplier's text (see :mod:`.errors`), and it is not words -- it is an
#: identifier from this file. So a merchant was shown ``preflight_blocked``.
#:
#: Enumerating the set here is what lets a test prove the mobile copy map covers
#: all of it, rather than the screen falling back to the identifier again.
OUTBOX_REASONS = frozenset({
    # Written by `claim` when a lease expires mid-send.
    "dispatch_lease_expired",
    # Written by `dispatch` on the read-back paths.
    "absence_not_proven", "awaiting_create_readback", "readback_required",
    # Written by `dispatch`'s handler for the retryable and the unclassified.
    "preflight_deferred", "preflight_blocked",
    # Written by `worker.run_once` when the connection cannot be loaded.
    "connection_unavailable",
} | set(PREFLIGHT_REASONS.values()))

#: How long the outbox may go undrained before this deployment stops calling a
#: queued order "queued to send".
#:
#: Measured against the drain's own clamp rather than chosen. `supplier_worker`'s
#: loop sleeps `max(60, min(interval, 3600))`, so a healthy drain completes a
#: tick every 60s and the slowest one a deployment can legally configure
#: completes every 3600s. One missed tick at that ceiling is 3600s; two is 7200.
#: Below 7200 a stopped worker and a slow one are genuinely indistinguishable
#: from the outside, so the notice waits until they are not.
DRAIN_STALL_SECONDS = 7200

#: What is known about the process that turns a queued supplier order into a
#: real one.
#:
#: ``NO_DRAIN_HAS_EVER_RUN`` is the state this deployment is actually in, and it
#: is the reason this enumeration exists. `worker.run_once` is the only caller
#: of `claim`/`dispatch`, its only entry point is `supplier_worker.py`, and that
#: is absent from the `Procfile` -- so every intent ever created here sits at
#: ``READY`` forever while the merchant reads "Queued to send to your supplier".
#:
#: That copy was not a bug in the wording. It was unfalsifiable: `run_once`
#: returned its counts to stdout and persisted nothing about itself, so no read
#: path could tell a queue that is moving from a queue nothing is attached to,
#: and neither could a test. The fix is not to reword the promise -- it is to
#: record the tick, which is what makes the promise a measurement.
#:
#: ``TICKING_BUT_NOT_COMPLETING`` is why two timestamps are kept instead of one.
#: A drain that starts every tick and dies inside it would otherwise be
#: indistinguishable from one that was never deployed, and those call for
#: opposite responses: the first is an incident, the second is unfinished setup.
DRAIN_STATES = ("DRAINING", "DRAIN_STALLED", "TICKING_BUT_NOT_COMPLETING",
                "NO_DRAIN_HAS_EVER_RUN")

#: Outbox states that are positive proof this deployment never contacted the
#: supplier about an intent.
#:
#: Both are reached only through `dispatch`'s final handler, whose first branch
#: is ``if sent or intent["state"] in {"UNKNOWN", "RECONCILE"} or ambiguous_write:
#: state = "UNKNOWN"``. So arriving at ``READY`` or ``BLOCKED`` *is* the proof:
#: the send had not happened when the row was settled.
#:
#: The two differ in one way that matters and is not expressed here. ``BLOCKED``
#: is terminal -- `claim` selects ``state IN ('READY','UNKNOWN','RECONCILE')``,
#: so nothing ever picks a ``BLOCKED`` row up again -- while ``READY`` is live
#: and will be sent the moment a drain exists. Never-sent is therefore necessary
#: but not sufficient for recovery, and `_recoverable_intent` requires both.
#:
#: Everything absent from this tuple fails closed on purpose. ``SENDING`` may be
#: mid-write, ``UNKNOWN`` is the state whose own reason string is
#: ``absence_not_proven``, ``RECONCILE`` is the same family, ``LINKED`` is a
#: confirmed provider order, and an intent with no outbox row at all cannot
#: testify either way. Guessing "not sent" on any of those buys the same goods
#: twice.
NEVER_SENT_STATES = ("READY", "BLOCKED")

#: The single state from which a merchant may be offered a fresh attempt:
#: never sent, and never going to be.
RECOVERABLE_STATES = ("BLOCKED",)


def _recoverable_intent(state, provider_order_id):
    """Whether a dead intent may be retired so its order can be ordered again.

    One predicate, three callers -- `list_obligations` (does the merchant get a
    button?), `create_intent` (may the old row be retired?) and the conditional
    `UPDATE` inside `supersede` (is it still true at the moment of writing?).
    Written once because the previous answer to all three questions was
    ``intent_id is not None``: the existence of a row, not the evidence on it.
    That is what told a merchant "You have already ordered this from your
    supplier" about an order where nothing had been ordered.

    `provider_order_id` is checked as well as the state, even though no path
    settles to ``BLOCKED`` while holding one. `settle` writes it with
    ``COALESCE(?,provider_order_id)``, so once set it is never cleared -- if a
    row ever acquires an id and later reads ``BLOCKED``, the id is the older and
    more expensive fact and it wins.
    """
    return state in RECOVERABLE_STATES and not provider_order_id


def record_drain_tick(*, now=None, completed=False):
    """Record that a drain process reached this line. Called only by the worker.

    Two columns, written at two moments, because "a tick began" and "a tick
    finished" are different facts and the gap between them is the only evidence
    of a drain that is running and failing.

    One row for the whole deployment, not one per connection: `run_once` drains
    every connection in one pass, so per-connection rows would be the same fact
    copied N times and would disagree the first time the worker died midway.
    """
    now = time.time() if now is None else float(now)
    ensure_schema()
    conn = db.connect()
    try:
        # `started_at` is never overwritten by a completion, and `completed_at`
        # is only ever moved forward, so the two cannot be read as a pair that
        # never happened.
        column = "completed_at" if completed else "started_at"
        conn.execute(
            f"INSERT INTO business_os_supplier_drain_ticks (scope,{column}) VALUES(?,?) "
            f"ON CONFLICT(scope) DO UPDATE SET {column}=?",
            ("worker", now, now))
        conn.commit()
    finally:
        conn.close()


def drain_status(*, now=None):
    """Whether anything is turning queued supplier orders into real ones.

    Read, never inferred. Returns ``None`` for both timestamps when no tick has
    ever been recorded, which is a fact about this deployment and not a missing
    value to be defaulted away.
    """
    now = time.time() if now is None else float(now)
    ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT started_at, completed_at FROM "
                           "business_os_supplier_drain_ticks WHERE scope='worker'").fetchone()
    finally:
        conn.close()
    started = row["started_at"] if row else None
    completed = row["completed_at"] if row else None
    if not started:
        state = "NO_DRAIN_HAS_EVER_RUN"
    elif not completed:
        state = "TICKING_BUT_NOT_COMPLETING"
    elif now - completed > DRAIN_STALL_SECONDS:
        # Stalled on the *completion*, not the start. A worker looping on a
        # crash keeps `started_at` fresh forever, and treating that as healthy
        # is the failure this column pair exists to catch.
        state = "DRAIN_STALLED"
    else:
        state = "DRAINING"
    return {"state": state, "started_at": started, "completed_at": completed,
            "stall_after_seconds": DRAIN_STALL_SECONDS}


def assert_sandbox(value):
    if (os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").upper() != "SANDBOX"
            or os.getenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "false").lower()
            not in {"false", "0", "off", ""}):
        raise FulfillmentError("production_fulfillment_locked")
    if type(value) is not int or value != 1:
        raise FulfillmentError("explicit_sandbox_required", 400)


def ensure_schema(conn=None):
    owned = conn is None
    conn = conn or db.connect()
    try:
        # `superseded_at` is the whole recovery contract. An intent is immutable
        # and stays immutable; what changes is whether it is still the live one
        # for its order. Nullable with no default, for the reason
        # `business_os_supplier_drain_ticks` gives below: a `0` would make "still
        # live" and "retired at the epoch" the same row.
        #
        # `UNIQUE(connection_id, order_id)` used to be declared here and is gone.
        # It was always implied by the canonical-order index below -- if
        # `order_id` is unique across the table then so is any pair containing it
        # -- so it constrained nothing that index did not, while being the one
        # constraint that could not be made conditional without rebuilding the
        # table. `_reshape_intents_for_supersession` drops it where it still
        # exists.
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_intents (
            id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, business_id TEXT NOT NULL,
            store_id TEXT NOT NULL, merchant_id TEXT NOT NULL, order_id TEXT NOT NULL,
            external_account_id TEXT NOT NULL, external_shop_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, external_order_ref TEXT NOT NULL UNIQUE,
            snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL, superseded_at DOUBLE PRECISION,
            UNIQUE(connection_id, idempotency_key))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_outbox (
            intent_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'READY',
            provider_order_id TEXT, provider_status TEXT,
            funding_state TEXT NOT NULL DEFAULT 'FUNDING_NOT_READY',
            lease_token TEXT, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
            available_at DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, updated_at DOUBLE PRECISION NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_outbox_due "
                     "ON business_os_supplier_outbox(state, available_at, lease_until)")
        # Proof that something drains the outbox. `scope` is a fixed single-row
        # key ('worker') rather than an autoincrement id: the row is a latch, not
        # a log, and a TEXT primary key upserts identically on SQLite and
        # PostgreSQL where an INTEGER PRIMARY KEY singleton needs a CHECK on one
        # and a sequence on the other.
        #
        # Both timestamps are nullable with no default. A zero or a `now()`
        # default would make a deployment that has never drained anything look
        # exactly like one that just drained successfully, which is the entire
        # fact this table exists to record.
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_drain_ticks (
            scope TEXT PRIMARY KEY, started_at DOUBLE PRECISION,
            completed_at DOUBLE PRECISION)""")
        if owned:
            conn.commit()
            # Only on a connection this function owns. The reshape has to commit
            # between statements -- a failed `ALTER` poisons the whole
            # transaction on PostgreSQL -- and committing a caller's transaction
            # underneath it is the hazard that already cost this package a hung
            # route. Every caller that reads `superseded_at` (`create_intent`,
            # `list_obligations`) calls `ensure_schema()` with no argument, so
            # the column is in place before anything selects it.
            _reshape_intents_for_supersession(conn)
    finally:
        if owned:
            conn.close()


def _reshape_intents_for_supersession(conn):
    """Bring an already-created intents table up to the supersession shape.

    Idempotent and separate from the `CREATE TABLE` above because
    `CREATE TABLE IF NOT EXISTS` is a no-op on a database that already has the
    old shape -- which is every database that has ever run this code. Without
    this, the new DDL would only ever apply to a fresh database, and the test
    suite (fresh SQLite every run) would prove a behaviour production does not
    have. That is this mission's recurring defect with a schema in the subject
    position, so it is measured here rather than assumed to have applied.

    There is no migration framework; schema is hand-rolled and must be safe to
    run on every boot.
    """
    # `ADD COLUMN IF NOT EXISTS` is PostgreSQL-only -- `db._translate_alter_table`
    # injects it there -- so on SQLite the duplicate is caught rather than
    # declared away. Same shape as `diagnostics.ensure_schema`.
    try:
        conn.execute("ALTER TABLE business_os_supplier_intents "
                     "ADD COLUMN superseded_at DOUBLE PRECISION")
        conn.commit()
    except Exception:
        conn.rollback()
    # The unconditional canonical-order index is what makes a replacement
    # impossible, so it goes before the conditional one arrives. Dropped by its
    # own name rather than rebuilt: it is a standalone index on both engines.
    try:
        conn.execute("DROP INDEX IF EXISTS uq_supplier_canonical_order")
        conn.commit()
    except Exception:
        conn.rollback()
    if db.IS_POSTGRES:
        # A table constraint, not an index, so it needs the constraint spelling
        # even though PostgreSQL backs it with an index of the same name.
        try:
            conn.execute("ALTER TABLE business_os_supplier_intents DROP CONSTRAINT "
                         "IF EXISTS business_os_supplier_intents_connection_id_order_id_key")
            conn.commit()
        except Exception:
            conn.rollback()
    else:
        _drop_sqlite_order_uniqueness(conn)
    # Last, so it is never created against a table still missing the column it
    # reads. One live intent per canonical customer order, exactly as before;
    # the retired ones simply stop counting.
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_live_canonical_order "
                     "ON business_os_supplier_intents(order_id) WHERE superseded_at IS NULL")
        conn.commit()
    except Exception:
        conn.rollback()


def _drop_sqlite_order_uniqueness(conn):
    """Rebuild the table on SQLite, where an inline UNIQUE cannot be dropped.

    Only reached on SQLite, and only when the old constraint is actually still
    declared -- read out of `sqlite_master` rather than guessed at, so a fresh
    database (which now gets the new shape from `ensure_schema`) is left alone.

    This exists so a developer's long-lived `coinpilotx.db` behaves like
    production rather than refusing every retry with `concurrent_intent_conflict`
    while the test suite, on a fresh file, proves the opposite. A local database
    that silently disagrees with prod is how this whole class of defect gets
    written in the first place.
    """
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                           "AND name='business_os_supplier_intents'").fetchone()
    except Exception:
        return
    declared = (row["sql"] if row and row["sql"] else "") if row else ""
    if "connection_id, order_id" not in declared.replace("\n", " "):
        return
    # The replacement table below is a fixed shape, so the copy is the fixed
    # column list -- not whatever the live table happens to have. Read the live
    # columns only to refuse the rebuild when they are not a superset: copying a
    # subset would write NOT NULL columns as NULL, and silently dropping a column
    # this version has not heard of would lose data. Neither is worth doing
    # automatically, and the fallback (keep the old table) is safe.
    try:
        live = {str(info[1]) for info in
                conn.execute("PRAGMA table_info(business_os_supplier_intents)").fetchall()}
    except Exception:
        return
    columns = ("id", "connection_id", "business_id", "store_id", "merchant_id", "order_id",
               "external_account_id", "external_shop_id", "idempotency_key",
               "external_order_ref", "snapshot_json", "snapshot_hash", "created_at",
               "superseded_at")
    if live != set(columns):
        return
    names = ",".join(columns)
    try:
        conn.execute("DROP TABLE IF EXISTS business_os_supplier_intents__reshape")
        # Deliberately no UNIQUE(connection_id, order_id), which is the point of
        # the rebuild, and deliberately no indexes: `_reshape_intents_for_supersession`
        # creates the conditional one immediately after this returns.
        conn.execute("""CREATE TABLE business_os_supplier_intents__reshape (
            id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, business_id TEXT NOT NULL,
            store_id TEXT NOT NULL, merchant_id TEXT NOT NULL, order_id TEXT NOT NULL,
            external_account_id TEXT NOT NULL, external_shop_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, external_order_ref TEXT NOT NULL UNIQUE,
            snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL, superseded_at DOUBLE PRECISION,
            UNIQUE(connection_id, idempotency_key))""")
        conn.execute(f"INSERT INTO business_os_supplier_intents__reshape ({names}) "
                     f"SELECT {names} FROM business_os_supplier_intents")
        conn.execute("DROP TABLE business_os_supplier_intents")
        conn.execute("ALTER TABLE business_os_supplier_intents__reshape "
                     "RENAME TO business_os_supplier_intents")
        conn.commit()
    except Exception:
        # Left as it was. The surviving constraint refuses a retry with
        # `concurrent_intent_conflict`, which is a refusal and not a wrong
        # success, so failing here cannot place or double-place an order.
        conn.rollback()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value, name, limit=200):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise FulfillmentError("invalid_" + name, 400)
    return value


def _canonical_order(conn, order_id):
    """One customer order from the canonical marketplace ledger, or None.

    Joins to ``marketplace_listings`` for ``listing_type`` because
    ``marketplace_orders`` does not carry one and the physical/digital
    distinction is a property of the product, not of the sale. Resolved through
    the same rule the rest of the app uses (``effective_listing_type``) rather
    than by reading the raw column, so a listing that predates the column and
    only has ``product_type`` set is classified the same way here as it is on the
    listing page.

    Returns None for anything unparseable, so an unusable order id refuses
    identically to an absent one.
    """
    from services.marketplace_listing_types import effective_listing_type

    try:
        resolved = int(str(order_id).strip())
    except (TypeError, ValueError):
        return None
    row = conn.execute(
        "SELECT o.id, o.seller_user_id, o.listing_id, o.quantity, o.status, "
        "l.listing_type, l.product_type FROM marketplace_orders o "
        "JOIN marketplace_listings l ON l.id = o.listing_id WHERE o.id = ?",
        (resolved,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["status"] = str(item.get("status") or "").strip().lower()
    item["listing_type"] = effective_listing_type(item.get("listing_type"),
                                                  item.get("product_type"))
    item["quantity"] = int(item.get("quantity") or 0)
    return item


def create_intent(*, connection_id, business_id, store_id, actor_user_id, order_id,
                  items, shipping_quote, expected_supplier_cost_cents,
                  isSandbox=None, idempotency_key, context=None):
    """Authorize a merchant, reference canonical order lines, atomically enqueue.

    One sandbox intent per canonical order/connection. Splits/replacements need a
    later explicit revision contract; changing a replay's content is rejected.
    Snapshots are backend-only, never use this return value as a public DTO.

    The destination is not a parameter. It used to be, taken straight from the
    request body, which meant a merchant-authenticated call could name any
    address while the one the buyer paid to ship to sat frozen on the
    transaction with nothing comparing the two. `order_destination` now states
    it, from that frozen record, and is the only thing that does.
    """
    from . import connections, gateway
    assert_sandbox(isSandbox)
    metadata = connections.get_connection(connection_id, business_id, store_id,
                                          actor_user_id, context=context, write=True)
    bundle = connections.worker_connection(connection_id, business_id, store_id)
    meta = bundle["connection"]
    if metadata.get("id") != meta.get("id") or metadata.get("status") != "CONNECTED":
        raise FulfillmentError("connection_not_ready")
    # Connecting no longer requires choosing a CJ shop, because importing
    # products does not need one. Fulfilment does: `_validate_observed` proves a
    # placed order came back on the shop we bound, and with nothing bound there
    # is no such proof to make. Refusing here is not a policy preference -- it
    # is declining to place an order whose provenance we could not check.
    if not meta.get("external_shop_id"):
        raise FulfillmentError("shop_binding_required", 409)
    _text(idempotency_key, "idempotency_key", 128)
    if type(expected_supplier_cost_cents) is not int or expected_supplier_cost_cents < 0:
        raise FulfillmentError("invalid_expected_supplier_cost", 400)
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise FulfillmentError("invalid_items", 400)
    clean_items = []
    for item in items:
        if not isinstance(item, dict) or type(item.get("quantity")) is not int or not 1 <= item["quantity"] <= 10000:
            raise FulfillmentError("invalid_quantity", 400)
        clean_items.append({key: _text(item.get(key), key) for key in
                            ("canonical_product_id", "pid", "vid", "sku")} |
                           {"quantity": item["quantity"]})
    clean_items.sort(key=lambda x: (x["canonical_product_id"], x["vid"]))
    if len({item["canonical_product_id"] for item in clean_items}) != len(clean_items):
        raise FulfillmentError("duplicate_order_line", 400)
    supplier_items_cost = Decimal(0)
    for item in clean_items:
        binding = gateway.get_product_binding(connection_id, business_id, store_id, item["canonical_product_id"])
        if binding["pid"] != item["pid"] or binding["vid"] != item["vid"]:
            raise FulfillmentError("product_binding_mismatch", 400)
        detail = gateway.get_snapshot(binding["snapshot_id"], connection_id, business_id, store_id, actor_user_id, context=context)
        variants = detail["data"].get("variants", [])
        variant = next((v for v in variants if v.get("pid") == item["pid"] and v.get("vid") == item["vid"] and v.get("sku") == item["sku"]), None)
        if not variant or variant.get("currency") != "USD":
            raise FulfillmentError("product_binding_mismatch", 400)
        try:
            price = Decimal(variant.get("price"))
            if not price.is_finite() or price < 0:
                raise InvalidOperation
            supplier_items_cost += price * item["quantity"]
        except (InvalidOperation, TypeError, ValueError):
            raise FulfillmentError("supplier_cost_unverified") from None
    address = order_destination(order_id)
    if not isinstance(shipping_quote, dict):
        raise FulfillmentError("invalid_quote", 400)
    quote_snapshot = gateway.get_snapshot(_text(shipping_quote.get("snapshot_id"), "quote_snapshot_id"), connection_id,
                                          business_id, store_id, actor_user_id, context=context)
    if quote_snapshot["kind"] != "shipping":
        raise FulfillmentError("shipping_snapshot_required", 400)
    quoted_rows = quote_snapshot["data"].get("quote_request", {}).get("reqDTOS", [])
    if len(quoted_rows) != 1 or not isinstance(quoted_rows[0], dict):
        raise FulfillmentError("shipping_quote_items_unverified", 400)
    quoted_input = quoted_rows[0]
    quoted_items = quoted_input.get("freightTrialSkuList", [])
    if (not isinstance(quoted_items, list) or any(not isinstance(i, dict) for i in quoted_items)
            or sorted((i.get("vid", ""), i.get("sku", ""), i.get("skuQuantity", 0)) for i in quoted_items)
            != sorted((i["vid"], i["sku"], i["quantity"]) for i in clean_items)):
        raise FulfillmentError("shipping_quote_items_mismatch", 400)
    for source, target in (("destAreaCode", "shippingCountryCode"), ("province", "shippingProvince"),
                           ("city", "shippingCity"), ("recipientAddress", "shippingAddress"), ("zip", "shippingZip")):
        if quoted_input.get(source) != address.get(target):
            raise FulfillmentError("shipping_quote_destination_mismatch", 400)
    options = [q for q in quote_snapshot["data"].get("quotes", [])
               if q.get("option_id") == shipping_quote.get("option_id") and q.get("channel_id") == shipping_quote.get("channel_id")]
    if len(options) != 1 or not options[0].get("available"):
        raise FulfillmentError("shipping_quote_not_verified", 400)
    option = options[0]
    if option.get("destination") != address["shippingCountryCode"] or option.get("currency") != "USD":
        raise FulfillmentError("shipping_quote_route_mismatch", 400)
    try:
        quoted = datetime.fromisoformat(option["quoted_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - quoted).total_seconds()
        freight = Decimal(option["provider_total"])
        total = (supplier_items_cost + freight) * 100
        if age < 0 or age > QUOTE_MAX_AGE_SECONDS or not freight.is_finite() or freight < 0 or total != total.to_integral_value():
            raise ValueError
        if int(total) != expected_supplier_cost_cents:
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise FulfillmentError("supplier_cost_reapproval_required") from None
    quote = {"logisticName": _text(option.get("service"), "logistic_name", 50),
             "fromCountryCode": _text(option.get("origin"), "origin", 2),
             "currency": "USD", "quoted_at": option["quoted_at"], "provider_total": str(freight),
             "snapshot_id": quote_snapshot["snapshot_id"], "option_id": option.get("option_id"),
             "channel_id": option.get("channel_id")}
    snapshot = {"items": clean_items, "shipping_destination": address,
                "shipping_quote": quote, "expected_supplier_cost_cents": expected_supplier_cost_cents,
                "isSandbox": isSandbox, "payType": 3, "orderFlow": 1}
    encoded = _json(snapshot)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    ensure_schema()
    conn = db.connect()
    try:
        webhook_inbox._begin(conn)
        merchant = connections._authorize(conn, business_id, store_id, actor_user_id, context=context, write=True)
        current_connection = connections._row(conn, connection_id, business_id, store_id, merchant)
        _validate_binding({**meta, "connection_id": connection_id}, current_connection)
        # The customer order is a marketplace_orders row, keyed by listing_id —
        # the same identity space canonical_product_id now lives in. It used to be
        # read from business_os_mkt_orders, whose product ids could never match a
        # listing id, so this check could not have passed once the binding moved.
        #
        # This stays firmly the *customer* order. The CJ supplier order is the
        # intent written below; the two are deliberately separate rows with
        # separate lifecycles, and nothing here copies a status between them.
        canonical = _canonical_order(conn, order_id)
        if canonical is None or str(canonical["seller_user_id"]) != str(meta["merchant_id"]):
            raise FulfillmentError("order_not_found", 404)
        if canonical["status"] in {"cancelled", "refunded", "disputed"} or canonical["listing_type"] != "physical":
            raise FulfillmentError("order_not_eligible")
        canonical_items = {str(canonical["listing_id"]): int(canonical["quantity"])}
        if set(canonical_items) != {item["canonical_product_id"] for item in clean_items} or any(canonical_items.get(item["canonical_product_id"]) != item["quantity"] for item in clean_items):
            raise FulfillmentError("order_line_mismatch", 400)
        # Two questions that used to be one `OR`, separated because they have
        # different answers once an intent can be retired.
        #
        # The idempotency key is matched across every intent ever written,
        # superseded or not: the key is the caller's promise that this is the
        # same request, and a key that has been spent must never mint a second
        # supplier order. The canonical order is matched against *live* intents
        # only, because a retired one is precisely what recovery leaves behind.
        replayed = conn.execute("SELECT * FROM business_os_supplier_intents "
                                "WHERE connection_id=? AND idempotency_key=?",
                                (connection_id, idempotency_key)).fetchone()
        if replayed:
            replayed = dict(replayed)
            if (replayed["snapshot_hash"] != digest or replayed["order_id"] != str(order_id)):
                raise FulfillmentError("immutable_intent_conflict")
            if replayed["superseded_at"] is not None:
                # Not `duplicate: True`. That answer means "the order your
                # request asked for exists", and for a retired intent it does
                # not -- this is the defect that made a retry look like a
                # success while placing nothing. A spent key cannot be reused,
                # and saying so is the only honest answer left.
                raise FulfillmentError("intent_superseded_use_new_key", 409)
            webhook_inbox._commit(conn)
            return {"intent_id": replayed["id"], "duplicate": True}
        live = conn.execute("SELECT i.*,o.state,o.provider_order_id FROM business_os_supplier_intents i "
                            "LEFT JOIN business_os_supplier_outbox o ON o.intent_id=i.id "
                            "WHERE i.connection_id=? AND i.order_id=? AND i.superseded_at IS NULL",
                            (connection_id, str(order_id))).fetchone()
        if live:
            live = dict(live)
            # A different key for the same order is only allowed to proceed when
            # the intent holding that order can be proven never to have been
            # sent *and* to be past retrying. Anything else -- in flight,
            # unconfirmed, linked, or missing its outbox row entirely -- keeps
            # the order and refuses, because the alternative is ordering the
            # same goods a second time.
            if not _recoverable_intent(live.get("state"), live.get("provider_order_id")):
                raise FulfillmentError("immutable_intent_conflict")
            # Conditional on the evidence a second time, inside the transaction
            # that creates the replacement, so a concurrent dispatch that moved
            # the row out of BLOCKED between the read above and this write loses
            # the race instead of being overwritten. The outbox is re-checked in
            # the same statement rather than trusted from the row just read.
            # The state list is expanded from `RECOVERABLE_STATES` rather than
            # spelled here, so a state added to that tuple reaches this statement
            # too. A literal would have been a second, shorter vocabulary that
            # agreed with the first only until someone edited it.
            placeholders = ",".join("?" for _ in RECOVERABLE_STATES)
            retired = conn.execute(
                "UPDATE business_os_supplier_intents SET superseded_at=? WHERE id=? "
                "AND superseded_at IS NULL AND EXISTS (SELECT 1 FROM business_os_supplier_outbox o "
                f"WHERE o.intent_id=business_os_supplier_intents.id AND o.state IN ({placeholders}) "
                "AND o.provider_order_id IS NULL)",
                (time.time(), live["id"], *RECOVERABLE_STATES))
            if retired.rowcount != 1:
                raise FulfillmentError("immutable_intent_conflict")
        identity = "cjf_" + uuid.uuid4().hex
        external_ref = "pss_" + uuid.uuid4().hex
        now = time.time()
        # ON CONFLICT avoids poisoning PostgreSQL transactions during races.
        cursor = conn.execute("INSERT INTO business_os_supplier_intents "
            "(id,connection_id,business_id,store_id,merchant_id,order_id,external_account_id,"
            "external_shop_id,idempotency_key,external_order_ref,snapshot_json,snapshot_hash,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (identity, connection_id, business_id, store_id, meta["merchant_id"], str(order_id),
             meta["external_account_id"], meta["external_shop_id"], idempotency_key,
             external_ref, encoded, digest, now))
        if cursor.rowcount != 1:
            raise FulfillmentError("concurrent_intent_conflict")
        conn.execute("INSERT INTO business_os_supplier_outbox(intent_id,available_at,updated_at) VALUES(?,?,?)",
                     (identity, now, now))
        webhook_inbox._commit(conn)
        return {"intent_id": identity, "duplicate": False}
    except Exception:
        webhook_inbox._rollback(conn)
        raise
    finally:
        conn.close()


def claim(*, now=None, lease_seconds=120):
    """Claim one due write/read-back using a DB conditional update + fencing token."""
    now = time.time() if now is None else now
    token = uuid.uuid4().hex
    conn = db.connect()
    try:
        webhook_inbox._begin(conn)
        conn.execute("UPDATE business_os_supplier_outbox SET state='UNKNOWN', last_error='dispatch_lease_expired' "
                     "WHERE state='SENDING' AND lease_until<=?", (now,))
        row = conn.execute("SELECT intent_id FROM business_os_supplier_outbox "
             "WHERE state IN ('READY','UNKNOWN','RECONCILE') AND available_at<=? AND lease_until<=? "
             "ORDER BY available_at,intent_id LIMIT 1", (now, now)).fetchone()
        if not row:
            webhook_inbox._commit(conn)
            return None
        cursor = conn.execute("UPDATE business_os_supplier_outbox SET lease_token=?, lease_until=?, "
             "attempts=attempts+1,updated_at=? WHERE intent_id=? AND lease_until<=? "
             "AND state IN ('READY','UNKNOWN','RECONCILE')", (token, now + lease_seconds, now, row[0], now))
        if cursor.rowcount != 1:
            webhook_inbox._commit(conn)
            return None
        result = dict(conn.execute("SELECT i.*,o.state,o.lease_token,o.attempts,o.provider_order_id "
             "FROM business_os_supplier_intents i JOIN business_os_supplier_outbox o ON i.id=o.intent_id "
             "WHERE i.id=?", (row[0],)).fetchone())
        webhook_inbox._commit(conn)
        return result
    except Exception:
        webhook_inbox._rollback(conn)
        raise
    finally:
        conn.close()


def settle(intent, state, *, now=None, delay=0, error=None, provider_order_id=None, provider_status=None):
    now = time.time() if now is None else now
    conn = db.connect()
    try:
        cursor = conn.execute("UPDATE business_os_supplier_outbox SET state=?,lease_token=NULL,lease_until=0,"
            "available_at=?,last_error=?,provider_order_id=COALESCE(?,provider_order_id),"
            "provider_status=COALESCE(?,provider_status),updated_at=? WHERE intent_id=? AND lease_token=?",
            (state, now + delay, error, provider_order_id, provider_status, now, intent["id"], intent["lease_token"]))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def _sending(intent, now):
    conn = db.connect()
    try:
        cursor = conn.execute("UPDATE business_os_supplier_outbox SET state='SENDING',updated_at=?,lease_until=? "
            "WHERE intent_id=? AND lease_token=? AND lease_until>? AND state='READY'",
            (now, now + 90, intent["id"], intent["lease_token"], now))
        conn.commit()
        if cursor.rowcount != 1:
            raise FulfillmentError("dispatch_claim_lost")
    finally:
        conn.close()


def _validate_binding(intent, meta):
    if meta.get("id") != intent["connection_id"]:
        raise FulfillmentError("connection_binding_changed")
    for field in ("merchant_id", "business_id", "store_id", "external_account_id", "external_shop_id"):
        if str(meta.get(field)) != str(intent[field]):
            raise FulfillmentError("connection_binding_changed")
    if meta.get("status") != "CONNECTED":
        raise FulfillmentError("connection_not_ready")


def _validate_observed(intent, snapshot, observed):
    if not observed or not observed.get("order_id"):
        raise FulfillmentError("absence_not_proven")
    if observed.get("external_order_ref") != intent["external_order_ref"]:
        raise FulfillmentError("provider_order_binding_mismatch")
    if str(observed.get("shop_id")) != str(intent["external_shop_id"]):
        raise FulfillmentError("provider_shop_mismatch")
    if type(observed.get("is_sandbox")) is not int or observed["is_sandbox"] != 1:
        raise FulfillmentError("provider_sandbox_unverified")
    expected_lines = sorted((i["vid"], i["quantity"]) for i in snapshot["items"])
    actual_lines = sorted((p.get("vid"), p.get("quantity")) for p in observed.get("products", []))
    if actual_lines != expected_lines:
        raise FulfillmentError("provider_order_lines_mismatch")


def observe_linked(meta, provider_order_id, observed):
    """Read-only supplier reconciliation; never changes canonical payment/order state."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT i.* FROM business_os_supplier_intents i JOIN business_os_supplier_outbox o ON o.intent_id=i.id WHERE i.connection_id=? AND i.business_id=? AND i.store_id=? AND o.provider_order_id=? AND o.state='LINKED'",
                           (meta["id"], meta["business_id"], meta["store_id"], provider_order_id)).fetchone()
        if row is None:
            raise FulfillmentError("supplier_order_not_bound")
        intent = dict(row)
        _validate_binding(intent, meta)
        _validate_observed(intent, json.loads(intent["snapshot_json"]), observed)
        if observed["order_id"] != provider_order_id:
            raise FulfillmentError("provider_order_binding_mismatch")
        conn.execute("UPDATE business_os_supplier_outbox SET provider_status=?,updated_at=? WHERE intent_id=? AND state='LINKED' AND provider_order_id=?",
                     (observed.get("provider_status") or "UNKNOWN", time.time(), intent["id"], provider_order_id))
        conn.commit()
    finally:
        conn.close()


def dispatch_shop(shops, shop_id):
    """The one CJ shop a binding may place an order through, or a refusal.

    Three conditions, and until now they lived only inside :func:`dispatch`. A
    CJ "shop" can be a Shopify or Woo storefront the merchant authorized; only
    the one CJ's own *API* app creates can receive an order placed over the API.
    And CJ addresses an order by shop name, so two shops sharing a name make the
    destination ambiguous no matter which id was bound.

    Measuring them only at dispatch meant a merchant could choose a shop, see
    the choice accepted, and learn at the first real order that it was never a
    shop an order could go to. :func:`connections.bind_shop` calls this function
    at bind time for exactly that reason -- same conditions, now also measured
    where the choice is made and not only where it is spent.

    Returns the selected shop so the caller can name it; raises otherwise.
    """
    selected = [s for s in shops if s.get("shop_id") == shop_id and s.get("status") == 1]
    if len(selected) != 1 or not selected[0].get("name") or str(selected[0].get("platform")).lower() != "api":
        raise FulfillmentError("api_shop_binding_required")
    if len([s for s in shops if s.get("name") == selected[0]["name"]]) != 1:
        raise FulfillmentError("ambiguous_shop_name")
    return selected[0]


def dispatch(intent, adapter, meta, *, now=None):
    """One worker attempt; no exception text/provider body is persisted or logged."""
    now = time.time() if now is None else now
    sent = False
    provider_failure = None
    try:
        _validate_binding(intent, meta)
        snapshot = json.loads(intent["snapshot_json"])
        if hashlib.sha256(_json(snapshot).encode()).hexdigest() != intent["snapshot_hash"]:
            raise FulfillmentError("intent_integrity_failed")
        assert_sandbox(snapshot.get("isSandbox"))
        if intent["state"] in {"UNKNOWN", "RECONCILE"}:
            observed = adapter.get_fulfillment(external_order_ref=intent["external_order_ref"])
            if not observed or not observed.get("order_id"):
                # A single not-found/read timeout is never evidence permitting POST.
                settle(intent, "UNKNOWN", now=now, delay=300, error="absence_not_proven")
                return "UNKNOWN"
            _validate_observed(intent, snapshot, observed)
            settle(intent, "LINKED", now=now, provider_order_id=str(observed["order_id"]),
                   provider_status=str(observed.get("provider_status") or "UNKNOWN")[:100])
            return "LINKED"
        # Check canonical cancellation/ownership again after queueing.
        from . import gateway
        conn = db.connect()
        try:
            current_order = _canonical_order(conn, intent["order_id"])
        finally:
            conn.close()
        if not current_order or str(current_order["seller_user_id"]) != str(intent["merchant_id"]) or current_order["status"] in {"cancelled", "refunded", "disputed"}:
            raise FulfillmentError("order_not_eligible")
        selected_shop = dispatch_shop(adapter.get_shops(), intent["external_shop_id"])
        # Backend provider validation, never labels/SKUs inferred from display text.
        item_total = Decimal(0)
        for item in snapshot["items"]:
            binding = gateway.get_product_binding(intent["connection_id"], intent["business_id"], intent["store_id"], item["canonical_product_id"])
            if binding["pid"] != item["pid"] or binding["vid"] != item["vid"]:
                raise FulfillmentError("product_binding_mismatch")
            product = adapter.get_product(item["pid"])
            variants = adapter.get_variants(item["pid"])
            variants = variants.get("variants", []) if isinstance(variants, dict) else variants
            if product.get("pid") != item["pid"] or not any(
                v.get("pid") == item["pid"] and v.get("vid") == item["vid"] and v.get("sku") == item["sku"]
                for v in variants):
                raise FulfillmentError("provider_variant_mismatch")
            variant = next(v for v in variants if v.get("vid") == item["vid"])
            price = Decimal(variant.get("price"))
            if variant.get("currency") != "USD" or not price.is_finite() or price < 0:
                raise FulfillmentError("supplier_cost_unverified")
            item_total += price * item["quantity"]
            inventory = adapter.get_inventory(item["pid"], vid=item["vid"])
            warehouses = [w for v in inventory.get("variants", []) if v.get("vid") == item["vid"] and v.get("pid") == item["pid"]
                          for w in v.get("warehouses", []) if w.get("country") == snapshot["shipping_quote"]["fromCountryCode"]]
            # Never sum unrelated warehouses or infer in-stock from unknown.
            if not any(w.get("state") == "IN_STOCK" and w.get("verified") == 1 and type(w.get("total")) is int and w["total"] >= item["quantity"] for w in warehouses):
                raise FulfillmentError("inventory_quantity_not_verified")
        quoted_at = datetime.fromisoformat(snapshot["shipping_quote"]["quoted_at"].replace("Z", "+00:00"))
        # `now`, not `datetime.now()`. The snapshot is pinned by `snapshot_hash`,
        # so `quoted_at` cannot be edited to reach this branch -- which meant that
        # while this read the wall clock, the only way to make the quote stale was
        # to let 300 real seconds pass. No test did, so the age half of the
        # condition below had never been true in either direction, and the
        # reapproval rule standing between a merchant and a price CJ has since
        # changed was the one rule nothing had ever executed.
        age = (datetime.fromtimestamp(now, timezone.utc) - quoted_at).total_seconds()
        if not 0 <= age <= QUOTE_MAX_AGE_SECONDS:
            raise FulfillmentError("supplier_quote_expired")
        if (item_total + Decimal(snapshot["shipping_quote"]["provider_total"])) * 100 != snapshot["expected_supplier_cost_cents"]:
            raise FulfillmentError("supplier_cost_reapproval_required")
        payload = dict(snapshot["shipping_destination"])
        payload.update({"orderNumber": intent["external_order_ref"], "isSandbox": snapshot["isSandbox"],
                        "payType": snapshot["payType"], "orderFlow": snapshot["orderFlow"],
                        "logisticName": snapshot["shipping_quote"]["logisticName"],
                        "fromCountryCode": snapshot["shipping_quote"]["fromCountryCode"],
                        "storeName": selected_shop["name"],
                        "products": [{"vid": item["vid"], "quantity": item["quantity"]}
                                     for item in snapshot["items"]]})
        assert_sandbox(payload.get("isSandbox"))
        _sending(intent, now)
        sent = True
        adapter.create_sandbox_fulfillment(payload)
        # Always prove identity/sandbox through independent read-back, not POST payload echo.
        settle(intent, "UNKNOWN", now=now, delay=2, error="awaiting_create_readback")
        return "UNKNOWN"
    except Exception as exc:
        from .errors import SupplierError
        if isinstance(exc, SupplierError):
            provider_failure = exc
        retry_after = getattr(exc, "retry_after", None)
        delay = max(30, float(retry_after)) if retry_after else max(30, min(3600, 2 ** min(intent["attempts"], 10)))
        if sent or intent["state"] in {"UNKNOWN", "RECONCILE"} or getattr(exc, "ambiguous_write", False):
            state = "UNKNOWN"
        elif getattr(exc, "http_status", None) == 429 or str(getattr(exc, "code", "")).lower() in {
                "rate_limited", "quota_exhausted", "provider_unavailable", "provider_timeout"}:
            state = "READY"
        else:
            state = "BLOCKED"
        # Codes are chosen locally; no arbitrary provider/transport exceptions
        # copied. `PREFLIGHT_REASONS` is a closed dict keyed by this module's own
        # `FulfillmentError` constants, so a provider-originated `SupplierError`
        # code can only miss and fall through to `preflight_blocked` -- which is
        # how the merchant gets told which of a dozen causes it was without any
        # provider-derived string reaching the column.
        #
        # The two non-BLOCKED branches keep one word each on purpose: they are
        # not refusals a merchant acts on, they are this worker saying "later".
        safe_error = ("readback_required" if state == "UNKNOWN"
                      else "preflight_deferred" if state == "READY"
                      # `str(...)` so a code that is somehow not a string misses
                      # the dict instead of raising `TypeError` on an unhashable
                      # key -- inside the one handler whose job is to make sure
                      # the row is always settled.
                      else PREFLIGHT_REASONS.get(str(getattr(exc, "code", "")), "preflight_blocked"))
        settle(intent, state, now=now, delay=delay, error=safe_error)
        return state
    finally:
        from . import connections
        try:
            connections.record_activity(intent["connection_id"], intent["business_id"], intent["store_id"],
                                        adapter=adapter, error=provider_failure)
        except Exception:
            pass  # Telemetry cannot change write disposition or reveal secrets.


def fund_fulfillment(*args, **kwargs):
    raise FulfillmentError("supplier_funding_locked")


def real_order_activity(conn, connection_id):
    """Count orders that reach CJ as *real*, and when the last one did.

    CJ's inactivity rule counts real orders only, and this deployment sends
    none: `assert_sandbox` rejects any payload without an integral
    ``isSandbox=1``, on every path, so the count returned here is structurally
    zero rather than incidentally zero. That is the correct answer and the
    whole reason the caller needs it -- an integration that never places a real
    order is exactly the one CJ eventually disables.

    Counted from the snapshot rather than from a column, because the snapshot
    is what was actually sent. A column would be a second place for the sandbox
    flag to live, and the two could disagree; the payload cannot disagree with
    itself. ``LINKED`` is the only state where CJ acknowledged an order, so
    intents that never left the outbox are correctly not counted.
    """
    rows = conn.execute(
        "SELECT i.snapshot_json, i.created_at FROM business_os_supplier_intents i "
        "JOIN business_os_supplier_outbox o ON o.intent_id=i.id "
        "WHERE i.connection_id=? AND o.state='LINKED' ORDER BY i.created_at DESC",
        (connection_id,)).fetchall()
    count, last_at = 0, None
    for row in rows:
        try:
            sandbox = json.loads(row["snapshot_json"]).get("isSandbox")
        except (ValueError, TypeError):
            # Unreadable snapshot: we cannot show it was a sandbox order, and
            # guessing "real" would silence the warning this function exists to
            # raise. Not counted, and it does not move the clock.
            continue
        if type(sandbox) is int and sandbox == 1:
            continue
        count += 1
        if last_at is None:
            last_at = row["created_at"]
    return count, last_at


def get_intent(intent_id, connection_id, business_id, store_id, actor_user_id, *, context=None):
    from . import connections
    connections.get_connection(connection_id, business_id, store_id, actor_user_id, context=context)
    conn = db.connect()
    try:
        row = conn.execute("SELECT i.id,i.order_id,i.created_at,o.state,o.provider_order_id,o.provider_status,o.funding_state,o.updated_at "
            "FROM business_os_supplier_intents i JOIN business_os_supplier_outbox o ON o.intent_id=i.id "
            "WHERE i.id=? AND i.connection_id=? AND i.business_id=? AND i.store_id=?",
            (intent_id, connection_id, business_id, store_id)).fetchone()
        if row is None:
            raise FulfillmentError("not_found", 404)
        return dict(row) | {"isSandbox": 1, "production_fulfillment_enabled": False}
    finally:
        conn.close()


#: The state of one obligation when no intent exists for it yet. Deliberately
#: not a member of the outbox's vocabulary: the outbox describes the delivery of
#: an intent, and there is no intent here. Every other value this field can take
#: is an outbox state passed through verbatim rather than re-spelled, because a
#: second copy of that vocabulary would be one more thing to keep in step with
#: `dispatch`, which is the only code that decides those names.
AWAITING_SUPPLIER_ORDER = "AWAITING_SUPPLIER_ORDER"

#: Why one obligation cannot be discharged right now. Each names the fact that
#: is absent, not the consequence -- a merchant told "cannot order" learns
#: nothing, and neither does an operator reading a log.
#:
#: A shipping quote is deliberately absent from this list. Every obligation
#: needs one and the server can obtain one on demand for nothing (it is a read),
#: so "no quote yet" is the next step rather than an obstacle. `blockers` stays
#: the set of things that cannot be resolved by asking this server again.
SUPPLIER_ORDER_ALREADY_PLACED = "SUPPLIER_ORDER_ALREADY_PLACED"
SHOP_BINDING_REQUIRED = "SHOP_BINDING_REQUIRED"
NOT_SHIPPING_LANE = "NOT_SHIPPING_LANE"
DESTINATION_MISSING = "DESTINATION_MISSING"
DESTINATION_INCOMPLETE = "DESTINATION_INCOMPLETE"
SUPPLIER_SKU_MISSING = "SUPPLIER_SKU_MISSING"
SUPPLIER_COST_UNKNOWN = "SUPPLIER_COST_UNKNOWN"

#: Every blocker, in the order a merchant should read them. `test_supplier_obligations`
#: pins this against the mobile copy, and the mutation battery inverts each one.
BLOCKERS = (SUPPLIER_ORDER_ALREADY_PLACED, SHOP_BINDING_REQUIRED, NOT_SHIPPING_LANE,
            DESTINATION_MISSING, DESTINATION_INCOMPLETE, SUPPLIER_SKU_MISSING,
            SUPPLIER_COST_UNKNOWN)

#: Supplier destination field -> the frozen checkout field holding the same fact.
#: `create_intent` requires all six; `marketplace_fulfillment` collects all six,
#: under different names, and freezes them onto the transaction. This tuple is
#: the entire bridge, and its absence is what gap 15 was.
#:
#: `shippingCountry` has no frozen counterpart because the checkout stores only
#: the alpha-2 code; it is resolved through `marketplace_fulfillment.country_name`
#: and so appears here as None.
_DESTINATION_REQUIRED = (
    ("shippingCountryCode", "address_country"),
    ("shippingCountry", None),
    ("shippingProvince", "address_region"),
    ("shippingCity", "address_city"),
    ("shippingCustomerName", "contact_name"),
    ("shippingAddress", "address_line1"),
)

#: Fields the supplier will use when present and does not require. Omitted
#: rather than sent empty: `_text` in `create_intent` rejects an empty string,
#: and a blank phone number is not a phone number.
_DESTINATION_OPTIONAL = (
    ("shippingZip", "address_postal_code"),
    ("shippingPhone", "contact_phone"),
    ("shippingAddress2", "address_line2"),
)


def supplier_destination(metadata):
    """Where the supplier must ship, read off the order the buyer actually paid.

    Why this is derived and not passed in
    -------------------------------------
    `create_intent` used to take `shipping_destination` from its caller, and its
    caller took it from the request body. Two things were wrong with that. The
    smaller one is that no surface could build it, so the one action that
    discharges an obligation had no caller outside a test that monkeypatched it
    away. The larger one is that a merchant-authenticated request could name any
    address at all -- the address the buyer paid to ship to was frozen on the
    transaction and nothing compared the two.

    So the destination is not an input. It is a fact of the order, and this is
    the only function that states it.

    What it reads
    -------------
    `marketplace_fulfillment.snapshot` freezes `{"kind", "details"}` onto
    `seller_transactions.metadata_json` under `fulfillment`, and
    `validate_details` has already cleaned, length-capped and tag-stripped every
    value in `details`. Nothing is re-validated here; `create_intent`'s `_text`
    remains the gate on what reaches the supplier.

    Returns ``(destination, blockers)``. Exactly one is non-empty.

    On `DESTINATION_INCOMPLETE` being reachable
    -------------------------------------------
    It is not defensive. `_REGION_REQUIRED` holds ten countries, so a buyer in
    the United Kingdom completes a valid checkout with no `address_region` --
    while CJ requires `shippingProvince` unconditionally. A UK dropship sale is
    therefore a real order that genuinely cannot be placed, and saying which
    field is missing is the difference between a merchant fixing it and a
    merchant watching a row say "awaiting" forever.
    """
    from services import marketplace_fulfillment as mf

    frozen = (metadata if isinstance(metadata, dict) else {}).get("fulfillment")
    details = (frozen or {}).get("details") if isinstance(frozen, dict) else None
    if not isinstance(details, dict) or not details:
        return {}, [DESTINATION_MISSING]
    # `order_kind` rather than `frozen["kind"]`: the stored value can be an
    # undecided lane, and resolving it is that function's job, not this one's.
    if mf.order_kind(metadata) != "shipping":
        return {}, [NOT_SHIPPING_LANE]

    destination = {}
    for supplier_key, frozen_key in _DESTINATION_REQUIRED:
        value = (mf.country_name(details.get("address_country"))
                 if frozen_key is None else details.get(frozen_key))
        value = value.strip() if isinstance(value, str) else ""
        if not value:
            return {}, [DESTINATION_INCOMPLETE]
        destination[supplier_key] = value
    for supplier_key, frozen_key in _DESTINATION_OPTIONAL:
        value = details.get(frozen_key)
        if isinstance(value, str) and value.strip():
            destination[supplier_key] = value.strip()
    return destination, []


def _frozen_metadata(conn, order_id):
    """The checkout record for one canonical order, or ``{}``.

    `marketplace_orders.seller_transaction_id` and `seller_transactions.id` are
    both INTEGER, so this join needs no cast -- unlike the intent join in
    `list_obligations`, where `create_intent` writes `str(order_id)` into a TEXT
    column. Worth stating because the two joins sit in the same function and
    look like they should be written the same way.
    """
    row = conn.execute(
        "SELECT t.metadata_json FROM marketplace_orders o "
        "JOIN seller_transactions t ON t.id = o.seller_transaction_id WHERE o.id = ?",
        (order_id,)).fetchone()
    if row is None:
        return {}
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


#: Blocker -> the refusal a caller asking to act on that order receives. Each
#: is a statement about the order, not about the request, which is why they are
#: 409s: the caller did nothing wrong and repeating the call cannot help until
#: the order itself changes.
_DESTINATION_REFUSALS = {
    DESTINATION_MISSING: "order_destination_missing",
    DESTINATION_INCOMPLETE: "order_destination_incomplete",
    NOT_SHIPPING_LANE: "order_not_shipping_lane",
}


def order_destination(order_id):
    """The supplier destination for one canonical order, or refuse and say why.

    The single place anything server-side turns a paid order into an address a
    supplier can ship to. `create_intent` and `quote_for_order` both call it, so
    the quote is quoted for exactly the address the order will be placed to --
    which is also what makes `create_intent`'s own quote cross-checks pass
    rather than being a hurdle a caller has to guess its way over.

    `_text` is still applied on the way out even though `validate_details` has
    already cleaned and length-capped every value. It is cheap, it is the gate
    this module has always had on what reaches a provider, and a frozen record
    written by an older version of the checkout is not something to trust on
    the strength of who wrote it.
    """
    try:
        resolved = int(str(order_id).strip())
    except (TypeError, ValueError):
        raise FulfillmentError("order_not_found", 404) from None
    conn = db.connect()
    try:
        metadata = _frozen_metadata(conn, resolved)
    finally:
        conn.close()
    destination, blockers = supplier_destination(metadata)
    if blockers:
        raise FulfillmentError(_DESTINATION_REFUSALS[blockers[0]])
    return {key: _text(value, key, 500) for key, value in destination.items()}


def _stocked_origin(pid, vid, *, connection_id, business_id, store_id, actor_user_id,
                    context=None, adapter=None):
    """The warehouse country to quote freight from.

    Not a hardcoded "CN". `reports/cj-discovery/CJ_DROPSHIPPING_FORENSIC_REPORT.md`
    records what CJ's freight endpoint actually wants: "Product properties come
    from productProEnSet and eligible origins from inventory." Quoting from a
    country that holds none of the stock prices a shipment that will not happen.

    Only a warehouse CJ has verified is eligible -- `_warehouse_stock` reports
    IN_STOCK solely for `verifiedWarehouse == 1` with a positive total, and
    everything else is UNKNOWN, which is not stock.
    """
    from . import gateway
    result = gateway.read("inventory", business_id=business_id, store_id=store_id,
                          actor_user_id=actor_user_id, connection_id=connection_id,
                          params={"pid": pid, "vid": vid}, context=context, adapter=adapter)
    for row in result["data"].get("variants", []):
        if row.get("vid") != vid:
            continue
        for warehouse in row.get("warehouses", []):
            country = warehouse.get("country")
            if (warehouse.get("state") == "IN_STOCK" and isinstance(country, str)
                    and len(country) == 2):
                return country.upper()
    raise FulfillmentError("supplier_origin_unknown")


def quote_for_order(*, connection_id, business_id, store_id, actor_user_id, order_id,
                    context=None, adapter=None):
    """Freight options for one paid order, priced to the address it is going to.

    Why this function is the other half of gap 15
    ---------------------------------------------
    `create_intent` demands a shipping-quote snapshot and then cross-checks five
    of that snapshot's *request* fields against the destination and its line
    list against the order's. The only writer of such a snapshot is
    `gateway.read("shipping", ...)`, and that had zero callers in
    `mobile-native/src`, `templates/` and `static/` -- measured, not assumed.

    The reason is visible in the cross-checks themselves: the request is not a
    lookup a screen can assemble out of what it holds. Origin comes from the
    inventory, properties from the product, weight and SKU from the bound
    variant, and every address field from a record frozen on the transaction
    that no client has ever seen. So the server assembles it, which also means
    the quote is necessarily for the address the order will be placed to rather
    than for whatever a caller typed.

    Each option carries the exact `expected_supplier_cost_cents` a merchant
    approving it would hand to `create_intent`, so the two-call flow does not
    ask the caller to reproduce this module's arithmetic. `create_intent`
    recomputes it and refuses on disagreement regardless -- the field is a
    convenience, never the authority.

    Options CJ marked unavailable are returned rather than dropped, with a null
    cost and CJ's own restrictions. A merchant who cannot see why the cheap
    service is missing assumes the platform lost it.
    """
    from . import connections, gateway

    destination = order_destination(order_id)
    connection = connections.get_connection(connection_id, business_id, store_id,
                                            actor_user_id, context=context)
    conn = db.connect()
    try:
        canonical = _canonical_order(conn, order_id)
    finally:
        conn.close()
    if canonical is None or str(canonical["seller_user_id"]) != str(connection.get("merchant_id")):
        raise FulfillmentError("order_not_found", 404)
    if (canonical["status"] in {"cancelled", "refunded", "disputed"}
            or canonical["listing_type"] != "physical"):
        raise FulfillmentError("order_not_eligible")
    quantity = canonical["quantity"]
    if not 1 <= quantity <= 10000:
        raise FulfillmentError("invalid_quantity", 400)

    binding = gateway.get_product_binding(connection_id, business_id, store_id,
                                          str(canonical["listing_id"]))
    detail = gateway.get_snapshot(binding["snapshot_id"], connection_id, business_id,
                                  store_id, actor_user_id, context=context)
    variant = next((v for v in detail["data"].get("variants", [])
                    if v.get("pid") == binding["pid"] and v.get("vid") == binding["vid"]), None)
    if not variant or variant.get("currency") != "USD":
        raise FulfillmentError("product_binding_mismatch", 400)
    sku = variant.get("sku")
    if not isinstance(sku, str) or not sku:
        raise FulfillmentError("supplier_sku_unknown")
    try:
        unit = Decimal(variant["price"])
        grams = Decimal(variant["weight_grams"])
        items_cost = (unit * quantity * 100)
        if (not unit.is_finite() or unit < 0 or not grams.is_finite() or grams <= 0
                or items_cost != items_cost.to_integral_value()):
            raise InvalidOperation
    except (InvalidOperation, KeyError, TypeError, ValueError):
        # `_money` answers None for a price CJ stated as a range and for a
        # weight it did not state at all. Either way the freight this would
        # quote is for a parcel whose contents we cannot price.
        raise FulfillmentError("supplier_shipping_inputs_unknown") from None
    properties = [p for p in (detail["data"].get("logistics_properties") or [])
                  if isinstance(p, str) and p]
    if not properties:
        # CJ routes batteries, liquids and magnets differently, and the adapter
        # refuses a request with no `productProp` at all. Substituting a plausible
        # "ORDINARY" would quote the wrong service for exactly the goods where it
        # matters most.
        raise FulfillmentError("supplier_logistics_properties_unknown")
    origin = _stocked_origin(binding["pid"], binding["vid"], connection_id=connection_id,
                             business_id=business_id, store_id=store_id,
                             actor_user_id=actor_user_id, context=context, adapter=adapter)

    # Every key here is dictated by a cross-check in `create_intent` or by
    # `CJAdapter.estimate_shipping`'s allowlist. `zip` is set only when the
    # destination carries one, because the cross-check compares the two and
    # `None == None` is the correct match for an address in a country that has
    # no postal codes.
    line = {"srcAreaCode": origin, "destAreaCode": destination["shippingCountryCode"],
            "province": destination["shippingProvince"], "city": destination["shippingCity"],
            "recipientAddress": destination["shippingAddress"],
            "weight": float(grams * quantity), "productProp": properties,
            "skuList": [sku],
            "freightTrialSkuList": [{"vid": binding["vid"], "sku": sku,
                                     "skuQuantity": quantity}]}
    if destination.get("shippingZip"):
        line["zip"] = destination["shippingZip"]
    result = gateway.read("shipping", business_id=business_id, store_id=store_id,
                          actor_user_id=actor_user_id, connection_id=connection_id,
                          params={"reqDTOS": [line]}, context=context, adapter=adapter)

    options = []
    for option in result["data"].get("quotes", []):
        expected = None
        if option.get("available") and option.get("currency") == "USD":
            try:
                freight = Decimal(option["provider_total"]) * 100
                if freight.is_finite() and freight >= 0 and freight == freight.to_integral_value():
                    expected = int(items_cost + freight)
            except (InvalidOperation, KeyError, TypeError, ValueError):
                expected = None
        options.append({
            "option_id": option.get("option_id"), "channel_id": option.get("channel_id"),
            "service": option.get("service"), "origin": option.get("origin"),
            "destination": option.get("destination"),
            "freight_total": option.get("provider_total"), "currency": option.get("currency"),
            "estimated_transit": option.get("estimated_transit"),
            "restrictions": option.get("restrictions") or [],
            "available": bool(option.get("available")),
            "quoted_at": option.get("quoted_at"),
            # None, not 0. An option whose landed cost we cannot state is not a
            # free one, and `create_intent` will refuse it either way.
            "expected_supplier_cost_cents": expected,
        })
    return {"snapshot_id": result["snapshot_id"], "order_id": canonical["id"],
            "listing_id": canonical["listing_id"], "quantity": quantity,
            "supplier_items_cost_cents": int(items_cost), "options": options,
            "state": result["data"].get("state"), "cached": bool(result.get("cached")),
            "isSandbox": 1, "production_fulfillment_enabled": False}


def list_obligations(connection_id, business_id, store_id, actor_user_id, *,
                     limit=100, context=None):
    """Which paid sales still owe a purchase from the supplier.

    Why this exists
    ---------------
    Before this function, the module could create one intent, read one intent
    by id, and claim one for the worker. Nothing could answer the merchant's
    actual question -- "which of my orders needs a supplier order placed?" --
    so a buyer could pay for a published, bound dropship listing and the only
    record of the obligation was a `marketplace_orders` row indistinguishable
    from a hand-stocked sale. The supplier-orders screen rendered the absence
    of a list it had no endpoint for, and its machine-readable gap note named a
    table (`business_os_supplier_fulfillment_intents`) that does not exist, so
    anyone implementing it grepped for a name nothing in the repo has.

    Derived, not stored
    -------------------
    An obligation is not a row. It is the join of a paid order to the supplier
    source of the listing it was placed on, and it is computed on read. A
    `needs_supplier_order` column on `marketplace_orders` would be a second copy
    of a fact the source row already states, and the two would disagree the
    first time a merchant switched a listing from DROPSHIP to STOCKED. Nothing
    here writes anything.

    The order id join is a cast, on purpose
    ---------------------------------------
    `marketplace_orders.id` is INTEGER; `business_os_supplier_intents.order_id`
    is TEXT, because `create_intent` writes `str(order_id)`. On PostgreSQL
    `i.order_id = o.id` is a type error; on SQLite it is worse -- it silently
    matches nothing, so the list would come back with every obligation looking
    unplaced and no test would fail. `CAST(o.id AS TEXT)` is correct on both.

    Merchant-only payload
    ---------------------
    Every row carries `supplier_cost_cents`, which is the merchant's buying
    price and the number a buyer must never see. That is safe here and only
    here: the caller is authorized through `connections.get_connection`, which
    is a merchant-scope read. Nothing on a buyer-facing route may call this, and
    no field of it may be forwarded into a buyer payload.

    What is deliberately not enumerated
    -----------------------------------
    An order that was refunded or cancelled *after* a supplier order was placed
    is a real situation with real money in it, and it is not in this list: the
    merchant needs to cancel with the supplier, which is a different action from
    placing one. Answering it needs a cancellation path that does not exist yet,
    so it stays a declared gap rather than a row here that implies it is handled.
    """
    from services.marketplace_listing_types import effective_listing_type

    from . import connections

    # Same authorization as `get_intent`: the connection read is what proves the
    # actor may see this scope at all. Nothing below re-derives ownership.
    connection = connections.get_connection(connection_id, business_id, store_id,
                                            actor_user_id, context=context)
    # One blocker that is a property of the connection rather than of any order,
    # and blocks every one of them: `create_intent` refuses `shop_binding_required`
    # with nothing bound, because `_validate_observed` proves a placed order came
    # back on the shop we bound and there is no such proof to make. Evaluated once
    # here rather than per row, but reported on every row, because the merchant
    # reads rows.
    unbound = [SHOP_BINDING_REQUIRED] if not connection.get("external_shop_id") else []
    try:
        capped = min(max(int(limit), 1), 200)
    except (TypeError, ValueError):
        raise FulfillmentError("invalid_limit", 400) from None

    ensure_schema()
    conn = db.connect()
    try:
        # `LOWER(o.status) = 'paid'` is the whole paid vocabulary of *this*
        # table, measured rather than assumed: `pulse_upsert_marketplace_order`
        # (bot.py) is the only writer of `marketplace_orders`, and it hardcodes
        # 'paid' in both the INSERT and its ON CONFLICT update. The column's
        # DDL default 'pending_payment' is the only other value it can hold.
        #
        # Do not widen this to `marketplace_listing_types.PAID_ORDER_STATUSES`
        # ({paid, checkout_completed, succeeded}). That set is the vocabulary of
        # `seller_transactions` / `creator_transactions`, which is upstream of
        # this table, not in it -- see `buyer_paid_marketplace_listing_ids`.
        # Widening would add two statuses this column never holds and imply the
        # two vocabularies are one.
        #
        # What *would* break this: a second writer of `marketplace_orders` that
        # spells paid differently. Then an obligation goes invisible, which is
        # exactly the defect this function exists to fix. `test_supplier_obligations.py`
        # pins the writer count for that reason.
        rows = conn.execute(
            "SELECT o.id AS order_id, o.listing_id, o.quantity, o.status AS order_status, "
            "o.amount_cents, o.currency, o.paid_at, o.created_at AS ordered_at, "
            "l.title, l.listing_type, l.product_type, "
            "s.provider AS provider, s.provider_product_id, s.provider_variant_id, "
            # `v.sku`, not `s.external_sku`. Two adjacent columns on rows this
            # query already joins, holding SKUs from two different levels of the
            # supplier's hierarchy: `importer.link_source` writes the *product's*
            # `external_sku` onto the source row, while `_write_variants` writes
            # each *variant's* onto the variant row. `create_intent` matches the
            # bound variant's, so the source column was never the answer -- and
            # reporting it produced `invalid_sku` when it was NULL (which is the
            # normal case) and `product_binding_mismatch` when it was not, a
            # refusal that reads like a broken binding rather than a wrong field.
            #
            # The product-level column is not also carried: one obligation
            # carrying two spellings of "the SKU" is an invitation to read the
            # one that cannot work.
            "v.sku AS supplier_sku, "
            "s.supplier_cost_cents, s.supplier_cost_currency, "
            "o.seller_transaction_id, t.metadata_json, "
            "i.id AS intent_id, i.created_at AS intent_created_at, "
            # `supplier_order_status`, not `provider_status`, on the way out.
            # The column keeps its name; the wire field does not, because
            # "provider status" is already Stripe's subscription status on this
            # platform, and the mobile entitlement drift guard
            # (`noClientTierInference.test.ts`) lists `provider_status` among the
            # raw membership fields no unlisted file may hold. Two unrelated
            # meanings under one name is how a guard ends up either exempting a
            # file it should not or being weakened to let one through.
            "b.state AS outbox_state, b.provider_order_id, "
            "b.provider_status AS supplier_order_status, "
            "b.funding_state, b.last_error, b.updated_at AS intent_updated_at "
            "FROM marketplace_orders o "
            "JOIN marketplace_listings l ON l.id = o.listing_id "
            "JOIN marketplace_product_sources s ON s.listing_id = o.listing_id "
            # The bound variant, by the provider id the source row bound. A
            # multi-variant import records no `provider_variant_id` on the
            # source (see `importer.link_source`), and `NULL = NULL` matches
            # nothing in SQL -- so those obligations arrive with no SKU and are
            # blocked as such, which is the truth about them.
            "LEFT JOIN marketplace_listing_variants v ON v.listing_id = o.listing_id "
            "  AND v.provider_variant_id = s.provider_variant_id "
            # The buyer's frozen address. No cast: `seller_transaction_id` and
            # `seller_transactions.id` are both INTEGER, unlike the intent join
            # immediately below, where `create_intent` writes `str(order_id)`
            # into a TEXT column.
            "LEFT JOIN seller_transactions t ON t.id = o.seller_transaction_id "
            # Live intents only. A retired one is an audit row about an attempt
            # that was proven never to have been sent, and joining it would put
            # its dead state and its dead reason on the obligation as though
            # they described the order's present situation. The condition sits
            # in the JOIN rather than the WHERE on purpose: in a `WHERE` it
            # would discard the whole *order* whose only intent was retired,
            # which is exactly the order that most needs to appear in this list.
            #
            # `uq_supplier_live_canonical_order` keeps this 1:1 -- it is unique
            # on `order_id` among rows with `superseded_at IS NULL` -- so the
            # fan-out this LEFT JOIN could otherwise cause cannot happen.
            "LEFT JOIN business_os_supplier_intents i ON i.order_id = CAST(o.id AS TEXT) "
            "  AND i.superseded_at IS NULL "
            "LEFT JOIN business_os_supplier_outbox b ON b.intent_id = i.id "
            "WHERE s.supplier_connection_id = ? AND s.business_id = ? AND s.store_id = ? "
            "AND s.fulfillment_mode = ? AND LOWER(o.status) = 'paid' "
            "ORDER BY o.paid_at DESC, o.id DESC LIMIT ?",
            (connection_id, business_id, store_id, "DROPSHIP", capped)).fetchall()
    finally:
        conn.close()

    obligations = []
    for row in rows:
        item = dict(row)
        # Resolved through the app's own rule rather than by reading the raw
        # column, exactly as `_canonical_order` does. A listing that predates
        # `listing_type` and only has `product_type` set must classify the same
        # way here as it does on the listing page, and as it does in the guard
        # inside `create_intent` that will refuse a non-physical order.
        listing_type = effective_listing_type(item.pop("listing_type"),
                                              item.pop("product_type"))
        if listing_type != "physical":
            continue
        # Popped unconditionally, so the raw column cannot also travel in the
        # payload beside the field derived from it -- two spellings of one fact,
        # and a caller free to read the one that is None.
        outbox_state = item.pop("outbox_state")
        intent_id = item.get("intent_id")
        # Neither of these travels. `metadata_json` is the whole frozen checkout
        # blob -- the buyer's address and the commercial quote -- read here only
        # to answer "can this be ordered?", and `seller_transaction_id` is a
        # payments-ledger key with nothing to do on a fulfilment screen.
        try:
            metadata = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            metadata = {}
        item.pop("seller_transaction_id", None)
        _, destination_blockers = supplier_destination(
            metadata if isinstance(metadata, dict) else {})
        blockers = []
        # Ordered by `BLOCKERS`, so the list reads the same way every time and a
        # merchant is told the thing they must act on first.
        #
        # Measured, not assumed. This used to be `if intent_id is not None` --
        # the existence of a row -- which told a merchant "You have already
        # ordered this from your supplier" about an intent that `dispatch` had
        # refused to send. Nothing had been ordered, the buyer had paid, and
        # because BLOCKED is terminal the row said it forever.
        #
        # A recoverable intent is one proven never sent and past retrying, and it
        # blocks nothing: the merchant re-approves a current cost and
        # `create_intent` retires it. Every other intent still blocks, including
        # the ones that cannot testify -- see `NEVER_SENT_STATES`.
        if intent_id is not None and not _recoverable_intent(outbox_state, item.get("provider_order_id")):
            blockers.append(SUPPLIER_ORDER_ALREADY_PLACED)
        blockers.extend(unbound)
        blockers.extend(destination_blockers)
        if not item.get("supplier_sku"):
            blockers.append(SUPPLIER_SKU_MISSING)
        # UNKNOWN cost is not zero cost. A NULL here means the import never
        # established what this variant costs, and placing an order whose price
        # we cannot state is how a merchant discovers the margin afterwards.
        if item.get("supplier_cost_cents") is None:
            blockers.append(SUPPLIER_COST_UNKNOWN)
        obligations.append({
            **item,
            "blockers": blockers,
            # Not "nothing is wrong" -- "this server can finish this without
            # asking anyone for anything it does not already have." The shipping
            # quote is deliberately not a precondition: `quote_for_order` obtains
            # one on demand and it costs nothing, so needing one is the next step
            # rather than an obstacle.
            "can_place_supplier_order": not blockers,
            "listing_type": listing_type,
            # One field, derived once, in one place. A `placed` boolean *beside*
            # a state would be the same fact twice; `supplier_order_placed`
            # below is instead the one thing `state` cannot express without the
            # caller knowing which outbox names mean "already sent".
            #
            # It is no longer `intent_id is not None`. That spelling made the
            # word "placed" mean "a row exists", so an intent this deployment
            # had refused to send reported `placed: true` -- the same wrong fact
            # as the `SUPPLIER_ORDER_ALREADY_PLACED` blocker above, and it has
            # to move with it. Two derivations of one claim disagreeing is how a
            # screen ends up rendering a contradiction.
            #
            # An intent with no outbox row should be impossible -- `create_intent`
            # writes both in one transaction, and rolls both back together --
            # but the LEFT JOIN can express it, so it needs an answer.
            #
            # That answer is `UNKNOWN`, not `AWAITING_SUPPLIER_ORDER`. An intent
            # exists, so a purchase may already have been made; saying "no
            # supplier order yet" beside `supplier_order_placed: true` is both a
            # contradiction and the one error that costs money, because it
            # invites a merchant to order the same goods twice. `UNKNOWN` is
            # exactly the state whose copy tells them not to.
            "state": outbox_state or ("UNKNOWN" if intent_id else AWAITING_SUPPLIER_ORDER),
            "supplier_order_placed": intent_id is not None and not _recoverable_intent(
                outbox_state, item.get("provider_order_id")),
        })
    # Envelope-level, not per row, because it is one fact about the deployment
    # rather than a property of any sale -- the same reason `isSandbox` sits
    # here. Deliberately *not* folded into each row's `state`: a queued order's
    # state is READY whether or not a drain exists, and a `state` that changed
    # meaning depending on the worker would be two facts under one name, which
    # is the drift this module keeps paying for elsewhere.
    return {"obligations": obligations, "isSandbox": 1,
            "production_fulfillment_enabled": False,
            "drain": drain_status()}
