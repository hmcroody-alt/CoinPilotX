"""Business OS — Marketplace: the canonical order state machine + ledger settlement.

This is the real ``orders`` table the legacy 0-row ``marketplace_orders_placeholder``
was always meant to become. It owns the order lifecycle

    created ──pay──▶ paid ──fulfill──▶ fulfilled ──complete──▶ completed
        │                 │                  │
        └──cancel──▶ cancelled  └──refund──▶ refunded  └──refund──▶ refunded

and every money movement rides the SHARED canonical double-entry ledger
(``services.business_os.ledger.ledger``) — this module mutates NO bare balance and
creates NO second financial foundation. Integer cents everywhere; no floats.

Money accounts (mirrors advertising's escrow discipline):

  * ``platform:marketplace_intake``  — external buyer money already captured by the
    payment provider (allow-negative liability, ``platform:`` prefix);
  * ``mkt_order_escrow:<order_id>``  — per-order escrow that HOLDS the captured funds;
    NOT allow-negative, so it is overdraft-guarded by the ledger (a refund/settle that
    would take it negative is refused);
  * ``seller_payable:<seller_id>``   — accrued amount owed to the seller (NOT the same
    as a bank disbursement — see the module note);
  * ``platform:marketplace_revenue`` — platform fee accrual (allow-negative).

Flows:
  * **capture** (pay):   intake ─▶ escrow            (order total)
  * **settle** (complete): escrow ─▶ revenue (fee) + escrow ─▶ seller_payable (net)
  * **refund** (refunds.py): escrow ─▶ intake        (while funds are still in escrow)

NOTE ON PAYOUT EXECUTION: crediting ``seller_payable`` is the canonical *accrual* of
what the seller is owed. The actual bank/Stripe transfer that moves money OUT to the
seller is a provider-side disbursement that this sandbox cannot perform and this module
deliberately does not attempt; it is surfaced honestly in the completion report.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace.service import MarketplaceError
from services.business_os.marketplace import policy as _policy
from services.business_os.ledger import ledger as _ledger
from services import marketplace_seller_identity as _identity

try:
    from services.business_os.marketplace import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None

# The payments notification engine — the one that renders and sends *email*.
# It is a different thing from ``_notify`` above, which writes the in-app alert
# row and nothing else. Imported under a guard for the same reason ``_notify``
# is: a Business OS order must still be fulfillable in an environment where the
# notification layer failed to load.
try:
    from services import payments_notifications as _payments_notify
except Exception:  # pragma: no cover
    _payments_notify = None

LOGGER = logging.getLogger(__name__)


# The events/ticketing take rate, which imports this from here and is a separate
# product with its own pricing. It is NOT a marketplace fee default: marketplace
# commission comes from `services.business_os.marketplace.policy` and nothing
# else. It sat here as a "keep existing economics" fallback long enough to be
# mistaken for one, and production has never charged it — zero orders, zero
# products, zero settlements in either marketplace lane.
DEFAULT_FEE_BPS = 1000


ORDER_STATUSES = {"created", "paid", "fulfilled", "completed", "cancelled", "refunded"}
ALLOWED_ORDER_TRANSITIONS = {
    "created": {"paid", "cancelled"},
    "paid": {"fulfilled", "refunded"},
    "fulfilled": {"completed", "refunded"},
    "completed": set(),
    "cancelled": set(),
    "refunded": set(),
}
# States in which captured funds are still sitting in escrow (refundable).
IN_ESCROW_STATUSES = {"paid", "fulfilled"}


# --- account helpers (shared with refunds.py) -------------------------------
INTAKE_ACCOUNT = "platform:marketplace_intake"
PLATFORM_REVENUE_ACCOUNT = "platform:marketplace_revenue"


def escrow_account(order_id: Any) -> str:
    return f"mkt_order_escrow:{order_id}"


def seller_payable_account(seller_user_id: Any) -> str:
    return f"seller_payable:{_svc._sid(seller_user_id)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _fee_split(total_cents: int, fee_bps: int) -> tuple:
    fee = (int(total_cents) * int(fee_bps)) // 10000
    net = int(total_cents) - fee
    return fee, net


# --- reads ------------------------------------------------------------------
def get_order(order_id: Any, *, requester_user_id: Any = None, conn=None) -> Optional[dict]:
    """Fetch an order. When ``requester_user_id`` is given, only the buyer or the
    seller may read it; anyone else gets None (existence not leaked)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM business_os_mkt_orders WHERE order_id = ?", (str(order_id),))
        row = _row(cur.fetchone())
        if row is None:
            return None
        if requester_user_id is not None:
            rid = _svc._sid(requester_user_id)
            if rid not in (row.get("buyer_user_id"), row.get("seller_user_id")):
                return None
        return row
    finally:
        if owned:
            conn.close()


def get_order_items(order_id: Any, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_order_items WHERE order_id = ? ORDER BY id",
            (str(order_id),)).fetchall()]
    finally:
        if owned:
            conn.close()


def get_order_events(order_id: Any, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_order_events WHERE order_id = ? ORDER BY id",
            (str(order_id),)).fetchall()]
    finally:
        if owned:
            conn.close()


def list_orders(*, buyer_user_id: Any = None, seller_user_id: Any = None,
                status: Optional[str] = None, limit: int = 200, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = "SELECT * FROM business_os_mkt_orders WHERE 1=1"
        params: list = []
        if buyer_user_id is not None:
            q += " AND buyer_user_id = ?"; params.append(_svc._sid(buyer_user_id))
        if seller_user_id is not None:
            q += " AND seller_user_id = ?"; params.append(_svc._sid(seller_user_id))
        if status:
            q += " AND status = ?"; params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        return [_row(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


# --- internal state-machine helper ------------------------------------------
def _record_event(conn, order_id, from_status, to_status, actor, reason=None, meta=None):
    conn.execute(
        "INSERT INTO business_os_mkt_order_events "
        "(order_id, from_status, to_status, actor, reason, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(order_id), from_status, to_status,
         None if actor is None else str(actor), reason,
         None if meta is None else json.dumps(meta, sort_keys=True), _now_iso()))


def _assert_transition(cur_status, target):
    if target not in ALLOWED_ORDER_TRANSITIONS.get(cur_status, set()):
        raise MarketplaceError(
            f"Illegal order transition {cur_status} -> {target}.", 409, "illegal_transition")


# --- create -----------------------------------------------------------------
def create_order(buyer_user_id: Any, product_id: Any, *, quantity: int = 1,
                 context: Optional[dict] = None, conn=None) -> dict:
    """Place an order for an ``active`` product. Snapshots price + fee. Moves NO money
    (that happens at ``pay_order``). A seller cannot buy their own product."""
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise MarketplaceError("quantity must be a positive integer.", 400, "invalid_quantity")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        product = _svc.get_product(product_id, for_public=True, conn=conn)
        if product is None:
            raise MarketplaceError("Product not available.", 404, "not_found")
        if product.get("seller_user_id") == _svc._sid(buyer_user_id):
            raise MarketplaceError("You cannot buy your own product.", 400, "self_purchase")
        inv = product.get("inventory_qty")
        if inv is not None and inv < quantity:
            raise MarketplaceError("Not enough inventory.", 409, "insufficient_inventory")
        unit = int(product["price_cents"])
        # One fee authority for both marketplace lanes. This lane used to fall
        # back to a 10% "legacy" rate whenever the policy gates were shut, so the
        # same product cost a seller 10% here and something else on the other
        # lane — and the snapshot then relabelled itself to hide the divergence.
        commercial = _policy.quote(
            unit_price_cents=unit, quantity=quantity,
            currency=product.get("currency", "usd"),
        )
        subtotal = commercial.merchandise_net_cents
        fee_bps = commercial.platform_fee_bps
        fee, net = _fee_split(subtotal, fee_bps)
        snapshot = commercial.as_dict()
        oid = "mkto_" + uuid.uuid4().hex
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_mkt_orders "
            "(order_id, buyer_user_id, seller_user_id, status, currency, subtotal_cents, "
            "total_cents, platform_fee_bps, platform_fee_cents, seller_net_cents, "
            "refunded_cents, fulfillment_type, merchandise_gross_cents, seller_discount_cents, "
            "merchandise_net_cents, shipping_cents, tax_cents, buyer_service_fee_cents, "
            "seller_shipping_credit_cents, fee_policy_version, fee_base, return_policy_version, "
            "listing_policy_version, payout_policy_version, payout_status, policy_snapshot_json, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_order', ?, ?, ?)",
            (oid, _svc._sid(buyer_user_id), product["seller_user_id"],
             product.get("currency", "usd"), subtotal, subtotal, fee_bps, fee, net,
             product.get("fulfillment_type", "physical"), commercial.merchandise_gross_cents,
             commercial.seller_discount_cents, commercial.merchandise_net_cents,
             commercial.shipping_cents, commercial.tax_cents,
             commercial.buyer_service_fee_cents, commercial.seller_shipping_credit_cents,
             snapshot["fee_policy_version"], commercial.fee_base,
             commercial.return_policy_version, commercial.listing_policy_version,
             commercial.payout_policy_version,
             json.dumps(snapshot, sort_keys=True), now, now))
        conn.execute(
            "INSERT INTO business_os_mkt_order_items "
            "(order_id, product_id, title, unit_price_cents, quantity, line_total_cents, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (oid, product["product_id"], product.get("title"), unit, quantity, subtotal, now))
        _record_event(conn, oid, None, "created", buyer_user_id)
        if owned:
            conn.commit()
        return get_order(oid, conn=conn)
    finally:
        if owned:
            conn.close()


# --- pay (capture into escrow, atomic inventory decrement) ------------------
def pay_order(order_id: Any, buyer_user_id: Any, *, context: Optional[dict] = None,
              conn=None) -> dict:
    """created ─▶ paid. Atomically decrements inventory, then captures the order total
    into the per-order escrow via the canonical ledger. If capture fails, the inventory
    decrement and the state flip are COMPENSATED so the order returns to ``created``.

    NOTE: this models the *post-capture* bookkeeping. Collecting the card payment
    itself (Stripe PaymentIntent) is the provider's job and is done before this call;
    we record the captured funds moving into escrow.

    **This function will not run inside a caller's transaction, and says so.**
    ``post_entry`` opens, commits, and closes its own connection by design, so
    the capture can never be part of a transaction this function did not open.
    The old code accepted a caller's ``conn`` anyway and produced two concrete
    failures from it. It committed the inventory decrement only when it owned the
    connection, so a borrowed connection left an *uncommitted* decrement beside a
    *committed* ledger post. And it compensated on a second connection, which
    could not see that uncommitted decrement, so the ``+ quantity`` reversal
    landed on the committed value instead: a failed capture created stock.

    Rather than half-support that, a supplied ``conn`` is now refused. Nothing in
    the codebase passed one — the parameter was an invitation to a bug rather
    than a feature anyone used — and a loud refusal is worth more than a silent
    path that mis-states inventory.

    On the owned path the order writes are committed before the capture is
    attempted, which is not merely convenient: on SQLite ``post_entry`` takes a
    database-wide write lock via ``BEGIN IMMEDIATE``, so holding this
    transaction open across the capture deadlocks against it. Compensation and
    the ``capture_txn_ref`` write now both run on this function's own connection
    instead of on two further ones.

    That leaves one irreducible window: the capture succeeds and the
    ``capture_txn_ref`` write then fails. It is the safe direction — the money is
    in escrow and the order is marked paid — and unlike before it is
    *detectable*: see :func:`reconcile_captures`, the drift check whose absence
    was the standing reason this defect was recorded as open.
    """
    _svc._require_enabled()
    if conn is not None:
        raise MarketplaceError(
            "pay_order cannot run inside a caller-supplied transaction: the "
            "ledger capture commits on its own connection and cannot be rolled "
            "back with yours.", 500, "capture_needs_own_transaction")
    _svc._require_not_held(buyer_user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, requester_user_id=buyer_user_id, conn=conn)
        if order is None:
            raise MarketplaceError("Order not found.", 404, "not_found")
        if order.get("buyer_user_id") != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "paid")

        items = get_order_items(order_id, conn=conn)
        # 1) atomic inventory decrement for each line (guarded), then flip state.
        for it in items:
            cur = conn.execute(
                "UPDATE business_os_mkt_products SET inventory_qty = inventory_qty - ?, "
                "updated_at = ? WHERE product_id = ? AND inventory_qty IS NOT NULL "
                "AND inventory_qty >= ?",
                (it["quantity"], _now_iso(), it["product_id"], it["quantity"]))
            if getattr(cur, "rowcount", 0) == 0:
                # Either unlimited (NULL inventory) or genuinely out of stock.
                prod = conn.execute(
                    "SELECT inventory_qty FROM business_os_mkt_products WHERE product_id = ?",
                    (it["product_id"],)).fetchone()
                inv = None if prod is None else (prod["inventory_qty"] if hasattr(prod, "keys") else prod[0])
                if inv is not None:
                    raise MarketplaceError("Item sold out.", 409, "insufficient_inventory")
                # NULL inventory ⇒ unlimited (digital); nothing to decrement.
        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'paid', updated_at = ? "
            "WHERE order_id = ?", (_now_iso(), str(order_id)))
        _record_event(conn, order_id, "created", "paid", buyer_user_id)
        # Committed before the capture, and it has to be: `post_entry` opens its
        # own connection and on SQLite takes a database-wide write lock, so a
        # write transaction still open here deadlocks against it. The commit is
        # what releases that lock.
        conn.commit()

        # 2) capture into escrow (idempotent).
        try:
            txn = _ledger.post_entry(
                idempotency_key=f"mkt_capture:{order_id}",
                actor=_svc._sid(buyer_user_id),
                amount_cents=int(order["total_cents"]),
                currency=order.get("currency", "usd"),
                entry_type="marketplace_capture",
                source=INTAKE_ACCOUNT,
                destination=escrow_account(order_id),
                reason="Marketplace order captured into escrow.",
                related_object=str(order_id))
        except Exception:
            # Compensate on the SAME connection that made the decrement, rather
            # than opening a second one. Two connections were how a reversal
            # could ever be applied to a value that did not include the change it
            # was reversing.
            for it in items:
                conn.execute(
                    "UPDATE business_os_mkt_products SET inventory_qty = inventory_qty + ? "
                    "WHERE product_id = ? AND inventory_qty IS NOT NULL",
                    (it["quantity"], it["product_id"]))
            conn.execute(
                "UPDATE business_os_mkt_orders SET status = 'created', updated_at = ? "
                "WHERE order_id = ?", (_now_iso(), str(order_id)))
            _record_event(conn, order_id, "paid", "created", buyer_user_id,
                          reason="capture_failed")
            # Committed because it is the record of the reversal; losing it would
            # leave the order 'paid' with nothing behind it.
            conn.commit()
            raise MarketplaceError("Payment capture failed.", 502, "capture_failed")

        conn.execute(
            "UPDATE business_os_mkt_orders SET capture_txn_ref = ?, updated_at = ? "
            "WHERE order_id = ?",
            (txn.get("transaction_id"), _now_iso(), str(order_id)))
        conn.commit()

        _emit(order.get("seller_user_id"), "order_paid", order_id)
        return get_order(order_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- fulfill ----------------------------------------------------------------
def fulfill_order(order_id: Any, seller_user_id: Any, *, tracking_ref: Optional[str] = None,
                  context: Optional[dict] = None, conn=None) -> dict:
    """paid ─▶ fulfilled. Seller-only. Physical orders may carry a tracking ref;
    digital orders are considered delivered immediately.

    The buyer's shipping email is sent from here rather than from either route
    that reaches this point: ``services.business_os.orders.service`` is a facade
    over this function and ``marketplace.api`` calls it directly, so this is the
    single door into ``fulfilled`` and one emit site covers both.
    """
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, conn=conn)
        if order is None or order.get("seller_user_id") != _svc._sid(seller_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "fulfilled")
        # `delivered_at` is written once, here, and anchors the buyer's return
        # window. It is kept distinct from `updated_at`, which any later status
        # change rewrites — anchoring a deadline on a mutable column would let an
        # unrelated edit silently extend or shorten a buyer's rights.
        now = _now_iso()
        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'fulfilled', tracking_ref = ?, "
            "delivered_at = COALESCE(delivered_at, ?), "
            "updated_at = ? WHERE order_id = ?",
            (tracking_ref, now, now, str(order_id)))
        _record_event(conn, order_id, "paid", "fulfilled", seller_user_id,
                      meta={"tracking_ref": tracking_ref} if tracking_ref else None)
        if owned:
            conn.commit()
        _emit(order.get("buyer_user_id"), "order_fulfilled", order_id)
        fulfilled = get_order(order_id, conn=conn)
        if owned:
            # Only when this call owns the commit. A caller that passed its own
            # connection has not committed yet and may still roll back, and a
            # buyer told "your order shipped" cannot be untold. (The in-app
            # `_emit` above does not make that distinction; leaving its
            # placement alone is deliberate — it predates this and changing when
            # an existing alert fires is a separate decision from adding a new
            # one.) Reads `fulfilled`, not `order`: `order` is the pre-UPDATE
            # row and still carries the old, empty tracking reference.
            _email_buyer_shipped(conn, fulfilled)
        return fulfilled
    finally:
        if owned:
            conn.close()


# --- complete (settle escrow → fee + seller net) ----------------------------
def complete_order(order_id: Any, buyer_user_id: Any, *, context: Optional[dict] = None,
                   actor: Any = None, conn=None) -> dict:
    """fulfilled ─▶ completed. Buyer confirms receipt (or an admin completes it),
    which SETTLES escrow: the platform fee accrues to marketplace revenue and the
    remainder accrues to the seller's payable account. Settlement is computed from
    the CURRENT escrow balance so any prior partial refund is already netted out and
    escrow always zeroes exactly."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, conn=conn)
        if order is None:
            raise MarketplaceError("Order not found.", 404, "not_found")
        # Buyer confirms; an explicit admin actor may also complete.
        actor = actor if actor is not None else buyer_user_id
        if actor is None or (_svc._sid(actor) != order.get("buyer_user_id")
                             and _svc._sid(buyer_user_id) != order.get("buyer_user_id")):
            # Only the buyer path is exposed here; admin completion goes via admin.py.
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "completed")

        remaining = _ledger.get_balance(escrow_account(order_id), order.get("currency", "usd"))
        # The rate this order was quoted at, not a default. An order with no
        # recorded rate was never quoted a commission, so taking one at
        # settlement would be charging a fee the seller never saw.
        fee, net = _fee_split(remaining, order.get("platform_fee_bps") or 0)

        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'completed', updated_at = ? "
            "WHERE order_id = ?", (_now_iso(), str(order_id)))
        _record_event(conn, order_id, "fulfilled", "completed", actor,
                      meta={"settled_cents": remaining, "fee_cents": fee, "net_cents": net})
        if owned:
            conn.commit()

        settle_ref = None
        if fee > 0:
            _ledger.post_entry(
                idempotency_key=f"mkt_settle_fee:{order_id}",
                actor=_svc._sid(actor), amount_cents=fee,
                currency=order.get("currency", "usd"), entry_type="marketplace_fee",
                source=escrow_account(order_id), destination=PLATFORM_REVENUE_ACCOUNT,
                reason="Marketplace platform fee.", related_object=str(order_id))
        if net > 0:
            txn = _ledger.post_entry(
                idempotency_key=f"mkt_settle_net:{order_id}",
                actor=_svc._sid(actor), amount_cents=net,
                currency=order.get("currency", "usd"), entry_type="marketplace_payout_accrual",
                source=escrow_account(order_id),
                destination=seller_payable_account(order.get("seller_user_id")),
                reason="Marketplace seller net accrual.", related_object=str(order_id))
            settle_ref = txn.get("transaction_id")
        if settle_ref:
            c3 = db.connect()
            try:
                c3.execute(
                    "UPDATE business_os_mkt_orders SET settle_txn_ref = ?, updated_at = ? "
                    "WHERE order_id = ?", (settle_ref, _now_iso(), str(order_id)))
                c3.commit()
            finally:
                c3.close()

        _emit(order.get("seller_user_id"), "order_completed", order_id)
        return get_order(order_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- cancel (only before payment; no money moved) ---------------------------
def cancel_order(order_id: Any, buyer_user_id: Any, *, reason: Optional[str] = None,
                 context: Optional[dict] = None, conn=None) -> dict:
    """created ─▶ cancelled. Buyer-only, and only before payment (no funds moved). A
    paid order is unwound through a refund, not a cancel."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = get_order(order_id, requester_user_id=buyer_user_id, conn=conn)
        if order is None or order.get("buyer_user_id") != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        _assert_transition(order.get("status"), "cancelled")
        conn.execute(
            "UPDATE business_os_mkt_orders SET status = 'cancelled', updated_at = ? "
            "WHERE order_id = ?", (_now_iso(), str(order_id)))
        _record_event(conn, order_id, "created", "cancelled", buyer_user_id, reason=reason)
        if owned:
            conn.commit()
        return get_order(order_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- money summary (audit tool) ---------------------------------------------
def order_money_summary(order_id: Any, conn=None) -> dict:
    """Derived money view for an order straight off the canonical ledger balances —
    never a stored authority."""
    order = get_order(order_id, conn=conn)
    if order is None:
        raise MarketplaceError("Order not found.", 404, "not_found")
    cur = order.get("currency", "usd")
    return {
        "order_id": str(order_id),
        "status": order.get("status"),
        "total_cents": order.get("total_cents"),
        "refunded_cents": order.get("refunded_cents"),
        "escrow_balance_cents": _ledger.get_balance(escrow_account(order_id), cur),
        "seller_payable_cents": _ledger.get_balance(
            seller_payable_account(order.get("seller_user_id")), cur),
        "platform_fee_cents": order.get("platform_fee_cents"),
        "currency": cur,
    }


# --- reconciliation ---------------------------------------------------------
_CAPTURE_KEY_PREFIX = "mkt_capture:"

# Statuses in which the money has legitimately left escrow again. `completed`
# settles escrow out to the fee and payable accounts, and `refunded` sends it
# back, so an empty escrow in those states is correct rather than drift.
_SETTLED_STATUSES = {"completed", "refunded", "cancelled"}


def reconcile_captures(*, limit: int = 500, conn=None) -> dict:
    """Find orders whose state and whose captured money disagree.

    :func:`pay_order` cannot make the order write and the ledger write one
    transaction, because ``post_entry`` owns and commits its own connection. The
    window is small and it always fails in the safe direction, but "small" is not
    "never" and an undetected discrepancy in a money system is the one that grows.
    This is the detector the fix would otherwise have been missing — the reason
    money bug #4 was previously recorded as deliberately open.

    Reports, never repairs. Two orders can look identical here and need opposite
    treatment — one is a crash mid-commit, the other is a support agent who moved
    money by hand — and a job that guesses is a job that will eventually guess
    wrong with somebody's money. Each finding names the order, the kind of drift,
    and both numbers, so the decision belongs to a person.

    Three kinds of drift are looked for:

    * ``captured_not_paid`` — escrow holds the money but the order never reached
      ``paid``. This is the irreducible window: capture succeeded and the
      surrounding commit did not. Re-running :func:`pay_order` is safe and is
      usually the repair, because the capture key is derived from the order id
      and the second capture is a no-op.
    * ``paid_not_captured`` — the order says ``paid`` and escrow is empty. Under
      the current code this should be unreachable; if it appears, something wrote
      the status outside this module.
    * ``missing_capture_ref`` — captured and paid, but ``capture_txn_ref`` is
      unset, so the order cannot name the transaction that funded it. Harmless to
      balances and awkward for anyone doing an investigation.
    """
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = _rows_list(conn.execute(
            "SELECT order_id, status, total_cents, currency, capture_txn_ref "
            "FROM business_os_mkt_orders ORDER BY created_at DESC LIMIT ?",
            (int(limit),)).fetchall())
        findings = []
        for row in rows:
            order_id = str(row.get("order_id"))
            status = str(row.get("status") or "")
            currency = row.get("currency") or "usd"
            escrow = _ledger.get_balance(escrow_account(order_id), currency)
            captured = _capture_transaction(order_id, conn=conn)
            if status in _SETTLED_STATUSES:
                # Escrow has legitimately moved on; nothing here is comparable.
                continue
            if captured is not None and status == "created":
                findings.append({
                    "order_id": order_id, "kind": "captured_not_paid",
                    "status": status, "escrow_balance_cents": escrow,
                    "captured_cents": int(captured.get("amount_cents") or 0),
                    "capture_transaction_id": captured.get("transaction_id"),
                })
            elif captured is None and status in {"paid", "fulfilled"}:
                findings.append({
                    "order_id": order_id, "kind": "paid_not_captured",
                    "status": status, "escrow_balance_cents": escrow,
                    "captured_cents": 0, "capture_transaction_id": None,
                })
            elif captured is not None and not row.get("capture_txn_ref"):
                findings.append({
                    "order_id": order_id, "kind": "missing_capture_ref",
                    "status": status, "escrow_balance_cents": escrow,
                    "captured_cents": int(captured.get("amount_cents") or 0),
                    "capture_transaction_id": captured.get("transaction_id"),
                })
        return {
            "scanned": len(rows),
            "drift_count": len(findings),
            "findings": findings,
        }
    finally:
        if owned:
            conn.close()


def _capture_transaction(order_id: Any, conn=None) -> Optional[dict]:
    """The capture posting for an order, or None if it was never made.

    Looked up by the derived idempotency key rather than by scanning entries,
    because that key is the thing the ledger enforces uniqueness on: if a row
    exists under it, exactly one capture happened for this order, which is the
    fact this function is asked for.
    """
    try:
        return _ledger.get_transaction(f"{_CAPTURE_KEY_PREFIX}{order_id}", conn=conn)
    except Exception:
        return None


def _rows_list(rows) -> list:
    out = []
    for r in rows or []:
        d = _row(r)
        if d is not None:
            out.append(d)
    return out


def _emit(user_id, kind, order_id):
    if _notify is None or user_id is None:
        return
    try:
        _notify.emit_order_event(user_id, kind, order_id)
    except Exception:
        pass


def _item_summary(items: list) -> str:
    """One line naming what shipped: the first item, plus a count of the rest.

    An order carries one item row today, but the table is keyed to allow more,
    and an email that silently named only the first of four would be worse than
    one that says so.
    """
    titles = [str((item or {}).get("title") or "").strip() for item in (items or [])]
    titles = [t for t in titles if t]
    if not titles:
        return ""
    if len(titles) == 1:
        return titles[0][:160]
    return f"{titles[0][:120]} and {len(titles) - 1} more"


def _email_buyer_shipped(conn, order: Optional[dict]) -> None:
    """Send the buyer the ``order_shipped`` payment email for a fulfilled order.

    The in-app alert raised beside this one is server-derived text with no
    tracking reference, no item, no store and no name — it reads "Your order has
    been fulfilled." and stops. This is the same ``order_shipped`` email Pulse
    Marketplace sends, so a buyer gets the same facts whichever of the two
    marketplaces the order was placed in.

    **Physical orders only.** ``fulfill_order`` treats a digital order as
    delivered the moment it is placed; mailing that buyer a "Track order" button
    beside an empty tracking field describes a shipment that never happened.
    This is the Business OS spelling of the shipping-lane guard the Pulse side
    gets from ``SHIPPING_KINDS``.

    Runs on the caller's connection, after the commit. It never raises and never
    rolls anything back: the fulfilment is already durable by the time this is
    called, so a lookup that fails must cost the buyer a line of the email
    rather than the email itself — every key the template reads is optional.
    """
    order = dict(order or {})
    if str(order.get("fulfillment_type") or "physical") != "physical":
        return
    order_id = str(order.get("order_id") or "")
    if _payments_notify is None:
        # Logged rather than swallowed: "nobody was emailed" and "everything is
        # fine" are indistinguishable from the outside, which is precisely how
        # this event went unproduced on the other marketplace for so long.
        LOGGER.warning("BUSOS_MKT_SHIPPED_NO_NOTIFIER order=%s", order_id)
        return
    try:
        # Business OS keys users as TEXT; the notification engine addresses them
        # by integer id. A buyer this module cannot name in the engine's terms
        # is a buyer it cannot mail.
        buyer_id = int(str(order.get("buyer_user_id") or "").strip())
    except (TypeError, ValueError):
        LOGGER.warning("BUSOS_MKT_SHIPPED_BAD_RECIPIENT order=%s buyer=%s",
                       order_id, order.get("buyer_user_id"))
        return
    if buyer_id <= 0:
        return

    context = {
        "order_id": order_id,
        # The order id is already the reference a buyer would quote to support,
        # so it is not decorated into something support cannot search for.
        "order_reference": order_id,
        "tracking_reference": str(order.get("tracking_ref") or ""),
        # No carrier key: ``business_os_mkt_orders`` records a tracking
        # reference but not who is carrying it, and the template omits a fact
        # whose value is empty rather than printing a blank row.
    }
    try:
        context["buyer_first_name"] = _payments_notify.greeting_first_name(_row(conn.execute(
            "SELECT full_name, display_name, username FROM users WHERE user_id = ? LIMIT 1",
            (buyer_id,)).fetchone()))
        seller = _row(conn.execute(
            "SELECT display_name FROM business_os_mkt_sellers WHERE seller_user_id = ? LIMIT 1",
            (_svc._sid(order.get("seller_user_id")),)).fetchone()) or {}
        # Through the canonical accessor rather than off the column, so this
        # buyer-facing name is resolved the same way every other buyer-facing
        # surface resolves it. Reading the column raw differs in one case that
        # matters here: a ``display_name`` of "   " is not empty, so it would
        # survive the template's omit-when-empty rule and print exactly the
        # blank row that rule exists to prevent. ``store_name`` strips, so an
        # all-whitespace name becomes "" and the store line is dropped.
        #
        # ``store_name`` and not ``display_store_name``: the latter substitutes
        # "PulseSoc Store" for an absent name, and this template would rather
        # say nothing about the store than attribute the shipment to a shop the
        # buyer never bought from. Omission is this email's existing choice and
        # is preserved.
        context["store_name"] = _identity.store_name(seller)
        context["item_summary"] = _item_summary(get_order_items(order_id, conn=conn))
    except Exception as exc:  # noqa: BLE001 - see docstring
        LOGGER.warning("BUSOS_MKT_SHIPPED_CONTEXT_FAILED order=%s error=%s", order_id, exc)

    # ``email_only``, unlike the Pulse Marketplace emit site. There the buyer is
    # told nothing else, so the in-app row is the only one there is. Here
    # ``_emit(..., "order_fulfilled", ...)`` has already sent this same buyer an
    # alert titled "Order shipped" through the orchestrator; the full fan-out
    # would put a second one beside it. Email is the channel that was missing.
    _payments_notify.emit(_payments_notify.ORDER_SHIPPED, buyer_id, context,
                          email_only=True)
