"""Business OS — Marketplace: offers (negotiation) + inventory reservations.

The mission's biggest zero-foundation gap: before this module there was no offers
table, no routes, and the mobile client's ``MARKETPLACE_OFFERS_ENABLED`` flag was
hard-false over a stub API. This module is the canonical server-authoritative
negotiation engine, built additively — it creates its OWN tables, never mutates
the legacy pulse tables, and never edits the order/payment engine it hands off to.

State machine (client sends verbs, never raw statuses):

    needs_response ──counter──▶ countered ──counter──▶ countered … (alternating)
        │  │  │                     │  │  │
        │  │  └──decline──▶ declined┘  │  │
        │  └──withdraw──▶ withdrawn────┘  │
        └───────accept──▶ accepted ◀──accept
                              │  │  │
                              │  │  └──withdraw──▶ withdrawn   (reservation released)
                              │  └────expire────▶ expired      (reservation released)
                              └──convert──▶ converted          (order created)

    (needs_response / countered also expire when their clock runs out.)

Money rules — non-negotiable:

  * **Accepting an offer NEVER moves money.** Acceptance creates a temporary
    inventory reservation with an expiry. Payment happens only through the one
    canonical engine (:func:`orders.pay_order`), after :func:`convert_offer`
    creates a normal order at the agreed price.
  * ``amount_cents`` is the proposed PER-UNIT price (mirrors ``price_cents`` on
    the product), so ``total = amount * quantity`` with no rounding ambiguity.

Inventory rules:

  * Acceptance takes a HARD hold: a guarded atomic decrement (the same SQL shape
    ``pay_order`` uses), so an accepted offer's stock cannot be sold out from
    under the buyer during the reservation window. NULL inventory = unlimited
    (digital); the reservation row is still recorded but holds nothing.
  * Expiry / withdrawal / decline-after-accept restore exactly what was held.
  * **The one honest race:** ``convert_offer`` releases the hold and creates the
    order in a single committed transaction, and the buyer then pays through
    ``pay_order``, whose own guarded decrement is the final authority. Between
    that commit and the payment a concurrent purchase can snatch the restored
    unit; the race loses SAFELY — pay returns 409 ``insufficient_inventory``,
    no money moves, nothing oversells. Skipping pay's decrement would require
    editing the money engine, which this module deliberately does not do.

Gated by ``BUSINESS_OS_MARKETPLACE`` exactly like the rest of the package: every
entry point raises 503 ``disabled`` while the flag is off.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace.service import MarketplaceError
from services.business_os.marketplace import orders as _orders

try:
    from services.business_os.marketplace import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


# --- vocabulary --------------------------------------------------------------
OFFER_STATUSES = {
    "needs_response", "countered", "accepted",
    "declined", "expired", "withdrawn", "converted",
}
ALLOWED_OFFER_TRANSITIONS = {
    "needs_response": {"countered", "accepted", "declined", "expired", "withdrawn"},
    "countered": {"countered", "accepted", "declined", "expired", "withdrawn"},
    "accepted": {"expired", "withdrawn", "converted"},
    "declined": set(),
    "expired": set(),
    "withdrawn": set(),
    "converted": set(),
}
# States in which a negotiation is still alive (no reservation yet).
NEGOTIATING_STATUSES = {"needs_response", "countered"}

RESERVATION_STATUSES = {"active", "released", "consumed", "expired"}

# Server-authoritative defaults; callers may narrow but the server owns the cap.
OFFER_TTL_HOURS = 72          # a proposal waits at most 3 days for a response
RESERVATION_TTL_HOURS = 48    # accepted stock is held at most 2 days
MAX_TTL_HOURS = 24 * 14


# --- small helpers (same shapes as the rest of the package) ------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now_iso() -> str:
    return _iso(_now())


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _ttl_hours(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > MAX_TTL_HOURS:
        raise MarketplaceError(
            f"expires_in_hours must be an integer between 1 and {MAX_TTL_HOURS}.",
            400, "invalid_expiry")
    return value


def _emit(user_id, kind, offer_id):
    if _notify is None or user_id is None:
        return
    try:
        _notify.emit_order_event(user_id, kind, offer_id)
    except Exception:
        pass


# --- schema (additive; idempotent; mirrors schema.py ownership pattern) ------
def ensure_schema(conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_offers ("
            " offer_id TEXT PRIMARY KEY,"
            " product_id TEXT NOT NULL,"
            " buyer_user_id TEXT NOT NULL,"
            " seller_user_id TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'needs_response',"
            " currency TEXT NOT NULL DEFAULT 'usd',"
            " quantity INTEGER NOT NULL,"
            " initial_amount_cents INTEGER NOT NULL,"
            " current_amount_cents INTEGER NOT NULL,"
            " current_proposer TEXT NOT NULL DEFAULT 'buyer',"
            " expires_at TEXT,"
            " accepted_at TEXT,"
            " agreed_amount_cents INTEGER,"
            " reservation_id TEXT,"
            " converted_order_id TEXT,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_offer_events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " offer_id TEXT NOT NULL,"
            " from_status TEXT,"
            " to_status TEXT NOT NULL,"
            " actor TEXT,"
            " amount_cents INTEGER,"
            " reason TEXT,"
            " metadata_json TEXT,"
            " created_at TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_offer_reservations ("
            " reservation_id TEXT PRIMARY KEY,"
            " offer_id TEXT NOT NULL,"
            " product_id TEXT NOT NULL,"
            " quantity INTEGER NOT NULL,"
            " inventory_held INTEGER NOT NULL DEFAULT 0,"
            " status TEXT NOT NULL DEFAULT 'active',"
            " expires_at TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_offers_product "
            "ON business_os_mkt_offers (product_id, status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_offers_buyer "
            "ON business_os_mkt_offers (buyer_user_id, status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_offers_seller "
            "ON business_os_mkt_offers (seller_user_id, status)")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


# --- reads ------------------------------------------------------------------
def get_offer(offer_id: Any, *, requester_user_id: Any = None, conn=None) -> Optional[dict]:
    """Fetch an offer. With ``requester_user_id``, only the buyer or seller may
    read it; anyone else gets None (existence not leaked)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _row(conn.execute(
            "SELECT * FROM business_os_mkt_offers WHERE offer_id = ?",
            (str(offer_id),)).fetchone())
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


def get_offer_events(offer_id: Any, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_offer_events WHERE offer_id = ? ORDER BY id",
            (str(offer_id),)).fetchall()]
    finally:
        if owned:
            conn.close()


def get_reservation(reservation_id: Any, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return _row(conn.execute(
            "SELECT * FROM business_os_mkt_offer_reservations WHERE reservation_id = ?",
            (str(reservation_id),)).fetchone())
    finally:
        if owned:
            conn.close()


def list_offers(*, buyer_user_id: Any = None, seller_user_id: Any = None,
                product_id: Any = None, status: Optional[str] = None,
                limit: int = 200, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = "SELECT * FROM business_os_mkt_offers WHERE 1=1"
        params: list = []
        if buyer_user_id is not None:
            q += " AND buyer_user_id = ?"; params.append(_svc._sid(buyer_user_id))
        if seller_user_id is not None:
            q += " AND seller_user_id = ?"; params.append(_svc._sid(seller_user_id))
        if product_id is not None:
            q += " AND product_id = ?"; params.append(str(product_id))
        if status:
            if status not in OFFER_STATUSES:
                raise MarketplaceError("Unknown offer status.", 400, "invalid_status")
            q += " AND status = ?"; params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        return [_row(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


# --- internal helpers --------------------------------------------------------
def _record_event(conn, offer_id, from_status, to_status, actor,
                  amount_cents=None, reason=None, meta=None):
    conn.execute(
        "INSERT INTO business_os_mkt_offer_events "
        "(offer_id, from_status, to_status, actor, amount_cents, reason, "
        "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(offer_id), from_status, to_status,
         None if actor is None else str(actor),
         amount_cents, reason,
         None if meta is None else json.dumps(meta, sort_keys=True), _now_iso()))


def _assert_transition(cur_status, target):
    if target not in ALLOWED_OFFER_TRANSITIONS.get(cur_status, set()):
        raise MarketplaceError(
            f"Illegal offer transition {cur_status} -> {target}.",
            409, "illegal_transition")


def _validate_amount(amount_cents):
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents <= 0:
        raise MarketplaceError(
            "amount_cents must be a positive integer (per-unit).", 400, "invalid_amount")


def _recipient_of(offer: dict) -> str:
    """The party whose turn it is to respond to the proposal on the table."""
    return (offer["seller_user_id"] if offer.get("current_proposer") == "buyer"
            else offer["buyer_user_id"])


def _require_recipient(offer: dict, user_id: Any) -> str:
    rid = _svc._sid(user_id)
    if rid != _recipient_of(offer):
        # Not-found for strangers (no existence leak); explicit 409 for the
        # party who proposed and is trying to answer their own proposal.
        if rid in (offer.get("buyer_user_id"), offer.get("seller_user_id")):
            raise MarketplaceError(
                "It is the other party's turn to respond to this proposal.",
                409, "not_your_turn")
        raise MarketplaceError("Offer not found.", 404, "not_found")
    return rid


def _release_hold(conn, reservation: dict, to_status: str) -> None:
    """Restore held stock (if any) and close the reservation row."""
    if to_status not in RESERVATION_STATUSES:  # pragma: no cover - internal misuse
        raise MarketplaceError("Bad reservation status.", 500, "internal")
    if reservation.get("status") != "active":
        return
    if int(reservation.get("inventory_held") or 0):
        conn.execute(
            "UPDATE business_os_mkt_products SET inventory_qty = inventory_qty + ?, "
            "updated_at = ? WHERE product_id = ? AND inventory_qty IS NOT NULL",
            (reservation["quantity"], _now_iso(), reservation["product_id"]))
    conn.execute(
        "UPDATE business_os_mkt_offer_reservations SET status = ?, updated_at = ? "
        "WHERE reservation_id = ?",
        (to_status, _now_iso(), reservation["reservation_id"]))


def _expire_offer_row(conn, offer: dict, *, reason: str) -> None:
    """Flip one offer to expired, releasing its reservation if it holds one.
    Caller owns the transaction."""
    if offer.get("reservation_id"):
        res = get_reservation(offer["reservation_id"], conn=conn)
        if res is not None:
            _release_hold(conn, res, "expired")
    conn.execute(
        "UPDATE business_os_mkt_offers SET status = 'expired', updated_at = ? "
        "WHERE offer_id = ?", (_now_iso(), offer["offer_id"]))
    _record_event(conn, offer["offer_id"], offer.get("status"), "expired",
                  None, reason=reason)
    _svc._audit(conn, subject_type="offer", subject_ref=offer["offer_id"],
                action="offer.expire", actor=None, reason=reason,
                before={"status": offer.get("status")}, after={"status": "expired"})


def _lapsed(offer: dict, now: Optional[datetime] = None) -> bool:
    """Whether this offer's clock has run out (negotiation expiry, or the
    reservation expiry once accepted)."""
    now = now or _now()
    status = offer.get("status")
    if status in NEGOTIATING_STATUSES:
        exp = offer.get("expires_at")
        return bool(exp) and _parse_iso(exp) <= now
    if status == "accepted":
        exp = offer.get("expires_at")
        return bool(exp) and _parse_iso(exp) <= now
    return False


# --- create ------------------------------------------------------------------
def create_offer(buyer_user_id: Any, product_id: Any, amount_cents: int, *,
                 quantity: int = 1, expires_in_hours: Optional[int] = None,
                 context: Optional[dict] = None, conn=None) -> dict:
    """Buyer proposes a per-unit price on an ``active`` product. Moves no money,
    holds no stock — a proposal is words, not a claim on inventory."""
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    _validate_amount(amount_cents)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise MarketplaceError("quantity must be a positive integer.", 400, "invalid_quantity")
    ttl = _ttl_hours(expires_in_hours, OFFER_TTL_HOURS)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        product = _svc.get_product(product_id, for_public=True, conn=conn)
        if product is None:
            raise MarketplaceError("Product not available.", 404, "not_found")
        bid = _svc._sid(buyer_user_id)
        if product.get("seller_user_id") == bid:
            raise MarketplaceError("You cannot make an offer on your own product.",
                                   400, "self_offer")
        inv = product.get("inventory_qty")
        if inv is not None and inv < quantity:
            raise MarketplaceError("Not enough inventory.", 409, "insufficient_inventory")
        dup = conn.execute(
            "SELECT offer_id FROM business_os_mkt_offers WHERE product_id = ? "
            "AND buyer_user_id = ? AND status IN ('needs_response','countered','accepted')",
            (product["product_id"], bid)).fetchone()
        if dup is not None:
            raise MarketplaceError(
                "You already have an open offer on this product.", 409, "duplicate_offer")
        oid = "mkoff_" + uuid.uuid4().hex
        now = _now()
        conn.execute(
            "INSERT INTO business_os_mkt_offers "
            "(offer_id, product_id, buyer_user_id, seller_user_id, status, currency, "
            "quantity, initial_amount_cents, current_amount_cents, current_proposer, "
            "expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'needs_response', ?, ?, ?, ?, 'buyer', ?, ?, ?)",
            (oid, product["product_id"], bid, product["seller_user_id"],
             product.get("currency", "usd"), quantity, int(amount_cents),
             int(amount_cents), _iso(now + timedelta(hours=ttl)),
             _iso(now), _iso(now)))
        _record_event(conn, oid, None, "needs_response", bid, amount_cents=int(amount_cents))
        _svc._audit(conn, subject_type="offer", subject_ref=oid, action="offer.create",
                    actor=bid, after={"status": "needs_response",
                                      "amount_cents": int(amount_cents),
                                      "quantity": quantity})
        if owned:
            conn.commit()
        _emit(product["seller_user_id"], "offer_received", oid)
        return get_offer(oid, conn=conn)
    finally:
        if owned:
            conn.close()


# --- respond: counter / accept / decline -------------------------------------
def counter_offer(offer_id: Any, user_id: Any, amount_cents: int, *,
                  expires_in_hours: Optional[int] = None,
                  context: Optional[dict] = None, conn=None) -> dict:
    """The recipient of the current proposal puts a new per-unit price on the
    table. Turn flips; the expiry clock resets."""
    _svc._require_enabled()
    _svc._require_not_held(user_id, context)
    _validate_amount(amount_cents)
    ttl = _ttl_hours(expires_in_hours, OFFER_TTL_HOURS)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        offer = get_offer(offer_id, requester_user_id=user_id, conn=conn)
        if offer is None:
            raise MarketplaceError("Offer not found.", 404, "not_found")
        if _lapsed(offer):
            _expire_offer_row(conn, offer, reason="lapsed_on_touch")
            if owned:
                conn.commit()
            raise MarketplaceError("Offer has expired.", 409, "offer_expired")
        _assert_transition(offer["status"], "countered")
        rid = _require_recipient(offer, user_id)
        new_proposer = "seller" if rid == offer["seller_user_id"] else "buyer"
        now = _now()
        conn.execute(
            "UPDATE business_os_mkt_offers SET status = 'countered', "
            "current_amount_cents = ?, current_proposer = ?, expires_at = ?, "
            "updated_at = ? WHERE offer_id = ?",
            (int(amount_cents), new_proposer, _iso(now + timedelta(hours=ttl)),
             _iso(now), offer["offer_id"]))
        _record_event(conn, offer_id, offer["status"], "countered", rid,
                      amount_cents=int(amount_cents))
        _svc._audit(conn, subject_type="offer", subject_ref=offer["offer_id"],
                    action="offer.counter", actor=rid,
                    before={"status": offer["status"],
                            "amount_cents": offer["current_amount_cents"]},
                    after={"status": "countered", "amount_cents": int(amount_cents)})
        if owned:
            conn.commit()
        other = (offer["buyer_user_id"] if rid == offer["seller_user_id"]
                 else offer["seller_user_id"])
        _emit(other, "offer_countered", offer["offer_id"])
        return get_offer(offer_id, conn=conn)
    finally:
        if owned:
            conn.close()


def accept_offer(offer_id: Any, user_id: Any, *,
                 reservation_hours: Optional[int] = None,
                 context: Optional[dict] = None, conn=None) -> dict:
    """The recipient accepts the price on the table. NO money moves. Stock is
    HELD: a guarded atomic decrement (same shape as ``pay_order``'s) backed by a
    reservation row with an expiry. The buyer completes via :func:`convert_offer`
    then the canonical payment path."""
    _svc._require_enabled()
    _svc._require_not_held(user_id, context)
    ttl = _ttl_hours(reservation_hours, RESERVATION_TTL_HOURS)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        offer = get_offer(offer_id, requester_user_id=user_id, conn=conn)
        if offer is None:
            raise MarketplaceError("Offer not found.", 404, "not_found")
        if _lapsed(offer):
            _expire_offer_row(conn, offer, reason="lapsed_on_touch")
            if owned:
                conn.commit()
            raise MarketplaceError("Offer has expired.", 409, "offer_expired")
        _assert_transition(offer["status"], "accepted")
        rid = _require_recipient(offer, user_id)

        # Product must still be purchasable at acceptance time.
        product = _svc.get_product(offer["product_id"], for_public=True, conn=conn)
        if product is None:
            raise MarketplaceError("Product is no longer available.", 409, "product_unavailable")

        # Hard hold: guarded atomic decrement; NULL inventory = unlimited.
        held = 0
        cur = conn.execute(
            "UPDATE business_os_mkt_products SET inventory_qty = inventory_qty - ?, "
            "updated_at = ? WHERE product_id = ? AND inventory_qty IS NOT NULL "
            "AND inventory_qty >= ?",
            (offer["quantity"], _now_iso(), offer["product_id"], offer["quantity"]))
        if getattr(cur, "rowcount", 0) == 0:
            if product.get("inventory_qty") is not None:
                raise MarketplaceError("Item sold out.", 409, "insufficient_inventory")
            # unlimited — reservation recorded, nothing decremented
        else:
            held = 1

        now = _now()
        res_id = "mkres_" + uuid.uuid4().hex
        expires = _iso(now + timedelta(hours=ttl))
        conn.execute(
            "INSERT INTO business_os_mkt_offer_reservations "
            "(reservation_id, offer_id, product_id, quantity, inventory_held, "
            "status, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (res_id, offer["offer_id"], offer["product_id"], offer["quantity"],
             held, expires, _iso(now), _iso(now)))
        conn.execute(
            "UPDATE business_os_mkt_offers SET status = 'accepted', accepted_at = ?, "
            "agreed_amount_cents = current_amount_cents, reservation_id = ?, "
            "expires_at = ?, updated_at = ? WHERE offer_id = ?",
            (_iso(now), res_id, expires, _iso(now), offer["offer_id"]))
        _record_event(conn, offer_id, offer["status"], "accepted", rid,
                      amount_cents=offer["current_amount_cents"],
                      meta={"reservation_id": res_id, "reservation_expires_at": expires})
        _svc._audit(conn, subject_type="offer", subject_ref=offer["offer_id"],
                    action="offer.accept", actor=rid,
                    before={"status": offer["status"]},
                    after={"status": "accepted",
                           "agreed_amount_cents": offer["current_amount_cents"],
                           "reservation_id": res_id})
        if owned:
            conn.commit()
        other = (offer["buyer_user_id"] if rid == offer["seller_user_id"]
                 else offer["seller_user_id"])
        _emit(other, "offer_accepted", offer["offer_id"])
        return get_offer(offer_id, conn=conn)
    finally:
        if owned:
            conn.close()


def decline_offer(offer_id: Any, user_id: Any, *, reason: Optional[str] = None,
                  context: Optional[dict] = None, conn=None) -> dict:
    """The recipient declines the proposal on the table. Terminal."""
    _svc._require_enabled()
    _svc._require_not_held(user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        offer = get_offer(offer_id, requester_user_id=user_id, conn=conn)
        if offer is None:
            raise MarketplaceError("Offer not found.", 404, "not_found")
        _assert_transition(offer["status"], "declined")
        rid = _require_recipient(offer, user_id)
        conn.execute(
            "UPDATE business_os_mkt_offers SET status = 'declined', updated_at = ? "
            "WHERE offer_id = ?", (_now_iso(), offer["offer_id"]))
        _record_event(conn, offer_id, offer["status"], "declined", rid, reason=reason)
        _svc._audit(conn, subject_type="offer", subject_ref=offer["offer_id"],
                    action="offer.decline", actor=rid, reason=reason,
                    before={"status": offer["status"]}, after={"status": "declined"})
        if owned:
            conn.commit()
        other = (offer["buyer_user_id"] if rid == offer["seller_user_id"]
                 else offer["seller_user_id"])
        _emit(other, "offer_declined", offer["offer_id"])
        return get_offer(offer_id, conn=conn)
    finally:
        if owned:
            conn.close()


def withdraw_offer(offer_id: Any, buyer_user_id: Any, *,
                   context: Optional[dict] = None, conn=None) -> dict:
    """The buyer pulls their offer at any live stage. If it was accepted, the
    reservation is released and held stock restored."""
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        offer = get_offer(offer_id, requester_user_id=buyer_user_id, conn=conn)
        if offer is None:
            raise MarketplaceError("Offer not found.", 404, "not_found")
        bid = _svc._sid(buyer_user_id)
        if bid != offer["buyer_user_id"]:
            raise MarketplaceError("Only the buyer may withdraw an offer.",
                                   403, "not_buyer")
        _assert_transition(offer["status"], "withdrawn")
        if offer.get("reservation_id"):
            res = get_reservation(offer["reservation_id"], conn=conn)
            if res is not None:
                _release_hold(conn, res, "released")
        conn.execute(
            "UPDATE business_os_mkt_offers SET status = 'withdrawn', updated_at = ? "
            "WHERE offer_id = ?", (_now_iso(), offer["offer_id"]))
        _record_event(conn, offer_id, offer["status"], "withdrawn", bid)
        _svc._audit(conn, subject_type="offer", subject_ref=offer["offer_id"],
                    action="offer.withdraw", actor=bid,
                    before={"status": offer["status"]}, after={"status": "withdrawn"})
        if owned:
            conn.commit()
        return get_offer(offer_id, conn=conn)
    finally:
        if owned:
            conn.close()


# --- expiry sweep ------------------------------------------------------------
def expire_offers(*, now: Optional[datetime] = None, conn=None) -> int:
    """Sweep every live offer whose clock has run out. Negotiating offers simply
    expire; accepted offers also release their reservation and restore stock.
    Safe to run repeatedly (idempotent per offer). Returns how many expired."""
    _svc._require_enabled()
    now = now or _now()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_offers "
            "WHERE status IN ('needs_response','countered','accepted') "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (_iso(now),)).fetchall()]
        for offer in rows:
            _expire_offer_row(conn, offer, reason="sweep")
        if owned:
            conn.commit()
        return len(rows)
    finally:
        if owned:
            conn.close()


# --- convert (accepted -> real order at the agreed price) --------------------
def convert_offer(offer_id: Any, buyer_user_id: Any, *,
                  context: Optional[dict] = None, conn=None) -> dict:
    """accepted ─▶ converted. Creates a CANONICAL order (same table, same event
    stream, same downstream payment engine) at the agreed per-unit price, and
    consumes the reservation in the same committed transaction. NO money moves
    here — the buyer pays through :func:`orders.pay_order` like any other order.

    The reservation's held stock is restored just before the order is created,
    because ``pay_order`` performs its own guarded decrement and must not
    double-count. See the module docstring for why this hand-off race is safe.
    """
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        offer = get_offer(offer_id, requester_user_id=buyer_user_id, conn=conn)
        if offer is None:
            raise MarketplaceError("Offer not found.", 404, "not_found")
        bid = _svc._sid(buyer_user_id)
        if bid != offer["buyer_user_id"]:
            raise MarketplaceError("Only the buyer may convert an offer.", 403, "not_buyer")
        if _lapsed(offer):
            _expire_offer_row(conn, offer, reason="lapsed_on_touch")
            if owned:
                conn.commit()
            raise MarketplaceError("Offer has expired.", 409, "offer_expired")
        _assert_transition(offer["status"], "converted")

        res = get_reservation(offer["reservation_id"], conn=conn) if offer.get("reservation_id") else None
        if res is None or res.get("status") != "active":
            raise MarketplaceError("Reservation is no longer active.", 409, "reservation_gone")

        product = _svc.get_product(offer["product_id"], for_public=True, conn=conn)
        if product is None:
            raise MarketplaceError("Product is no longer available.", 409, "product_unavailable")

        unit = int(offer["agreed_amount_cents"])
        quantity = int(offer["quantity"])
        subtotal = unit * quantity
        fee_bps = _orders.DEFAULT_FEE_BPS
        fee, net = _orders._fee_split(subtotal, fee_bps)
        order_id = "mkto_" + uuid.uuid4().hex
        now = _now_iso()

        # Release the hold (pay_order will take its own guarded decrement) and
        # consume the reservation — same transaction as the order insert.
        _release_hold(conn, res, "consumed")
        conn.execute(
            "INSERT INTO business_os_mkt_orders "
            "(order_id, buyer_user_id, seller_user_id, status, currency, subtotal_cents, "
            "total_cents, platform_fee_bps, platform_fee_cents, seller_net_cents, "
            "refunded_cents, fulfillment_type, created_at, updated_at) "
            "VALUES (?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (order_id, bid, offer["seller_user_id"], offer.get("currency", "usd"),
             subtotal, subtotal, fee_bps, fee, net,
             product.get("fulfillment_type", "physical"), now, now))
        conn.execute(
            "INSERT INTO business_os_mkt_order_items "
            "(order_id, product_id, title, unit_price_cents, quantity, line_total_cents, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_id, offer["product_id"], product.get("title"), unit, quantity,
             subtotal, now))
        _orders._record_event(conn, order_id, None, "created", bid,
                              meta={"offer_id": offer["offer_id"],
                                    "agreed_amount_cents": unit})
        conn.execute(
            "UPDATE business_os_mkt_offers SET status = 'converted', "
            "converted_order_id = ?, updated_at = ? WHERE offer_id = ?",
            (order_id, now, offer["offer_id"]))
        _record_event(conn, offer_id, "accepted", "converted", bid,
                      amount_cents=unit, meta={"order_id": order_id})
        _svc._audit(conn, subject_type="offer", subject_ref=offer["offer_id"],
                    action="offer.convert", actor=bid,
                    before={"status": "accepted"},
                    after={"status": "converted", "order_id": order_id,
                           "total_cents": subtotal})
        if owned:
            conn.commit()
        return get_offer(offer_id, conn=conn)
    finally:
        if owned:
            conn.close()
